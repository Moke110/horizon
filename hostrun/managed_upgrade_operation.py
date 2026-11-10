"""Internal normal-path coordinator; failures retain the overall recovery marker."""
import hashlib
import json
from pathlib import Path
import uuid

from hostrun import cleanup, dist
from hostrun.managed_upgrade_scope import exclusive_scope
from hostrun.managed_upgrade_data import _transaction, backup_owned_data
from hostrun.managed_upgrade import stage_code_transaction
from hostrun.managed_upgrade_initialize import initialize_target_schema
from hostrun.windows_cleanup import PinnedEntry


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
        created = tx.create(temporary)
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


def run_upgrade(code, home, *, from_source, sha256):
    """Experimental only: public upgrade remains blocked pending recovery coverage."""
    code, home = cleanup._lexical_home(code), cleanup._lexical_home(home)
    if code != home.parent / 'app/current':
        raise cleanup.InstanceInvalid('Unexpected managed code')
    package = Path(from_source)
    if hashlib.sha256(package.read_bytes()).hexdigest() != sha256:
        return {'status': 'blocked', 'reason': 'artifact_digest_mismatch'}
    archive, _, manifest, manifest_sha, _ = dist._read_archive(package, None, None)
    archive.close()
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
        staged = stage_code_transaction(code, home, from_source=from_source,
                                        sha256=sha256, transaction_id=operation)
        if staged['status'] != 'ok':
            return staged
        initialized = initialize_target_schema(code, home, manifest_sha256=manifest_sha,
                                                snapshot_id=operation)
        if initialized['status'] != 'initialized':
            return initialized
        _publish_version(home, operation, manifest['version'])
        record = cleanup._read_registration(registration)
        record.pop('upgrade_operation')
        cleanup._write_registration(registration, record)
        return {'status': 'ok', 'version': manifest['version'], 'operation_id': operation}
