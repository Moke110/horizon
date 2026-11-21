import json
from uuid import uuid4

import pytest

from hostrun.agent_service import AgentService, CommandError
from hostrun.voyager_service import VoyagerService


def test_voyager_child_receives_only_bound_explicit_scope(tmp_path):
    from hostrun.voyager_child import child_evidence

    token = 'test'
    agent = AgentService(tmp_path, token=token)
    owner = agent.create_task(dict(schema_version=1, operation_id='task', title='Solar research'), token=token)
    voyager = VoyagerService(tmp_path, token=token, providers={})
    plan = voyager.create(dict(schema_version=1, operation_id='plan', task_id=owner['task_id'],
        scope=dict(text='Solar observations', sources=['crossref']),
        window=dict(mode='incremental', basis='discovered', from_at=0, until_at=20, overlap_seconds=0)), token=token)
    with pytest.raises(CommandError):
        child_evidence(voyager, plan['request_id'], str(uuid4()), token=token)
    with pytest.raises(CommandError):
        child_evidence(voyager, plan['request_id'], owner['task_id'], token=token)
    voyager.execute(dict(schema_version=1, operation_id='execute', request_id=plan['request_id'],
        expected_version=plan['version']), token=token)
    evidence = child_evidence(voyager, plan['request_id'], owner['task_id'], token=token)
    assert evidence['request_id'] == plan['request_id']
    assert evidence['scope_kind'] == 'temporary_explicit'
    assert evidence['gaps'][0]['source'] == 'crossref'
    assert 'test' not in json.dumps(evidence)
    with pytest.raises(CommandError) as cross_task:
        child_evidence(voyager, plan['request_id'], str(uuid4()), token=token)
    assert cross_task.value.code == 'invalid_task'
    with pytest.raises(CommandError) as invalid_request:
        child_evidence(voyager, 'Solar observations', owner['task_id'], token=token)
    assert invalid_request.value.code == 'invalid_request'


def test_parent_routes_voyager_to_private_context(tmp_path):
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler
    from pathlib import Path

    from hostrun import embedded_agent
    from test_desktop import worker
    from test_embedded_agent import local_model_server

    root = Path(__file__).resolve().parents[1]
    if not shutil.which('node') or not (root / 'agent/dist/main.js').exists():
        pytest.skip('built Pi required')
    token = 'test'
    app = worker(tmp_path)
    app.resolved['code'] = str(root)
    app.service = AgentService(tmp_path, token=token)
    app.service_token = token
    app.version_provider = lambda: {}
    task = app.service.create_task(dict(schema_version=1, operation_id='task', title='Solar research'), token=token)
    voyager = VoyagerService(tmp_path, token=token, providers={})
    plan = voyager.create(dict(schema_version=1, operation_id='plan', task_id=task['task_id'],
        scope=dict(text='Solar observations', sources=['crossref']),
        window=dict(mode='incremental', basis='discovered', from_at=0, until_at=20, overlap_seconds=0)), token=token)
    voyager.execute(dict(schema_version=1, operation_id='search', request_id=plan['request_id'],
        expected_version=plan['version']), token=token)
    app.task_id = task['task_id']
    app.turn = app.service.begin_turn(dict(schema_version=1, operation_id=str(uuid4()),
        task_id=task['task_id'], expected_version=task['version'], text='Delegate this retrieval to Voyager.'), token=token)
    app.input_version = app.turn['input_version']
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(data)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            name, arguments = {
                1: ('delegate', dict(role='voyager', input=plan['request_id'])),
                2: ('voyager_context', {}),
            }.get(len(requests), (None, None))
            content = ({'tool_calls': [{'index': 0, 'id': f'call-{name}', 'type': 'function',
                         'function': {'name': name, 'arguments': json.dumps(arguments)}}]} if name
                       else {'content': 'Retrieved evidence and coverage limitations.'})
            for delta, reason in [(dict(role='assistant', **content), None),
                                  ({}, 'tool_calls' if name else 'stop')]:
                event = dict(id='test', object='chat.completion.chunk', created=1, model='test-model',
                             choices=[dict(index=0, delta=delta, finish_reason=reason)])
                self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')

    server = local_model_server(Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        embedded_agent.configure(tmp_path, provider='horizon-test', model='test-model', api_key='fixture-key',
                                 base_url=f'http://127.0.0.1:{server.server_port}/v1')
        app.run_agent(app.turn['text'], task['session_id'])
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert len(requests) == 4
    assert [tool['function']['name'] for tool in requests[1]['tools']] == ['voyager_context']
    children = app.service.list_children(task['task_id'], token=token)
    assert len(children) == 1 and children[0]['role'] == 'voyager' and children[0]['status'] == 'succeeded'
    assert children[0]['session_id'] != task['session_id']
    assert json.loads(children[0]['result'])['evidence']['request_id'] == plan['request_id']
    assert app.service.list_turns(task['task_id'], token=token)[0]['status'] == 'completed'
