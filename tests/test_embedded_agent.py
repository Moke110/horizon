"""Exercise the real Pi SDK against a local OpenAI-compatible server."""
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hostrun import embedded_agent

ROOT = Path(__file__).resolve().parents[1]


def test_distribution_keeps_pi_catalog_but_excludes_private_dotfiles(tmp_path):
    from hostrun import distribution
    source = tmp_path / "source"
    asset = "agent/node_modules/@earendil-works/pi-ai/dist/providers/data/.manifest.json"
    target = source / asset
    target.parent.mkdir(parents=True)
    target.write_text('{}')
    (source / "agent/.env").write_text("SECRET=private")
    built = distribution.build(source=source, out=tmp_path / "dist", version="0.1.0", channel="prerelease",
                               compat={"os": "windows", "arch": "x86_64"})
    files = json.loads(Path(built["manifest"]).read_text())["files"]
    assert asset in {entry["path"] for entry in files}
    assert "agent/.env" not in {entry["path"] for entry in files}


def test_model_config_is_not_in_public_facts_or_argv(tmp_path):
    embedded_agent.configure(tmp_path, provider="deepseek", model="deepseek-chat", api_key="private-test-key")
    facts = embedded_agent.runtime(tmp_path, code=ROOT)
    assert facts["configured"]
    assert "private-test-key" not in json.dumps(facts)
    if facts["ok"]:
        assert "private-test-key" not in " ".join(embedded_agent.argv(tmp_path, "hello", kind="agent", code=ROOT))


