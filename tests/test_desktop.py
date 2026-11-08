import io
import queue
import threading
from pathlib import Path
import pytest
from hostrun.desktop_worker import DesktopWorker


def worker(tmp_path):
    app = DesktopWorker()
    app.home = tmp_path
    app.resolved = {'code': str(tmp_path)}
    app.events, app.answers = queue.Queue(), queue.Queue()
    app.closed = False
    app.cancelled = threading.Event()
    app.process = None
    return app


def test_console_answers_and_eof(tmp_path):
    app = worker(tmp_path)
    app.answers.put('yes')
    assert app.read('question') == 'yes'
    assert app.events.get() == ('prompt', 'question')
    app.answers.put(None)
    with pytest.raises(EOFError): app.read()
    app.closed = True
    with pytest.raises(EOFError): app.read()


def test_agent_stream_and_completion(tmp_path, monkeypatch):
    from hostrun import desktop_worker as module
    app = worker(tmp_path)
    monkeypatch.setattr(module.embedded_agent, 'argv', lambda *a, **kw: ['node', 'agent'])
    class Process:
        stdout = io.StringIO('invalid\n{"type":"text","text":"你好"}\n')
        stderr = io.StringIO('')
        def wait(self): return 0
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **kw: Process())
    app.run_agent('hello')
    assert app.events.get() == ('agent', {'type': 'text', 'text': '你好'})
    assert app.events.get() == ('done', None)
    assert app.process is None


def test_agent_failure_always_completes(tmp_path, monkeypatch):
    from hostrun import desktop_worker as module
    app = worker(tmp_path)
    def fail(*a, **kw): raise RuntimeError('missing model')
    monkeypatch.setattr(module.embedded_agent, 'argv', fail)
    app.run_agent('hello')
    assert app.events.get() == ('agent', {'type': 'error', 'message': 'missing model'})
    assert app.events.get() == ('done', None)


def test_cancel_stops_active_process(tmp_path):
    app = worker(tmp_path)
    class Process:
        stopped = False
        def poll(self): return None
        def terminate(self): self.stopped = True
    app.process = Process()
    app.stop()
    assert app.cancelled.is_set() and app.process.stopped


def test_desktop_boots_with_stable_home_without_legacy_install(tmp_path, monkeypatch):
    from hostrun import desktop
    import sys
    home = tmp_path / 'data'
    monkeypatch.setenv('HORIZON_DATA_HOME', str(home))
    monkeypatch.setattr(sys, 'argv', ['desktop', '--code', str(tmp_path / 'code'), '--archive', str(tmp_path / 'archive')])
    monkeypatch.setattr(desktop.desktop_processes, 'install', lambda: None)
    observed = []
    monkeypatch.setattr(desktop, 'serve', observed.append)
    def forbidden(*a, **kw):
        raise AssertionError('legacy install must not run')
    monkeypatch.setattr(desktop.console, 'install', forbidden)
    desktop.main()
    assert observed[0]['runtime'] == str(home)
    assert not (home / 'install').exists()


@pytest.mark.parametrize('supplied,authorized', [('wrong', False), ('launch-token', True)])
def test_bridge_socket_authentication_before_commands(supplied, authorized):
    import socket
    from hostrun.desktop_bridge import authenticate
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        with socket.create_connection(listener.getsockname()) as client:
            client.sendall((supplied + '\n{"action":"new"}\n').encode())
            connection, _ = listener.accept()
            with connection, connection.makefile('r', encoding='utf-8') as incoming:
                if authorized:
                    authenticate(incoming, 'launch-token')
                    assert incoming.readline() == '{"action":"new"}\n'
                else:
                    with pytest.raises(PermissionError):
                        authenticate(incoming, 'launch-token')


