"""SQLite connections with creation-time identities for managed test instances."""
import os
import sqlite3
from contextlib import contextmanager, ExitStack, closing
from pathlib import Path

from common import file_lock
from hostrun.cleanup import (InstanceBusy, InstanceInvalid, _lexical_home, control_directory,
                             instance_lock, preview, _read_registration, _write_registration)


@contextmanager
def database_connection(home):
    home = _lexical_home(home)
    control = control_directory() if os.name == 'nt' else None
    if control is None or home.parent.parent != control:
        home.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(home / 'horizon.sqlite3')) as database:
            yield database
        return
    from hostrun.windows_cleanup import PinnedEntry, pin_ancestors
    identity = home.parent.name
    registration = control / (identity + '.json')
    with instance_lock(home) as available:
        if not available:
            raise InstanceBusy('instance_busy')
        with file_lock(control / (identity + '.database.lock')), pin_ancestors(home), ExitStack() as held:
            with file_lock(control / (identity + '.inventory.lock')):
                preview(control, identity)
                record = _read_registration(registration)
                inventory = record.setdefault('data_inventory', {})
                parent = PinnedEntry(home)
                held.callback(parent.close)
                for name in ('horizon.sqlite3', 'horizon.sqlite3-journal'):
                    path = home / name
                    relative = 'data/' + name
                    expected = inventory.get(relative)
                    if expected is None:
                        created = PinnedEntry(path, create_parent=parent, create_file=True)
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
                    pin = PinnedEntry(path)
                    held.callback(pin.close)
                    stat = path.stat()
                    if expected != [stat.st_dev, stat.st_ino]:
                        raise InstanceInvalid('Database file identity changed')
                if any((home / ('horizon.sqlite3' + suffix)).exists() for suffix in ('-wal', '-shm')):
                    raise InstanceInvalid('Unenrolled SQLite sidecar')
                path = home / 'horizon.sqlite3'
                with path.open('rb') as stream:
                    header = stream.read(20)
                if header and (len(header) < 20 or header[18:20] != b'\x01\x01'):
                    raise InstanceInvalid('Unsupported managed database journal mode')
            with closing(sqlite3.connect(path)) as database:
                # Set before schema access/hot-journal recovery. The external
                # database lock already serializes managed connections.
                database.execute('PRAGMA locking_mode=EXCLUSIVE')
                # PERSIST retains the enrolled journal instead of unlinking it
                # after every commit. Both original handles remain held.
                if database.execute('PRAGMA journal_mode=PERSIST').fetchone()[0] != 'persist':
                    raise InstanceInvalid('Managed SQLite journal mode unavailable')
                yield database
