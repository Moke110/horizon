from hostrun.agent_service import AgentService, CommandError
from hostrun.agent_scheduling import command, list_schedules
import pytest


def test_schedule_public_create_edit_pause_replay_and_restart(tmp_path):
    service=AgentService(tmp_path,token='s')
    create={'schema_version':1,'operation_id':'create','title':'Plan','scope':'bounded topic','interval_seconds':60,'start_at':100,'next_at':160}
    plan=command(service,create,token='s')
    assert command(service,create,token='s')==plan
    assert plan['scope_kind']=='development_explicit'
    updated=command(service,{'schema_version':1,'operation_id':'edit','schedule_id':plan['schedule_id'],'expected_version':1,'scope':'changed topic'},token='s')
    paused=command(service,{'schema_version':1,'operation_id':'pause','schedule_id':plan['schedule_id'],'expected_version':2,'action':'pause'},token='s')
    assert paused['status']=='paused'
    assert list_schedules(AgentService(tmp_path,token='s'),token='s')==[paused]
    with pytest.raises(CommandError,match='Schedule changed'):
        command(service,{'schema_version':1,'operation_id':'stale','schedule_id':plan['schedule_id'],'expected_version':1,'action':'resume'},token='s')

def test_offline_triggers_merge_and_resume_uncovered_range(tmp_path):
    from hostrun.agent_scheduling import tick, finish_run
    from hostrun.agent_service import AgentService
    service=AgentService(tmp_path,token='s')
    plan=command(service,{'schema_version':1,'operation_id':'create','title':'Plan','scope':'bounded topic','interval_seconds':60,'start_at':100,'next_at':160},token='s')
    run=tick(service,now=350,token='s')[0]
    assert (run['from_at'],run['until_at'])==(100,340)
    assert tick(service,now=470,token='s')==[]
    finish_run(service,run['run_id'],covered_until=280,token='s')
    next_run=tick(AgentService(tmp_path,token='s'),now=470,token='s')[0]
    assert (next_run['from_at'],next_run['until_at'])==(280,460)

def test_global_main_capacity_keeps_other_plans_due(tmp_path):
    from hostrun.agent_service import AgentService
    from hostrun.agent_scheduling import tick, set_limits
    service=AgentService(tmp_path,token='s')
    for index in range(3):
        command(service,{'schema_version':1,'operation_id':str(index),'title':str(index),'scope':'topic','interval_seconds':60,'start_at':100,'next_at':160},token='s')
    assert len(tick(service,now=160,token='s'))==2
    assert tick(service,now=160,token='s')==[]
    set_limits(service,main=3,children=4,token='s')
    assert len(tick(service,now=160,token='s'))==1

def test_claim_binding_survives_retry_without_duplicate_input(tmp_path):
    from hostrun.agent_service import AgentService
    from hostrun.agent_scheduling import tick, bind_run
    service=AgentService(tmp_path,token='s')
    command(service,{'schema_version':1,'operation_id':'create','title':'Plan','scope':'topic','interval_seconds':60,'start_at':100,'next_at':160},token='s')
    run=tick(service,now=160,token='s')[0]
    first=bind_run(service,run,token='s')
    assert bind_run(AgentService(tmp_path,token='s'),run,token='s')==first
    assert len(service.list_tasks(token='s'))==1
    assert len(service.list_tasks(token='s')[0]['messages'])==1

