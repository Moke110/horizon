import pytest
from hostrun.voyager_service import VoyagerService


def request(operation='create'):
    return {'schema_version':1,'operation_id':operation,'scope':{'text':'可解释人工智能','alternatives':['explainable artificial intelligence'],'must_include':['解释'],'exclude':['广告'],'sources':['arxiv']},'window':{'mode':'initial','basis':'discovered','from_at':100,'until_at':200,'overlap_seconds':10}}


def test_desktop_source_settings_persist_and_never_read_back_credentials(tmp_path, monkeypatch):
    from hostrun import embedded_agent
    from hostrun.desktop_bridge import voyager_command

    monkeypatch.delenv('OPENALEX_API_KEY', raising=False)
    monkeypatch.setenv('TAVILY_API_KEY', 'launch-only-key')
    service = VoyagerService(tmp_path, token='s')
    status_command = {'action': 'voyager_source_settings'}
    save_command = {'action': 'voyager_save_source_key', 'source': 'openalex',
                    'key': 'private-openalex-test-key'}
    assert voyager_command(service, status_command, token='s') == (
        'voyager_source_settings', {'openalex': 'missing', 'tavily': 'environment'})
    with pytest.raises(PermissionError):
        voyager_command(service, save_command, token='invalid')
    assert voyager_command(service, save_command, token='s') == (
        'voyager_source_settings', {'openalex': 'saved', 'tavily': 'environment'})

    embedded_agent.configure(tmp_path, provider='custom', model='model', api_key='model-key')
    reopened = VoyagerService(tmp_path, token='s')
    assert voyager_command(reopened, status_command, token='s')[1] == {
        'openalex': 'saved', 'tavily': 'environment'}
    assert embedded_agent.load_model_config(tmp_path) == {
        'provider': 'custom', 'model': 'model', 'apiKey': 'model-key', 'baseUrl': ''}
    assert voyager_command(reopened, {**save_command, 'key': None}, token='s')[1]['openalex'] == 'missing'


def test_saved_source_keys_reach_both_providers_without_exposing_them(tmp_path, monkeypatch, capsys):
    import io
    import json
    import urllib.request
    from hostrun import model_config
    from hostrun.desktop_bridge import voyager_command

    monkeypatch.setenv('OPENALEX_API_KEY', 'ignored-openalex-key')
    monkeypatch.setenv('TAVILY_API_KEY', 'ignored-tavily-key')
    service = VoyagerService(tmp_path, token='s')
    source_keys = {'openalex': 'saved-openalex-key', 'tavily': 'saved-tavily-key'}
    for source, key in source_keys.items():
        voyager_command(service, {'action': 'voyager_save_source_key', 'source': source, 'key': key}, token='s')

    calls = []

    def openalex_response(req, timeout):
        calls.append(('openalex', req.full_url, req.get_header('Authorization')))
        return io.BytesIO(b'{"rate_limit":{"daily_remaining_usd":0,"endpoint_costs_usd":{"search":0.001}}}')

    class EmptyTavilyAccount:
        def open(self, req, timeout):
            calls.append(('tavily', req.full_url, req.get_header('Authorization')))
            return io.BytesIO(b'{}')

    monkeypatch.setattr(urllib.request, 'urlopen', openalex_response)
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: EmptyTavilyAccount())
    command = request()
    command['scope'].update(sources=list(source_keys), alternatives=[])
    command['window']['mode'] = 'incremental'
    reopened = VoyagerService(tmp_path, token='s')
    _, planned = voyager_command(reopened, {'action': 'voyager_create', **command}, token='s')
    _, result = voyager_command(reopened, {'action': 'voyager_execute', 'schema_version': 1,
        'operation_id': 'run', 'request_id': planned['request_id'], 'expected_version': planned['version']}, token='s')
    assert sorted(calls) == [
        ('openalex', 'https://api.openalex.org/rate-limit', 'Bearer saved-openalex-key'),
        ('tavily', 'https://api.tavily.com/usage', 'Bearer saved-tavily-key'),
    ]
    assert {gap['reason'] for gap in result['gaps']} == {'budget_unknown'}
    assert not any(key in json.dumps(result) for key in source_keys.values())

    monkeypatch.setattr('sys.argv', ['model_config', str(tmp_path)])
    assert model_config.main() == 0
    assert json.loads(capsys.readouterr().out)['config'] == {}


