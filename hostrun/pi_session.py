"""Bounded pipe guardian for one managed Pi session; EOF releases all handles."""
import json
import sys
import uuid
from pathlib import Path
from contextlib import contextmanager, ExitStack

from common import file_lock
from hostrun.cleanup import (InstanceBusy, InstanceInvalid, _lexical_home, control_directory,
                             instance_lock, preview, _read_registration, _write_registration)
from hostrun.windows_cleanup import PinnedEntry, pin_ancestors


@contextmanager
def managed_session(home, session_id, header):
    home = _lexical_home(home)
    control = control_directory()
    if home.parent.parent != control or str(uuid.UUID(session_id)) != session_id:
        raise InstanceInvalid('Invalid managed session')
    if not isinstance(header, dict) or header.get('type') != 'session' or header.get('id') != session_id:
        raise InstanceInvalid('Invalid session header')
    identity = home.parent.name
    registration = control / (identity + '.json')
    with instance_lock(home) as available:
        if not available:
            raise InstanceBusy('instance_busy')
        with file_lock(control / (identity + '.session-' + session_id + '.lock')), pin_ancestors(home), ExitStack() as held:
            with file_lock(control / (identity + '.inventory.lock')):
                preview(control, identity)
                record = _read_registration(registration)
                directories = record.setdefault('data_directories', {})
                inventory = record.setdefault('data_inventory', {})
                parent = PinnedEntry(home)
                held.callback(parent.close)
                for relative in ('data/agent', 'data/agent/pi', 'data/agent/pi/sessions',
                                 'data/agent/pi/sessions/' + session_id):
                    path = home.parent / relative
                    expected = directories.get(relative)
                    if expected is None:
                        created = PinnedEntry(path, create_parent=parent)
                        try:
                            stat = path.stat()
                            expected = [stat.st_dev, stat.st_ino]
                            directories[relative] = expected
                            try:
                                _write_registration(registration, record)
                            except BaseException:
                                created.delete()
                                raise
                        finally:
                            created.close()
                    parent = PinnedEntry(path)
                    held.callback(parent.close)
                    stat = path.stat()
                    if expected != [stat.st_dev, stat.st_ino]:
                        raise InstanceInvalid('Session directory identity changed')
                relative = 'data/agent/pi/sessions/' + session_id + '/session.jsonl'
                path = home.parent / relative
                expected = inventory.get(relative)
                if expected is None:
                    created = PinnedEntry(path, create_parent=parent, create_file=True, writable=True)
                    try:
                        stat = path.stat()
                        expected = [stat.st_dev, stat.st_ino]
                        inventory[relative] = expected
                        try:
                            _write_registration(registration, record)
                        except BaseException:
                            created.delete()
                            raise
                        created.write_bytes((json.dumps(header, ensure_ascii=False) + '\n').encode('utf-8'))
                    finally:
                        created.close()
                pin = PinnedEntry(path)
                held.callback(pin.close)
                stat = path.stat()
                if expected != [stat.st_dev, stat.st_ino]:
                    raise InstanceInvalid('Session file identity changed')
                with path.open('rb') as stream:
                    first_line = stream.readline(65537)
                if len(first_line) > 65536 or not first_line.endswith(b'\n'):
                    raise InstanceInvalid('Incomplete session header')
                persisted = json.loads(first_line)
                if not isinstance(persisted, dict) or any(
                        persisted.get(key) != header.get(key) for key in ('type', 'id', 'version')):
                    raise InstanceInvalid('Invalid persisted session header')
            yield path


def main():
    try:
        header_line = sys.stdin.buffer.readline(65537)
        if len(header_line) > 65536:
            raise ValueError('Header too large')
        header = json.loads(header_line)
        with managed_session(Path(sys.argv[1]), sys.argv[2], header) as path:
            print(json.dumps({'path': str(path)}), flush=True)
            # The Node owner keeps stdin open only while using the session.
            if sys.stdin.buffer.read(1):
                raise ValueError('Unexpected guardian input')
        return 0
    except (OSError, ValueError, RuntimeError):
        print('Managed Pi session unavailable', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