def test_authenticated_bridge_replayed_send_runs_model_once(tmp_path, monkeypatch):
    import json
    import socket
    from hostrun import desktop_bridge as bridge
    from hostrun.agent_service import AgentService
    monkeypatch.setenv('HORIZON_DATA_HOME', str(tmp_path))
    monkeypatch.setattr(bridge.desktop_runtime, 'executable', lambda code: Path('electron'))
    monkeypatch.setattr(bridge.embedded_agent, 'runtime', lambda *a, **k: {'model': 'test', 'provider': 'test', 'configured': True})
    monkeypatch.setattr(bridge.os, '_exit', lambda code: None)
    finished = threading.Event()
    failures = []
    calls = []
    query_started, query_release = threading.Event(), threading.Event()
    query_calls = []
    def discover(*a, **kw):
        query_calls.append(kw)
        query_started.set()
        assert query_release.wait(3)
    monkeypatch.setattr(bridge.embedded_agent, 'discover_and_configure', discover)
    def run_model(self, task, session_id, history):
        calls.append((task, session_id, history))
        self.events.put(('agent', {'type': 'text', 'text': 'Answer'}))
        self.events.put(('done', None))
    monkeypatch.setattr(DesktopWorker, 'run_agent', run_model)
    def client(env):
        try:
            with socket.create_connection(('127.0.0.1', int(env['HORIZON_DESKTOP_PORT'])), timeout=5) as sock:
                sock.sendall((env['HORIZON_DESKTOP_TOKEN'] + '\n').encode())
                with sock.makefile('r', encoding='utf-8') as incoming:
                    def send(command): sock.sendall((json.dumps(command) + '\n').encode())
                    def wait_state(predicate):
                        while True:
                            event = json.loads(incoming.readline())
                            if event['kind'] == 'state' and predicate(event['data']): return event['data']
                    wait_state(lambda state: True)
                    send({'action': 'configure', 'provider': 'test', 'key': 'secret'})
                    assert query_started.wait(2)
                    send({'action': 'configure', 'provider': 'test', 'key': 'second'})
                    send({'action': 'new', 'operation_id': 'create'})
                    during = wait_state(lambda state: bool(state['chats']))
                    assert during['configuring']
                    assert len(query_calls) == 1
                    query_release.set()
                    wait_state(lambda state: not state['configuring'])
                    for invalid in ({'action': 'send', 'text': 'Rejected'}, {'action': 'send', 'operation_id': 'stale', 'expected_version': 0, 'text': 'Rejected'}):
                        send(invalid)
                        refreshed = wait_state(lambda state: True)
                        assert refreshed['chats'][0]['messages'] == []
                        error = json.loads(incoming.readline())
                        assert error['kind'] == 'command_error'
                        assert error['data']['code'] in ('invalid_command', 'version_conflict')
                    command = {'action': 'send', 'operation_id': 'send', 'expected_version': 1, 'text': 'Hello'}
                    send(command)
                    wait_state(lambda state: not state['busy'] and len(state['chats'][0]['messages']) == 2)
                    send(command)
                    wait_state(lambda state: not state['busy'])
                    send({'action': 'send', 'operation_id': 'followup', 'expected_version': 3, 'text': 'Continue'})
                    wait_state(lambda state: not state['busy'] and len(state['chats'][0]['messages']) == 4)
                    original_id = refreshed['chats'][0]['task_id']
                    send({'action': 'new', 'operation_id': 'create-second'})
                    second = wait_state(lambda state: len(state['chats']) == 2)
                    assert second['chats'][second['current']]['task_id'] != original_id
                    send({'action': 'new', 'operation_id': 'create'})
                    replayed = wait_state(lambda state: True)
                    assert len(replayed['chats']) == 2
                    assert replayed['chats'][replayed['current']]['task_id'] == original_id
                    owner=AgentService(tmp_path,token='test-control')
                    active=owner.list_tasks(token='test-control')[-1]
                    owner.begin_turn({'schema_version':1,'operation_id':'exit-pending','task_id':active['task_id'],'expected_version':active['version'],'text':'pending work'},token='test-control')
                    send({'action':'exit'})
                    while True:
                        event=json.loads(incoming.readline())
                        if event['kind']=='exit_ready': break
                    assert owner.list_turns(active['task_id'],token='test-control')[-1]['status']=='paused'
                    assert not owner.recoverable_turns(token='test-control')
        except BaseException as exc:
            failures.append(exc)
        finally:
            finished.set()
    class Gui:
        returncode = 0
        def __init__(self, *args, env, **kwargs):
            threading.Thread(target=client, args=(env,), daemon=True).start()
        def poll(self): return 0 if finished.is_set() else None
    monkeypatch.setattr(bridge.desktop_processes, 'GuiProcess', Gui)
    bridge.serve({'code': str(tmp_path), 'runtime': str(tmp_path)})
    assert not failures
    assert len(calls) == 2
    assert calls[0][0] == 'Hello'
    assert calls[0][1] == calls[1][1]
    assert calls[1][0] == 'Continue'
    assert calls[0][2] == []
    assert calls[1][2] == [{'role': 'You', 'text': 'Hello'}, {'role': 'Horizon', 'text': 'Answer'}]
    reopened = AgentService(tmp_path, token='new-launch')
    assert sorted(len(task['messages']) for task in reopened.list_tasks(token='new-launch')) == [0, 5]
    assert len(reopened.events(token='new-launch')) == 8