def test_user_scope_constraints_queries_budgets_survive_replay_and_restart(tmp_path):
    service=VoyagerService(tmp_path,token='s')
    created=service.create(request(),token='s')
    assert service.create(request(),token='s')==created
    assert created['scope']['kind']=='temporary_explicit'
    assert created['scope']['text']=='可解释人工智能'
    assert created['scope']['must_include']==['解释'] and created['scope']['exclude']==['广告']
    assert {q['term'] for q in created['queries']}=={'可解释人工智能','explainable artificial intelligence'}
    assert {q['phase'] for q in created['queries']}=={'foundation','recent'}
    assert all(q['must_include']==['解释'] and q['exclude']==['广告'] and q['source']=='arxiv' for q in created['queries'])
    assert created['limits']=={'seconds':900,'queries':20,'candidates':100,'fulltexts':20}
    assert VoyagerService(tmp_path,token='s').get(created['request_id'],token='s')==created

class OnePage:
    def __init__(self):self.calls=[]
    def search(self,query):
        self.calls.append(query)
        return {'candidates':[{'source':'arxiv','source_id':'paper-1','title':'解释 research','abstract':'bounded','url':'https://arxiv.org/abs/1','published_at':20,'discovered_at':150}], 'exhausted':True,'next_cursor':None,'receipt':'local-page-1'}


def test_public_execution_preserves_old_newly_discovered_candidate_and_actual_query(tmp_path):
    source=OnePage();service=VoyagerService(tmp_path,token='s',providers={'arxiv':source},clock=lambda:150)
    command=request();command['scope']['alternatives']=[];command['window']['mode']='incremental'
    planned=service.create(command,token='s')
    result=service.execute({'schema_version':1,'operation_id':'run','request_id':planned['request_id'],'expected_version':1},token='s')
    assert result['status']=='complete'
    assert result['used']['queries']==1 and result['used']['candidates']==1
    assert result['candidates'][0]['published_at']==20 and result['candidates'][0]['discovered_at']==150
    assert source.calls[0]['window']['effective_from_at']==90
    assert source.calls[0]['must_include']==['解释'] and source.calls[0]['exclude']==['广告']
    assert result['queries'][0]['attempts'][0]['actual_query']==source.calls[0]
    assert result['successful_cursors']=={planned['queries'][0]['query_id']:200}
    assert service.execute({'schema_version':1,'operation_id':'run','request_id':planned['request_id'],'expected_version':1},token='s')==result
    assert len(source.calls)==1

def test_concurrent_continue_cannot_steal_a_live_provider_execution(tmp_path):
    import threading
    from hostrun.agent_service import CommandError
    entered=threading.Event();release=threading.Event()
    class Blocking(OnePage):
        def search(self,query):entered.set();release.wait(3);return super().search(query)
    source=Blocking();first=VoyagerService(tmp_path,token='s',providers={'arxiv':source},clock=lambda:150)
    command=request();command['scope']['alternatives']=[];command['window']['mode']='incremental';planned=first.create(command,token='s')
    result=[]
    thread=threading.Thread(target=lambda:result.append(first.execute({'schema_version':1,'operation_id':'run','request_id':planned['request_id'],'expected_version':1},token='s')));thread.start();assert entered.wait(2)
    try:
        second=VoyagerService(tmp_path,token='s',providers={'arxiv':source},clock=lambda:150);current=second.get(planned['request_id'],token='s')
        with pytest.raises(CommandError,match='already running'):
            second.execute({'schema_version':1,'operation_id':'takeover','request_id':planned['request_id'],'expected_version':current['version'],'continue':True,'acknowledge_unknown':True},token='s')
    finally:release.set();thread.join(5)
    assert len(source.calls)==1 and result[0]['status']=='complete'

