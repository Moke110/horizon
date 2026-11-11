import json
from pathlib import Path

import pytest

from hostrun.cleanup import prepare_instance, initialize_layout, cleanup_instance, preview


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    control = tmp_path / 'Horizon/test-instances'
    control.mkdir(parents=True)
    value = prepare_instance(control)
    initialize_layout(control, value['instance_id'])
    return value, control


def test_outbox_owned_update_keep_data_and_full_cleanup(instance, tmp_path):
    from hostrun.managed_artifact import write_outbox
    value, control = instance
    relative = 'PRIVATE-DIRECTORY/PRIVATE-FILENAME.txt'
    path = value['home'] / 'outbox' / relative
    write_outbox(value['home'], relative, 'PRIVATE-CONTENT')
    identity = path.stat().st_ino
    write_outbox(value['home'], relative, 'updated PRIVATE-CONTENT')
    assert path.stat().st_ino == identity
    assert path.read_text() == 'updated PRIVATE-CONTENT'
    plan = preview(control, value['instance_id'])
    assert plan['outbox_candidates']['file_count'] == 1
    assert plan['outbox_candidates']['directory_count'] == 2
    assert 'PRIVATE-' not in json.dumps(plan, default=str)
    kept = cleanup_instance(control, value['instance_id'], tmp_path / 'keep-report',
                            mode='keep-data', confirmation=value['instance_id'])
    assert kept['status'] == 'uninstalled'
    assert path.stat().st_ino == identity
    cleaned = cleanup_instance(control, value['instance_id'], tmp_path / 'full-report',
                               confirmation=value['instance_id'])
    assert cleaned['status'] == 'cleaned'
    assert not path.exists()
    assert 'PRIVATE-' not in json.dumps(cleaned)
    assert 'PRIVATE-' not in (Path(cleaned['report']) / 'cleanup.json').read_text()


def test_real_pi_write_then_read_has_cleanup_receipt(instance, tmp_path):
    import os
    import subprocess
    import sys
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from hostrun import embedded_agent
    value, control = instance
    target = value['home'] / 'outbox/PRIVATE-PI.txt'
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            if len(requests) <= 2:
                name = 'write_file' if len(requests) == 1 else 'read_file'
                arguments = {'path': str(target)}
                if name == 'write_file': arguments['content'] = 'PRIVATE-BODY'
                delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': name, 'type': 'function',
                         'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
                finish = 'tool_calls'
            else:
                delta, finish = {'role': 'assistant', 'content': 'Done'}, 'stop'
            for change, reason in [(delta, None), ({}, finish)]:
                event = {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'test-model',
                         'choices': [{'index': 0, 'delta': change, 'finish_reason': reason}]}
                self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        embedded_agent.configure(value['home'], provider='horizon-test', model='test-model', api_key='PRIVATE-KEY',
                                 base_url=f'http://127.0.0.1:{server.server_port}/v1')
        process = subprocess.run(embedded_agent.argv(value['home'], 'Write then read', kind='push', code=Path.cwd()),
                                 env={**os.environ, 'HORIZON_PYTHON': sys.executable}, capture_output=True,
                                 text=True, encoding='utf-8', timeout=45)
        assert process.returncode == 0, process.stderr
        assert 'PRIVATE-KEY' not in process.stdout + process.stderr
        assert len(requests) == 3
        assert 'PRIVATE-BODY' in json.dumps(requests[-1]['messages'])
        assert target.read_text() == 'PRIVATE-BODY'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    result = cleanup_instance(control, value['instance_id'], tmp_path / 'pi-report', confirmation=value['instance_id'])
    assert result['status'] == 'cleaned'
    assert 'PRIVATE-' not in json.dumps(result)


