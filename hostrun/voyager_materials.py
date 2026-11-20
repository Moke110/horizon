"""Offline user-input materials and immutable, content-addressed text snapshots."""
import base64
import io
import hashlib
import hmac
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from common import file_lock
from hostrun.agent_service import CommandError
from hostrun.managed_database import database_connection


class MaterialsService:
    def __init__(self, home, *, token, fault_observer=None):
        if not isinstance(token,str) or not token: raise ValueError('Launch token required')
        self.home=Path(home);self._token=token
        self._fault=fault_observer or (lambda phase:None)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS voyager_materials(material_id TEXT PRIMARY KEY, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS voyager_material_operations(operation_id TEXT PRIMARY KEY, request TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS voyager_material_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL);
            ''')

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
        from pypdf import PdfReader
        from pdf_ingest.ingest import build_text_and_anchors
        status = 'pending_text'
        pages = []
        reason = 'damaged_or_unsupported'
        try:
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                reason = 'encrypted'
            elif len(reader.pages) > 2000:
                reason = 'page_limit'
            else:
                pages = [page.extract_text() or '' for page in reader.pages]
                if sum(len(text.encode('utf8')) for text in pages) > 4 * 1024 * 1024:
                    pages = []
                    reason = 'text_limit'
                else:
                    reason = 'extraction_quality_unverified' if all(text.strip() for text in pages) and pages else 'scan_or_empty_page'
                    status = 'extracted_unverified' if reason == 'extraction_quality_unverified' else 'pending_text'
        except Exception:
            pass
        text, anchors = build_text_and_anchors(pages)
        self._publish(data, digest)
        text_data = text.encode('utf8')
        text_hash = hashlib.sha256(text_data).hexdigest()
        self._publish(text_data, text_hash)
        identity = str(uuid.uuid4())
        result = dict(schema_version=1, material_id=identity, entity_id=identity, version=1, title=command['title'],
            source=dict(kind='user_pdf', label=command['source_label'], source_id=digest),
            snapshot=dict(content_hash=text_hash, original_hash=digest, extraction_version='pypdf.7.1.1', byte_length=len(text_data)),
            pdf=dict(status=status, reason=reason, page_count=len(pages), anchors=anchors, ocr=False), summary=None)
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
        if result['source']['kind'] != 'user_pdf':
            raise CommandError('invalid_material', 'Material is not a PDF')
        return self._read_blob(result['snapshot']['original_hash'])

    def list_materials(self,*,token):
        self._authorize(token)
        with self._connect() as db:return [json.loads(r[0]) for r in db.execute('SELECT result FROM voyager_materials ORDER BY rowid DESC')]

    def get_package(self,material_id,*,token):
        self._authorize(token)
        with self._connect() as db:row=db.execute('SELECT result FROM voyager_materials WHERE material_id=?',(material_id,)).fetchone()
        if row is None:raise CommandError('not_found','Unknown material')
        result=json.loads(row[0]);result['original_text']=self._read_blob(result['snapshot']['content_hash']).decode('utf8')
        return result