@pytest.mark.parametrize('resource',['seconds','queries','candidates','fulltexts'])
def test_zero_budget_never_calls_provider_or_advances_success(tmp_path,resource):
    source=OnePage();service=VoyagerService(tmp_path,token='s',providers={'arxiv':source},clock=lambda:150)
    command=request();command['limits']={resource:0};planned=service.create(command,token='s')
    result=service.execute({'schema_version':1,'operation_id':'run','request_id':planned['request_id'],'expected_version':1},token='s')
    assert result['status']=='partial' and result['stop_reason']==resource+'_budget'
    assert source.calls==[] and result['successful_cursors']=={} and result['gaps']


def test_query_cap_continue_uses_new_allowance_and_keeps_old_costs_and_gaps(tmp_path):
    source=OnePage();service=VoyagerService(tmp_path,token='s',providers={'arxiv':source},clock=lambda:150)
    command=request();command['window']['mode']='incremental';command['limits']={'queries':1}
    planned=service.create(command,token='s')
    first=service.execute({'schema_version':1,'operation_id':'run','request_id':planned['request_id'],'expected_version':1},token='s')
    assert first['status']=='partial' and first['used']['queries']==1 and len(first['gaps'])==1
    reopened=VoyagerService(tmp_path,token='s',providers={'arxiv':source},clock=lambda:150)
    second=reopened.execute({'schema_version':1,'operation_id':'continue','request_id':planned['request_id'],'expected_version':first['version'],'continue':True},token='s')
    assert second['status']=='partial' and second['used']['queries']==2 and second['gaps']==[]
    third=reopened.execute({'schema_version':1,'operation_id':'confirm-complete','request_id':planned['request_id'],'expected_version':second['version'],'continue':True},token='s')
    assert third['status']=='complete' and len(source.calls)==2 and len(third['segments'])==3

def test_process_death_keeps_reservation_cost_and_requires_explicit_unknown_ack(tmp_path):
    import json,subprocess,sys
    from pathlib import Path
    from hostrun.agent_service import CommandError
    service=VoyagerService(tmp_path,token='s',clock=lambda:150)
    command=request();command['scope']['alternatives']=[];command['window']['mode']='incremental';planned=service.create(command,token='s')
    execution={'schema_version':1,'operation_id':'cut','request_id':planned['request_id'],'expected_version':1}
    program="""import os,sys,json
from hostrun.voyager_service import VoyagerService
class Crash:
 def search(self,query):os._exit(23)
VoyagerService(sys.argv[1],token='s',providers={'arxiv':Crash()},clock=lambda:150).execute(json.loads(sys.argv[2]),token='s')
"""
    child=subprocess.run([sys.executable,'-X','utf8','-c',program,str(tmp_path),json.dumps(execution)],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=10)
    assert child.returncode==23,child.stderr
    interrupted=service.get(planned['request_id'],token='s');assert interrupted['inflight'] and interrupted['used']['queries']==1
    source=OnePage();reopened=VoyagerService(tmp_path,token='s',providers={'arxiv':source},clock=lambda:150)
    resume={'schema_version':1,'operation_id':'resume','request_id':planned['request_id'],'expected_version':interrupted['version'],'continue':True}
    with pytest.raises(CommandError,match='unknown'):reopened.execute(resume,token='s')
    resume['acknowledge_unknown']=True;done=reopened.execute(resume,token='s')
    assert done['status']=='complete' and done['used']['queries']==2 and len(source.calls)==1
    assert done['queries'][0]['attempts'][0]['status']=='unknown'
    old=reopened.execute(execution,token='s')
    assert old['status']=='uncertain' and old['used']['queries']==1

