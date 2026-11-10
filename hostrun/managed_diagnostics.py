"""Creation-time identities for the two fixed managed diagnostic log files."""
import os
from contextlib import ExitStack

from common import file_lock
from hostrun.cleanup import (InstanceBusy, InstanceInvalid, _lexical_home, control_directory,
                             instance_lock, preview, _read_registration, _write_registration)


def append_managed_event(home, payload, *, limit):
    if os.name != 'nt':
        return False
    home = _lexical_home(home)
    control = control_directory()
    if home.parent.parent != control:
        return False
    from hostrun.windows_cleanup import PinnedEntry, pin_ancestors
    identity = home.parent.name
    registration = control / (identity + '.json')
    with instance_lock(home) as available:
        if not available:
            raise InstanceBusy('instance_busy')
        with file_lock(control / (identity + '.inventory.lock'), blocking=False) as acquired, pin_ancestors(home), ExitStack() as held:
            if not acquired:
                return True  # Bounded diagnostics may drop an event while state is busy.
            preview(control, identity)
            record = _read_registration(registration)
            directories = record.setdefault('data_directories', {})
            inventory = record.setdefault('data_inventory', {})
            parent = PinnedEntry(home)
            held.callback(parent.close)
            folder = home / 'diagnostics'
            expected = directories.get('data/diagnostics')
            if expected is None:
                created = PinnedEntry(folder, create_parent=parent)
                try:
                    stat = folder.stat()
                    expected = [stat.st_dev, stat.st_ino]
                    directories['data/diagnostics'] = expected
                    try:
                        _write_registration(registration, record)
                    except BaseException:
                        created.delete()
                        raise
                finally:
                    created.close()
            folder_pin = PinnedEntry(folder)
            held.callback(folder_pin.close)
            stat = folder.stat()
            if expected != [stat.st_dev, stat.st_ino]:
                raise InstanceInvalid('Diagnostic directory identity changed')
            pins = {}
            for name in ('events.jsonl', 'events.previous.jsonl'):
                path = folder / name
                relative = 'data/diagnostics/' + name
                expected = inventory.get(relative)
                if expected is None:
                    created = PinnedEntry(path, create_parent=folder_pin, create_file=True, writable=True)
                    try:
                        stat = path.stat()
                        expected = [stat.st_dev, stat.st_ino]
                        inventory[relative] = expected
                        try:
                            _write_registration(registration, record)
                        except BaseException:
                            created.delete()
                            raise
                    finally:
                        created.close()
                pin = PinnedEntry(path, writable=True)
                held.callback(pin.close)
                stat = path.stat()
                if expected != [stat.st_dev, stat.st_ino]:
                    raise InstanceInvalid('Diagnostic file identity changed')
                pins[name] = pin
            current = folder / 'events.jsonl'
            if current.stat().st_size >= limit:
                # Keep both enrolled FileIDs: flush the previous segment before
                # truncating the current one. An interruption can duplicate events.
                pins['events.previous.jsonl'].write_bytes(current.read_bytes())
                pins['events.jsonl'].write_bytes(b'')
            pins['events.jsonl'].write_bytes(payload, append=True)
    return True