@pytest.mark.parametrize('phase', ['pre_receipt', 'post_receipt', 'partial_body', 'partial_update', 'replacement'])
def test_outbox_hard_exit_preserves_unknown_and_cleans_receipted_partial(instance, tmp_path, phase):
    import os
    import subprocess
    import sys
    from hostrun.managed_artifact import write_outbox
    value, control = instance
    write_outbox(value['home'], 'seed.txt', 'seed')
    target = value['home'] / 'outbox/PRIVATE-INTERRUPTED.txt'
    if phase == 'partial_update': write_outbox(value['home'], target.name, 'PRIVATE-OLD-BODY')
    script = r'''
import ctypes,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from native_registration import intercept_registration_publish,handle_path
from hostrun.managed_artifact import write_outbox
home=Path(sys.argv[1]); phase=sys.argv[2]; target=home/'outbox/PRIVATE-INTERRUPTED.txt'
if phase in ('pre_receipt','post_receipt','replacement'):
    def stop(source,destination):
        if target.exists(): os._exit(77)
    intercept_registration_publish(stop,after=phase!='pre_receipt')
else:
    native=ctypes.WinDLL; api=native('kernel32',use_last_error=True)
    def write(handle,buffer,size,count,overlapped):
        api.WriteFile.argtypes=write.argtypes
        if handle_path(api,handle)==target:
            assert api.WriteFile(handle,buffer,3,count,overlapped)
            os._exit(77)
        return api.WriteFile(handle,buffer,size,count,overlapped)
    class Kernel:
        def __getattr__(self,name): return write if name=='WriteFile' else getattr(api,name)
    ctypes.WinDLL=lambda name,**kwargs: Kernel() if name=='kernel32' else native(name,**kwargs)
write_outbox(home,target.name,'PRIVATE-NEW-BODY')
'''
    process = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script, str(value['home']), phase],
                             env=os.environ, capture_output=True, text=True, timeout=20)
    assert process.returncode == 77, process.stderr
    assert target.exists()
    if phase == 'replacement':
        target.rename(tmp_path / 'retained-original')
        target.write_text('PRIVATE-REPLACEMENT')
    result = cleanup_instance(control, value['instance_id'], tmp_path / 'exit-report', confirmation=value['instance_id'])
    unknown = phase in ('pre_receipt', 'replacement')
    assert result['status'] == ('partial' if unknown else 'cleaned')
    assert target.exists() == unknown
    if phase == 'replacement': assert target.read_text() == 'PRIVATE-REPLACEMENT'
    assert 'PRIVATE-' not in json.dumps(result)


@pytest.mark.parametrize('kind', ['unknown_directory', 'unknown_file', 'replacement', 'hardlink', 'junction'])
def test_outbox_refuses_unowned_or_redirected_content(instance, tmp_path, kind):
    import os
    from hostrun.managed_artifact import write_outbox
    value, control = instance
    outbox = value['home'] / 'outbox'
    target = outbox / 'PRIVATE-NAME.txt'
    external = tmp_path / 'external'
    external.mkdir()
    if kind == 'unknown_directory':
        outbox.mkdir()
    elif kind == 'junction':
        import subprocess
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(outbox), str(external)], check=True, capture_output=True)
    else:
        write_outbox(value['home'], 'seed.txt', 'seed')
        if kind in ('replacement', 'hardlink'):
            write_outbox(value['home'], target.name, 'PRIVATE-ORIGINAL')
            if kind == 'replacement':
                target.rename(external / 'original')
                target.write_text('PRIVATE-REPLACEMENT')
            else:
                os.link(target, external / 'alias')
        else:
            target.write_text('PRIVATE-UNKNOWN')
    before = target.read_bytes() if target.exists() else None
    with pytest.raises((OSError, ValueError)):
        write_outbox(value['home'], target.name, 'PRIVATE-NEW')
    assert (target.read_bytes() if target.exists() else None) == before
    assert not (external / target.name).exists()
    result = cleanup_instance(control, value['instance_id'], tmp_path / 'refusal-report', confirmation=value['instance_id'])
    assert result['status'] == ('cleaned' if kind == 'hardlink' else 'partial')
    if kind == 'replacement': assert target.read_text() == 'PRIVATE-REPLACEMENT'
    if kind == 'hardlink': assert (external / 'alias').read_text() == 'PRIVATE-ORIGINAL'
    assert 'PRIVATE-' not in json.dumps(result)


@pytest.mark.parametrize('boundary', ['directory', 'file'])
def test_outbox_failed_receipt_never_writes_body(instance, tmp_path, monkeypatch, boundary):
    from hostrun.managed_artifact import write_outbox
    from native_registration import intercept_registration_publish
    value, control = instance
    if boundary == 'file': write_outbox(value['home'], 'seed.txt', 'seed')
    def refuse(source, target):
        raise OSError(5, 'PRIVATE-OS-ERROR')
    with monkeypatch.context() as faults:
        intercept_registration_publish(refuse, faults)
        with pytest.raises(OSError):
            write_outbox(value['home'], 'PRIVATE-NAME.txt', 'PRIVATE-BODY')
    assert not (value['home'] / 'outbox/PRIVATE-NAME.txt').exists()
    result = cleanup_instance(control, value['instance_id'], tmp_path / 'receipt-report', confirmation=value['instance_id'])
    assert result['status'] == 'cleaned'
    assert 'PRIVATE-' not in json.dumps(result)
