import pytest

from hostrun.agent_service import AgentService


def test_desktop_home_is_stable_and_independent_of_install(monkeypatch, tmp_path):
    from hostrun.agent_service import desktop_home
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.delenv('HORIZON_DATA_HOME', raising=False)
    assert desktop_home() == tmp_path / 'local' / 'Horizon' / 'data'
    monkeypatch.setenv('HORIZON_DATA_HOME', str(tmp_path / 'custom'))
    assert desktop_home() == tmp_path / 'custom'


def test_authenticated_creation_survives_restart_and_duplicate(tmp_path):
    service = AgentService(tmp_path, token='secret')
    command = {'schema_version': 1, 'operation_id': 'create-1', 'title': 'Research'}
    with pytest.raises(PermissionError):
        service.create_task(command, token='wrong')
    assert service.list_tasks(token='secret') == []
    task = service.create_task(command, token='secret')
    reopened = AgentService(tmp_path, token='next-launch')
    assert reopened.create_task(command, token='next-launch') == task
    assert reopened.list_tasks(token='next-launch') == [task]
    assert len(reopened.events(token='next-launch')) == 1


def test_conflicting_operation_and_unknown_schema_do_not_change_tasks(tmp_path):
    service = AgentService(tmp_path, token='secret')
    command = {'schema_version': 1, 'operation_id': 'create-1', 'title': 'Research'}
    task = service.create_task(command, token='secret')
    with pytest.raises(ValueError, match='conflicts'):
        service.create_task({**command, 'title': 'Different'}, token='secret')
    with pytest.raises(ValueError, match='schema_version'):
        service.create_task({**command, 'schema_version': 2}, token='secret')
    assert service.list_tasks(token='secret') == [task]
    assert len(service.events(token='secret')) == 1


@pytest.mark.parametrize('phase,committed', [('before_commit', False), ('after_commit', True)])
def test_interrupted_creation_is_recoverable_without_duplicate_event(tmp_path, phase, committed):
    def interrupt(point):
        if point == phase:
            raise RuntimeError('interrupted')
    service = AgentService(tmp_path, token='secret', transaction_observer=interrupt)
    command = {'schema_version': 1, 'operation_id': 'retry', 'title': 'Research'}
    with pytest.raises(RuntimeError, match='interrupted'):
        service.create_task(command, token='secret')
    reopened = AgentService(tmp_path, token='new')
    assert len(reopened.list_tasks(token='new')) == int(committed)
    assert len(reopened.events(token='new')) == int(committed)
    result = reopened.create_task(command, token='new')
    assert reopened.list_tasks(token='new') == [result]
    assert len(reopened.events(token='new')) == 1


def test_fresh_desktop_home_can_inspect_embedded_runtime(tmp_path):
    from hostrun import embedded_agent
    code = tmp_path / 'installation'
    (code / 'runtime').mkdir(parents=True)
    (code / 'runtime/node.exe').touch()
    (code / 'agent/dist').mkdir(parents=True)
    (code / 'agent/dist/main.js').touch()
    facts = embedded_agent.runtime(tmp_path / 'fresh-data', code=code)
    assert facts['ok']
    assert facts['configured'] is False


def test_messages_persist_and_retry_once(tmp_path):
    service = AgentService(tmp_path, token='secret')
    task = service.create_task({'schema_version': 1, 'operation_id': 'create', 'title': 'New'}, token='secret')
    cmd = {'schema_version': 1, 'operation_id': 'message', 'task_id': task['task_id'], 'role': 'You', 'text': 'Hello', 'expected_version': 1}
    with pytest.raises(PermissionError):
        service.append_message(cmd, token='wrong')
    result = service.append_message(cmd, token='secret')
    reopened = AgentService(tmp_path, token='next')
    assert reopened.append_message(cmd, token='next') == result
    assert reopened.list_tasks(token='next')[0]['messages'] == [{'role': 'You', 'text': 'Hello'}]
    assert len(reopened.events(token='next')) == 2


