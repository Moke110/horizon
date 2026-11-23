"""Offline user-input materials and immutable, content-addressed text snapshots."""
import base64
import io
import hashlib
import hmac
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path

from common import file_lock
from hostrun.agent_service import CommandError
from hostrun.managed_database import database_connection
from hostrun.voyager_material_lifecycle import MaterialLifecycle, SCHEMA, _record, material_write_lock, serialized_material_write


class MaterialsService(MaterialLifecycle):
    def __init__(self, home, *, token, fault_observer=None):
        if not isinstance(token,str) or not token: raise ValueError('Launch token required')
        self.home=Path(home);self._token=token
        self._fault=fault_observer or (lambda phase:None)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS voyager_materials(material_id TEXT PRIMARY KEY, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS voyager_material_operations(operation_id TEXT PRIMARY KEY, request TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS voyager_material_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS voyager_academic_identity ON voyager_materials(json_extract(result,'$.academic.identity'));
            ''')
            db.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        with database_connection(self.home) as db:
            with db: yield db

    def _authorize(self,token):
        if not isinstance(token,str) or not hmac.compare_digest(token,self._token):raise PermissionError('Unauthorized materials command')

    def _blob(self,digest):
        from hostrun.managed_import import managed_paths
        managed=managed_paths(self.home)
        return (managed[1]/'originals/voyager-text' if managed else self.home/'snapshots/voyager-text')/(digest+'.txt')

    @contextmanager
    def _publication_lock(self,folder):
        lock=folder/'publication.lock'
        descriptor,seed=tempfile.mkstemp(prefix='.lock-seed-',dir=folder)
        try:
            with os.fdopen(descriptor,'wb') as stream:
                stream.write(b'\0');stream.flush();os.fsync(stream.fileno())
            try:os.link(seed,lock,follow_symlinks=False)
            except FileExistsError:pass
        finally:
            if os.path.exists(seed):os.unlink(seed)
        if lock.is_symlink() or lock.is_junction() or lock.stat().st_size!=1:raise CommandError('snapshot_corrupt','Invalid publication lock')
        with file_lock(lock):yield

    def _publish(self,data,digest):
        from hostrun.managed_import import managed_paths,store,_blocks
        target=self._blob(digest)
        if managed_paths(self.home):
            with store(self.home) as material:
                name=target.relative_to(material.root).as_posix()
                pin=material.existing(name)
                if pin:
                    if hashlib.sha256(b''.join(_blocks(pin))).hexdigest()!=digest:raise CommandError('snapshot_corrupt','Snapshot changed')
                else:
                    self._fault('before_publish')
                    material.publish(name,[data],hashlib.md5(data).hexdigest())
        else:
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.parent.resolve()!=target.parent.absolute():raise CommandError('snapshot_corrupt','Linked snapshot directory')
            with self._publication_lock(target.parent):
                if target.is_symlink() or target.is_junction():raise CommandError('snapshot_corrupt','Linked snapshot entry')
                if target.exists():
                    self._read_blob(digest)
                else:
                    descriptor,temporary=tempfile.mkstemp(prefix='.pending-',dir=target.parent)
                    try:
                        with os.fdopen(descriptor,'wb') as stream:
                            stream.write(data);stream.flush();os.fsync(stream.fileno())
                        self._fault('before_publish')
                        # Linking a complete same-directory temporary publishes
                        # atomically without replacing a concurrently added name.
                        try:os.link(temporary,target,follow_symlinks=False)
                        except FileExistsError as error:raise CommandError('snapshot_conflict','Snapshot appeared during publication') from error
                    finally:
                        if os.path.exists(temporary):os.unlink(temporary)
                    self._read_blob(digest)
        self._fault('after_publish')

    def _read_blob(self,digest):
        from hostrun.managed_import import managed_paths,store,_blocks
        target=self._blob(digest)
        if managed_paths(self.home):
            with store(self.home) as material:
                pin=material.existing(target.relative_to(material.root).as_posix())
                if pin is None:raise CommandError('snapshot_corrupt','Missing snapshot')
                data=b''.join(_blocks(pin))
        else:
            try:
                if target.is_symlink() or target.is_junction() or target.resolve()!=target.absolute() or target.stat().st_nlink!=1:raise CommandError('snapshot_corrupt','Snapshot identity changed')
                data=target.read_bytes()
            except FileNotFoundError as error:raise CommandError('snapshot_corrupt','Missing snapshot') from error
        if hashlib.sha256(data).hexdigest()!=digest:raise CommandError('snapshot_corrupt','Snapshot changed')
        return data

    @serialized_material_write
    def register_text(self,command,*,token):
        self._authorize(token)
        if not isinstance(command,dict) or type(command.get('schema_version')) is not int or command['schema_version']!=1:raise CommandError('invalid_command','schema_version 1 required')
        if set(command)-{'schema_version','operation_id','title','text','source_label'}:raise CommandError('invalid_command','Unsupported material input field')
        if any(not isinstance(command.get(k),str) or not command[k].strip() for k in ('operation_id','title','text','source_label')):raise CommandError('invalid_command','operation, title, text and source label required')
        if len(command['title'])>500 or len(command['source_label'])>2000 or len(command['text'].encode())>4*1024*1024:raise CommandError('invalid_command','Material input too large')
        request=json.dumps(command,sort_keys=True,ensure_ascii=False)
        with self._connect() as db:
            old=db.execute('SELECT request,result FROM voyager_material_operations WHERE operation_id=?',(command['operation_id'],)).fetchone()
        if old:
            if old[0]!=request:raise CommandError('operation_conflict','operation_id conflicts')
            return json.loads(old[1])
        data=command['text'].encode('utf8');digest=hashlib.sha256(data).hexdigest()
        self._publish(data,digest)
        identity=str(uuid.uuid4())
        result=dict(schema_version=1,material_id=identity,entity_id=identity,version=1,title=command['title'],source=dict(source_id=str(uuid.uuid4()),kind='user_input',label=command['source_label'],received_at=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')),snapshot=dict(snapshot_id=str(uuid.uuid4()),content_hash=digest,hash_algorithm='sha256',extraction_version='pasted_text.v1',byte_length=len(data)),summary=None)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT request,result FROM voyager_material_operations WHERE operation_id=?',(command['operation_id'],)).fetchone()
            if old:
                if old[0]!=request:raise CommandError('operation_conflict','operation_id conflicts')
                return json.loads(old[1])
            encoded=json.dumps(result,ensure_ascii=False)
            db.execute('INSERT INTO voyager_materials VALUES(?,?)',(identity,encoded))
            db.execute('INSERT INTO voyager_material_operations VALUES(?,?,?)',(command['operation_id'],request,encoded))
            db.execute('INSERT INTO voyager_material_events(event) VALUES(?)',(json.dumps(dict(schema_version=1,type='voyager.material_registered',material_id=identity,operation_id=command['operation_id'])),))
            self._fault('before_commit')
        self._fault('after_commit')
        return result

    def register_web(self, command, *, token, cancel=None):
        from hostrun import voyager_web as web
        self._authorize(token)
        if (not isinstance(command, dict) or command.get('schema_version') != 1 or
                set(command)-{'schema_version','operation_id','url','dynamic'} or
                not isinstance(command.get('operation_id'),str) or not command['operation_id'].strip() or
                not isinstance(command.get('url'),str) or type(command.get('dynamic',False)) is not bool):
            raise CommandError('invalid_command','A public HTTPS page URL is required')
        try:
            web.public_address(command['url'])
        except ValueError as error:
            raise CommandError('invalid_command','A public HTTPS page URL is required') from error
        request=json.dumps(command,sort_keys=True)
        operation=command['operation_id']
        def replay(db):
            old=db.execute('SELECT request,result FROM voyager_material_operations WHERE operation_id=?',(operation,)).fetchone()
            if old and old[0]!=request:
                raise CommandError('operation_conflict','operation_id conflicts')
            return json.loads(old[1]) if old else None
        with self._connect() as db:
            previous=replay(db)
        if previous is not None:
            return previous
        try:
            text,details,original=web.acquire(command['url'],dynamic=command.get('dynamic',False),cancel=cancel)
        except (OSError, ValueError, HTTPException):
            text,details,original=web.unavailable('fetch_failed',command['url'])
        if cancel and cancel.is_set():
            raise CommandError('cancelled','Web capture cancelled')
        data=text.encode('utf8')
        digest=hashlib.sha256(data).hexdigest()
        with material_write_lock(self.home):
            self._publish(data,digest)
            snapshot=dict(snapshot_id=str(uuid.uuid4()),content_hash=digest,hash_algorithm='sha256',
                          extraction_version=web.extractor_version(),byte_length=len(data))
            if original:
                original_hash=hashlib.sha256(original).hexdigest()
                self._publish(original,original_hash)
                snapshot['original_hash']=original_hash
            identity=str(uuid.uuid4())
            result=dict(schema_version=1,material_id=identity,entity_id=identity,version=1,
                        title=details['metadata'].get('title') or command['url'],
                        source=dict(kind='public_web',source_id=details['url'],label=command['url'],
                                    received_at=datetime.now(timezone.utc).isoformat()),
                        snapshot=snapshot,web=details,summary=None)
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                previous=replay(db)
                if previous is not None:return previous
                if cancel and cancel.is_set():
                    raise CommandError('cancelled','Web capture cancelled')
                encoded=json.dumps(result,ensure_ascii=False)
                db.execute('INSERT INTO voyager_materials VALUES(?,?)',(identity,encoded))
                db.execute('INSERT INTO voyager_material_operations VALUES(?,?,?)',(operation,request,encoded))
                db.execute('INSERT INTO voyager_material_events(event) VALUES(?)',(json.dumps(dict(type='voyager.web_registered',material_id=identity,operation_id=operation)),))
            return result

    def register_pdf(self, command, *, token):
        """Accept bounded bytes, retaining the original and page anchors atomically."""
        self._authorize(token)
        required = {'schema_version', 'operation_id', 'title', 'pdf_base64', 'source_label'}
        if not isinstance(command, dict) or set(command) != required or command.get('schema_version') != 1:
            raise CommandError('invalid_command', 'PDF import fields are invalid')
        if any(not isinstance(command.get(k), str) or not command[k].strip() for k in required - {'schema_version'}):
            raise CommandError('invalid_command', 'PDF import strings required')
        if len(command['pdf_base64']) > 14 * 1024 * 1024 or len(command['title']) > 500 or len(command['source_label']) > 2000:
            raise CommandError('invalid_command', 'PDF input exceeds bounds')
        try:
            data = base64.b64decode(command['pdf_base64'], validate=True)
        except ValueError as error:
            raise CommandError('invalid_command', 'Invalid PDF encoding') from error
        if not data.startswith(b'%PDF-') or len(data) > 10 * 1024 * 1024:
            raise CommandError('invalid_command', 'PDF bytes required, at most 10 MiB')
        digest = hashlib.sha256(data).hexdigest()
        request = json.dumps({k:v for k,v in command.items() if k != 'pdf_base64'}, sort_keys=True) + digest
        with self._connect() as db:
            previous = db.execute('SELECT request,result FROM voyager_material_operations WHERE operation_id=?', (command['operation_id'],)).fetchone()
        if previous:
            if previous[0] != request:
                raise CommandError('operation_conflict', 'operation_id conflicts')
            return json.loads(previous[1])
        extracted = self.extract_academic_pdf(data)
        text, pdf = extracted['text'], extracted['pdf']
        with material_write_lock(self.home):
            self._publish(data, digest)
            text_data = text.encode('utf8')
            text_hash = hashlib.sha256(text_data).hexdigest()
            self._publish(text_data, text_hash)
            identity = str(uuid.uuid4())
            result = dict(schema_version=1, material_id=identity, entity_id=identity, version=1, title=command['title'],
                source=dict(kind='user_pdf', label=command['source_label'], source_id=digest),
                snapshot=dict(content_hash=text_hash, original_hash=digest, extraction_version='pypdf.7.1.1', byte_length=len(text_data)),
                pdf=pdf, summary=None)
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                previous = db.execute('SELECT request,result FROM voyager_material_operations WHERE operation_id=?', (command['operation_id'],)).fetchone()
                if previous:
                    if previous[0] != request:
                        raise CommandError('operation_conflict', 'operation_id conflicts')
                    return json.loads(previous[1])
                encoded = json.dumps(result)
                db.execute('INSERT INTO voyager_materials VALUES(?,?)', (identity, encoded))
                db.execute('INSERT INTO voyager_material_operations VALUES(?,?,?)', (command['operation_id'], request, encoded))
                db.execute('INSERT INTO voyager_material_events(event) VALUES(?)', (json.dumps(dict(type='voyager.pdf_registered', material_id=identity)),))
            return result

    @staticmethod
    def extract_academic_pdf(data, *, timeout=25):
        import subprocess
        import sys
        from pdf_ingest.ingest import build_text_and_anchors
        status = 'pending_text'
        pages = []
        reason = 'damaged_or_unsupported'
        with tempfile.TemporaryDirectory(prefix='horizon-pdf-') as folder:
            source = Path(folder) / 'input.pdf'
            output = Path(folder) / 'result.json'
            source.write_bytes(data)
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            try:
                completed = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-m', 'hostrun.pdf_worker', str(source), str(output)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, **options)
                if completed.returncode == 0 and output.stat().st_size <= 25 * 1024 * 1024:
                    extracted = json.loads(output.read_text(encoding='utf8'))
                    pages = extracted['pages']
                    reason = extracted['reason']
                    if reason == 'extraction_quality_unverified':
                        status = 'extracted_unverified'
            except (subprocess.TimeoutExpired, OSError, ValueError, KeyError):
                reason = 'extraction_limit_or_failure'
        text, anchors = build_text_and_anchors(pages)
        return dict(text=text, pdf=dict(status=status, reason=reason, page_count=len(pages), anchors=anchors, ocr=False))

    def academic_disposition(self, request_id, source, source_id, *, token):
        self._authorize(token)
        with self._connect() as db:
            row = db.execute('''SELECT json_extract(result,'$.academic.status')
                FROM voyager_materials
                WHERE json_extract(result,'$.academic.status') IN ('abandoned','supplemented')
                  AND EXISTS (SELECT 1 FROM json_each(result,'$.academic.provenance') AS origin
                    WHERE json_extract(origin.value,'$.request_id')=?
                      AND json_extract(origin.value,'$.source')=?
                      AND json_extract(origin.value,'$.source_id')=?)
                LIMIT 1''', (request_id, source, source_id)).fetchone()
        return row[0] if row else None

    def academic_receipt(self, operation, *, token):
        self._authorize(token)
        with self._connect() as db:
            row = db.execute('SELECT result FROM voyager_material_operations WHERE operation_id=?', (operation,)).fetchone()
        return json.loads(row[0]) if row else None

    @serialized_material_write
    def register_academic(self, operation, request_id, candidate, capture, attempts, *, token):
        from hostrun.voyager_fulltext import audit_url, doi_of
        from hostrun.voyager_web import extractor_version
        from urllib.parse import urlsplit
        import re

        self._authorize(token)
        source_id = candidate['source_id']
        identity = candidate['source'] + ':' + source_id
        if candidate['source'] == 'arxiv':
            identity = 'arxiv:' + re.sub(r'v\d+$', '', source_id)
        doi = doi_of(candidate)
        if doi:
            identity = 'doi:' + doi
        request = json.dumps([request_id, candidate['source'], source_id])
        text = capture['text'].encode('utf8') if capture else b''
        text_hash = hashlib.sha256(text).hexdigest()
        original = capture['original'] if capture else None
        original_hash = hashlib.sha256(original).hexdigest() if original else None
        self._publish(text, text_hash)
        if original_hash:
            self._publish(original, original_hash)
        origin = audit_url(urlsplit(candidate['url']))
        found_at = datetime.now(timezone.utc).isoformat()
        provenance = dict(request_id=request_id, source=candidate['source'], source_id=source_id,
                          source_type=candidate.get('source_type'), url=origin,
                          original_hash=original_hash, format=capture['kind'] if capture else None,
                          related_dois=candidate.get('related_dois', []), attempts=attempts,
                          reference_ids=candidate.get('reference_ids', []),
                          reference_count=candidate.get('reference_count'), cited_by_count=candidate.get('cited_by_count'))
        notice_type = candidate.get('source_type') if candidate.get('source_type') in (
            'correction', 'retraction', 'expression-of-concern') else None
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT request,result FROM voyager_material_operations WHERE operation_id=?', (operation,)).fetchone()
            if previous:
                if previous[0] != request:
                    raise CommandError('operation_conflict', 'operation_id conflicts')
                return json.loads(previous[1])
            request_row = db.execute('SELECT result FROM voyager_requests WHERE request_id=?', (request_id,)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='voyager_requests'").fetchone() else None
            task_id = json.loads(request_row[0]).get('task_id') if request_row else None
            if task_id:
                provenance['task_id'] = task_id
            versions = db.execute("""SELECT result FROM voyager_materials
                WHERE json_extract(result,'$.academic.identity')=? ORDER BY rowid DESC""", (identity,)).fetchall()
            existing = [json.loads(row[0]) for row in versions]
            current = next((item for item in existing if item['snapshot']['content_hash'] == text_hash
                            and item['academic'].get('notice_type') == notice_type), None)
            if current is not None:
                result = current
                if provenance not in result['academic']['provenance']:
                    result['academic']['provenance'].append(provenance)
                result['academic']['attempts'] = attempts
                db.execute('UPDATE voyager_materials SET result=? WHERE material_id=?',
                           (json.dumps(result, ensure_ascii=False), result['material_id']))
            else:
                material_id = str(uuid.uuid4())
                kind = capture['kind'] if capture else 'missing'
                extraction = {'xml': 'jats_body.v1', 'pdf': 'pypdf.7.1.1', 'missing': 'unavailable.v1'}
                algorithm = extractor_version() if kind == 'html' else extraction[kind]
                removed = db.execute('SELECT entity_id, version FROM voyager_removed_academic_versions WHERE identity=?',
                                     (identity,)).fetchone()
                entity_id = existing[0]['entity_id'] if existing else removed[0] if removed else material_id
                current_version = max((item['version'] for item in existing), default=0)
                if removed:
                    current_version = max(current_version, removed[1])
                snapshot = dict(snapshot_id=str(uuid.uuid4()), content_hash=text_hash, hash_algorithm='sha256',
                                extraction_version=algorithm, byte_length=len(text))
                if original_hash:
                    snapshot['original_hash'] = original_hash
                result = dict(schema_version=1, material_id=material_id,
                    entity_id=entity_id, version=current_version+1, title=candidate['title'][:500], summary=None,
                    source=dict(kind='academic', label=origin, source_id=source_id, received_at=found_at),
                    snapshot=snapshot, academic=dict(identity=identity, format=kind, notice_type=notice_type,
                        status='available' if capture else 'pending_fulltext', attempts=attempts,
                        url=capture['url'] if capture else origin, provenance=[provenance]))
                if kind == 'pdf':
                    result['pdf'] = capture['pdf']
                db.execute('INSERT INTO voyager_materials VALUES(?,?)',
                           (material_id, json.dumps(result, ensure_ascii=False)))
            db.execute('INSERT INTO voyager_material_operations VALUES(?,?,?)',
                       (operation, request, json.dumps(result, ensure_ascii=False)))
            db.execute('INSERT INTO voyager_material_events(event) VALUES(?)',(json.dumps(dict(
                type='voyager.academic_recorded', material_id=result['material_id'])),))
        return result

    @serialized_material_write
    def resolve_pdf(self, command, *, token):
        self._authorize(token)
        if not isinstance(command,dict) or command.get('schema_version') != 1 or command.get('action') not in ('supplement','abandon'):
            raise CommandError('invalid_command','PDF decision required')
        if set(command)-{'schema_version','operation_id','material_id','expected_version','action','text'}:
            raise CommandError('invalid_command','Unsupported PDF decision field')
        if not isinstance(command.get('operation_id'),str) or not command['operation_id']:
            raise CommandError('invalid_command','Operation required')
        text=command.get('text','')
        if command['action']=='supplement' and (not isinstance(text,str) or not text.strip() or len(text.encode())>4*1024*1024):
            raise CommandError('invalid_command','Bounded supplement text required')
        request=json.dumps(command,sort_keys=True)
        if command['action']=='supplement':
            digest=hashlib.sha256(text.encode()).hexdigest()
            self._publish(text.encode(),digest)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT request,result FROM voyager_material_operations WHERE operation_id=?',(command['operation_id'],)).fetchone()
            if previous:
                if previous[0]!=request:raise CommandError('operation_conflict','Operation conflicts')
                return json.loads(previous[1])
            row=db.execute('SELECT result FROM voyager_materials WHERE material_id=?',(command.get('material_id'),)).fetchone()
            if not row:raise CommandError('not_found','Unknown PDF')
            result=json.loads(row[0])
            if result['source']['kind']!='user_pdf':raise CommandError('invalid_material','PDF required')
            if type(command.get('expected_version')) is not int or command['expected_version']!=result['version']:raise CommandError('version_conflict','PDF changed')
            result['version']+=1
            result['pdf']['status']='supplemented' if command['action']=='supplement' else 'abandoned'
            if command['action']=='supplement':
                result['snapshot'].update(content_hash=digest,byte_length=len(text.encode()),extraction_version='user_supplement.v1')
                result['pdf']['supplement_has_page_mapping']=False
            encoded=json.dumps(result)
            db.execute('UPDATE voyager_materials SET result=? WHERE material_id=?',(encoded,result['material_id']))
            db.execute('INSERT INTO voyager_material_operations VALUES(?,?,?)',(command['operation_id'],request,encoded))
            db.execute('INSERT INTO voyager_material_events(event) VALUES(?)',(json.dumps(dict(type='voyager.pdf_resolved',material_id=result['material_id'])),))
        return result

    def pdf_original(self, material_id, *, token):
        self._authorize(token)
        with self._connect() as db:
            row = db.execute('SELECT result FROM voyager_materials WHERE material_id=?', (material_id,)).fetchone()
        if row is None:
            raise CommandError('not_found', 'Unknown material')
        result = json.loads(row[0])
        if not result.get('pdf') or not result['snapshot'].get('original_hash'):
            raise CommandError('invalid_material', 'Material is not a PDF')
        return self._read_blob(result['snapshot']['original_hash'])

    def list_summaries(self, *, token, archived=False, include_version=False):
        self._authorize(token)
        with self._connect() as db:
            rows = db.execute("SELECT material_id,json_extract(result,'$.title'),json_extract(result,'$.source.label'),"
                              "json_extract(result,'$.version') "
                              "FROM voyager_materials WHERE COALESCE(json_extract(result,'$.archived'),0)=? "
                              "ORDER BY rowid DESC", (archived,))
            return [dict(material_id=mid, title=title, source={'label': label},
                         **({'version': version} if include_version else {}))
                    for mid,title,label,version in rows]

    def list_materials(self,*,token,archived=False):
        self._authorize(token)
        with self._connect() as db:
            rows = db.execute("SELECT result FROM voyager_materials "
                              "WHERE COALESCE(json_extract(result,'$.archived'),0)=? ORDER BY rowid DESC", (archived,))
            return [json.loads(row[0]) for row in rows]

    def get_package(self, material_id, *, token, task_id=None):
        self._authorize(token)
        with self._connect() as db:row=db.execute('SELECT result FROM voyager_materials WHERE material_id=?',(material_id,)).fetchone()
        if row is None:raise CommandError('not_found','Unknown material')
        result = json.loads(row[0])
        if academic := result.get('academic'):
            academic['abstract_authorizations'] = [
                record for record in academic.get('abstract_authorizations', [])
                if task_id is not None and record['task_id'] == task_id]
        result['original_text'] = self._read_blob(result['snapshot']['content_hash']).decode('utf8')
        return result

    def _bind_reference(self, db, material, owner):
        """Bind a verified citation on the caller's transaction."""
        material_id = material['material_id']
        snapshot = material['snapshot']
        content_hash = snapshot['content_hash']
        reference = {
            'material_id': material_id,
            'material_version': material['version'],
            'content_hash': content_hash,
            'source_kind': material['source']['kind'],
            'title': material['title'],
        }
        if 'snapshot_id' in snapshot:
            reference['snapshot_id'] = snapshot['snapshot_id']
        owner_id = f"{owner}:{content_hash}"
        db.execute("INSERT INTO voyager_material_references VALUES(?,?,?,?) "
                   "ON CONFLICT(material_id,owner_id) DO NOTHING",
                   (material_id, owner_id, content_hash, snapshot.get('original_hash')))
        return reference

    def validate_citation(self, db, citation):
        """Read-only verification of a saved material version, excerpt and locator.

        Keeper previews must never bind a reference, download a source or write
        any material row. This method only reads the committed material and its
        immutable snapshot, then reports the pinned metadata an owner would use.
        """
        material = _record(db, citation.get('material_id'))
        version = citation.get('material_version')
        if version is not None and material['version'] != version:
            raise CommandError('version_conflict', '资料版本已更新，请重新核对快照')
        body = self._read_blob(material['snapshot']['content_hash']).decode('utf8')
        anchor = _citation_anchor(material, body, citation['excerpt'],
                                  citation['locator'])
        reference = {
            'material_id': material['material_id'],
            'material_version': material['version'],
            'content_hash': material['snapshot']['content_hash'],
            'source_kind': material['source']['kind'],
            'title': material['title'],
        }
        if 'snapshot_id' in material['snapshot']:
            reference['snapshot_id'] = material['snapshot']['snapshot_id']
        reference['anchor'] = anchor
        reference['source_label'] = material['source'].get('label')
        return reference

    def report_reference(self, db, citation, *, report_id, task_id, abstract_operation, token):
        """Validate and bind Editor evidence within the caller's write transaction."""
        self._authorize(token)
        material = _record(db, citation["material_id"])
        owner = f"report:{report_id}:1"
        if abstract_operation is None:
            resolved = self.validate_citation(db, citation)
            self._bind_reference(db, material, owner)
            return {**resolved, "excerpt": citation["excerpt"], "locator": citation["locator"]}
        grants = (material.get("academic") or {}).get("abstract_authorizations", [])
        for grant in grants:
            if (grant["operation_id"] == abstract_operation and grant["task_id"] == task_id
                    and citation["locator"] == "abstract" and citation["excerpt"] in grant["abstract"]):
                return {**self._bind_reference(db, material, owner), "status": "abstract",
                        "abstract_operation": abstract_operation, "excerpt": citation["excerpt"],
                        "locator": "abstract", "limitation": grant["limitation"]}
        raise CommandError("invalid_source", "本任务的授权摘要不包含该摘录")

    def report_abstract_view(self, citation, owner, task_id, *, token):
        reference = self.resolve_reference(citation["material_id"], owner, token=token)
        if reference["status"] == "removed":
            return {"status": "removed", "segment": None}
        try:
            package = self.get_package(citation["material_id"], task_id=task_id, token=token)
        except CommandError as error:
            if error.code != "not_found":
                raise
            return {"status": "removed", "segment": None}
        for grant in package["academic"]["abstract_authorizations"]:
            if grant["operation_id"] == citation["abstract_operation"]:
                return {"status": "abstract", "segment": grant["abstract"],
                        "limitation": grant["limitation"]}
        return {"status": "unavailable", "segment": None}

    def knowledge_reference(self, db, material_id, excerpt, *, card_id, token):
        """Verify and bind a Knowledge Card citation on the caller's transaction."""
        self._authorize(token)
        material = _record(db, material_id)
        self._check_knowledge_excerpt(material, excerpt)
        return self._bind_reference(db, material, f"card:{card_id}")

    def validate_knowledge_pin(self, db, citation):
        """Verify the exact saved Card snapshot before copying its citation."""
        material = _record(db, citation["material_id"])
        snapshot = material["snapshot"]
        if any(citation.get(field) != actual for field, actual in (
                ("material_version", material["version"]),
                ("content_hash", snapshot["content_hash"]),
                ("snapshot_id", snapshot.get("snapshot_id")))):
            raise CommandError('version_conflict', '资料快照已更新，请重新核对来源')
        self._check_knowledge_excerpt(material, citation["excerpt"])

    def _check_knowledge_excerpt(self, material, excerpt):
        body = self._read_blob(material['snapshot']['content_hash']).decode('utf8')
        if ' '.join(excerpt.split()) not in ' '.join(body.split()):
            raise CommandError('invalid_source', '摘录不在所选资料的快照中')

    def theme_reference(self, db, citation, *, theme_id, token):
        """Verify and bind a Theme evidence citation on the caller's transaction."""
        self._authorize(token)
        material = _record(db, citation['material_id'])
        version = citation.get('material_version')
        if version is not None and material['version'] != version:
            raise CommandError('version_conflict', '资料版本已更新，请重新核对快照')
        body = self._read_blob(material['snapshot']['content_hash']).decode('utf8')
        anchor = _citation_anchor(material, body, citation['excerpt'], citation['locator'])
        reference = self._bind_reference(db, material, f"theme:{theme_id}")
        return {**reference, 'anchor': anchor, 'source_label': material['source'].get('label')}

    @serialized_material_write
    def citation_view(self, citation, owner_id, *, token):
        """Open the pinned snapshot page or paragraph behind one citation."""
        self._authorize(token)
        material_id = citation['material_id']
        with self._connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT content_hash FROM voyager_material_references "
                             "WHERE material_id=? AND owner_id=?", (material_id, owner_id)).fetchone()
            if row is None:
                raise CommandError('not_found', 'Unknown material reference')
            content_hash = row[0]
            removed = db.execute("SELECT 1 FROM voyager_removed_materials "
                                 "WHERE material_id=?", (material_id,)).fetchone() is not None
        if content_hash != citation['content_hash']:
            raise CommandError('snapshot_changed', '引用与已绑定的快照摘要不符')
        result = {
            'schema_version': 1,
            'status': 'removed' if removed else 'available',
            'material_id': material_id,
            'material_version': citation.get('material_version'),
            'content_hash': content_hash,
            'title': citation['title'],
            'source': {
                'kind': citation.get('source_kind'),
                'label': citation.get('source_label'),
            },
            'locator': citation['locator'],
            'excerpt': citation['excerpt'],
            'anchor': citation['anchor'],
            'segment': None,
            'segment_truncated': False,
        }
        if removed:
            return result
        body = self._read_blob(content_hash).decode('utf8')
        anchor = citation['anchor']
        segment_start, segment_end = anchor['start'], anchor['end']
        segment = body[segment_start:segment_end]
        match = _excerpt_pattern(citation['excerpt']).search(segment)
        if match is None:
            raise CommandError('invalid_source', '摘录不在所选资料的快照中')
        if len(segment) > 4000:
            start = max(0, min(match.start() - 600, len(segment) - 4000))
            segment = segment[start:start + 4000]
            result['segment_truncated'] = True
        result['segment'] = segment
        return result

    @staticmethod
    def removed_references(db):
        return {row[0] for row in db.execute("SELECT material_id FROM voyager_removed_materials")}


def _excerpt_pattern(excerpt):
    return re.compile(r'\s+'.join(re.escape(word) for word in excerpt.split()))


def citation_anchors(material, body):
    """Return the saved page or paragraph boundaries used for citations."""
    anchors = (material.get('pdf') or {}).get('anchors') or (material.get('web') or {}).get('anchors')
    if not anchors:
        anchors = [
            dict(id=f'p{index}', start=match.start(), end=match.end())
            for index, match in enumerate(re.finditer(r'[^\r\n]+', body), 1)
            if match.group().strip()
        ]
    return anchors


def _citation_anchor(material, body, excerpt, locator):
    anchors = citation_anchors(material, body)
    locator = locator.strip().lower()
    page = re.fullmatch(r'(?:page\s*:?[ \t]*|第\s*)([0-9]{1,7})\s*(?:页)?', locator)
    if page:
        anchors = [anchor for anchor in anchors if anchor.get('page') == int(page[1])]
    elif re.fullmatch(r'p[0-9]+', locator):
        anchors = [anchor for anchor in anchors if anchor.get('id') == locator]
    pattern = _excerpt_pattern(excerpt)
    matches = [anchor for anchor in anchors
               if pattern.search(body, anchor['start'], anchor['end'])]
    if not matches:
        raise CommandError('invalid_source', '所选快照页或段落不包含该摘录')
    if len(matches) > 1:
        raise CommandError('ambiguous_source', '摘录出现在多处，请填写快照页码或段落编号')
    return {key: matches[0][key] for key in ('id', 'page', 'start', 'end') if key in matches[0]}
