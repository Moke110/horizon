"""Archive, immutable citation bindings and exact-snapshot reclamation."""
import hashlib
import hmac
import json
import os
import re
import tempfile
from contextlib import closing, contextmanager
from functools import wraps
from pathlib import Path

from common import file_lock
from hostrun.agent_service import CommandError


SCHEMA = """
CREATE TABLE IF NOT EXISTS voyager_material_references(
    material_id TEXT NOT NULL, owner_id TEXT NOT NULL, content_hash TEXT NOT NULL, original_hash TEXT,
    PRIMARY KEY(material_id, owner_id)
);
CREATE TABLE IF NOT EXISTS voyager_removed_materials(
    material_id TEXT PRIMARY KEY, confirmation TEXT NOT NULL
);
"""
_BLOB_NAME = re.compile(r'[0-9a-f]{64}\.txt')


@contextmanager
def material_write_lock(home):
    """Prevent GC from racing between publication and registration in any process."""
    identity = hashlib.sha256(os.path.normcase(str(home.resolve())).encode()).hexdigest()
    lock = Path(tempfile.gettempdir()) / 'horizon-material-writes' / (identity + '.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    if not lock.exists():
        descriptor, name = tempfile.mkstemp(prefix='.lock-', dir=lock.parent)
        try:
            with os.fdopen(descriptor, 'wb') as seed:
                seed.write(b'\0')
            # Publish the initialized Windows lock byte in one operation.
            try:
                os.link(name, lock, follow_symlinks=False)
            except FileExistsError:
                pass
        finally:
            Path(name).unlink()
    if lock.is_symlink() or lock.is_junction() or lock.stat().st_size != 1:
        raise CommandError('snapshot_corrupt', 'Invalid material write lock')
    with file_lock(lock):
        yield


def serialized_material_write(method):
    @wraps(method)
    def operation(self, *args, **kwargs):
        with material_write_lock(self.home):
            return method(self, *args, **kwargs)
    return operation


def _record(db, material_id):
    row = db.execute('SELECT result FROM voyager_materials WHERE material_id=?', (material_id,)).fetchone()
    if row is None:
        raise CommandError('not_found', 'Unknown material')
    return json.loads(row[0])


def _impact(db, record):
    mid = record['material_id']
    refs = [dict(owner_id=owner, content_hash=digest, original_hash=original)
            for owner, digest, original in db.execute('SELECT owner_id, content_hash, original_hash '
            'FROM voyager_material_references WHERE material_id=? ORDER BY owner_id', (mid,))]
    snapshot = record['snapshot']
    impact = dict(material_id=mid, version=record['version'], references=refs,
                  content_hash=snapshot['content_hash'], original_hash=snapshot.get('original_hash'),
                  retrievals=sorted({origin['request_id'] for origin in
                      record.get('academic', {}).get('provenance', [])}))
    impact['confirmation'] = hashlib.sha256(json.dumps(impact, sort_keys=True).encode()).hexdigest()
    return impact


class MaterialLifecycle:
    def archive_material(self, material_id, *, token):
        self._authorize(token)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record = _record(db, material_id)
            if not record.get('archived'):
                record['archived'] = True
                db.execute('UPDATE voyager_materials SET result=? WHERE material_id=?',
                           (json.dumps(record), material_id))
        return record

    @serialized_material_write
    def register_reference(self, material_id, owner_id, content_hash, *, token):
        self._authorize(token)
        if not isinstance(owner_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,256}', owner_id):
            raise CommandError('invalid_reference', 'A bounded stable citation owner ID is required')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record = _record(db, material_id)
            previous = db.execute('SELECT content_hash FROM voyager_material_references '
                                  'WHERE material_id=? AND owner_id=?', (material_id, owner_id)).fetchone()
            if previous and previous[0] != content_hash:
                raise CommandError('reference_conflict', 'Citation owner is already bound to a different snapshot')
            if previous:
                return dict(material_id=material_id, owner_id=owner_id, content_hash=content_hash)
            if content_hash != record['snapshot']['content_hash']:
                raise CommandError('snapshot_changed', 'Reference does not match the published snapshot')
            self._read_blob(content_hash)
            original_hash = record['snapshot'].get('original_hash')
            if original_hash and original_hash != content_hash:
                self._read_blob(original_hash)
            db.execute('INSERT INTO voyager_material_references VALUES(?,?,?,?)',
                       (material_id, owner_id, content_hash, original_hash))
        return dict(material_id=material_id, owner_id=owner_id, content_hash=content_hash)

    @serialized_material_write
    def resolve_reference(self, material_id, owner_id, *, token):
        self._authorize(token)
        with self._connect() as db:
            row = db.execute('SELECT content_hash FROM voyager_material_references '
                             'WHERE material_id=? AND owner_id=?', (material_id, owner_id)).fetchone()
            if row is None:
                raise CommandError('not_found', 'Unknown material reference')
            removed = db.execute('SELECT 1 FROM voyager_removed_materials WHERE material_id=?',
                                 (material_id,)).fetchone()
        return dict(material_id=material_id, owner_id=owner_id, content_hash=row[0],
                    status='removed' if removed else 'available',
                    original_text=None if removed else self._read_blob(row[0]).decode('utf8'))

    def deletion_impact(self, material_id, *, token):
        self._authorize(token)
        with self._connect() as db:
            db.execute('BEGIN')
            record = _record(db, material_id)
            if not record.get('archived'):
                raise CommandError('archive_required', '永久删除前请先归档资料')
            return _impact(db, record)

    @serialized_material_write
    def delete_material(self, material_id, confirmation, *, token):
        self._authorize(token)
        if not isinstance(confirmation, str) or not re.fullmatch(r'[0-9a-f]{64}', confirmation):
            raise CommandError('confirmation_required', '请核对并确认删除影响')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT confirmation FROM voyager_removed_materials WHERE material_id=?',
                                  (material_id,)).fetchone()
            if previous:
                if hmac.compare_digest(confirmation, previous[0]):
                    return {'material_id': material_id, 'status': 'removed'}
                raise CommandError('confirmation_required', '请核对并确认删除影响')
            record = _record(db, material_id)
            if not record.get('archived') or not hmac.compare_digest(confirmation, _impact(db, record)['confirmation']):
                raise CommandError('confirmation_required', '删除影响已变更，请重新核对')
            db.execute('INSERT INTO voyager_removed_materials VALUES(?,?)', (material_id, confirmation))
            db.execute("UPDATE voyager_material_operations SET request='removed', result=? "
                       "WHERE json_extract(result,'$.material_id')=?", (json.dumps({'material_id': material_id,
                           'academic': {'status': 'removed'}}), material_id))
            db.execute('DELETE FROM voyager_materials WHERE material_id=?', (material_id,))
        return {'material_id': material_id, 'status': 'removed'}

    @serialized_material_write
    def collect_orphan_snapshots(self, *, token):
        from hostrun.managed_import import managed_paths, store, _blocks

        self._authorize(token)
        folder = self._blob('0' * 64).parent
        with self._connect() as db:
            live = {value for row in db.execute("""
                SELECT json_extract(result,'$.snapshot.content_hash'),
                       json_extract(result,'$.snapshot.original_hash') FROM voyager_materials
                UNION SELECT json_extract(op.result,'$.snapshot.content_hash'),
                             json_extract(op.result,'$.snapshot.original_hash')
                      FROM voyager_material_operations op JOIN voyager_materials m
                      ON m.material_id=json_extract(op.result,'$.material_id')
                UNION SELECT r.content_hash, r.original_hash
                      FROM voyager_material_references r JOIN voyager_materials m
                      ON m.material_id=r.material_id
            """) for value in row if value}
        removed = []
        if not folder.exists():
            return {'removed': removed}
        if folder.resolve() != folder.absolute() or not folder.is_dir():
            raise CommandError('snapshot_corrupt', 'Linked snapshot directory')
        if managed_paths(self.home):
            with store(self.home) as material:
                prefix = folder.relative_to(material.root).as_posix() + '/'
                inventory = material.inventory(prefix)
                for name in sorted(inventory):
                    if not name.startswith(prefix) or not _BLOB_NAME.fullmatch(name.removeprefix(prefix)):
                        continue
                    digest = Path(name).stem
                    if digest in live:
                        continue
                    path = material.root / name
                    if path.exists() or path.is_symlink() or path.is_junction():
                        pin = material.existing(name)
                        actual = hashlib.sha256()
                        for block in _blocks(pin):
                            actual.update(block)
                        if actual.hexdigest() != digest:
                            continue
                        pin.delete()
                    inventory.pop(name)
                    material.save()
                    removed.append(digest)
        else:
            for path in sorted(folder.iterdir()):
                if not _BLOB_NAME.fullmatch(path.name) or path.stem in live or not path.is_file():
                    continue
                try:
                    if os.name == 'nt':
                        from hostrun.windows_cleanup import PinnedEntry, pin_ancestors
                        with pin_ancestors(folder), closing(PinnedEntry(path, delete=True)) as pin:
                            if path.stat().st_nlink != 1:
                                continue
                            actual = hashlib.sha256()
                            for block in _blocks(pin):
                                actual.update(block)
                            if actual.hexdigest() != path.stem:
                                continue
                            pin.delete()
                    else:
                        self._read_blob(path.stem)
                        path.unlink()
                except (CommandError, OSError, ValueError):
                    continue
                removed.append(path.stem)
        return {'removed': removed}
