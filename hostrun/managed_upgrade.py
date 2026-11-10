"""Identity-enrolled code upgrades; data migrations require a separate transaction."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import uuid

from common import file_lock, now
from hostrun import cleanup, dist
from hostrun.windows_cleanup import PinnedEntry, pin_ancestors


def stage_code_transaction(code, home, *, from_source, sha256):
    """Experimental internal code transaction, not the public upgrade contract."""
    home = cleanup._lexical_home(home)
    root, control = home.parent, home.parent.parent
    if control != cleanup.control_directory():
        raise cleanup.InstanceInvalid('Unexpected managed instance location')
    with cleanup.instance_lock(home) as available:
        if not available:
            return {'status': 'blocked', 'reason': 'instance_busy'}
    with cleanup.lifecycle_lock(control / (root.name + '.lock'), exclusive=True) as available:
        if not available:
            return {'status': 'blocked', 'reason': 'instance_busy'}
    code = cleanup._lexical_home(code)
    if code != root / 'app/current':
        return {'status': 'blocked', 'reason': 'unexpected_managed_code'}
    package = Path(from_source)
    if not package.is_file() or hashlib.sha256(package.read_bytes()).hexdigest() != sha256:
        return {'status': 'blocked', 'reason': 'artifact_digest_mismatch'}
    try:
        archive, manifest_bytes, manifest, manifest_sha, channel = dist._read_archive(package, None, None)
        with archive:
            dist._check_compat(manifest)
            payloads = {row['path']: archive.read(row['path']) for row in manifest['files']}
            for row in manifest['files']:
                if len(payloads[row['path']]) != row['bytes'] or hashlib.sha256(payloads[row['path']]).hexdigest() != row['sha256']:
                    return {'status': 'blocked', 'reason': 'file_digest_mismatch'}
    except dist._InstallBlocked as exc:
        return exc.payload
    payloads['manifest.json'] = manifest_bytes
    payloads['.horizon/current'] = (manifest['version'] + '\n').encode()
    payloads['.horizon/dist.lock.json'] = json.dumps({
        'schema_version': 1, 'distribution': 'package', 'version': manifest['version'],
        'channel': channel, 'artifact_sha256': sha256, 'source_url': '', 'installed_at': now(),
        'requires': manifest['compat'], 'skill_digest': '', 'package_file_count': len(manifest['files']),
        'manifest_sha256': manifest_sha}).encode()
    identity = root.name
    cleanup.preview(control, identity)
    with file_lock(control / (identity + '.gate'), blocking=False) as gate, \
            cleanup.lifecycle_lock(control / (identity + '.lock'), exclusive=True) as acquired:
        if not gate or not acquired:
            return {'status': 'blocked', 'reason': 'instance_busy'}
        with pin_ancestors(code), ExitStack() as held:
            cleanup.preview(control, identity)
            registration = control / (identity + '.json')
            record = cleanup._read_registration(registration)
            if record.get('upgrade') or record.get('installation'):
                return {'status': 'blocked', 'reason': 'managed_upgrade_recovery_pending'}
            inventory = record['application_inventory']
            directories = record['application_directories']
            previous = json.loads((code / 'manifest.json').read_text(encoding='utf-8'))
            old_names = [row['path'] for row in previous['files']] + ['manifest.json', '.horizon/current', '.horizon/dist.lock.json']
            # Validate every target before creating any upgrade contents.
            for name in set(old_names) | set(payloads):
                path = code / name
                relative = path.relative_to(root).as_posix()
                if not dist._safe_relpath(name):
                    raise cleanup.InstanceInvalid('Unsafe installed manifest path')
                if path.exists():
                    with pin_ancestors(path.parent):
                        pin = PinnedEntry(path)
                        try:
                            stat = path.stat()
                            if inventory.get(relative) != [stat.st_dev, stat.st_ino]:
                                raise cleanup.InstanceInvalid('Unowned upgrade target')
                        finally:
                            pin.close()
                elif name in old_names:
                    raise cleanup.InstanceInvalid('Missing installed file')
            operation = uuid.uuid4().hex
            record['upgrade'] = operation
            cleanup._write_registration(registration, record)

            def save():
                cleanup._write_registration(registration, record)

            directory_pins = {}
            def directory(path):
                if path in directory_pins:
                    return directory_pins[path]
                parent = directory(path.parent) if path != code else None
                relative = path.relative_to(root).as_posix()
                if path.exists():
                    pin = PinnedEntry(path)
                    held.callback(pin.close)
                    stat = path.stat()
                    if directories.get(relative) != [stat.st_dev, stat.st_ino]:
                        raise cleanup.InstanceInvalid('Unowned upgrade directory')
                    directory_pins[path] = pin
                    return pin
                pin = PinnedEntry(path, create_parent=parent, create_share=3, create_delete=False)
                held.callback(pin.close)
                stat = path.stat()
                directories[relative] = [stat.st_dev, stat.st_ino]
                save()  # A receipt failure leaves an unclaimed empty directory, never adopted.
                directory_pins[path] = pin
                return pin

            base = code / '.horizon/upgrades' / operation
            created = {}
            for name, payload in payloads.items():
                path = base / 'new' / name
                parent = directory(path.parent)
                pin = PinnedEntry(path, create_parent=parent, create_file=True, writable=True, create_share=3)
                held.callback(pin.close)
                stat = path.stat()
                inventory[path.relative_to(root).as_posix()] = [stat.st_dev, stat.st_ino]
                try:
                    save()
                except BaseException:
                    pin.delete()
                    raise
                pin.write_bytes(payload)
                created[name] = (path, pin)

            def move(source, destination, pin):
                original = source.relative_to(root).as_posix()
                target = destination.relative_to(root).as_posix()
                inventory[target] = inventory[original]
                save()  # Both names refer only to the already-enrolled identity.
                pin.rename(destination)
                inventory.pop(original)
                save()

            for name in old_names:
                source = code / name
                destination = base / 'old' / name
                directory(destination.parent)
                with pin_ancestors(source.parent):
                    pin = PinnedEntry(source, delete=True)
                    held.callback(pin.close)
                    stat = source.stat()
                    if inventory[source.relative_to(root).as_posix()] != [stat.st_dev, stat.st_ino]:
                        raise cleanup.InstanceInvalid('Installed file changed')
                    move(source, destination, pin)
            for name, (source, pin) in created.items():
                destination = code / name
                directory(destination.parent)
                move(source, destination, pin)
            record.pop('upgrade')
            save()
    return {'status': 'ok', 'version': manifest['version'], 'data_migration': 'not_performed'}
