"""Voyager-owned explicit scopes, retrieval budgets and durable public results."""
from contextlib import contextmanager
import hmac
import hashlib
import tempfile
import json
import re
from urllib.parse import urlsplit
from pathlib import Path
import time
import uuid

from hostrun.agent_service import CommandError
from hostrun.managed_database import database_connection
from common import file_lock

DEFAULT_LIMITS={'seconds':900,'queries':20,'candidates':100,'fulltexts':20}


class ExplicitScopeProvider:
    """Read an explicit temporary scope; never manufacture a Frontier."""
    def resolve(self,value):
        if not isinstance(value,dict) or not isinstance(value.get('text'),str) or not value['text'].strip() or len(value['text'].encode('utf-8'))>4096:
            raise CommandError('invalid_scope','A bounded explicit scope is required')
        result={'kind':'temporary_explicit','text':value['text']}
        for name in ('alternatives','must_include','exclude','sources'):
            values=value.get(name,[])
            if not isinstance(values,list) or len(values)>16 or any(not isinstance(v,str) or not v.strip() or len(v.encode('utf-8'))>(80 if name=='sources' else 512 if name=='alternatives' else 256) for v in values):
                raise CommandError('invalid_scope','Scope lists must contain bounded nonempty strings')
            result[name]=list(dict.fromkeys(values))
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]+',source) for source in result['sources']):raise CommandError('invalid_scope','Source identifiers are invalid')
        if sum(len(term.encode('utf-8')) for name in ('must_include','exclude') for term in result[name])>4096:raise CommandError('invalid_scope','Hard constraints exceed their byte budget')
        if not result['sources']:raise CommandError('invalid_scope','At least one source is required')
        return result