def test_cancel_rejects_late_provider_result_and_preserves_no_success_cursor(tmp_path):
    import threading
    from hostrun.agent_service import CommandError
    entered=threading.Event();release=threading.Event()
    class Blocking(OnePage):
        def search(self,query):entered.set();assert release.wait(5);return super().search(query)
    service=VoyagerService(tmp_path,token='s',providers={'arxiv':Blocking()},clock=lambda:150)
    command=request();planned=service.create(command,token='s');results=[]
    thread=threading.Thread(target=lambda:results.append(service.execute({'schema_version':1,'operation_id':'run','request_id':planned['request_id'],'expected_version':1},token='s')));thread.start();assert entered.wait(2)
    try:
        current=service.get(planned['request_id'],token='s')
        cancelled=service.cancel({'schema_version':1,'operation_id':'cancel','request_id':planned['request_id'],'expected_version':current['version']},token='s')
        assert cancelled['status']=='cancelled'
    finally:release.set();thread.join(5)
    current=service.get(planned['request_id'],token='s')
    assert current['status']=='cancelled' and current['candidates']==[] and current['successful_cursors']=={}
    with pytest.raises(CommandError):service.execute({'schema_version':1,'operation_id':'resume','request_id':planned['request_id'],'expected_version':current['version'],'continue':True},token='s')

def test_fulltext_budget_is_reserved_before_work_and_receipt_does_not_refund_cost(tmp_path):
    service=VoyagerService(tmp_path,token='s',providers={'arxiv':OnePage()},clock=lambda:150)
    command=request();command['scope']['alternatives']=[];command['window']['mode']='incremental';command['limits']={'fulltexts':1}
    planned=service.create(command,token='s');done=service.execute({'schema_version':1,'operation_id':'run','request_id':planned['request_id'],'expected_version':1},token='s')
    reserve={'schema_version':1,'operation_id':'body-reserve','request_id':planned['request_id'],'expected_version':done['version'],'candidate_source':'arxiv','candidate_id':'paper-1'}
    held=service.reserve_fulltext(reserve,token='s')
    assert held['used']['fulltexts']==1 and held['status']=='partial' and held['stop_reason']=='fulltexts_budget'
    assert service.reserve_fulltext(reserve,token='s')==held
    receipt=service.record_fulltext({'schema_version':1,'operation_id':'body-receipt','request_id':planned['request_id'],'expected_version':held['version'],'work_id':'body-reserve','status':'succeeded','evidence_ref':'material:owned-snapshot'},token='s')
    assert receipt['used']['fulltexts']==1 and receipt['fulltext_work'][0]['evidence_ref']=='material:owned-snapshot'
    denied=service.reserve_fulltext({**reserve,'operation_id':'body-extra','expected_version':receipt['version']},token='s')
    assert denied['used']['fulltexts']==1 and len(denied['fulltext_work'])==1

def test_pending_fulltext_work_cannot_become_complete_on_continue(tmp_path):
    from hostrun.agent_service import CommandError
    service=VoyagerService(tmp_path,token='s',providers={'arxiv':OnePage()},clock=lambda:150)
    command=request();command['scope']['alternatives']=[];command['window']['mode']='incremental'
    plan=service.create(command,token='s');done=service.execute({'schema_version':1,'operation_id':'run','request_id':plan['request_id'],'expected_version':1},token='s')
    held=service.reserve_fulltext({'schema_version':1,'operation_id':'body','request_id':plan['request_id'],'expected_version':done['version'],'candidate_source':'arxiv','candidate_id':'paper-1'},token='s')
    assert held['status']!='complete'
    resume={'schema_version':1,'operation_id':'continue','request_id':plan['request_id'],'expected_version':held['version'],'continue':True}
    with pytest.raises(CommandError,match='unknown'):service.execute(resume,token='s')
    continued=service.execute({**resume,'acknowledge_unknown':True},token='s')
    assert continued['status']=='partial' and continued['fulltext_work'][0]['status']=='unknown'
    assert any(gap.get('work_id')=='body' for gap in continued['gaps'])