@pytest.mark.skipif(not shutil.which("node") or not (ROOT / "agent/dist/main.js").exists(), reason="build the Pi agent first")
@pytest.mark.parametrize("kind", ["intent_judgement", "kb_read", "push", "push_tool", "push_private", "push_database", "push_snapshot", "push_hardlink"])
def test_real_pi_stream_and_tool_policy(tmp_path, kind):
    requests = []
    database = tmp_path / "horizon.sqlite3"
    database.write_text("preserved database")
    if kind == "push_hardlink":
        (tmp_path / "alias.json").hardlink_to(database)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [({"role": "assistant", "content": "Horizon ready"}, None), ({}, "stop")]
            if kind in {"push_tool", "push_private", "push_database", "push_snapshot", "push_hardlink"} and len(requests) == 1:
                target = tmp_path / ("agent/model.json" if kind == "push_private" else "outbox/result.json")
                if kind == "push_database": target = database
                if kind == "push_snapshot": target = tmp_path / "snapshots/state.json"
                if kind == "push_hardlink": target = tmp_path / "alias.json"
                chunks = [({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_test", "type": "function",
                    "function": {"name": "write_file", "arguments": json.dumps({"path": str(target), "content": '{"ok":true}'})}}]}, None), ({}, "tool_calls")]
            for delta, finish in chunks:
                event = {"id": "test", "object": "chat.completion.chunk", "created": 1, "model": "test-model",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        embedded_agent.configure(tmp_path, provider="horizon-test", model="test-model", api_key="private-test-key",
                                 base_url=f"http://127.0.0.1:{server.server_port}/v1")
        import os, sys
        proc = subprocess.run(embedded_agent.argv(tmp_path, "Say ready", kind=kind, code=ROOT), cwd=ROOT,
                              env={**os.environ, "HORIZON_PYTHON": sys.executable}, capture_output=True,
                              text=True, encoding="utf-8", timeout=45)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        events = [json.loads(line) for line in proc.stdout.splitlines()]
        assert next(e for e in events if e["type"] == "final")["text"] == "Horizon ready"
        assert "private-test-key" not in proc.stdout + proc.stderr
        names = {t["function"]["name"] for t in requests[0].get("tools", [])}
        expected = set() if kind == "intent_judgement" else {"read_file"} if kind == "kb_read" else {"horizon_cli", "read_file", "write_file", "delegate"}
        assert names == expected
        assert "Development responsibilities" not in json.dumps(requests)
        if kind == "push_tool":
            assert json.loads((tmp_path / "outbox/result.json").read_text()) == {"ok": True}
            assert len(requests) == 2
            assert any(e["type"] == "tool_result" and not e["isError"] for e in events)
        if kind in {"push_database", "push_snapshot", "push_hardlink"}:
            assert database.read_text() == "preserved database"
            assert not (tmp_path / "snapshots/state.json").exists()
            assert any(e["type"] == "tool_result" and e["isError"] for e in events)
        if kind == "push_private":
            assert json.loads(embedded_agent.config_path(tmp_path).read_text())["apiKey"] == "private-test-key"
            assert any(e["type"] == "tool_result" and e["isError"] for e in events)
    finally:
        server.shutdown()
        server.server_close()
        worker.join()

@pytest.mark.skipif(not shutil.which("node") or not (ROOT / "agent/dist/main.js").exists(), reason="build Pi first")
def test_pi_session_survives_process_restart_and_isolates_tasks(tmp_path):
    import os, sys, uuid
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for delta, finish in [({'role': 'assistant', 'content': 'Remembered blue comet session-'}, None), ({'content': 'private-key'}, None), ({}, 'stop')]:
                event = {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'test-model', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        embedded_agent.configure(tmp_path, provider='horizon-test', model='test-model', api_key='session-private-key', base_url=f'http://127.0.0.1:{server.server_port}/v1')
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        for identity, prompt in [(first, 'Remember blue comet'), (first, 'What did I say?'), (second, 'New conversation')]:
            history = tmp_path / 'history.json'
            history.write_text(json.dumps([{'role': 'You', 'text': 'Legacy violet moon'}, {'role': 'Horizon', 'text': 'Legacy response'}]))
            argv = embedded_agent.argv(tmp_path, prompt, kind='agent', code=ROOT) + ['--session-id', identity, '--history-file', str(history)]
            proc = subprocess.run(argv, cwd=ROOT, env={**os.environ, 'HORIZON_PYTHON': sys.executable}, capture_output=True, text=True, encoding='utf-8', timeout=45)
            assert proc.returncode == 0, proc.stdout + proc.stderr
            events = [json.loads(line) for line in proc.stdout.splitlines()]
            assert next(e for e in events if e['type'] == 'session')['sessionId'] == identity
            streamed = ''.join(e['text'] for e in events if e['type'] == 'text')
            assert 'session-private-key' not in streamed
            assert '[redacted]' in streamed
        assert 'Legacy violet moon' in json.dumps(requests[0]['messages'])
        assert json.dumps(requests[1]['messages']).count('Legacy violet moon') == 1
        assert 'Remember blue comet' in json.dumps(requests[1]['messages'])
        assert 'Remembered blue comet' in json.dumps(requests[1]['messages'])
        assert 'Remember blue comet' not in json.dumps(requests[2]['messages'])
        sessions = list((tmp_path / 'agent/pi/sessions').rglob('*.jsonl'))
        assert len(sessions) == 2
        assert all('session-private-key' not in p.read_text(encoding='utf-8') for p in sessions)
    finally:
        server.shutdown(); server.server_close(); thread.join()

@pytest.mark.skipif(not shutil.which('node') or not (ROOT / 'agent/dist/main.js').exists(), reason='build Pi first')
def test_pi_provider_error_does_not_persist_credentials(tmp_path):
    import os, sys, uuid
    key = 'provider-error-private-key'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(401)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': {'message': 'Rejected credential ' + key, 'type': 'authentication_error'}}).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        embedded_agent.configure(tmp_path, provider='horizon-test', model='test-model', api_key=key,
                                 base_url=f'http://127.0.0.1:{server.server_port}/v1')
        argv = embedded_agent.argv(tmp_path, 'Hello', kind='agent', code=ROOT, session_id=str(uuid.uuid4()))
        proc = subprocess.run(argv, cwd=ROOT, env={**os.environ, 'HORIZON_PYTHON': sys.executable},
                              capture_output=True, text=True, encoding='utf-8', timeout=45)
        assert proc.returncode != 0
        assert key not in proc.stdout + proc.stderr
        assert '[redacted]' in proc.stdout
        sessions = list((tmp_path / 'agent/pi/sessions').rglob('*.jsonl'))
        assert sessions
        persisted = '\n'.join(p.read_text(encoding='utf-8') for p in sessions)
        assert key not in persisted
        assert 'Rejected credential' in persisted
        assert '[redacted]' in persisted
    finally:
        server.shutdown(); server.server_close(); worker.join()

@pytest.mark.skipif(not shutil.which("node") or not (ROOT / "agent/dist/main.js").exists(), reason="build Pi first")
def test_child_role_exposes_only_bounded_tools(tmp_path):
    import os, sys, uuid
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            event = {'id':'test','object':'chat.completion.chunk','created':1,'model':'test-model','choices':[{'index':0,'delta':{'role':'assistant','content':'bounded result'},'finish_reason':'stop'}]}
            self.wfile.write(('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        embedded_agent.configure(tmp_path,provider='horizon-test',model='test-model',api_key='child-key',base_url=f'http://127.0.0.1:{server.server_port}/v1')
        for role in ('development_reader','development_summarizer'):
            args=embedded_agent.argv(tmp_path,'bounded input',kind='agent',code=ROOT,session_id=str(uuid.uuid4()))+['--child-role',role]
            proc=subprocess.run(args,cwd=ROOT,env={**os.environ,'HORIZON_PYTHON':sys.executable},capture_output=True,text=True,encoding='utf-8',timeout=45)
            assert proc.returncode == 0,proc.stdout+proc.stderr
            assert {t['function']['name'] for t in requests[-1]['tools']} == {'read_file','developer_context'}
            assert role in json.dumps(requests[-1]['messages'])
        assert len(list((tmp_path/'agent/pi/sessions').rglob('*.jsonl'))) == 2
    finally:
        server.shutdown();server.server_close();worker.join()


@pytest.mark.skipif(not shutil.which('node') or not (ROOT/'agent/dist/main.js').exists(), reason='build Pi first')
def test_recovered_turn_replays_stable_distinct_delegations_without_duplicate_context(tmp_path):
    import os,sys,uuid
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers()
            if len(requests)%3 == 1:
                calls=[{'index':i,'id':f'call_{i}','type':'function','function':{'name':'delegate','arguments':json.dumps({'role':'development_reader','input':'same bounded input'})}} for i in range(2)]
                chunks=[({'role':'assistant','tool_calls':calls},None),({},'tool_calls')]
            elif len(requests)%3 == 2:
                chunks=[({'role':'assistant','tool_calls':[{'index':0,'id':'call_0','type':'function','function':{'name':'delegate','arguments':json.dumps({'role':'development_reader','input':'different next response input'})}}]},None),({},'tool_calls')]
            else: chunks=[({'role':'assistant','content':'Both child artifacts received'},None),({},'stop')]
            for delta,finish in chunks:
                event={'id':'test','object':'chat.completion.chunk','created':1,'model':'test-model','choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
                self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        embedded_agent.configure(tmp_path,provider='horizon-test',model='test-model',api_key='recovery-key',base_url=f'http://127.0.0.1:{server.server_port}/v1')
        session_id=str(uuid.uuid4()); identities=[]
        args=embedded_agent.argv(tmp_path,'Recovery unique question',kind='agent',code=ROOT,session_id=session_id)+['--turn-id','stable-turn']
        for _ in range(2):
            proc=subprocess.Popen(args,cwd=ROOT,env={**os.environ,'HORIZON_PYTHON':sys.executable},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
            timer=threading.Timer(35,proc.kill);timer.start();ids=[]
            try:
                for line in proc.stdout:
                    event=json.loads(line)
                    if event['type']=='delegate_request':
                        ids.append(event['requestId'])
                        proc.stdin.write(json.dumps({'requestId':event['requestId'],'result':{'status':'succeeded','artifact_ref':'agent-child:'+str(len(ids)),'result':'read result'}})+'\n');proc.stdin.flush()
                assert proc.wait(timeout=5)==0,proc.stderr.read()
            finally:
                timer.cancel()
                if proc.poll() is None:proc.kill();proc.wait()
            identities.append(ids)
        assert len(identities[0])==3 and len(set(identities[0]))==3
        assert identities[1]==identities[0]
        assert len(requests)==6
        for index in (0,3):
            encoded=json.dumps(requests[index]['messages'])
            assert encoded.count('Recovery unique question')==1
            assert 'Both child artifacts received' not in encoded
            assert {t['function']['name'] for t in requests[index]['tools']}=={'read_file','delegate'}
    finally:
        server.shutdown();server.server_close();thread.join()