class VoyagerService:
    def __init__(self,home,*,token,scope_provider=None,providers=None,clock=time.time,on_change=None):
        if not isinstance(token,str) or not token:raise ValueError('A launch token is required')
        self.home=Path(home);self._token=token;self.scope_provider=scope_provider or ExplicitScopeProvider()
        if providers is None:
            from hostrun.voyager_sources import default_providers
            from hostrun.voyager_doi_sources import CrossrefProvider, OpenAlexProvider
            providers = default_providers()
            providers.update(crossref=CrossrefProvider(), openalex=OpenAlexProvider())
        self.providers=dict(providers);self.clock=clock;self.on_change=on_change or (lambda event:None)
        with self._connect() as db:self.initialize_database(db)

    def _notify(self,value):
        try:self.on_change({'schema_version':1,'request_id':value['request_id'],'version':value['version'],'status':value['status']})
        except Exception:pass  # A disconnected viewer cannot undo committed work.

    @staticmethod
    def initialize_database(db):
        db.executescript('''CREATE TABLE IF NOT EXISTS voyager_requests(request_id TEXT PRIMARY KEY,result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS voyager_operations(operation_id TEXT PRIMARY KEY,request TEXT NOT NULL,result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS voyager_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,operation_id TEXT UNIQUE NOT NULL,event TEXT NOT NULL);''')

    @contextmanager
    def _connect(self):
        with database_connection(self.home) as db:
            try:
                yield db;db.commit()
            except BaseException:
                db.rollback();raise

    def _authorize(self,token):
        if not isinstance(token,str) or not hmac.compare_digest(token,self._token):raise PermissionError('Invalid launch token')

    def _validate(self,command):
        if not isinstance(command,dict) or type(command.get('schema_version')) is not int or command['schema_version']!=1 or not isinstance(command.get('operation_id'),str) or not command['operation_id'].strip():
            raise CommandError('invalid_command','schema_version and operation_id required')

    def _replay(self,db,command,action):
        encoded=json.dumps({'action':action,'command':command},sort_keys=True)
        row=db.execute('SELECT request,result FROM voyager_operations WHERE operation_id=?',(command['operation_id'],)).fetchone()
        if row:
            if row[0]!=encoded:raise CommandError('operation_conflict','operation_id conflicts')
            return encoded,json.loads(row[1])
        return encoded,None

    def _save(self,db,value,command,request,event):
        value['operation_id']=command['operation_id'];encoded=json.dumps(value)
        db.execute('INSERT INTO voyager_requests VALUES(?,?) ON CONFLICT(request_id) DO UPDATE SET result=excluded.result',(value['request_id'],encoded))
        db.execute('INSERT INTO voyager_operations VALUES(?,?,?)',(command['operation_id'],request,encoded))
        db.execute('INSERT INTO voyager_events(operation_id,event) VALUES(?,?)',(command['operation_id'],json.dumps({'schema_version':1,'type':event,'request_id':value['request_id'],'operation_id':command['operation_id']})))
        return value

    def get(self,request_id,*,token):
        self._authorize(token)
        with self._connect() as db:
            row=db.execute('SELECT result FROM voyager_requests WHERE request_id=?',(request_id,)).fetchone()
            if not row:raise CommandError('not_found','Unknown Voyager request')
            return json.loads(row[0])

    def list(self,*,token):
        self._authorize(token)
        with self._connect() as db:return [json.loads(r[0]) for r in db.execute('SELECT result FROM voyager_requests ORDER BY rowid DESC')]

    def create(self,command,*,token):
        self._authorize(token);self._validate(command)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE');encoded,previous=self._replay(db,command,'create')
            if previous is not None:return previous
            scope=self.scope_provider.resolve(command.get('scope'))
            if not isinstance(command.get('limits',{}),dict):raise CommandError('invalid_budget','Budgets must be an object')
            limits={**DEFAULT_LIMITS,**command.get('limits',{})}
            if set(limits)!=set(DEFAULT_LIMITS) or any(type(v) is not int or v<0 or v>{'seconds':86400,'queries':1000,'candidates':10000,'fulltexts':1000}[k] for k,v in limits.items()):raise CommandError('invalid_budget','Budgets must be nonnegative integer limits')
            window=command.get('window')
            if not isinstance(window,dict) or window.get('mode') not in ('initial','incremental') or window.get('basis') not in ('published','discovered') or any(type(window.get(k)) is not int or window[k]<0 for k in ('from_at','until_at','overlap_seconds')) or window['from_at']>=window['until_at']:
                raise CommandError('invalid_window','A nonempty UTC interval and explicit time basis are required')
            if len(scope['sources'])*len(set([scope['text'],*scope['alternatives']]))*(2 if window['mode']=='initial' else 1)>128:raise CommandError('invalid_scope','Too many expanded queries')
            identity=str(uuid.uuid4());queries=[]
            for source in scope['sources']:
                for term in dict.fromkeys([scope['text'],*scope['alternatives']]):
                    for phase in ('foundation','recent') if window['mode']=='initial' else ('incremental',):
                        queries.append({'query_id':str(uuid.uuid5(uuid.UUID(identity),str(len(queries)))),'source':source,'term':term,'phase':phase,'must_include':scope['must_include'],'exclude':scope['exclude'],'status':'pending','cursor':None,'attempts':[]})
            value={'schema_version':1,'request_id':identity,'scope':scope,'window':window,'queries':queries,'limits':limits,'version':1,'status':'planned','used':{k:0 for k in DEFAULT_LIMITS},'candidates':[],'successful_cursors':{},'gaps':[]}
            return self._save(db,value,command,encoded,'voyager.request_created')

    def _change(self,command,action,mutate,*,token):
        self._authorize(token)
        if not isinstance(command,dict) or type(command.get('schema_version')) is not int or command['schema_version']!=1 or not isinstance(command.get('operation_id'),str) or not command['operation_id'].strip():raise CommandError('invalid_command','schema_version and operation_id required')
        operation=command['operation_id'];request=json.dumps({'action':action,'command':command},sort_keys=True)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT request,result FROM voyager_operations WHERE operation_id=?',(operation,)).fetchone()
            if old:
                if old[0]!=request:raise CommandError('operation_conflict','operation_id conflicts')
                return json.loads(old[1]),False
            row=db.execute('SELECT result FROM voyager_requests WHERE request_id=?',(command.get('request_id'),)).fetchone()
            if not row:raise CommandError('not_found','Unknown retrieval request')
            value=json.loads(row[0])
            if type(command.get('expected_version')) is not int or command['expected_version']!=value['version']:raise CommandError('version_conflict','Retrieval request changed')
            interrupted=None
            if action=='execution_started' and value.get('execution_operation') and value['status'] in ('running','uncertain'):
                interrupted=json.loads(json.dumps(value))
                interrupted['status']='uncertain' if interrupted.get('inflight') else 'partial'
                for query in interrupted['queries']:
                    if query['attempts'] and query['attempts'][-1]['status']=='reserved':query['attempts'][-1]['status']='unknown'
            mutate(value);value['version']+=1
            if interrupted is not None:
                db.execute('UPDATE voyager_operations SET result=? WHERE operation_id=?',(json.dumps(interrupted),interrupted['execution_operation']))
                event_id=str(uuid.uuid4())
                db.execute('INSERT INTO voyager_events(operation_id,event) VALUES(?,?)',(event_id,json.dumps({'schema_version':1,'type':'voyager.execution_interrupted','request_id':value['request_id'],'operation_id':event_id})))
            self._gaps(value)
            encoded=json.dumps(value)
            db.execute('UPDATE voyager_requests SET result=? WHERE request_id=?',(encoded,value['request_id']))
            db.execute('INSERT INTO voyager_operations VALUES(?,?,?)',(operation,request,encoded))
            db.execute('INSERT INTO voyager_events(operation_id,event) VALUES(?,?)',(operation,json.dumps({'schema_version':1,'type':'voyager.'+action,'request_id':value['request_id'],'operation_id':operation})))
        self._notify(value)
        return value,True

    @staticmethod
    def _gaps(value):
        value['gaps']=[{'query_id':q['query_id'],'source':q['source'],'reason':q.get('error') or q['status'],'window':value['window']} for q in value['queries'] if q['status']!='complete']
        value['gaps'] += [{'work_id':work['work_id'],'reason':'fulltext_'+work['status']} for work in value.get('fulltext_work',[]) if work['status']!='succeeded']

    def _account_time(self,value):
        segment=value['segments'][-1]
        elapsed=max(segment['used']['seconds'],int(max(0,self.clock()-segment['started_at'])))
        value['used']['seconds']+=elapsed-segment['used']['seconds'];segment['used']['seconds']=elapsed

    @staticmethod
    def _limit_reason(value):
        segment=value['segments'][-1]
        return next((key+'_budget' for key in DEFAULT_LIMITS if segment['used'][key]>=segment['limits'][key]),None)

    def reserve(self,command,*,token):
        def mutate(value):
            if value['status']!='running' or value.get('inflight'):raise CommandError('execution_busy','A query is already pending or execution stopped')
            self._account_time(value);reason=self._limit_reason(value)
            if reason:value.update(status='partial',stop_reason=reason);return
            query=next((q for q in value['queries'] if q['query_id']==command.get('query_id')),None)
            if not query or query['status']!='pending':raise CommandError('invalid_query','Query is not pending')
            segment=value['segments'][-1];window=dict(value['window'])
            window['effective_from_at']=max(0,window['from_at']-window['overlap_seconds']) if window['mode']=='incremental' else window['from_at']
            if query['phase']=='foundation':window['effective_from_at']=0
            actual={'schema_version':1,'query_id':query['query_id'],'source':query['source'],'term':query['term'],'phase':query['phase'],'must_include':query['must_include'],'exclude':query['exclude'],'window':window,'cursor':query['cursor'],'limit':segment['limits']['candidates']-segment['used']['candidates'],'timeout_seconds':segment['limits']['seconds']-segment['used']['seconds']}
            value['inflight']={'reservation_id':command['operation_id'],'execution_operation':value['execution_operation'],'query_id':query['query_id'],'actual_query':actual}
            query['attempts'].append({'reservation_id':command['operation_id'],'status':'reserved','actual_query':actual})
            segment['used']['queries']+=1;value['used']['queries']+=1
        return self._change(command,'query_reserved',mutate,token=token)[0]

    @staticmethod
    def _bounded_page(page):
        if not isinstance(page,dict) or set(page)-{'candidates','exhausted','next_cursor','receipt','source_query','error_code'}:raise CommandError('invalid_provider_response','Invalid page fields')
        if 'error_code' in page:
            if set(page)!={'error_code'} or page['error_code'] not in ('unconfigured','provider_failed','timeout','authentication','budget_unknown','rate_limited'):raise CommandError('invalid_provider_response','Invalid provider error shape')
            return
        rows=page.get('candidates')
        if not isinstance(rows,list) or len(rows)>10000:raise CommandError('invalid_provider_response','Candidate page is too large')
        sizes={'source':80,'source_id':512,'title':2048,'abstract':32768,'url':4096,'doi':512,'journal':512,'source_type':64,'peer_review_status':64}
        allowed=set(sizes)|{'published_at','discovered_at','authors','is_preprint','missing_fields','reference_ids','reference_count','cited_by_count'}
        total=0
        for row in rows:
            if not isinstance(row,dict) or set(row)-allowed:raise CommandError('invalid_provider_response','Invalid candidate fields')
            for key,limit in sizes.items():
                if key in row:
                    if not isinstance(row[key],str) or len(row[key].encode('utf-8'))>limit:raise CommandError('invalid_provider_response','Candidate text exceeds its bound')
                    total+=len(row[key].encode('utf-8'))
            for key in ('reference_count', 'cited_by_count'):
                if row.get(key) is not None and (type(row[key]) is not int or not 0 <= row[key] <= 2**53-1):
                    raise CommandError('invalid_provider_response','Invalid citation count')
            for key,count,limit in (('authors',100,256),('missing_fields',16,64),('reference_ids',100,512)):
                values=row.get(key,[])
                if not isinstance(values,list) or len(values)>count or any(not isinstance(v,str) or len(v.encode('utf-8'))>limit for v in values):raise CommandError('invalid_provider_response','Candidate list exceeds its bound')
                total+=sum(len(v.encode('utf-8')) for v in values)
            if row.get('is_preprint') is not None and type(row['is_preprint']) is not bool:raise CommandError('invalid_provider_response','Invalid preprint state')
            address=urlsplit(row.get('url',''))
            if address.scheme not in ('https','http') or not address.hostname or address.username or address.password:raise CommandError('invalid_provider_response','Candidate URL must be a credential-free HTTP address')
            total+=512
            if total>2*1024*1024:raise CommandError('invalid_provider_response','Candidate page exceeds its byte budget')
        for key,limit in (('receipt',512),('next_cursor',4096),('source_query',16384)):
            if page.get(key) is not None and (not isinstance(page[key],str) or len(page[key].encode('utf-8'))>limit):raise CommandError('invalid_provider_response','Page metadata exceeds its bound')

    def record(self,command,*,token):
        self._authorize(token);self._bounded_page(command.get('page'))
        def mutate(value):
            lease=value.get('inflight')
            if value['status']!='running' or not lease or lease['reservation_id']!=command.get('reservation_id'):raise CommandError('execution_changed','Reserved execution is no longer running')
            query=next(q for q in value['queries'] if q['query_id']==lease['query_id']);attempt=query['attempts'][-1]
            self._account_time(value);segment=value['segments'][-1];page=command.get('page')
            error=page.get('error_code') if isinstance(page,dict) else 'invalid_provider_response'
            if error:
                query.update(status='failed',error=error if error in ('unconfigured','provider_failed','timeout','authentication','budget_unknown','rate_limited') else 'provider_failed');attempt['status']='failed';value['inflight']=None;return
            rows=page.get('candidates')
            if not isinstance(rows,list) or len(rows)>lease['actual_query']['limit'] or type(page.get('exhausted')) is not bool or not isinstance(page.get('receipt'),str) or not page['receipt'] or len(page['receipt'])>512:raise CommandError('invalid_provider_response','Provider response is outside its reservation')
            cursor=page.get('next_cursor')
            if (page['exhausted'] and cursor is not None) or (not page['exhausted'] and (not isinstance(cursor,str) or not cursor or cursor==query['cursor'])):raise CommandError('invalid_provider_response','Provider pagination did not make progress')
            for row in rows:
                if not isinstance(row,dict) or any(not isinstance(row.get(k),str) or not row[k].strip() for k in ('source','source_id','title','url')) or any(row.get(k) is not None and (type(row[k]) is not int or row[k]<0) for k in ('published_at','discovered_at')):raise CommandError('invalid_provider_response','Candidate metadata is invalid')
                text=(row['title']+' '+row.get('abstract','')).casefold()
                if row['source']!=query['source'] or any(t.casefold() not in text for t in query['must_include']) or any(t.casefold() in text for t in query['exclude']):continue
                stamp=row.get(value['window']['basis']+'_at');start=lease['actual_query']['window']['effective_from_at']
                if stamp is None or stamp>=value['window']['until_at'] or (query['phase']!='foundation' and stamp<start):continue
                previous=next((item for item in value['candidates'] if (item['source'],item['source_id'])==(row['source'],row['source_id'])),None)
                if previous:
                    if query['query_id'] not in previous['query_ids']:previous['query_ids'].append(query['query_id'])
                else:
                    candidate = {**row, 'first_seen_at': int(self.clock()), 'query_ids': [query['query_id']],
                                 'scope_kind': value['scope']['kind'], 'request_id': value['request_id']}
                    doi = row.get('doi', '').strip().lower().removeprefix('https://doi.org/')
                    if re.fullmatch(r'10\.\d{4,9}/[^\s]+', doi):
                        candidate['canonical_id'] = 'doi:' + doi
                    value['candidates'].append(candidate)
            segment['used']['candidates']+=len(rows);value['used']['candidates']+=len(rows)
            if 'source_query' in page:attempt['source_query']=page['source_query']
            attempt.update(status='received',receipt=page['receipt'],candidate_count=len(rows));value['inflight']=None
            if segment['used']['seconds']>=segment['limits']['seconds']:
                value.update(status='partial',stop_reason='seconds_budget');return
            query['cursor']=cursor
            if page['exhausted']:
                query['status']='complete';value['successful_cursors'][query['query_id']]=value['window']['until_at']
            reason=self._limit_reason(value)
            if reason:value.update(status='partial',stop_reason=reason)
        return self._change(command,'query_recorded',mutate,token=token)[0]

    def reserve_fulltext(self,command,*,token):
        """Charge one body acquisition before its owner performs external IO."""
        def mutate(value):
            if value['status']=='cancelled' or not value.get('segments'):raise CommandError('invalid_transition','No active retrieval allowance')
            self._account_time(value);reason=self._limit_reason(value)
            if reason:value.update(status='partial',stop_reason=reason);return
            if not any((row['source'],row['source_id'])==(command.get('candidate_source'),command.get('candidate_id')) for row in value['candidates']):raise CommandError('not_found','Candidate is not part of this retrieval')
            value['segments'][-1]['used']['fulltexts']+=1;value['used']['fulltexts']+=1
            value.setdefault('fulltext_work',[]).append({'work_id':command['operation_id'],'execution_operation':value['execution_operation'],'candidate_source':command['candidate_source'],'candidate_id':command['candidate_id'],'status':'reserved'})
            value.update(status='partial',stop_reason=self._limit_reason(value) or 'fulltext_pending')
        return self._change(command,'fulltext_reserved',mutate,token=token)[0]

    def record_fulltext(self,command,*,token):
        """Record only an acquisition owner's reference; Voyager stores no body here."""
        def mutate(value):
            if value['status']=='cancelled':raise CommandError('execution_changed','Retrieval was cancelled')
            self._account_time(value)
            work=next((row for row in value.get('fulltext_work',[]) if row['work_id']==command.get('work_id')),None)
            if not work or work['status']!='reserved':raise CommandError('invalid_transition','Body reservation is not pending')
            status=command.get('status');reference=command.get('evidence_ref')
            if status not in ('succeeded','failed') or (status=='succeeded' and (not isinstance(reference,str) or not reference or len(reference)>512 or any(c in reference for c in ('\n','\r')))):raise CommandError('invalid_receipt','A bounded owner reference is required')
            work.update(status=status,evidence_ref=reference if status=='succeeded' else None)
            if self._limit_reason(value):value.update(status='partial',stop_reason=self._limit_reason(value))
            elif status=='failed':value.update(status='partial',stop_reason='fulltext_failed')
            elif not self._limit_reason(value) and all(q['status']=='complete' for q in value['queries']) and all(work['status']=='succeeded' for work in value.get('fulltext_work',[])):value.update(status='complete',stop_reason=None)
        return self._change(command,'fulltext_recorded',mutate,token=token)[0]

    def cancel(self,command,*,token):
        def mutate(value):
            if value['status'] in ('cancelled','complete'):raise CommandError('invalid_transition','Retrieval is already terminal')
            value['status']='cancelled';value['stop_reason']='cancelled'
            for query in value['queries']:
                if query['status']!='complete':query['status']='cancelled'
                if query['attempts'] and query['attempts'][-1]['status']=='reserved':query['attempts'][-1]['status']='cancelled_unknown'
        return self._change(command,'request_cancelled',mutate,token=token)[0]

    def execute(self,command,*,token):
        self._authorize(token);self._validate(command)
        with self._connect() as db:
            _,previous=self._replay(db,command,'execution_started')
            if previous is not None:return previous
        identity=hashlib.sha256((str(self.home.resolve())+'\0'+str(command.get('request_id'))).encode()).hexdigest()
        lock=Path(tempfile.gettempdir())/'horizon-voyager-executions'/(identity+'.lock')
        with file_lock(lock,blocking=False) as acquired:
            if not acquired:raise CommandError('execution_busy','Retrieval request is already running')
            return self._execute_owned(command,token=token)

    def _execute_owned(self,command,*,token):
        def begin(value):
            if value['status'] in ('cancelled','complete'):raise CommandError('invalid_transition','Start a new retrieval request')
            if (value.get('inflight') or any(work['status']=='reserved' for work in value.get('fulltext_work',[]))) and command.get('acknowledge_unknown') is not True:raise CommandError('confirmation_required','Previous provider outcome is unknown; acknowledge its reserved budget before continuing')
            if value['status']!='planned' and command.get('continue') is not True:raise CommandError('invalid_transition','Continuation must be explicit')
            if value['status']=='cancelled':raise CommandError('execution_changed','Cancelled retrieval cannot be resumed')
            if value['status']=='running' and not command.get('continue'):raise CommandError('execution_busy','Retrieval is already running')
            for query in value['queries']:
                if query['status'] in ('failed','unconfigured'):query['status']='pending';query.pop('error',None)
                if query['attempts'] and query['attempts'][-1]['status']=='reserved':query['attempts'][-1]['status']='unknown'
            for work in value.get('fulltext_work',[]):
                if work['status']=='reserved':work['status']='unknown'
            value['inflight']=None;value['status']='running';value['execution_operation']=command['operation_id'];value['stop_reason']=None
            value.setdefault('segments',[]).append({'execution_operation':command['operation_id'],'limits':dict(value['limits']),'used':{k:0 for k in DEFAULT_LIMITS},'started_at':self.clock()})
        value,fresh=self._change(command,'execution_started',begin,token=token)
        if not fresh:return value
        while value['status']=='running' and value.get('execution_operation')==command['operation_id']:
            query=next((q for q in value['queries'] if q['status']=='pending'),None)
            if query is None:break
            if query['source'] not in self.providers:
                def unavailable(current):next(q for q in current['queries'] if q['query_id']==query['query_id']).update(status='unconfigured',error='unconfigured')
                value,_=self._change({'schema_version':1,'operation_id':str(uuid.uuid4()),'request_id':value['request_id'],'expected_version':value['version']},'source_unconfigured',unavailable,token=token);continue
            value=self.reserve({'schema_version':1,'operation_id':str(uuid.uuid4()),'request_id':value['request_id'],'query_id':query['query_id'],'expected_version':value['version']},token=token)
            if value['status']!='running':break
            lease=value['inflight']
            try:page=self.providers[query['source']].search(lease['actual_query'])
            except Exception:page={'error_code':'provider_failed'}
            try:value=self.record({'schema_version':1,'operation_id':str(uuid.uuid4()),'request_id':value['request_id'],'expected_version':value['version'],'reservation_id':lease['reservation_id'],'page':page},token=token)
            except CommandError:
                value=self.get(value['request_id'],token=token);break
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE');current=json.loads(db.execute('SELECT result FROM voyager_requests WHERE request_id=?',(value['request_id'],)).fetchone()[0])
            if current.get('execution_operation')==command['operation_id'] and current['status']=='running':
                current['status']='uncertain' if current.get('inflight') else 'partial' if any(work['status']!='succeeded' for work in current.get('fulltext_work',[])) else 'complete' if all(q['status']=='complete' for q in current['queries']) else 'blocked' if all(q['status']=='unconfigured' for q in current['queries']) else 'partial' if any(q['status']=='complete' for q in current['queries']) else 'failed'
                current['version']+=1;self._gaps(current);db.execute('UPDATE voyager_requests SET result=? WHERE request_id=?',(json.dumps(current),current['request_id']))
            db.execute('UPDATE voyager_operations SET result=? WHERE operation_id=?',(json.dumps(current),command['operation_id']))
            event_id=str(uuid.uuid4())
            db.execute('INSERT INTO voyager_events(operation_id,event) VALUES(?,?)',(event_id,json.dumps({'schema_version':1,'type':'voyager.execution_finished','operation_id':event_id,'execution_operation':command['operation_id'],'request_id':current['request_id'],'status':current['status']})))
        self._notify(current)
        return current

    def recent(self,*,token,before=None):
        self._authorize(token)
        with self._connect() as db:
            boundary=db.execute('SELECT rowid FROM voyager_requests WHERE request_id=?',(before,)).fetchone() if before else None
            if before and not boundary:raise CommandError('not_found','Unknown retrieval page')
            rows=db.execute('SELECT result FROM voyager_requests WHERE rowid<? ORDER BY rowid DESC LIMIT 50',(boundary[0] if boundary else 9223372036854775807,)).fetchall()
        return [{'request_id':v['request_id'],'version':v['version'],'status':v['status'],'scope_text':v['scope']['text'][:256],'stop_reason':v.get('stop_reason')} for v in map(lambda r:json.loads(r[0]),rows)]

    def view(self,request_id,*,token):
        value=self.get(request_id,token=token)
        result={k:v for k,v in value.items() if k not in ('queries','candidates','segments','gaps','fulltext_work')}
        result.update(queries=[{**{k:q[k] for k in ('query_id','source','term','phase','status','cursor')},'attempt_count':len(q['attempts']),'attempts':q['attempts'][-1:]} for q in value['queries']],candidates=[{k:v for k,v in row.items() if k!='abstract'} for row in value['candidates'][:50]],candidate_count=len(value['candidates']),segments=value.get('segments',[])[-3:],segment_count=len(value.get('segments',[])),gaps=value['gaps'][:100],gap_count=len(value['gaps']),fulltext_work=value.get('fulltext_work',[])[:50])
        return result

    def query_history(self,request_id,query_id,*,token,offset=0):
        if type(offset) is not int or offset<0:raise CommandError('invalid_command','History offset must be nonnegative')
        value=self.get(request_id,token=token);query=next((q for q in value['queries'] if q['query_id']==query_id),None)
        if not query:raise CommandError('not_found','Unknown query')
        return {'request_id':request_id,'query_id':query_id,'offset':offset,'total':len(query['attempts']),'items':query['attempts'][offset:offset+10]}