def test_child_handoff_persists_success_and_failure(tmp_path, monkeypatch):
    from hostrun.agent_service import AgentService
    from hostrun import desktop_worker as module
    app = worker(tmp_path)
    app.service = AgentService(tmp_path, token='launch')
    app.service_token = 'launch'
    task = app.service.create_task({'schema_version':1,'operation_id':'parent','title':'parent'}, token='launch')
    app.task_id = task['task_id']
    app.input_version = task['version']
    monkeypatch.setattr(module.embedded_agent, 'argv', lambda *a, **kw: ['node', 'child'])
    class Process:
        stdout = io.StringIO('{"type":"final","text":"verified"}\n')
        stderr = io.StringIO('')
        def wait(self): return 0
        def poll(self): return 0
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **kw: Process())
    first = app.delegate({'requestId':'one','role':'development_reader','input':'Read contract'})
    assert first['status'] == 'succeeded' and first['result'] == 'verified'
    assert first['artifact_ref'].startswith('agent-child:')
    def fail(*a, **kw): raise RuntimeError('spawn failed')
    monkeypatch.setattr(module.subprocess, 'Popen', fail)
    second = app.delegate({'requestId':'two','role':'development_summarizer','input':'Summarize'})
    assert second['status'] == 'failed'
    assert app.delegate({'requestId':'one','role':'development_reader','input':'Read contract'}) == first
    restored = AgentService(tmp_path, token='new')
    assert len(restored.list_children(task['task_id'], token='new')) == 2


def test_child_empty_result_is_failed_and_retains_prior_results(tmp_path, monkeypatch):
    from hostrun.agent_service import AgentService
    from hostrun import desktop_worker as module
    app = worker(tmp_path)
    app.service = AgentService(tmp_path, token='launch'); app.service_token = 'launch'
    task = app.service.create_task({'schema_version':1,'operation_id':'parent','title':'parent'}, token='launch')
    app.task_id, app.input_version = task['task_id'], task['version']
    monkeypatch.setattr(module.embedded_agent, 'argv', lambda *a, **kw: ['node'])
    class Process:
        stdout = io.StringIO(''); stderr = io.StringIO('')
        def wait(self): return 0
        def poll(self): return 0
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **kw: Process())
    result = app.delegate({'requestId':'empty','role':'development_reader','input':'read'})
    assert result['status'] == 'failed'
    with pytest.raises(ValueError): app.delegate({'requestId':'bad','role':'keeper','input':'write'})
    assert len(app.service.list_children(app.task_id, token='launch')) == 1