@pytest.mark.parametrize('coverage_mode',['complete','partial','malformed'])
def test_real_concurrent_schedule_workers_keep_separate_results(tmp_path,coverage_mode):
    import json, time, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from hostrun import embedded_agent
    from hostrun.schedule_runtime import ScheduleRuntime
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            result='alpha-result' if 'alpha' in json.dumps(request['messages']) else 'beta-result'
            content=result if coverage_mode=='malformed' else json.dumps({'schema_version':1,'result':result,'coverage':{'from_at':100,'until_at':160,'covered_until':160 if coverage_mode=='complete' else 140,'status':coverage_mode}})
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            for delta,finish in [({'role':'assistant','content':content},None),({},'stop')]:
                chunk={'id':'test','object':'chat.completion.chunk','created':1,'model':'test-model','choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
                self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    service=AgentService(tmp_path,token='s')
    embedded_agent.configure(tmp_path,provider='horizon-test',model='test-model',api_key='local-only',base_url=f'http://127.0.0.1:{server.server_port}/v1')
    runtime=ScheduleRuntime(service,'s',{'code':str(Path(__file__).resolve().parents[1])},lambda:{})
    try:
        for scope in ('alpha','beta'):
            command(service,{'schema_version':1,'operation_id':scope,'title':scope,'scope':scope,'interval_seconds':60,'start_at':100,'next_at':160},token='s')
        runtime.step(now=160)
        assert len(runtime.workers)==2
        deadline=time.monotonic()+25
        while runtime.workers and time.monotonic()<deadline:
            time.sleep(.05);runtime.step(now=160)
        assert not runtime.workers
        turns=service.list_turns(token='s')
        assert {t['result'] if coverage_mode=='malformed' else json.loads(t['result'])['result'] for t in turns}=={'alpha-result','beta-result'}
        plans=list_schedules(service,token='s')
        assert {p['covered_until'] for p in plans}=={100 if coverage_mode=='malformed' else 160 if coverage_mode=='complete' else 140}
        if coverage_mode=='malformed':assert all(p['active_run']['status']=='coverage_pending' for p in plans)
        assert all(t['status']=='completed' for t in turns)
    finally:
        runtime.stop();server.shutdown();server.server_close();thread.join()

def test_paused_run_blocks_only_its_plan_and_releases_main_capacity(tmp_path):
    from hostrun.agent_scheduling import tick,bind_run
    service=AgentService(tmp_path,token='s')
    for name in ('first','second','third'):
        command(service,{'schema_version':1,'operation_id':name,'title':name,'scope':name,'interval_seconds':60,'start_at':100,'next_at':160},token='s')
    runs=tick(service,now=160,token='s')
    turn=bind_run(service,runs[0],token='s')
    service.transition_turn({'schema_version':1,'operation_id':'pause','turn_id':turn['turn_id'],'expected_version':1,'action':'pause'},token='s')
    new=tick(service,now=220,token='s')
    assert len(new)==1 and new[0]['schedule_id'] not in {r['schedule_id'] for r in runs}
    plans=list_schedules(service,token='s')
    assert next(p for p in plans if p['schedule_id']==runs[0]['schedule_id'])['active_run']['run_id']==runs[0]['run_id']


def test_schedule_edit_is_stale_after_atomic_trigger(tmp_path):
    from hostrun.agent_scheduling import tick
    service=AgentService(tmp_path,token='s')
    plan=command(service,{'schema_version':1,'operation_id':'create','title':'Plan','scope':'topic','interval_seconds':60,'start_at':100,'next_at':160},token='s')
    tick(service,now=160,token='s')
    with pytest.raises(CommandError,match='Schedule changed'):
        command(service,{'schema_version':1,'operation_id':'edit','schedule_id':plan['schedule_id'],'expected_version':1,'scope':'new'},token='s')

def test_tick_and_finish_publish_events_with_coverage(tmp_path):
    import json
    from hostrun.agent_scheduling import tick,finish_run
    service=AgentService(tmp_path,token='s')
    command(service,{'schema_version':1,'operation_id':'create','title':'Plan','scope':'topic','interval_seconds':60,'start_at':100,'next_at':160},token='s')
    run=tick(service,now=160,token='s')[0]
    finish_run(service,run['run_id'],covered_until=150,token='s')
    with service._connect() as db:events=[json.loads(r[0])['type'] for r in db.execute('SELECT event FROM agent_events')]
    assert events==['agent.schedule_changed','agent.schedule_triggered','agent.schedule_covered']

def test_child_execution_wait_releases_slot_and_cancelled_waiter_never_runs(tmp_path):
    import threading,time
    from hostrun.agent_capacity import child_slot
    from hostrun.agent_scheduling import set_limits
    service=AgentService(tmp_path,token='s');set_limits(service,main=2,children=1,token='s')
    cancelled=threading.Event();waiting=threading.Event();finished=threading.Event();outcome=[]
    def wait_for_slot():
        waiting.set()
        try:
            with child_slot(service,'s',cancelled):outcome.append('executed')
        except RuntimeError:outcome.append('cancelled')
        finally:finished.set()
    with child_slot(service,'s',threading.Event()):
        thread=threading.Thread(target=wait_for_slot);thread.start();assert waiting.wait(1)
        cancelled.set();assert finished.wait(2)
    thread.join();assert outcome==['cancelled']
    with child_slot(service,'s',threading.Event()):pass

def test_execution_coverage_declaration_is_strict_and_bounded():
    from hostrun.agent_scheduling import execution_coverage
    run={'from_at':100,'until_at':160}
    assert execution_coverage(run,'ordinary prose') is None
    assert execution_coverage(run,'{"schema_version":1,"coverage":{"from_at":100,"until_at":160,"covered_until":130,"status":"partial"}}')==130
    assert execution_coverage(run,'{"schema_version":1,"coverage":{"from_at":100,"until_at":160,"covered_until":200,"status":"complete"}}') is None

def test_cancelled_schedule_notifies_coverage_review_once(tmp_path):
    from hostrun.agent_scheduling import tick,bind_run
    from hostrun.schedule_runtime import ScheduleRuntime
    service=AgentService(tmp_path,token='s')
    command(service,{'schema_version':1,'operation_id':'create','title':'Plan','scope':'topic','interval_seconds':60,'start_at':100,'next_at':160},token='s')
    run=tick(service,now=160,token='s')[0];turn=bind_run(service,run,token='s')
    service.transition_turn({'schema_version':1,'operation_id':'cancel','turn_id':turn['turn_id'],'expected_version':turn['version'],'action':'cancel'},token='s')
    runtime=ScheduleRuntime(service,'s',{},lambda:{})
    assert runtime.step(now=160)
    assert not runtime.step(now=160)

def test_initial_empty_boundary_does_not_create_or_hold_run(tmp_path):
    from hostrun.agent_scheduling import tick
    service=AgentService(tmp_path,token='s')
    command(service,{'schema_version':1,'operation_id':'create','title':'Plan','scope':'topic','interval_seconds':60,'start_at':100,'next_at':100},token='s')
    assert tick(service,now=100,token='s')==[]
    assert list_schedules(service,token='s')[0]['active_run'] is None
    run=tick(service,now=160,token='s')[0]
    assert (run['from_at'],run['until_at'])==(100,160)
