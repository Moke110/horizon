"""Internal enrolled data snapshots; public managed upgrades remain disabled."""
from contextlib import contextmanager, ExitStack, closing
import hashlib
import os
import re
import sqlite3
import uuid

from common import file_lock
from hostrun import cleanup
from hostrun.windows_cleanup import PinnedEntry, pin_ancestors


class _DataTransaction:
    def __init__(self, home, held):
        self.home, self.root, self.held = home, home.parent, held
        self.registration = self.root.parent / (self.root.name + '.json')
        self.record = cleanup._read_registration(self.registration)
        self.inventory = self.record.setdefault('data_inventory', {})
        self.directories = self.record.setdefault('data_directories', {})
        self.pins = {}

    def save(self):
        cleanup._write_registration(self.registration, self.record)

    def directory(self, path):
        if path in self.pins:
            return self.pins[path]
        parent = self.directory(path.parent) if path != self.home else None
        name = path.relative_to(self.root).as_posix()
        if path.exists():
            pin = PinnedEntry(path)
            self.held.callback(pin.close)
            stat = path.stat()
            if path != self.home and self.directories.get(name) != [stat.st_dev, stat.st_ino]:
                raise cleanup.InstanceInvalid('Data directory identity changed')
        else:
            pin = PinnedEntry(path, create_parent=parent, create_share=3, create_delete=False)
            self.held.callback(pin.close)
            stat = path.stat()
            self.directories[name] = [stat.st_dev, stat.st_ino]
            self.save()  # Failed receipts leave unknown empty directories, not adopted contents.
        self.pins[path] = pin
        return pin

    def pin(self, name, *, writable=False):
        path = self.root / name
        self.directory(path.parent)
        pin = PinnedEntry(path, writable=writable)
        self.held.callback(pin.close)
        stat = path.stat()
        if self.inventory.get(name) != [stat.st_dev, stat.st_ino]:
            raise cleanup.InstanceInvalid('Data file identity changed')
        return pin

    def create(self, name):
        path = self.root / name
        parent = self.directory(path.parent)
        pin = PinnedEntry(path, create_parent=parent, create_file=True, writable=True, create_share=3)
        self.held.callback(pin.close)
        stat = path.stat()
        self.inventory[name] = [stat.st_dev, stat.st_ino]
        try:
            self.save()
        except BaseException:
            pin.delete()
            raise
        return pin


@contextmanager
def _transaction(home):
    home = cleanup._lexical_home(home)
    control, identity = home.parent.parent, home.parent.name
    if control != cleanup.control_directory() or home.name != 'data':
        raise cleanup.InstanceInvalid('Unexpected managed home')
    cleanup.preview(control, identity)
    with file_lock(control / (identity + '.gate'), blocking=False) as gate, \
            cleanup.lifecycle_lock(control / (identity + '.lock'), exclusive=True) as available:
        if not gate or not available:
            raise cleanup.InstanceBusy('instance_busy')
        with pin_ancestors(home), ExitStack() as held:
            cleanup.preview(control, identity)
            yield _DataTransaction(home, held)


def backup_owned_data(home):
    """Snapshot only registered business files; never discover ownership by scanning."""
    with _transaction(home) as tx:
        if tx.record.get('upgrade') or tx.record.get('model_update'):
            raise cleanup.InstanceInvalid('Pending transaction prevents data backup')
        sources = sorted(name for name in tx.inventory if re.fullmatch(cleanup.UPGRADE_DATA_PATTERN, name))
        for name in sources:
            tx.pin(name)
        database = 'data/horizon.sqlite3'
        if database in sources:
            with (tx.root / database).open('rb') as stream:
                header = stream.read(20)
            if header and (len(header) < 20 or header[18:20] != b'\x01\x01'):
                raise cleanup.InstanceInvalid('Unsupported managed database journal mode')
            tx.pin(database + '-journal')
            if any((tx.home / ('horizon.sqlite3' + suffix)).exists() for suffix in ('-wal', '-shm')):
                raise cleanup.InstanceInvalid('Unenrolled SQLite sidecar')
        identity = uuid.uuid4().hex
        snapshot = {'complete': False, 'files': {}}
        tx.record.setdefault('data_backups', {})[identity] = snapshot
        tx.save()
        for source in sources:
            target = 'data/upgrade-backups/' + identity + '/' + source.removeprefix('data/')
            created = tx.create(target)
            if source == database:
                created.close()
                tx.pin(target)
                with closing(sqlite3.connect(tx.root / source)) as reader, closing(sqlite3.connect(tx.root / target)) as writer:
                    reader.execute('PRAGMA locking_mode=EXCLUSIVE')
                    if reader.execute('PRAGMA journal_mode=PERSIST').fetchone()[0] != 'persist':
                        raise cleanup.InstanceInvalid('Unsupported backup journal mode')
                    writer.execute('PRAGMA journal_mode=OFF')
                    reader.backup(writer)
                with (tx.root / target).open('r+b') as stream:
                    os.fsync(stream.fileno())
            else:
                created.write_bytes((tx.root / source).read_bytes())
                created.close()
                tx.pin(target)
            snapshot['files'][source] = hashlib.sha256((tx.root / target).read_bytes()).hexdigest()
            tx.save()
        snapshot['complete'] = True
        tx.save()
    return {'status': 'backed_up', 'snapshot_id': identity}


def restore_owned_data(home, snapshot_id):
    """Restore authenticated snapshot bytes only into currently enrolled targets."""
    with _transaction(home) as tx:
        if tx.record.get('model_update'):
            raise cleanup.InstanceInvalid('Pending model transaction prevents data restore')
        snapshot = tx.record.get('data_backups', {}).get(snapshot_id)
        if not snapshot or not snapshot['complete'] or tx.record.get('upgrade') not in (None, snapshot_id):
            raise cleanup.InstanceInvalid('Unavailable data snapshot')
        payloads, targets = {}, {}
        for name, digest in snapshot['files'].items():
            backup = 'data/upgrade-backups/' + snapshot_id + '/' + name.removeprefix('data/')
            tx.pin(backup)
            payload = (tx.root / backup).read_bytes()
            if hashlib.sha256(payload).hexdigest() != digest:
                raise cleanup.InstanceInvalid('Backup content changed')
            payloads[name] = payload
            if (tx.root / name).exists():
                targets[name] = tx.pin(name, writable=True)
        journal = 'data/horizon.sqlite3-journal'
        if 'data/horizon.sqlite3' in payloads:
            targets[journal] = tx.pin(journal, writable=True)
        tx.record['upgrade'] = snapshot_id
        tx.save()
        for name, payload in payloads.items():
            pin = targets.get(name) or tx.create(name)
            pin.write_bytes(payload)
        if journal in targets:
            targets[journal].write_bytes(b'')
        tx.record.pop('upgrade')
        tx.save()
    return {'status': 'restored', 'snapshot_id': snapshot_id}