def test_stale_message_version_does_not_change_committed_task(tmp_path):
    service = AgentService(tmp_path, token='secret')
    task = service.create_task({'schema_version': 1, 'operation_id': 'create', 'title': 'New'}, token='secret')
    command = {'schema_version': 1, 'operation_id': 'message', 'task_id': task['task_id'], 'role': 'You', 'text': 'Hello', 'expected_version': 0}
    with pytest.raises(ValueError, match='expected_version'):
        service.append_message(command, token='secret')
    assert service.list_tasks(token='secret') == [task]
    assert len(service.events(token='secret')) == 1

@pytest.mark.parametrize('method,command,code', [
    ('create_task', None, 'invalid_command'),
    ('append_message', [], 'invalid_command'),
    ('create_task', {'schema_version': True, 'operation_id': 'bad', 'title': 'Bad'}, 'unsupported_schema'),
    ('append_message', {'schema_version': 1, 'operation_id': 'bad', 'role': 'You', 'text': 'Bad', 'task_id': []}, 'invalid_command'),
])
def test_malformed_commands_return_stable_errors_without_writes(tmp_path, method, command, code):
    from hostrun.agent_service import CommandError
    service = AgentService(tmp_path, token='secret')
    with pytest.raises(CommandError) as error:
        getattr(service, method)(command, token='secret')
    assert error.value.code == code
    assert service.list_tasks(token='secret') == []
    assert service.events(token='secret') == []

def test_task_has_stable_distinct_session_identity(tmp_path):
    service = AgentService(tmp_path, token='secret')
    first = service.create_task({'schema_version': 1, 'operation_id': 'a', 'title': 'A'}, token='secret')
    second = service.create_task({'schema_version': 1, 'operation_id': 'b', 'title': 'B'}, token='secret')
    assert first['session_id'] != second['session_id']
    reopened = AgentService(tmp_path, token='next')
    assert {t['task_id']: t['session_id'] for t in reopened.list_tasks(token='next')} == {
        first['task_id']: first['session_id'], second['task_id']: second['session_id']}

def test_existing_tasks_gain_session_identity_without_losing_messages(tmp_path):
    import json
    import sqlite3
    service = AgentService(tmp_path, token='secret')
    task = service.create_task({'schema_version': 1, 'operation_id': 'old', 'title': 'Old'}, token='secret')
    task.pop('session_id')
    with sqlite3.connect(tmp_path / 'horizon.sqlite3') as db:
        db.execute('UPDATE agent_tasks SET result=?', (json.dumps(task),))
    first = AgentService(tmp_path, token='new').list_tasks(token='new')[0]
    second = AgentService(tmp_path, token='again').list_tasks(token='again')[0]
    assert first['session_id'] == second['session_id']
    assert first['messages'] == task['messages']

def test_child_results_are_isolated_replayable_and_persistent(tmp_path):
    service = AgentService(tmp_path, token='s')
    task = service.create_task({'schema_version':1,'operation_id':'p','title':'parent'},token='s')
    cmd = {'schema_version':1,'operation_id':'c1','task_id':task['task_id'],'role':'development_reader','input_text':'read contract','input_version':1}
    child = service.start_child(cmd,token='s')
    assert child['parent_session_id'] == task['session_id']
    assert child['session_id'] != task['session_id']
    assert service.start_child(cmd,token='s') == child
    with pytest.raises(ValueError):
        service.finish_child({'schema_version':1,'operation_id':'stale-finish','child_id':child['child_id'],'expected_version':0,'status':'succeeded'}, token='s')
    done = service.finish_child({'schema_version':1,'operation_id':'f1','expected_version':1,'child_id':child['child_id'],'status':'succeeded','result':'read complete'},token='s')
    other = service.start_child({**cmd,'operation_id':'c2','role':'development_summarizer'},token='s')
    service.finish_child({'schema_version':1,'operation_id':'f2','expected_version':1,'child_id':other['child_id'],'status':'failed','error':'provider failed'},token='s')
    reopened = AgentService(tmp_path,token='new')
    children = reopened.list_children(task['task_id'],token='new')
    assert children[0] == done
    assert children[1]['status'] == 'failed'
    assert children[0]['artifact_ref'] == child['artifact_ref']
    assert len(reopened.events(token='new')) == 5
    with pytest.raises(ValueError): service.start_child({**cmd,'operation_id':'bad','role':'keeper'},token='s')
    with pytest.raises(ValueError): service.start_child({**cmd,'operation_id':'stale','input_version':2},token='s')
    with pytest.raises(PermissionError): service.list_children(task['task_id'],token='wrong')

