"""Managed upgrade coordination and explicit recovery through enrolled identities."""
import hashlib
import json
import re
from pathlib import Path
import uuid

from hostrun import cleanup, dist
from hostrun.managed_upgrade_scope import exclusive_scope
from hostrun.managed_upgrade_data import _transaction, backup_owned_data, restore_owned_data
from hostrun.managed_upgrade import stage_code_transaction, rollback_code_transaction
from hostrun.managed_upgrade_initialize import initialize_target_schema
from hostrun.windows_cleanup import PinnedEntry, pin_ancestors


def _publish_version(home, operation, version):
    with _transaction(home) as tx:
        current = 'data/version.json'
        temporary = 'data/version-' + operation + '.tmp'
        prior = 'data/version-' + uuid.uuid4().hex + '.tmp'
        old = None
        if (tx.root / current).exists():
            tx.pin(current).close()
            old = PinnedEntry(tx.root / current, delete=True)
            tx.held.callback(old.close)
            stat = (tx.root / current).stat()
            if tx.inventory.get(current) != [stat.st_dev, stat.st_ino]:
                raise cleanup.InstanceInvalid('Version identity changed')
        journal = {'before': tx.inventory[current] if old else None, 'new': None,
                   'temporary': temporary, 'previous': prior}
        tx.record['upgrade_operation']['version'] = journal
        tx.save()
        created = tx.create(temporary)
        journal['new'] = tx.inventory[temporary]
        tx.save()
        created.write_bytes(json.dumps({'schema_version': 1, 'version': version,
                                       'backup': 'upgrade-backups/' + operation}).encode())
        if old:
            tx.inventory[prior] = tx.inventory[current]
            tx.save()
            old.rename(tx.root / prior)
            del tx.inventory[current]
            tx.save()
        tx.inventory[current] = tx.inventory[temporary]
        tx.save()
        created.rename(tx.root / current)
        del tx.inventory[temporary]
        tx.save()


def _restore_version(home):
    with _transaction(home) as tx:
        journal = tx.record['upgrade_operation'].get('version')
        if journal is None:
            return
        current, previous = 'data/version.json', journal['previous']
        pins = {}
        identities = {}
        for name in (current, previous, journal['temporary']):
            path = tx.root / name
            if not path.exists():
                continue
            pin = PinnedEntry(path, delete=True)
            tx.held.callback(pin.close)
            stat = path.stat()
            identity = [stat.st_dev, stat.st_ino]
            expected = tx.inventory.get(name)
            # A newly created temporary receipt may precede journal.new.
            allowed = (journal['before'], journal['new']) if name != journal['temporary'] else (journal['new'] or expected,)
            if identity != expected or identity not in allowed:
                raise cleanup.InstanceInvalid('Version recovery identity changed')
            pins[name], identities[name] = pin, identity
        before, new = journal['before'], journal['new']
        if before is not None and identities.get(current) != before and identities.get(previous) != before:
            raise cleanup.InstanceInvalid('Original version unavailable')
        if current in pins and identities[current] == new:
            pins[current].delete()
            tx.inventory.pop(current, None)
            tx.save()
            identities.pop(current)
        if before is not None and identities.get(current) != before:
            tx.inventory[current] = before
            tx.save()
            pins[previous].rename(tx.root / current)
            tx.inventory.pop(previous, None)
            tx.save()
        elif before is None and current not in identities:
            tx.inventory.pop(current, None)
            tx.save()


