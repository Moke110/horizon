"""Public persistent schedule commands: explicit development scopes only."""
import json
import uuid
from hostrun.agent_service import CommandError


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS agent_scheduler_settings (id INTEGER PRIMARY KEY, result TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS agent_schedules (schedule_id TEXT PRIMARY KEY, result TEXT NOT NULL)')


def list_schedules(service, *, token):
    service._authorize(token)
    with service._connect() as db:
        initialize(db)
        return [json.loads(row[0]) for row in db.execute('SELECT result FROM agent_schedules ORDER BY rowid')]


def command(service, value, *, token):
    service._authorize(token)
    if not isinstance(value, dict) or (type(value.get('schema_version')) is not int or value['schema_version'] != 1) or not isinstance(value.get('operation_id'), str) or not value['operation_id'].strip():
        raise CommandError('invalid_command', 'schema_version and operation_id required')
    operation=value['operation_id']; request=json.dumps({'schedule_command':value},sort_keys=True)
    with service._connect() as db:
        initialize(db); db.execute('BEGIN IMMEDIATE')
        previous=db.execute('SELECT request,result FROM agent_operations WHERE operation_id=?',(operation,)).fetchone()
        if previous:
            if previous[0]!=request: raise CommandError('operation_conflict','operation_id conflicts')
            return json.loads(previous[1])
        identity=value.get('schedule_id')
        if identity:
            row=db.execute('SELECT result FROM agent_schedules WHERE schedule_id=?',(identity,)).fetchone()
            if not row: raise CommandError('not_found','Unknown schedule')
            result=json.loads(row[0])
            if type(value.get('expected_version')) is not int or value['expected_version']!=result['version']: raise CommandError('version_conflict','Schedule changed')
        else:
            result={'schema_version':1,'schedule_id':str(uuid.uuid4()),'version':0,'status':'active','scope_kind':'development_explicit','covered_until':value.get('start_at'),'active_run':None}
        for field in ('title','scope','interval_seconds','next_at'):
            if field in value: result[field]=value[field]
        if value.get('action')=='abandon_run':
            if not result['active_run'] or result['active_run']['status']!='coverage_pending':raise CommandError('version_conflict','Only a coverage review run can be abandoned')
            result['active_run']=None;result['status']='paused'
        if value.get('action') in ('pause','resume'): result['status']='paused' if value['action']=='pause' else 'active'
        if not all(isinstance(result.get(k),str) and result[k].strip() and len(result[k])<=16000 for k in ('title','scope')): raise CommandError('invalid_command','title and bounded explicit scope required')
        if type(result.get('interval_seconds')) is not int or result['interval_seconds']<1: raise CommandError('invalid_command','Positive interval required')
        for field in ('next_at','covered_until'):
            if type(result.get(field)) is not int or result[field]<0: raise CommandError('invalid_command','Nonnegative UTC epoch seconds required')
        result['version']+=1; result['operation_id']=operation
        encoded=json.dumps(result)
        db.execute('INSERT INTO agent_schedules VALUES(?,?) ON CONFLICT(schedule_id) DO UPDATE SET result=excluded.result',(result['schedule_id'],encoded))
        db.execute('INSERT INTO agent_operations VALUES(?,?,?)',(operation,request,encoded))
        db.execute('INSERT INTO agent_events(operation_id,event) VALUES(?,?)',(operation,json.dumps({'schema_version':1,'type':'agent.schedule_changed','schedule_id':result['schedule_id'],'operation_id':operation})))
        return result

def tick(service, *, now, token, occupied=0):
    service._authorize(token)
    if type(now) is not int or now < 0: raise CommandError('invalid_command','Nonnegative UTC epoch seconds required')
    claimed=[]
    with service._connect() as db:
        initialize(db);db.execute('BEGIN IMMEDIATE')
        rows=db.execute('SELECT schedule_id,result FROM agent_schedules ORDER BY rowid').fetchall()
        settings=db.execute('SELECT result FROM agent_scheduler_settings WHERE id=1').fetchone()
        limit=json.loads(settings[0])['main'] if settings else 2
        turns={json.loads(r[0])['turn_id']:json.loads(r[0]) for r in db.execute('SELECT result FROM agent_turns')}
        active=occupied+sum(bool((run:=json.loads(encoded)['active_run'])) and turns.get(run.get('turn_id'),{'status':'running'})['status']=='running' for _,encoded in rows)
        for identity,encoded in rows:
            plan=json.loads(encoded)
            if plan['status']!='active' or now<plan['next_at']:continue
            until=plan['next_at']+((now-plan['next_at'])//plan['interval_seconds'])*plan['interval_seconds']
            plan['pending_until']=max(plan.get('pending_until',plan['covered_until']),until)
            if plan['active_run'] is None and active>=limit: continue
            plan['next_at']=until+plan['interval_seconds']
            if plan['active_run'] is None and plan['pending_until']>plan['covered_until']:
                active+=1
                run={'run_id':str(uuid.uuid4()),'schedule_id':identity,'from_at':plan['covered_until'],'until_at':plan['pending_until'],'scope':plan['scope'],'status':'claimed'}
                plan['active_run']=run;claimed.append(run)
            plan['version']+=1
            db.execute('UPDATE agent_schedules SET result=? WHERE schedule_id=?',(json.dumps(plan),identity))
            event_id=str(uuid.uuid4())
            db.execute('INSERT INTO agent_events(operation_id,event) VALUES(?,?)',(event_id,json.dumps({'schema_version':1,'type':'agent.schedule_triggered','schedule_id':identity,'until_at':until,'operation_id':event_id})))
    return claimed


def finish_run(service, run_id, *, covered_until, token, defer_remainder=False):
    service._authorize(token)
    with service._connect() as db:
        initialize(db);db.execute('BEGIN IMMEDIATE')
        for identity,encoded in db.execute('SELECT schedule_id,result FROM agent_schedules').fetchall():
            plan=json.loads(encoded);run=plan['active_run']
            if not run or run['run_id']!=run_id:continue
            if type(covered_until) is not int or not run['from_at']<=covered_until<=run['until_at']:raise CommandError('invalid_command','Coverage exceeds claimed range')
            plan['covered_until']=covered_until;plan['active_run']=None
            # A pending trigger must remain due even if the most recent tick
            # already advanced next_at while the previous run was active.
            if not defer_remainder and plan.get('pending_until',covered_until)>covered_until:
                plan['next_at']=min(plan['next_at'],plan['pending_until'])
            plan['version']+=1
            db.execute('UPDATE agent_schedules SET result=? WHERE schedule_id=?',(json.dumps(plan),identity))
            event_id=str(uuid.uuid5(uuid.UUID(run_id),'covered'))
            db.execute('INSERT INTO agent_events(operation_id,event) VALUES(?,?)',(event_id,json.dumps({'schema_version':1,'type':'agent.schedule_covered','schedule_id':identity,'covered_until':covered_until,'operation_id':event_id})))
            return plan
        raise CommandError('not_found','Unknown active run')



def get_limits(service, *, token):
    service._authorize(token)
    with service._connect() as db:
        initialize(db)
        row=db.execute('SELECT result FROM agent_scheduler_settings WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {'main':2,'children':4}


def set_limits(service, *, main, children, token):
    service._authorize(token)
    if any(type(v) is not int or not 1<=v<=32 for v in (main,children)):raise CommandError('invalid_command','Limits must be integers from 1 to 32')
    result={'main':main,'children':children}
    with service._connect() as db:
        initialize(db)
        db.execute('INSERT INTO agent_scheduler_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET result=excluded.result',(json.dumps(result),))
    return result

def bind_run(service, run, *, token):
    """Atomically bind a still-active schedule claim to one task/turn."""
    service._authorize(token)
    with service._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT result FROM agent_schedules WHERE schedule_id=?',(run['schedule_id'],)).fetchone()
        if not row:raise CommandError('not_found','Unknown schedule')
        plan=json.loads(row[0]);current=plan['active_run']
        if not current or current['run_id']!=run['run_id']:raise CommandError('version_conflict','Claim changed')
        if current.get('turn_id'):
            return json.loads(db.execute('SELECT result FROM agent_turns WHERE turn_id=?',(current['turn_id'],)).fetchone()[0])
        if plan['status']!='active':raise CommandError('version_conflict','Schedule paused')
        identity=uuid.UUID(run['run_id'])
        task_id=str(uuid.uuid5(identity,'task'));turn_id=str(uuid.uuid5(identity,'turn'));session_id=str(uuid.uuid5(identity,'session'))
        text=f"开发阶段显式范围：{run['scope']}\n仅执行 UTC 秒区间 [{run['from_at']}, {run['until_at']})。\n最终输出严格 JSON：{{\"schema_version\":1,\"result\":\"执行结果\",\"coverage\":{{\"from_at\":{run['from_at']},\"until_at\":{run['until_at']},\"covered_until\":实际完成前缀终点整数,\"status\":\"complete 或 partial 或 uncovered\"}}}}。这是开发阶段执行覆盖声明，不是来源检索证据；不可声称未执行时段已覆盖。"
        task={'schema_version':1,'operation_id':task_id,'task_id':task_id,'entity_id':task_id,'session_id':session_id,'version':2,'title':f"{plan['title']} · UTC {run['from_at']}–{run['until_at']}",'messages':[{'role':'You','text':text}]}
        turn={'schema_version':1,'turn_id':turn_id,'operation_id':turn_id,'entity_id':turn_id,'task_id':task_id,'session_id':session_id,'input_version':2,'version':1,'status':'running','text':text,'result':'','error':'','attempts':[]}
        db.execute('INSERT INTO agent_tasks VALUES(?,?)',(task_id,json.dumps(task)))
        db.execute('INSERT INTO agent_turns VALUES(?,?,?)',(turn_id,task_id,json.dumps(turn)))
        plan['active_run'].update(task_id=task_id,turn_id=turn_id);plan['version']+=1
        db.execute('UPDATE agent_schedules SET result=? WHERE schedule_id=?',(json.dumps(plan),run['schedule_id']))
        db.execute('INSERT INTO agent_events(operation_id,event) VALUES(?,?)',(turn_id,json.dumps({'schema_version':1,'type':'agent.schedule_run_bound','task_id':task_id,'turn_id':turn_id,'schedule_id':run['schedule_id']})))
    return turn


def execution_coverage(run, text):
    """Validate a development execution declaration, never retrieval proof."""
    try:value=json.loads(text)
    except (ValueError,TypeError):return None
    if not isinstance(value,dict) or type(value.get('schema_version')) is not int or value['schema_version']!=1:return None
    coverage=value.get('coverage')
    if not isinstance(coverage,dict) or set(coverage)!={'from_at','until_at','covered_until','status'}:return None
    if any(type(coverage.get(k)) is not int for k in ('from_at','until_at','covered_until')):return None
    if coverage['from_at']!=run['from_at'] or coverage['until_at']!=run['until_at']:return None
    end=coverage['covered_until']
    if not run['from_at']<=end<=run['until_at']:return None
    expected='complete' if end==run['until_at'] else 'uncovered' if end==run['from_at'] else 'partial'
    return end if coverage['status']==expected else None


def hold_coverage(service, run_id, *, token):
    service._authorize(token)
    with service._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for identity,encoded in db.execute('SELECT schedule_id,result FROM agent_schedules').fetchall():
            plan=json.loads(encoded);run=plan['active_run']
            if not run or run['run_id']!=run_id or run.get('status')=='coverage_pending':continue
            run['status']='coverage_pending';plan['version']+=1
            db.execute('UPDATE agent_schedules SET result=? WHERE schedule_id=?',(json.dumps(plan),identity))