def test_oversized_expanded_plan_is_rejected_before_persistence(tmp_path):
    from hostrun.agent_service import CommandError
    service=VoyagerService(tmp_path,token='s');command=request()
    command['scope']['sources']=['source-'+str(i) for i in range(16)]
    command['scope']['alternatives']=['term-'+str(i) for i in range(16)]
    with pytest.raises(CommandError,match='expanded'):service.create(command,token='s')
    assert service.list(token='s')==[]

def test_initial_foundation_and_recent_provider_windows_are_distinct(tmp_path):
    source=OnePage();service=VoyagerService(tmp_path,token='s',providers={'arxiv':source},clock=lambda:150)
    command=request();command['scope']['alternatives']=[];command['window']['basis']='published'
    plan=service.create(command,token='s');done=service.execute({'schema_version':1,'operation_id':'run','request_id':plan['request_id'],'expected_version':1},token='s')
    assert [(q['phase'],q['window']['effective_from_at']) for q in source.calls]==[('foundation',0),('recent',100)]
    assert done['window']['from_at']==100 and len(done['candidates'])==1


@pytest.mark.parametrize('cap',['seconds','candidates'])
def test_exact_time_or_candidate_cap_retains_results_but_never_marks_full_coverage(tmp_path,cap):
    now=[150]
    class Boundary(OnePage):
        def search(self,query):
            result=super().search(query)
            if cap=='seconds':now[0]=151
            return result
    source=Boundary();service=VoyagerService(tmp_path,token='s',providers={'arxiv':source},clock=lambda:now[0])
    command=request();command['scope']['alternatives']=[];command['window']['mode']='incremental';command['limits']={cap:1}
    plan=service.create(command,token='s');done=service.execute({'schema_version':1,'operation_id':'run','request_id':plan['request_id'],'expected_version':1},token='s')
    assert done['status']=='partial' and done['stop_reason']==cap+'_budget'
    assert len(done['candidates'])==1 and done['used'][cap]==1
    if cap=='seconds':assert done['successful_cursors']=={} and done['gaps']

def test_unbounded_provider_metadata_does_not_enter_durable_results_or_complete(tmp_path):
    class Oversized(OnePage):
        def search(self,query):
            result=super().search(query);result['candidates'][0]['abstract']='x'*40000;return result
    service=VoyagerService(tmp_path,token='s',providers={'arxiv':Oversized()},clock=lambda:150)
    command=request();command['scope']['alternatives']=[];command['window']['mode']='incremental';planned=service.create(command,token='s')
    result=service.execute({'schema_version':1,'operation_id':'run','request_id':planned['request_id'],'expected_version':1},token='s')
    assert result['status']=='uncertain' and result['candidates']==[] and result['successful_cursors']=={}
    assert result['used']['queries']==1 and result['inflight']

def test_error_page_cannot_smuggle_unbounded_fields_into_a_record(tmp_path):
    from hostrun.agent_service import CommandError
    class Checking(OnePage):
        def search(self,query):
            current=service.get(plan['request_id'],token='s');lease=current['inflight']
            with pytest.raises(CommandError,match='error'):
                service.record({'schema_version':1,'operation_id':'bad-page','request_id':plan['request_id'],'expected_version':current['version'],'reservation_id':lease['reservation_id'],'page':{'error_code':'provider_failed','receipt':'x'*40000}},token='s')
            assert service.get(plan['request_id'],token='s')==current
            return super().search(query)
    service=VoyagerService(tmp_path,token='s',providers={'arxiv':Checking()},clock=lambda:150)
    command=request();command['scope']['alternatives']=[];command['window']['mode']='incremental';plan=service.create(command,token='s')
    assert service.execute({'schema_version':1,'operation_id':'run','request_id':plan['request_id'],'expected_version':1},token='s')['status']=='complete'