def run_upgrade(code, home, *, from_source, sha256):
    """Coordinate a compatible target schema upgrade under the external exclusive lease."""
    code, home = cleanup._lexical_home(code), cleanup._lexical_home(home)
    if code != home.parent / 'app/current':
        raise cleanup.InstanceInvalid('Unexpected managed code')
    package = Path(from_source)
    if not package.is_file():
        return {'status': 'blocked', 'reason': 'source_not_found'}
    if hashlib.sha256(package.read_bytes()).hexdigest() != sha256:
        return {'status': 'blocked', 'reason': 'artifact_digest_mismatch'}
    try:
        archive, _, manifest, manifest_sha, _ = dist._read_archive(package, None, None)
        archive.close()
        dist._check_compat(manifest)
    except dist._InstallBlocked as exc:
        return exc.payload
    with exclusive_scope(home) as available:
        if not available:
            return {'status': 'blocked', 'reason': 'instance_busy'}
        registration = home.parent.parent / (home.parent.name + '.json')
        record = cleanup._read_registration(registration)
        if record.get('upgrade_operation') or record.get('upgrade') or record.get('model_update'):
            return {'status': 'blocked', 'reason': 'managed_upgrade_recovery_pending'}
        if 'data/horizon.sqlite3' not in record.get('data_inventory', {}):
            return {'status': 'blocked', 'reason': 'schema_database_snapshot_unavailable'}
        operation = uuid.uuid4().hex
        record['upgrade_operation'] = {'id': operation, 'artifact': sha256}
        cleanup._write_registration(registration, record)
        backup_owned_data(home, snapshot_id=operation)
        try:
            staged = stage_code_transaction(code, home, from_source=from_source,
                                            sha256=sha256, transaction_id=operation)
            if staged['status'] != 'ok':
                raise cleanup.InstanceInvalid('Code publication refused')
            initialized = initialize_target_schema(code, home, manifest_sha256=manifest_sha,
                                                    snapshot_id=operation)
            if initialized['status'] != 'initialized':
                raise cleanup.InstanceInvalid('Target initialization refused')
            _publish_version(home, operation, manifest['version'])
            record = cleanup._read_registration(registration)
            record.pop('upgrade_operation')
            cleanup._write_registration(registration, record)
        except Exception:
            # The overall marker remains throughout both restores. Any recovery
            # failure propagates with that marker intact and startup stays closed.
            _restore_version(home)
            record = cleanup._read_registration(registration)
            if operation in record.get('code_transactions', {}):
                rollback_code_transaction(code, home, operation)
            restore_owned_data(home, operation)
            record = cleanup._read_registration(registration)
            record.pop('upgrade_operation')
            cleanup._write_registration(registration, record)
            return {'status': 'blocked', 'reason': 'upgrade_commit_failed', 'recovery': 'restored'}
        return {'status': 'ok', 'version': manifest['version'], 'operation_id': operation}


def recover_upgrade(code, home, *, sha256):
    """Internal restart recovery, bound to this home's DPAPI-protected operation."""
    code, home = cleanup._lexical_home(code), cleanup._lexical_home(home)
    if code != home.parent / 'app/current':
        raise cleanup.InstanceInvalid('Unexpected managed code')
    with exclusive_scope(home) as available:
        if not available:
            return {'status': 'blocked', 'reason': 'instance_busy'}
        registration = home.parent.parent / (home.parent.name + '.json')
        record = cleanup._read_registration(registration)
        pending = record.get('upgrade_operation')
        if pending is None:
            return {'status': 'not_pending'}
        if pending['artifact'] != sha256:
            return {'status': 'blocked', 'reason': 'recovery_artifact_mismatch'}
        operation = pending['id']
        snapshot = record.get('data_backups', {}).get(operation)
        if record.get('upgrade') not in (None, operation):
            return {'status': 'blocked', 'reason': 'upgrade_recovery_unavailable'}
        try:
            if not snapshot or not snapshot['complete']:
                if record.get('upgrade') or operation in record.get('code_transactions', {}) or pending.get('version'):
                    return {'status': 'blocked', 'reason': 'upgrade_recovery_unavailable'}
                # Code/schema publication cannot begin before a complete snapshot.
                # Validate registered sources and partial backup identities;
                # leave the partial backup enrolled without copying it over data.
                with _transaction(home) as tx:
                    for name, identity in tx.inventory.items():
                        if not re.fullmatch(cleanup.UPGRADE_DATA_PATTERN, name) and not name.startswith('data/upgrade-backups/' + operation + '/'):
                            continue
                        path = tx.root / name
                        tx.held.enter_context(pin_ancestors(path.parent))
                        pin = PinnedEntry(path)
                        tx.held.callback(pin.close)
                        stat = path.stat()
                        if identity != [stat.st_dev, stat.st_ino]:
                            raise cleanup.InstanceInvalid('Interrupted backup identity changed')
            else:
                _restore_version(home)
                if operation in record.get('code_transactions', {}):
                    rollback_code_transaction(code, home, operation)
                restore_owned_data(home, operation)
            record = cleanup._read_registration(registration)
            record.pop('upgrade_operation')
            cleanup._write_registration(registration, record)
        except Exception:
            return {'status': 'blocked', 'reason': 'upgrade_recovery_failed'}
        return {'status': 'restored', 'operation_id': operation}
