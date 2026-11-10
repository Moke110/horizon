"""Run a verified target package's explicit SQL schema entry in the tool runtime."""
from contextlib import closing
import hashlib
import json
import sqlite3
import types
import uuid

from hostrun import cleanup
from hostrun.managed_upgrade_data import _transaction
from hostrun.windows_cleanup import PinnedEntry, pin_ancestors


def initialize_target_schema(code, home, *, manifest_sha256, snapshot_id):
    """Internal initializer only; not target-runtime or full-upgrade acceptance."""
    code, home = cleanup._lexical_home(code), cleanup._lexical_home(home)
    if code != home.parent / 'app/current':
        raise cleanup.InstanceInvalid('Unexpected target code')
    with _transaction(home) as tx:
        snapshot = tx.record.get('data_backups', {}).get(snapshot_id)
        if not snapshot or not snapshot['complete'] or tx.record.get('upgrade') or tx.record.get('model_update'):
            return {'status': 'blocked', 'reason': 'schema_snapshot_or_transaction_unavailable'}
        if 'data/horizon.sqlite3' not in snapshot['files']:
            return {'status': 'blocked', 'reason': 'schema_database_snapshot_unavailable'}
        def verified_bytes(path):
            tx.held.enter_context(pin_ancestors(path.parent))
            pin = PinnedEntry(path)
            tx.held.callback(pin.close)
            stat = path.stat()
            if tx.record.get('application_inventory', {}).get(path.relative_to(tx.root).as_posix()) != [stat.st_dev, stat.st_ino]:
                raise cleanup.InstanceInvalid('Target file identity changed')
            return path.read_bytes()
        manifest_bytes = verified_bytes(code / 'manifest.json')
        if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
            raise cleanup.InstanceInvalid('Target manifest digest changed')
        manifest = json.loads(manifest_bytes)
        row = next((entry for entry in manifest['files'] if entry['path'] == 'hostrun/agent_service.py'), None)
        if row is None:
            return {'status': 'blocked', 'reason': 'target_schema_entry_unavailable'}
        path = code / 'hostrun/agent_service.py'
        payload = verified_bytes(path)
        if hashlib.sha256(payload).hexdigest() != row['sha256']:
            raise cleanup.InstanceInvalid('Target schema digest changed')
        module = types.ModuleType('horizon_target_schema_' + uuid.uuid4().hex)
        module.__file__ = str(path)
        exec(compile(payload, str(path), 'exec'), module.__dict__)
        initializer = getattr(getattr(module, 'AgentService', None), 'initialize_database', None)
        if not callable(initializer) or getattr(initializer, '__module__', None) != module.__name__:
            return {'status': 'blocked', 'reason': 'target_schema_entry_unavailable'}
        if any((home / ('horizon.sqlite3' + suffix)).exists() for suffix in ('-wal', '-shm')):
            raise cleanup.InstanceInvalid('Unenrolled SQLite sidecar')
        tx.record['upgrade'] = snapshot_id
        tx.save()
        for name in ('data/horizon.sqlite3', 'data/horizon.sqlite3-journal'):
            if name not in tx.inventory:
                tx.create(name).close()
            tx.pin(name)
        database = home / 'horizon.sqlite3'
        with database.open('rb') as stream:
            header = stream.read(20)
        if header and (len(header) < 20 or header[18:20] != b'\x01\x01'):
            raise cleanup.InstanceInvalid('Unsupported managed database journal mode')
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('PRAGMA locking_mode=EXCLUSIVE')
            if db.execute('PRAGMA journal_mode=PERSIST').fetchone()[0] != 'persist':
                raise cleanup.InstanceInvalid('Unsupported managed database journal mode')
            initializer(db)
        tx.record.pop('upgrade')
        tx.save()
    return {'status': 'initialized', 'module_sha256': row['sha256']}