def test_late_fulltext_receipt_keeps_evidence_but_cannot_erase_time_gap(tmp_path):
    now=[150];service=VoyagerService(tmp_path,token='s',providers={'arxiv':OnePage()},clock=lambda:now[0])
    command=request();command['scope']['alternatives']=[];command['window']['mode']='incremental';command['limits']={'seconds':1};plan=service.create(command,token='s')
    done=service.execute({'schema_version':1,'operation_id':'run','request_id':plan['request_id'],'expected_version':1},token='s')
    held=service.reserve_fulltext({'schema_version':1,'operation_id':'body','request_id':plan['request_id'],'expected_version':done['version'],'candidate_source':'arxiv','candidate_id':'paper-1'},token='s')
    now[0]=151
    receipt=service.record_fulltext({'schema_version':1,'operation_id':'body-receipt','request_id':plan['request_id'],'expected_version':held['version'],'work_id':'body','status':'succeeded','evidence_ref':'material:owned'},token='s')
    assert receipt['status']=='partial' and receipt['stop_reason']=='seconds_budget' and receipt['used']['seconds']==1
    assert receipt['fulltext_work'][0]['evidence_ref']=='material:owned'

def test_public_progress_events_are_emitted_after_the_snapshot_is_committed(tmp_path):
    observed=[]
    def changed(event):observed.append((event['version'],service.get(event['request_id'],token='s')['version']))
    service=VoyagerService(tmp_path,token='s',providers={'arxiv':OnePage()},clock=lambda:150,on_change=changed)
    command=request();command['scope']['alternatives']=[];command['window']['mode']='incremental';plan=service.create(command,token='s')
    service.execute({'schema_version':1,'operation_id':'run','request_id':plan['request_id'],'expected_version':1},token='s')
    assert len(observed)>=3 and all(announced==readback for announced,readback in observed)

def test_desktop_voyager_commands_route_scope_and_unconfigured_result(tmp_path):
    from hostrun.desktop_bridge import voyager_command
    service=VoyagerService(tmp_path,token='s',providers={},clock=lambda:150)
    kind,planned=voyager_command(service,{'action':'voyager_create',**request()},token='s')
    assert kind=='voyager_view' and planned['scope']['kind']=='temporary_explicit'
    kind,result=voyager_command(service,{'action':'voyager_execute','schema_version':1,'operation_id':'run','request_id':planned['request_id'],'expected_version':planned['version']},token='s')
    assert result['status']=='blocked' and result['used']['queries']==0 and result['successful_cursors']=={}
    assert all(q['attempt_count']==0 for q in result['queries'])
    assert voyager_command(service,{'action':'voyager_open','request_id':planned['request_id']},token='s')[1]==result

def test_academic_result_remains_readable_when_another_source_fails(tmp_path):
    from hostrun.desktop_bridge import voyager_command

    class Limited:
        def search(self, query):
            return {'error_code': 'rate_limited'}

    class Academic(OnePage):
        def search(self, query):
            page = super().search(query)
            page['source_query'] = 'https://export.arxiv.org/api/query?start=0'
            return page

    providers = {'arxiv': Academic(), 'europepmc': Limited()}
    service = VoyagerService(tmp_path, token='s', providers=providers, clock=lambda: 150)
    command = request()
    command['scope'].update(sources=['europepmc', 'arxiv'], alternatives=[])
    command['window']['mode'] = 'incremental'
    planned = service.create(command, token='s')
    service.execute({'schema_version': 1, 'operation_id': 'run', 'request_id': planned['request_id'], 'expected_version': 1}, token='s')
    reopened = VoyagerService(tmp_path, token='s', providers={})
    _, view = voyager_command(reopened, {'action': 'voyager_open', 'request_id': planned['request_id']}, token='s')
    assert view['status'] == 'partial'
    assert view['gaps'][0]['reason'] == 'rate_limited'
    assert view['candidates'][0]['source_id'] == 'paper-1'
    assert view['queries'][1]['attempts'][0]['source_query'].endswith('?start=0')
