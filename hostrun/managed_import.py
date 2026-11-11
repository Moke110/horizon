"""Identity receipts for local material copies and their business ledger."""
import ctypes
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager, ExitStack
from ctypes import wintypes as w

from common import file_lock
from hostrun.cleanup import (InstanceBusy, InstanceInvalid, _lexical_home, control_directory, material_name,
                             instance_lock, preview, _read_registration, _write_registration)
from hostrun.windows_cleanup import PinnedEntry, io_path, pin_ancestors


def managed_paths(home, archive=None):
    """Detect the lexical controlled location before callers resolve links."""
    if os.name != 'nt':
        return None
    home = _lexical_home(home)
    if home.parent.parent != control_directory():
        return None
    if home.name != 'data':
        raise InstanceInvalid('Invalid material home')
    expected = home.parent / 'archive'
    if archive is not None and _lexical_home(archive) != expected:
        raise InstanceInvalid('Invalid material archive')
    return home, expected


def _blocks(pin):
    api = pin.api
    api.SetFilePointerEx.argtypes = [w.HANDLE, ctypes.c_longlong, ctypes.c_void_p, w.DWORD]
    api.ReadFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    if not api.SetFilePointerEx(pin.handle, 0, None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(1 << 20)
    size = w.DWORD()
    while True:
        if not api.ReadFile(pin.handle, buffer, len(buffer), ctypes.byref(size), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if not size.value:
            break
        yield buffer.raw[:size.value]


class MaterialStore:
    def __init__(self, home, held):
        self.home, self.root, self.held = home, home.parent, held
        self.registration = home.parent.parent / (home.parent.name + '.json')
        self.record = _read_registration(self.registration)
        self.pins = {}

    def save(self):
        _write_registration(self.registration, self.record)

    def inventory(self, name, directory=False):
        prefix = 'archive' if name.startswith('archive/') else 'data'
        return self.record.setdefault(prefix + ('_directories' if directory else '_inventory'), {})

    def pin(self, name, *, create=False, directory=False):
        if name in self.pins:
            return self.pins[name]
        path = self.root / name
        if name in ('data', 'archive'):
            expected = self.record['children'][name]
            pin = PinnedEntry(path)
        else:
            parent_name = name.rsplit('/', 1)[0]
            parent = self.pin(parent_name, create=create, directory=True)
            inventory = self.inventory(name, directory)
            expected = inventory.get(name)
            if expected is None:
                if not create:
                    raise InstanceInvalid('Unowned material entry')
                pin = PinnedEntry(path, create_parent=parent, create_share=3, create_delete=not directory,
                                  create_file=not directory, writable=not directory)
                self.held.callback(pin.close)
                stat = io_path(path).stat()
                expected = inventory[name] = [stat.st_dev, stat.st_ino]
                try:
                    self.save()
                except BaseException:
                    if not directory:
                        pin.delete()
                    raise
            else:
                pin = PinnedEntry(path, delete=not directory)
        self.held.callback(pin.close)
        stat = io_path(path).stat()
        if expected != [stat.st_dev, stat.st_ino] or (not directory and name not in ('data', 'archive') and stat.st_nlink != 1):
            raise InstanceInvalid('Material identity changed')
        self.pins[name] = pin
        return pin

    def existing(self, name):
        path = io_path(self.root / name)
        if path.exists() or path.is_symlink() or path.is_junction():
            return self.pin(name)
        if name in self.inventory(name):
            raise InstanceInvalid('Missing material entry')
        return None

    def digest(self, pin):
        digest = hashlib.md5()
        for block in _blocks(pin):
            digest.update(block)
        return digest.hexdigest()

    def recover(self):
        journal = self.record.get('material_update')
        if not journal:
            return
        target, temporary, backup = (journal[key] for key in ('target', 'temporary', 'backup'))
        inventory = self.inventory(target)
        observed = {}
        # All parent handles remain held through the final receipt.
        for name in (target, temporary, backup):
            self.pin(name.rsplit('/', 1)[0], directory=True)
            path = io_path(self.root / name)
            if path.exists() or path.is_symlink() or path.is_junction():
                pin = PinnedEntry(self.root / name, delete=True)
                self.held.callback(pin.close)
                stat = path.stat()
                identity = [stat.st_dev, stat.st_ino]
                allowed = (journal['new'], journal['previous']) if name == target else (journal['new'],) if name == temporary else (journal['previous'],)
                if identity not in allowed or stat.st_nlink != 1:
                    raise InstanceInvalid('Material publication identity changed')
                observed[name] = (pin, identity)
        current = observed.get(target)
        if current and current[1] == journal['new']:
            if self.digest(current[0]) != journal['digest']:
                raise InstanceInvalid('Material publication content changed')
            selected = journal['new']
        elif current and current[1] == journal['previous']:
            selected = journal['previous']
        elif not current and journal['previous'] is None:
            selected = None
        elif not current and backup in observed and observed[backup][1] == journal['previous']:
            previous_pin = observed.pop(backup)[0]
            previous_pin.rename(self.root / target)
            current = (previous_pin, journal['previous'])
            selected = journal['previous']
        else:
            raise InstanceInvalid('Material publication cannot recover')
        if selected is None:
            inventory.pop(target, None)
        else:
            inventory[target] = selected
        self.save()
        for name in (temporary, backup):
            if name in observed:
                observed[name][0].delete()
            inventory.pop(name, None)
        self.record.pop('material_update')
        self.save()
        if selected is not None:
            self.pins[target] = current[0]

    def publish(self, target, blocks, expected=None):
        existing = self.existing(target)
        parent = target.rsplit('/', 1)[0]
        self.pin(parent, create=True, directory=True)
        temporary = parent + '/.horizon-copy-' + uuid.uuid4().hex + '.tmp'
        backup = parent + '/.horizon-copy-' + uuid.uuid4().hex + '.tmp'
        created = self.pin(temporary, create=True)
        written = False
        for block in blocks:
            created.write_bytes(block, append=True)
            written = True
        if not written:
            created.write_bytes(b'')
        digest = self.digest(created)
        if expected is not None and digest != expected:
            raise InstanceInvalid('Material copy verification failed')
        inventory = self.inventory(target)
        new, previous = inventory[temporary], inventory.get(target)
        self.record['material_update'] = {'target': target, 'temporary': temporary, 'backup': backup,
                                          'new': new, 'previous': previous, 'digest': digest}
        if previous is not None:
            inventory[backup] = previous
        self.save()
        if existing:
            existing.rename(self.root / backup)
        # The durable journal retains both possible target identities until commit.
        created.rename(self.root / target)
        inventory[target] = new
        self.save()
        if existing:
            existing.delete()
        inventory.pop(temporary, None)
        inventory.pop(backup, None)
        self.record.pop('material_update')
        self.save()


@contextmanager
def store(home, archive=None):
    paths = managed_paths(home, archive)
    if paths is None:
        raise InstanceInvalid('Not a managed material home')
    home, archive = paths
    with instance_lock(home) as acquired:
        if not acquired:
            raise InstanceBusy('instance_busy')
        with file_lock(home.parent.parent / (home.parent.name + '.inventory.lock')), pin_ancestors(home.parent), ExitStack() as held:
            preview(home.parent.parent, home.parent.name)
            state = MaterialStore(home, held)
            state.recover()
            yield state


def validate_copy(home, target):
    with store(home) as state:
        relative = target.relative_to(state.root).as_posix()
        if not material_name(relative) or re.fullmatch(r'\.horizon-copy-[0-9a-f]{32}\.tmp', target.name):
            raise InstanceInvalid('Invalid material destination')
        pin = state.existing(relative)
        return state.digest(pin) if pin is not None else None


def copy_verified(home, source, target, expected):
    with store(home) as state:
        relative = target.relative_to(state.root).as_posix()
        if not material_name(relative) or re.fullmatch(r'\.horizon-copy-[0-9a-f]{32}\.tmp', target.name):
            raise InstanceInvalid('Invalid material destination')
        if state.existing(relative):
            raise InstanceInvalid('Material target already exists')
        with io_path(source).open('rb') as stream:
            state.publish(relative, iter(lambda: stream.read(1 << 20), b''), expected)
    return expected


def load_ledger(home):
    with store(home) as state:
        pin = state.existing('data/archive-index/index.jsonl')
        if pin is None:
            return []
        return [json.loads(line) for line in b''.join(_blocks(pin)).decode('utf-8').splitlines() if line.strip()]


def register_ledger(home, archive, *, dry_run=False):
    from hostrun import archive_index
    with store(home, archive) as state:
        state.pin('archive', directory=True)
        pin = state.existing('data/archive-index/index.jsonl')
        existing = [] if pin is None else [json.loads(line) for line in b''.join(_blocks(pin)).decode('utf-8').splitlines() if line.strip()]
        known = {row.get('md5') for row in existing if row.get('md5')}
        scanned = duplicates = 0
        added = []
        # Business indexing is read-only discovery; it grants no deletion ownership.
        for path, relative in archive_index.material_files(io_path(archive)):
            if re.fullmatch(r'\.horizon-copy-[0-9a-f]{32}\.tmp', path.name):
                continue
            scanned += 1
            digest = archive_index.file_md5(path)
            if digest in known:
                duplicates += 1
                continue
            known.add(digest)
            added.append({'schema_version': 1, 'business_id': archive_index.business_id(relative),
                          'path': relative.as_posix(), 'md5': digest, 'size': io_path(path).stat().st_size})
        if added and not dry_run:
            payload = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in [*existing, *added]).encode('utf-8')
            state.publish('data/archive-index/index.jsonl', (payload,))
        return {'ok': True, 'index': str(state.home / 'archive-index/index.jsonl'), 'scanned': scanned,
                'registered': len(added), 'duplicates': duplicates, 'existing': len(existing),
                'total': len(existing) + len(added), 'entries': added}
