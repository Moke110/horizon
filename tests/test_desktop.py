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
