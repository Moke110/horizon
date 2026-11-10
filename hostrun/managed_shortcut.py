"""One creation-enrolled shortcut inside a committed test application."""
from contextlib import ExitStack
import hashlib

from hostrun import cleanup
from hostrun.managed_upgrade_scope import exclusive_scope
from hostrun.windows_cleanup import PinnedEntry, io_path, pin_ancestors
from hostrun.windows_shortcut import shell_link_bytes

RELATIVE = 'app/current/.horizon/test-launch.lnk'


def create(control, identity):
    cleanup.preview(control, identity)
    root = cleanup._lexical_home(control) / identity
    home, code = root / 'data', root / 'app/current'
    with exclusive_scope(home) as available, ExitStack() as held:
        if not available:
            raise cleanup.InstanceBusy('instance_busy')
        cleanup.preview(control, identity)
        record = cleanup._read_registration(root.parent / (identity + '.json'))
        if any(record.get(key) for key in ('installation', 'upgrade', 'upgrade_operation')):
            raise cleanup.InstanceInvalid('Incomplete application operation')
        inventory = record.get('application_inventory', {})
        for path in (code / 'runtime/pythonw.exe', code / 'hostrun/desktop.py'):
            held.enter_context(pin_ancestors(path.parent))
            pin = PinnedEntry(path)
            held.callback(pin.close)
            stat = io_path(path).stat()
            if inventory.get(path.relative_to(root).as_posix()) != [stat.st_dev, stat.st_ino]:
                raise cleanup.InstanceInvalid('Unowned shortcut target')
        destination = root / RELATIVE
        held.enter_context(pin_ancestors(destination.parent))
        parent = PinnedEntry(destination.parent)
        held.callback(parent.close)
        cleanup.preview(control, identity)
        if io_path(destination).exists():
            pin = PinnedEntry(destination)
            held.callback(pin.close)
            stat = io_path(destination).stat()
            if (inventory.get(RELATIVE) != [stat.st_dev, stat.st_ino]
                    or record.get('shortcut_digest') != hashlib.sha256(io_path(destination).read_bytes()).hexdigest()):
                raise cleanup.InstanceInvalid('Unknown or incomplete shortcut')
            return {'status': 'already_created', 'shortcut': str(destination)}
        if RELATIVE in inventory or record.get('shortcut_digest'):
            raise cleanup.InstanceInvalid('Missing registered shortcut')
        arguments = '-B -X utf8 -m hostrun.desktop --code "' + str(code) + '" --archive "' + str(root / 'archive') + '" --managed-home "' + str(home) + '"'
        payload = shell_link_bytes(code / 'runtime/pythonw.exe', arguments, code, 'Horizon test instance ' + identity)
        pin = PinnedEntry(destination, create_parent=parent, create_file=True, writable=True, create_share=3)
        held.callback(pin.close)
        stat = io_path(destination).stat()
        inventory[RELATIVE] = [stat.st_dev, stat.st_ino]
        record['shortcut_digest'] = hashlib.sha256(payload).hexdigest()
        try:
            cleanup._write_registration(root.parent / (identity + '.json'), record)
        except BaseException:
            pin.delete()
            raise
        pin.write_bytes(payload)
        return {'status': 'created', 'shortcut': str(destination)}
