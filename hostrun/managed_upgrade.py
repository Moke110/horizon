"""Identity-enrolled code upgrades; data migrations require a separate transaction."""
from contextlib import ExitStack
import hashlib
import json
import re
from pathlib import Path
import uuid

from common import now
from hostrun.managed_upgrade_scope import exclusive_scope, owns_scope
from hostrun import cleanup, dist
from hostrun.windows_cleanup import PinnedEntry, pin_ancestors


def stage_code_transaction(code, home, *, from_source, sha256, transaction_id=None):
    """Internal code-publication primitive; the coordinator owns data/schema/version."""
    if transaction_id is not None and (not isinstance(transaction_id, str) or not re.fullmatch(r'[0-9a-f]{32}', transaction_id)):
        raise cleanup.InstanceInvalid('Invalid code transaction identity')
    home = cleanup._lexical_home(home)
    root, control = home.parent, home.parent.parent
    if control != cleanup.control_directory():
        raise cleanup.InstanceInvalid('Unexpected managed instance location')
    if not owns_scope(home):
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
    with exclusive_scope(home) as acquired:
        if not acquired:
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
            old_digests = {}
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
                            if name in old_names:
                                old_digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
                        finally:
                            pin.close()
                elif name in old_names:
                    raise cleanup.InstanceInvalid('Missing installed file')
            operation = transaction_id or uuid.uuid4().hex
            if operation in record.get('code_transactions', {}):
                raise cleanup.InstanceInvalid('Code transaction already exists')
            transaction = {'before': {name: inventory[(code / name).relative_to(root).as_posix()] for name in old_names},
                           'after': {}, 'digests': old_digests}
            record.setdefault('code_transactions', {})[operation] = transaction
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
                transaction['after'][name] = [stat.st_dev, stat.st_ino]
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
    return {'status': 'ok', 'version': manifest['version'], 'data_migration': 'not_performed', 'transaction_id': operation}


def rollback_code_transaction(code, home, transaction_id=None):
    """Internal original-identity rollback; unknown replacements remain untouched."""
    home, code = cleanup._lexical_home(home), cleanup._lexical_home(code)
    root, control = home.parent, home.parent.parent
    if control != cleanup.control_directory() or code != root / 'app/current':
        raise cleanup.InstanceInvalid('Unexpected managed code location')
    cleanup.preview(control, root.name)
    with exclusive_scope(home) as available:
        if not available:
            raise cleanup.InstanceBusy('instance_busy')
        with pin_ancestors(code), ExitStack() as held:
            cleanup.preview(control, root.name)
            registration = control / (root.name + '.json')
            record = cleanup._read_registration(registration)
            identity = transaction_id or record.get('upgrade')
            transaction = record.get('code_transactions', {}).get(identity)
            if not transaction or record.get('upgrade') not in (None, identity):
                raise cleanup.InstanceInvalid('Unavailable code rollback')
            inventory, directories = record['application_inventory'], record['application_directories']
            base = code / '.horizon/upgrades' / identity
            directory_pins = {}
            def save():
                cleanup._write_registration(registration, record)
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
                        raise cleanup.InstanceInvalid('Rollback directory changed')
                else:
                    pin = PinnedEntry(path, create_parent=parent, create_share=3, create_delete=False)
                    held.callback(pin.close)
                    stat = path.stat()
                    directories[relative] = [stat.st_dev, stat.st_ino]
                    save()
                directory_pins[path] = pin
                return pin
            def inspect(path, expected):
                if not path.exists():
                    return None
                held.enter_context(pin_ancestors(path.parent))
                pin = PinnedEntry(path, delete=True)
                held.callback(pin.close)
                stat = path.stat()
                actual = [stat.st_dev, stat.st_ino]
                if actual not in expected or inventory.get(path.relative_to(root).as_posix()) != actual:
                    raise cleanup.InstanceInvalid('Rollback file identity changed')
                return pin, actual
            current, old = {}, {}
            for name in sorted(set(transaction['before']) | set(transaction['after'])):
                expected = [mapping[name] for mapping in (transaction['before'], transaction['after']) if name in mapping]
                current[name] = inspect(code / name, expected)
                if name in transaction['before']:
                    old[name] = inspect(base / 'old' / name, [transaction['before'][name]])
                    original = current[name] if current[name] and current[name][1] == transaction['before'][name] else old[name]
                    if not original:
                        raise cleanup.InstanceInvalid('Original code unavailable')
                    # Reopen the identity-checked path for a digest read while all ancestors stay pinned.
                    path = code / name if original is current[name] else base / 'old' / name
                    # A DELETE handle prevents ordinary reads; recheck identity after each reopen.
                    original[0].close()
                    with pin_ancestors(path.parent):
                        check = PinnedEntry(path)
                        try:
                            stat = path.stat()
                            if [stat.st_dev, stat.st_ino] != original[1] or hashlib.sha256(path.read_bytes()).hexdigest() != transaction['digests'][name]:
                                raise cleanup.InstanceInvalid('Original code content changed')
                        finally:
                            check.close()
                    reopened = inspect(path, [original[1]])
                    if original is current[name]: current[name] = reopened
                    else: old[name] = reopened
            record['upgrade'] = identity
            save()
            def move(source, target, pin):
                source_name, target_name = source.relative_to(root).as_posix(), target.relative_to(root).as_posix()
                inventory[target_name] = inventory[source_name]
                save()
                pin.rename(target)
                inventory.pop(source_name)
                save()
            for name in sorted(current):
                value = current[name]
                if value and value[1] != transaction['before'].get(name):
                    destination = base / 'failed' / name
                    directory(destination.parent)
                    move(code / name, destination, value[0])
                    value = None
                if name in transaction['before'] and not value:
                    directory((code / name).parent)
                    move(base / 'old' / name, code / name, old[name][0])
            record.pop('upgrade')
            save()
    return {'status': 'restored', 'transaction_id': identity}
