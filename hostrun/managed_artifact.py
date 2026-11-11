"""Creation receipts for existing write_file outputs; updates are not atomic."""
import json
import sys
from contextlib import ExitStack

from common import file_lock
from hostrun.cleanup import (InstanceBusy, InstanceInvalid, _lexical_home,
                             control_directory, instance_lock, outbox_name,
                             preview, _read_registration, _write_registration)
from hostrun.windows_cleanup import PinnedEntry, pin_ancestors, io_path


def write_outbox(home, relative, content):
    """Write one registered output; a failed body write may leave partial content."""
    if not isinstance(relative, str) or not outbox_name('data/outbox/' + relative) or not isinstance(content, str):
        raise InstanceInvalid('Invalid artifact request')
    payload = content.encode('utf-8')
    home = _lexical_home(home)
    control = control_directory()
    if home.name != 'data' or home.parent.parent != control:
        raise InstanceInvalid('Invalid artifact home')
    identity = home.parent.name
    registration = control / (identity + '.json')
    with instance_lock(home) as acquired:
        if not acquired:
            raise InstanceBusy('instance_busy')
        with file_lock(control / (identity + '.inventory.lock')), pin_ancestors(home), ExitStack() as held:
            preview(control, identity)
            record = _read_registration(registration)
            directories = record.setdefault('data_directories', {})
            inventory = record.setdefault('data_inventory', {})
            parent = PinnedEntry(home)
            held.callback(parent.close)
            parts = ['outbox', *relative.split('/')]
            for index in range(len(parts)):
                name = 'data/' + '/'.join(parts[:index + 1])
                path = home.parent / name
                is_file = index == len(parts) - 1
                entries = inventory if is_file else directories
                expected = entries.get(name)
                if expected is None:
                    pin = PinnedEntry(path, create_parent=parent, create_file=is_file, writable=is_file)
                    held.callback(pin.close)
                    stat = io_path(path).stat()
                    entries[name] = [stat.st_dev, stat.st_ino]
                    try:
                        _write_registration(registration, record)
                    except BaseException:
                        pin.delete()  # Only this newly created, still empty held object.
                        raise
                else:
                    pin = PinnedEntry(path, writable=is_file)
                    held.callback(pin.close)
                    stat = io_path(path).stat()
                    if expected != [stat.st_dev, stat.st_ino]:
                        raise InstanceInvalid('Artifact identity changed')
                if is_file:
                    if stat.st_nlink != 1:
                        raise InstanceInvalid('Artifact has external links')
                    pin.write_bytes(payload)
                parent = pin


def main():
    try:
        request = json.loads(sys.stdin.buffer.read())
        if not isinstance(request, dict) or set(request) != {'home', 'relative', 'content'}:
            raise ValueError('Invalid request')
        write_outbox(request['home'], request['relative'], request['content'])
        print('Written', flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, TypeError):
        print('Managed artifact unavailable', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
