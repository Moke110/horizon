import pytest
from hostrun import embedded_agent

def test_discovery_failure_preserves_configuration(tmp_path):
    embedded_agent.configure(tmp_path, provider='deepseek', model='old', api_key='secret')
    before = embedded_agent.config_path(tmp_path).read_bytes()
    with pytest.raises(Exception):
        embedded_agent.discover_and_configure(tmp_path, provider='custom', api_key='new', base_url='http://example.com')
    assert embedded_agent.config_path(tmp_path).read_bytes() == before

def test_discovery_and_selection_round_trip(tmp_path):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == '/v1/models'
            assert self.headers['Authorization'] == 'Bearer secret'
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({'data':[{'id':'chat-b'}, {'id':'chat-a'}, {'id':'embedding-x'}, {'id':'echo-secret'}]}).encode())
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        models = embedded_agent.discover_and_configure(tmp_path, provider='custom', api_key='  secret  ', base_url=f'http://127.0.0.1:{server.server_port}/v1')
        assert models == ['chat-a','chat-b']
        embedded_agent.select_model(tmp_path, 'chat-b')
        assert embedded_agent.runtime(tmp_path)['model'] == 'chat-b'
        embedded_agent.refresh_models(tmp_path)
        facts = embedded_agent.runtime(tmp_path)
        assert facts['model'] == 'chat-b'
        assert 'secret' not in json.dumps(facts)
        with pytest.raises(Exception): embedded_agent.select_model(tmp_path, 'unknown')
        assert embedded_agent.runtime(tmp_path)['model'] == 'chat-b'
    finally:
        server.shutdown(); server.server_close(); thread.join()