@pytest.mark.parametrize('phase,committed', [('before_commit',False),('after_commit',True)])
def test_child_commit_fault_replay_preserves_single_result(tmp_path,phase,committed):
    service=AgentService(tmp_path,token='s')
    task=service.create_task({'schema_version':1,'operation_id':'p','title':'parent'},token='s')
    def interrupt(point):
        if point==phase: raise RuntimeError('interrupted')
    faulty=AgentService(tmp_path,token='s',transaction_observer=interrupt)
    command={'schema_version':1,'operation_id':'c','task_id':task['task_id'],'role':'development_reader','input_text':'read','input_version':1}
    with pytest.raises(RuntimeError): faulty.start_child(command,token='s')
    reopened=AgentService(tmp_path,token='s')
    assert len(reopened.list_children(task['task_id'],token='s'))==int(committed)
    child=reopened.start_child(command,token='s')
    assert reopened.list_children(task['task_id'],token='s')==[child]
    assert len(reopened.events(token='s'))==2
    with pytest.raises(ValueError): reopened.start_child({**command,'input_text':'changed'},token='s')



def test_turn_recovery_commits_messages_once(tmp_path):
    service = AgentService(tmp_path, token='s')
    task = service.create_task({'schema_version':1,'operation_id':'create','title':'test'},token='s')
    command = {'schema_version':1,'operation_id':'send','task_id':task['task_id'],'expected_version':1,'text':'hello'}
    turn = service.begin_turn(command,token='s')
    assert service.begin_turn(command,token='s') == turn
    service = AgentService(tmp_path,token='s')
    assert service.recoverable_turns(token='s') == [turn]
    finish = {'schema_version':1,'operation_id':'finish','turn_id':turn['turn_id'],'expected_version':1,'status':'completed','result':'answer','error':''}
    done = service.commit_turn(finish,token='s')
    assert service.commit_turn(finish,token='s') == done
    assert not service.recoverable_turns(token='s')
    assert [m['text'] for m in service.list_tasks(token='s')[0]['messages']] == ['hello','answer']


@pytest.mark.parametrize('phase,committed',[('before_commit',False),('after_commit',True)])
def test_turn_commit_survives_process_exit(tmp_path,phase,committed):
    import subprocess,sys,json
    service=AgentService(tmp_path,token='s')
    task=service.create_task({'schema_version':1,'operation_id':'create','title':'test'},token='s')
    turn=service.begin_turn({'schema_version':1,'operation_id':'send','task_id':task['task_id'],'expected_version':1,'text':'hello'},token='s')
    command={'schema_version':1,'operation_id':'finish','turn_id':turn['turn_id'],'expected_version':1,'status':'completed','result':'answer'}
    script="import os,json,sys; from hostrun.agent_service import AgentService; s=AgentService(sys.argv[1],token='s',transaction_observer=lambda p: os._exit(71) if p==sys.argv[2] else None); s.commit_turn(json.loads(sys.argv[3]),token='s')"
    run=subprocess.run([sys.executable,'-X','utf8','-c',script,str(tmp_path),phase,json.dumps(command)],timeout=20)
    assert run.returncode==71
    restored=AgentService(tmp_path,token='s')
    assert (len(restored.list_tasks(token='s')[0]['messages'])==2) == committed
    restored.commit_turn(command,token='s')
    assert [m['text'] for m in restored.list_tasks(token='s')[0]['messages']]==['hello','answer']
    assert not restored.recoverable_turns(token='s')


def test_turn_attempt_snapshots_preserve_previous_versions(tmp_path):
    service=AgentService(tmp_path,token='s')
    task=service.create_task({'schema_version':1,'operation_id':'create','title':'test'},token='s')
    turn=service.begin_turn({'schema_version':1,'operation_id':'send','task_id':task['task_id'],'expected_version':1,'text':'hello'},token='s')
    for version in (1,2):
        service.attempt_turn({'schema_version':1,'operation_id':f'attempt-{version}','turn_id':turn['turn_id'],'snapshots':{'knowledge':{'provider':'development_empty','version':version}}},token='s')
    state=service.recoverable_turns(token='s')[0]
    assert [a['snapshots']['knowledge']['version'] for a in state['attempts']]==[1,2]
    assert state['input_version']==turn['input_version']


