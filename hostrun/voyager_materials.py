"""Offline user-input materials and immutable, content-addressed text snapshots."""
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

    def list_materials(self,*,token):
        self._authorize(token)
        with self._connect() as db:return [json.loads(r[0]) for r in db.execute('SELECT result FROM voyager_materials ORDER BY rowid DESC')]

    def get_package(self,material_id,*,token):
        self._authorize(token)
        with self._connect() as db:row=db.execute('SELECT result FROM voyager_materials WHERE material_id=?',(material_id,)).fetchone()
        if row is None:raise CommandError('not_found','Unknown material')
        result=json.loads(row[0]);result['original_text']=self._read_blob(result['snapshot']['content_hash']).decode('utf8')
        return result
