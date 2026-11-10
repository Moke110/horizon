"""First installation with creation-time receipts; legacy residue is never adopted."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path

from common import file_lock, now
from hostrun import cleanup, dist
from hostrun.windows_cleanup import PinnedEntry, io_path, pin_ancestors

_BATCH_SIZE = 32


def install(control_root, identity, package, digest):
    package = Path(package)
    if hashlib.sha256(io_path(package).read_bytes()).hexdigest() != digest:
        raise cleanup.InstanceInvalid('Package digest mismatch')
    try:
        archive, manifest_bytes, manifest, manifest_sha, channel = dist._read_archive(io_path(package), None, None)
        with archive:
            dist._check_compat(manifest)
            payloads = {row['path']: archive.read(row['path']) for row in manifest['files']}
            for row in manifest['files']:
                content = payloads[row['path']]
                if len(content) != row['bytes'] or hashlib.sha256(content).hexdigest() != row['sha256']:
                    raise cleanup.InstanceInvalid('Package file digest mismatch')
    except dist._InstallBlocked as exc:
        return exc.payload
    installed_at = now()
    payloads['manifest.json'] = manifest_bytes
    payloads['.horizon/current'] = (manifest['version'] + '\n').encode()
    payloads['.horizon/dist.lock.json'] = json.dumps({
        'schema_version': 1, 'distribution': 'package', 'version': manifest['version'],
        'channel': channel, 'artifact_sha256': digest, 'source_url': '', 'installed_at': installed_at,
        'requires': manifest['compat'], 'skill_digest': '', 'package_file_count': len(manifest['files']),
        'manifest_sha256': manifest_sha}).encode()
    control = Path(control_root).resolve(strict=True)
    cleanup.preview(control, identity)
    root, registration = control / identity, control / (identity + '.json')
    code = root / 'app/current'
    if not io_path(root / 'app').exists():
        cleanup.initialize_layout(control, identity)
    with file_lock(control / (identity + '.gate'), blocking=False) as entrance, cleanup.lifecycle_lock(control / (identity + '.lock'), exclusive=True) as acquired, pin_ancestors(root / 'app'), ExitStack() as held:
        if not entrance or not acquired:
            raise cleanup.InstanceBusy('instance_busy')
        cleanup.preview(control, identity)
        record = cleanup._read_registration(registration)
        if record.get('installation'):
            return {'status': 'blocked', 'reason': 'interrupted_inventory_unavailable'}
        if io_path(code).exists():
            raise cleanup.InstanceInvalid('Refusing existing application contents')
        app = PinnedEntry(root / 'app')
        held.callback(app.close)
        current = PinnedEntry(code, create_parent=app, create_share=3)
        held.callback(current.close)
        stat = io_path(code).stat()
        record['installation'] = {'digest': digest, 'identity': [stat.st_dev, stat.st_ino], 'protocol': 1}
        inventory = record['application_inventory'] = {}
        directories = record['application_directories'] = {'app/current': [stat.st_dev, stat.st_ino]}
        def save():
            cleanup._write_registration(registration, record)
        try:
            save()
        except BaseException:
            current.delete()
            raise
        directory_pins = {code: current}
        def directory(path):
            if path in directory_pins:
                return directory_pins[path]
            parent = directory(path.parent)
            pin = PinnedEntry(path, create_parent=parent, create_share=3)
            held.callback(pin.close)
            stat = io_path(path).stat()
            directories[path.relative_to(root).as_posix()] = [stat.st_dev, stat.st_ino]
            try:
                save()
            except BaseException:
                pin.delete()
                raise
            directory_pins[path] = pin
            return pin
        names = list(payloads)
        for offset in range(0, len(names), _BATCH_SIZE):
            batch = names[offset:offset + _BATCH_SIZE]
            parents = {name: directory((code / name).parent) for name in batch}
            empty = {}
            try:
                for name in batch:
                    path = code / name
                    pin = PinnedEntry(path, create_parent=parents[name], create_file=True, writable=True, create_share=3)
                    held.callback(pin.close)
                    empty[name] = pin
                    stat = io_path(path).stat()
                    inventory[path.relative_to(root).as_posix()] = [stat.st_dev, stat.st_ino]
                save()
            except BaseException:
                for pin in empty.values():
                    pin.delete()
                raise
            for name, pin in empty.items():
                pin.write_bytes(payloads[name])
        record.pop('installation')
        save()
    return dist._success(code, manifest, digest, package, '', channel, True, installed_at)