def test_child_tool_ids_are_scoped_to_parent_turn(tmp_path, monkeypatch):
    from hostrun.agent_service import AgentService
    from hostrun import desktop_worker as module
    app = worker(tmp_path)
    app.service = AgentService(tmp_path, token='launch'); app.service_token = 'launch'
    task = app.service.create_task({'schema_version':1,'operation_id':'parent','title':'parent'}, token='launch')
    app.task_id, app.input_version = task['task_id'], task['version']
    monkeypatch.setattr(module.embedded_agent, 'argv', lambda *a, **kw: ['node'])
    class Process:
        def __init__(self):
            self.stdout = io.StringIO('{"type":"final","text":"result"}\n'); self.stderr = io.StringIO('')
        def wait(self): return 0
        def poll(self): return 0
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **kw: Process())
    event = {'requestId':'call_0','role':'development_reader','input':'read'}
    first = app.delegate(event)
    assert app.delegate(event) == first
    task = app.service.append_message({'schema_version':1,'operation_id':'next-turn','task_id':app.task_id,'expected_version':task['version'],'role':'You','text':'again'}, token='launch')
    app.input_version = task['version']
    second = app.delegate(event)
    assert second['child_id'] != first['child_id']
    assert app.delegate(event) == second
    assert len(app.service.list_children(app.task_id, token='launch')) == 2


def test_worker_always_reaps_process_when_turn_commit_fails(tmp_path, monkeypatch):
    import uuid
    from hostrun import desktop_worker as module
    app = worker(tmp_path)
    app.task_id = str(uuid.uuid4()); app.turn = {'turn_id':'t','version':1}
    app.service_token = 's'; app.version_provider = lambda: {}
    class Service:
        def attempt_turn(self,*a,**kw): pass
        def commit_turn(self,*a,**kw): raise OSError('database unavailable')
    app.service = Service()
    class BrokenStream:
        def __iter__(self): raise OSError('pipe failed')
    class Process:
        stdout = BrokenStream(); stderr = io.StringIO(''); stopped = False; waited = False
        def poll(self): return 0 if self.stopped else None
        def terminate(self): self.stopped = True
        def wait(self,timeout=None): self.waited = True; return 0
    process=Process()
    monkeypatch.setattr(module.embedded_agent,'argv',lambda *a,**kw:['node'])
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**kw:process)
    app.run_agent('task')
    assert process.stopped and process.waited and app.process is None
    assert any(kind=='done' for kind,data in list(app.events.queue))


def test_model_failure_details_survive_restart_and_manual_continue(tmp_path, monkeypatch):
    import uuid,json
    from hostrun import desktop_worker as module
    from hostrun.agent_service import AgentService
    app=worker(tmp_path);app.service=AgentService(tmp_path,token='s');app.service_token='s'
    task=app.service.create_task({'schema_version':1,'operation_id':'p','title':'p'},token='s')
    app.turn=app.service.begin_turn({'schema_version':1,'operation_id':'t','task_id':task['task_id'],'expected_version':1,'text':'work'},token='s')
    app.task_id=task['task_id'];app.input_version=2;app.version_provider=lambda:{}
    monkeypatch.setattr(module.embedded_agent,'argv',lambda *a,**kw:['node'])
    class Process:
        def __init__(self,event,code): self.stdout=io.StringIO(json.dumps(event)+'\n');self.stderr=io.StringIO('');self.code=code
        def wait(self,timeout=None):return self.code
        def poll(self):return self.code
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**kw:Process({'type':'error','category':'authentication','retryable':False,'attempts':1,'message':'Check API key'},1))
    app.run_agent('work',app.turn['session_id'])
    reopened=AgentService(tmp_path,token='new')
    failed=reopened.list_turns(token='new')[0]
    assert failed['status']=='recovery_required' and failed['failure']['category']=='authentication'
    assert len(reopened.list_tasks(token='new')[0]['messages'])==1
    app.turn=app.service.transition_turn({'schema_version':1,'operation_id':'resume','turn_id':'t','expected_version':failed['version'],'action':'resume'},token='s')
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**kw:Process({'type':'final','text':'Recovered'},0))
    app.run_agent('work',app.turn['session_id'])
    assert reopened.list_turns(token='new')[0]['status']=='completed'
    assert len(reopened.list_tasks(token='new')[0]['messages'])==2