def test_turn_dto_and_unfinished_child_snapshot_refresh(tmp_path):
    service = AgentService(tmp_path, token='s')
    task = service.create_task({'schema_version':1,'operation_id':'p','title':'p'},token='s')
    begin = {'schema_version':1,'operation_id':'turn','task_id':task['task_id'],'expected_version':1,'text':'work'}
    turn = service.begin_turn(begin,token='s')
    assert turn['operation_id']=='turn' and turn['entity_id']=='turn'
    assert service.begin_turn(begin,token='s')==turn
    attempt = {'schema_version':1,'operation_id':'attempt','turn_id':'turn','snapshots':{'knowledge':{'version':2}}}
    observed=service.attempt_turn(attempt,token='s')
    assert observed['operation_id']=='attempt' and observed['entity_id']=='turn'
    assert service.attempt_turn(attempt,token='s')==observed
    child=service.start_child({'schema_version':1,'operation_id':'child','task_id':task['task_id'],'input_version':2,'role':'development_reader','input_text':'read','snapshots':{'knowledge':{'version':1}}},token='s')
    child=service.attempt_child({'schema_version':1,'operation_id':'retry','child_id':child['child_id'],'expected_version':1,'snapshots':{'knowledge':{'version':2}}},token='s')
    assert child['operation_id']=='retry'
    assert service.attempt_child({'schema_version':1,'operation_id':'retry','child_id':child['child_id'],'expected_version':1,'snapshots':{'knowledge':{'version':2}}},token='s') == child
    assert child['snapshots']['knowledge']['version']==2
    done=service.finish_child({'schema_version':1,'operation_id':'finish','child_id':child['child_id'],'expected_version':1,'status':'succeeded','result':'read'},token='s')
    with pytest.raises(ValueError):
        service.attempt_child({'schema_version':1,'operation_id':'late','child_id':child['child_id'],'expected_version':2,'snapshots':{'knowledge':{'version':3}}},token='s')
    assert service.list_children(task['task_id'],token='s')[0]['snapshots']==done['snapshots']
    commit={'schema_version':1,'operation_id':'commit','turn_id':'turn','expected_version':1,'status':'completed','result':'done'}
    result=service.commit_turn(commit,token='s')
    assert result['entity_id']=='turn' and result['operation_id']=='commit'
    assert service.commit_turn(commit,token='s')==result


def test_pause_resume_cancel_persist_and_reject_late_results(tmp_path):
    service=AgentService(tmp_path,token='s')
    task=service.create_task({'schema_version':1,'operation_id':'p','title':'p'},token='s')
    turn=service.begin_turn({'schema_version':1,'operation_id':'t','task_id':task['task_id'],'expected_version':1,'text':'work'},token='s')
    child=service.start_child({'schema_version':1,'operation_id':'c','task_id':task['task_id'],'input_version':2,'role':'development_reader','input_text':'read'},token='s')
    paused=service.transition_turn({'schema_version':1,'operation_id':'pause','turn_id':'t','expected_version':1,'action':'pause'},token='s')
    assert not AgentService(tmp_path,token='new').recoverable_turns(token='new')
    with pytest.raises(ValueError): service.finish_child({'schema_version':1,'operation_id':'late','child_id':child['child_id'],'expected_version':1,'status':'succeeded','result':'late'},token='s')
    resumed=service.transition_turn({'schema_version':1,'operation_id':'resume','turn_id':'t','expected_version':paused['version'],'action':'resume'},token='s')
    assert service.recoverable_turns(token='s')[0]['status']=='running'
    cancelled=service.transition_turn({'schema_version':1,'operation_id':'cancel','turn_id':'t','expected_version':resumed['version'],'action':'cancel'},token='s')
    assert cancelled['status']=='cancelled'
    assert service.list_children(task['task_id'],token='s')[0]['status']=='cancelled'
    with pytest.raises(ValueError): service.commit_turn({'schema_version':1,'operation_id':'late-turn','turn_id':'t','expected_version':resumed['version'],'status':'completed','result':'late'},token='s')
    assert len(service.list_tasks(token='s')[0]['messages'])==1
