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

