from hostrun.cleanup import prepare_instance
from pathlib import Path
import pytest


@pytest.fixture
def tmp_path(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    control = tmp_path / 'Horizon' / 'test-instances'
    control.mkdir(parents=True)
    return control


def test_new_test_instance_never_adopts_existing_installation(tmp_path):
    existing = tmp_path / 'existing'
    existing.mkdir()
    (existing / 'user.txt').write_text('preserve')
    first = prepare_instance(tmp_path)
    second = prepare_instance(tmp_path)
    assert first['root'] != second['root']
    assert (existing / 'user.txt').read_text() == 'preserve'
    assert first['status'] == 'prepared'
    assert first['code'] == first['root'] / 'app'
    assert first['home'] == first['root'] / 'data'


@pytest.mark.parametrize('root', [Path.home(), Path(Path.cwd().anchor)])
def test_refuses_shared_control_locations(root):
    with pytest.raises(ValueError, match='control root'):
        prepare_instance(root)


def test_preparation_refuses_report_or_suite_overlap(tmp_path):
    suite = tmp_path / 'suite'
    suite.mkdir()
    with pytest.raises(ValueError, match='overlap'):
        prepare_instance(tmp_path, protected_roots=[suite])


def test_preview_uses_fixed_instance_paths_not_registration_paths(tmp_path):
    import json
    from hostrun.cleanup import preview
    instance = prepare_instance(tmp_path)
    record = json.loads(instance['registration'].read_text())
    record['root'] = str(tmp_path / 'unrelated')
    instance['registration'].write_text(json.dumps(record))
    with pytest.raises(ValueError, match='registration'):
        preview(tmp_path, instance['instance_id'])


def test_preview_lists_preserved_data_and_requires_confirmation(tmp_path):
    from hostrun.cleanup import preview
    instance = prepare_instance(tmp_path)
    result = preview(tmp_path, instance['instance_id'], mode='keep-data')
    assert result['status'] == 'confirmation_required'
    assert str(instance['code']) in result['delete']
    assert str(instance['home']) in result['preserve']
    assert str(instance['registration']) in result['preserve']


def test_cleanup_lock_blocks_upgrade_before_recreating_deleted_home(tmp_path):
    from common import file_lock
    from hostrun.desktop_upgrade import upgrade
    instance = prepare_instance(tmp_path)
    with file_lock(tmp_path / (instance['instance_id'] + '.lock')):
        result = upgrade(instance['code'], instance['home'],
                         from_source='missing.zip', sha256='0' * 64)
    assert result == {'status': 'blocked', 'reason': 'instance_busy'}
    assert not instance['home'].exists()


def test_cleanup_lock_blocks_desktop_before_recreating_deleted_home(tmp_path):
    from common import file_lock
    import os
    import subprocess
    import sys
    instance = prepare_instance(tmp_path)
    with file_lock(tmp_path / (instance['instance_id'] + '.lock')):
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', '-c',
             'from hostrun.desktop import main; main()',
             '--code', str(instance['code']), '--archive', str(instance['archive'])],
            env={**os.environ, 'HORIZON_DATA_HOME': str(instance['home'])},
            capture_output=True, timeout=15)
    assert b'instance_busy' in result.stderr
    assert not instance['home'].exists()


@pytest.mark.parametrize('spelling', ['plain', 'parent', 'extended'])
def test_real_desktop_module_does_not_recreate_home_when_cleanup_busy(tmp_path, spelling):
    from common import file_lock
    import os
    import subprocess
    import sys
    instance = prepare_instance(tmp_path)
    home = str(instance['home'])
    if spelling == 'parent':
        home = str(instance['root'] / '..' / instance['instance_id'] / 'data')
    elif spelling == 'extended':
        home = '\\\\?\\' + home
    with file_lock(tmp_path / (instance['instance_id'] + '.lock')):
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', '-m', 'hostrun.desktop',
             '--code', str(instance['code']), '--archive', str(instance['archive'])],
            env={**os.environ, 'HORIZON_DATA_HOME': home},
            capture_output=True, timeout=3)
    assert result.returncode != 0
    assert not instance['home'].exists()


def test_missing_registration_cannot_bypass_upgrade_cleanup_lock(tmp_path):
    from hostrun.desktop_upgrade import upgrade
    instance = prepare_instance(tmp_path)
    instance['registration'].unlink()
    with pytest.raises(ValueError, match='registration'):
        upgrade(instance['code'], instance['home'],
                from_source='missing.zip', sha256='0' * 64)
    assert not instance['home'].exists()


def test_managed_upgrade_inventory_guard_applies_with_shared_desktop_lease(tmp_path):
    from hostrun.cleanup import instance_lock
    from hostrun.desktop_upgrade import upgrade
    from common import file_lock
    instance = prepare_instance(tmp_path)
    with instance_lock(instance['home']), file_lock(instance['home'] / 'desktop.lock'):
        result = upgrade(instance['code'], instance['home'],
                         from_source='missing.zip', sha256='0' * 64)
        assert result['reason'] == 'managed_upgrade_inventory_pending'


def test_existing_uuid_shaped_home_is_not_claimed(tmp_path):
    from hostrun.desktop_upgrade import upgrade
    home = tmp_path.parent / 'ordinary' / '0123456789abcdef0123456789abcdef' / 'data'
    result = upgrade(tmp_path / 'application', home,
                     from_source='missing.zip', sha256='0' * 64)
    assert result['reason'] == 'source_not_found'


def test_replaced_instance_directory_is_not_owned(tmp_path):
    from hostrun.cleanup import preview
    instance = prepare_instance(tmp_path)
    instance['root'].rename(tmp_path / 'original-preserved')
    instance['root'].mkdir()
    with pytest.raises(ValueError, match='identity'):
        preview(tmp_path, instance['instance_id'])


def test_plain_forged_registration_is_not_authority(tmp_path):
    import json
    from hostrun.cleanup import preview
    instance = prepare_instance(tmp_path)
    instance['registration'].write_text(json.dumps({
        'schema_version': 1, 'instance_id': instance['instance_id'], 'state': 'prepared'}))
    with pytest.raises(ValueError, match='registration'):
        preview(tmp_path, instance['instance_id'])


def test_independent_report_handles_missing_application_without_copying_secrets(tmp_path):
    from hostrun.cleanup import collect_instance_report
    instance = prepare_instance(tmp_path)
    instance['home'].mkdir()
    (instance['home'] / 'model-config.json').write_text('SECRET-CREDENTIAL-MARKER')
    report = collect_instance_report(tmp_path, instance['instance_id'], tmp_path.parent / 'report')
    text = '\n'.join(p.read_text(encoding='utf-8') for p in report.iterdir() if p.is_file())
    assert 'SECRET-CREDENTIAL-MARKER' not in text
    assert (report / 'summary.md').exists()
    assert not instance['code'].exists()


def test_report_inside_instance_is_rejected_before_writing(tmp_path):
    from hostrun.cleanup import collect_instance_report
    instance = prepare_instance(tmp_path)
    with pytest.raises(ValueError, match='report'):
        collect_instance_report(tmp_path, instance['instance_id'], instance['root'] / 'report')
    assert not (instance['root'] / 'report').exists()


def test_layout_creation_records_children_and_refuses_preexisting_content(tmp_path):
    from hostrun.cleanup import initialize_layout, preview
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    assert instance['home'].is_dir()
    instance['home'].rename(instance['root'] / 'old-data')
    instance['home'].mkdir()
    with pytest.raises(ValueError, match='identity'):
        preview(tmp_path, instance['instance_id'])


def test_layout_never_adopts_existing_data(tmp_path):
    from hostrun.cleanup import initialize_layout
    instance = prepare_instance(tmp_path)
    instance['home'].mkdir()
    (instance['home'] / 'existing.txt').write_text('keep')
    with pytest.raises(ValueError, match='existing'):
        initialize_layout(tmp_path, instance['instance_id'])
    assert (instance['home'] / 'existing.txt').read_text() == 'keep'


def test_confirmed_cleanup_preserves_report_and_unknown_content(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='test', api_key='SECRET-MARKER')
    (instance['root'] / 'unknown.txt').write_text('preserve')
    report_root = tmp_path.parent / 'report'
    result = cleanup_instance(tmp_path, instance['instance_id'], report_root,
                              confirmation=instance['instance_id'])
    assert result['status'] == 'partial'
    assert not (instance['home'] / 'agent' / 'model.json').exists()
    assert (instance['root'] / 'unknown.txt').read_text() == 'preserve'
    assert Path(result['report']).is_dir()
    assert instance['registration'].exists()


@pytest.mark.parametrize('name', ['model-config.json', 'horizon.sqlite3', 'horizon.sqlite3-wal',
                                 'horizon.sqlite3-shm', 'version.json'])
@pytest.mark.parametrize('replaced', [False, True])
def test_root_filename_alone_never_authorizes_cleanup(tmp_path, name, replaced):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    path = instance['home'] / name
    if replaced:
        path.write_text('ORIGINAL')
        path.rename(path.with_name(name + '.retained'))
    path.write_text('USER CONTENT')
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'partial'
    assert path.read_text() == 'USER CONTENT'
    assert 'data/' + name not in result['removed']


def test_managed_diagnostic_events_are_enrolled_before_append(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.diagnostics import record_event
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    record_event(instance['home'], 'desktop_started', pid=123)
    assert (instance['home'] / 'diagnostics' / 'events.jsonl').is_file()
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'
    assert 'desktop_started' in (Path(result['report']) / 'events.jsonl').read_text()


def test_desktop_report_is_retained_outside_managed_instance(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.diagnostics import record_event, export_report
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    record_event(instance['home'], 'desktop_started', pid=123)
    report = export_report(instance['home'], code=instance['code'])
    assert report.parent == tmp_path.parent / 'test-reports' / instance['instance_id']
    assert 'desktop_started' in (report / 'events.jsonl').read_text()
    assert not (instance['home'] / 'diagnostics' / 'reports').exists()
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'cleanup-reports',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'
    assert (report / 'summary.md').is_file()


def test_previous_internal_reports_are_not_adopted_or_removed(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.diagnostics import record_event, export_report
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    record_event(instance['home'], 'desktop_started')
    old = instance['home'] / 'diagnostics' / 'reports'
    old.mkdir()
    (old / 'user-report.txt').write_text('PRESERVE')
    report = export_report(instance['home'])
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'cleanup-reports',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'partial'
    assert (old / 'user-report.txt').read_text() == 'PRESERVE'
    assert (report / 'summary.md').exists()


@pytest.mark.parametrize('level', ['root', 'instance'])
def test_desktop_report_refuses_redirected_retained_directory(tmp_path, level):
    import _winapi
    from hostrun.cleanup import initialize_layout
    from hostrun.diagnostics import export_report
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    external = tmp_path.parent / 'user-documents'
    external.mkdir()
    (external / 'keep.txt').write_text('USER DOCUMENT')
    root = tmp_path.parent / 'test-reports'
    link = root
    if level == 'instance':
        root.mkdir()
        link = root / instance['instance_id']
    _winapi.CreateJunction(str(external), str(link))
    with pytest.raises(ValueError, match='Reparse'):
        export_report(instance['home'])
    assert [path.name for path in external.iterdir()] == ['keep.txt']


def test_ordinary_desktop_report_default_location_is_unchanged(tmp_path):
    from hostrun.diagnostics import export_report
    home = tmp_path.parent / 'ordinary-home'
    report = export_report(home)
    assert report.parent == home / 'diagnostics' / 'reports'
    assert (report / 'summary.md').exists()


@pytest.mark.parametrize('entry', ['desktop', 'independent', 'cleanup'])
def test_managed_report_final_directory_cannot_be_replaced_before_write(tmp_path, monkeypatch, entry):
    import _winapi
    from hostrun.cleanup import initialize_layout
    from hostrun.diagnostics import export_report
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    external = tmp_path.parent / 'external'
    external.mkdir()
    original = Path.open
    attempted = []
    def open_file(path, *args, **kwargs):
        target = 'cleanup.json' if entry == 'cleanup' else 'events.jsonl'
        if path.name == target and path.parent.name.startswith('horizon-report-') and not attempted:
            folder = path.parent
            try:
                folder.rename(folder.with_name(folder.name + '.moved'))
            except PermissionError:
                attempted.append('blocked')
            else:
                attempted.append('replaced')
                _winapi.CreateJunction(str(external), str(folder))
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', open_file)
        if entry == 'desktop':
            report = export_report(instance['home'])
        elif entry == 'independent':
            from hostrun.cleanup import collect_instance_report
            report = collect_instance_report(tmp_path, instance['instance_id'], tmp_path.parent / 'reports')
        else:
            from hostrun.cleanup import cleanup_instance
            result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'reports',
                                      confirmation=instance['instance_id'])
            report = Path(result['report'])
    assert attempted == ['blocked']
    assert not list(external.iterdir())
    assert (report / 'summary.md').is_file()


def test_managed_diagnostic_rotation_keeps_bounded_owned_files(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.diagnostics import record_event
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    for number in range(1700):
        record_event(instance['home'], 'error', code='x' * 100, pid=number)
    folder = instance['home'] / 'diagnostics'
    assert (folder / 'events.jsonl').stat().st_size <= 263168
    assert (folder / 'events.previous.jsonl').is_file()
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


@pytest.mark.parametrize('name', ['events.jsonl', 'events.previous.jsonl'])
def test_diagnostic_writer_and_cleanup_preserve_foreign_replacements(tmp_path, name):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.diagnostics import record_event
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    record_event(instance['home'], 'desktop_started', pid=123)
    path = instance['home'] / 'diagnostics' / name
    path.rename(path.with_name(name + '.retained'))
    path.write_text('USER FILE')
    record_event(instance['home'], 'desktop_exit', pid=123)
    assert path.read_text() == 'USER FILE'
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'partial'
    assert path.read_text() == 'USER FILE'


def test_diagnostic_receipt_failure_does_not_write_unowned_events(tmp_path, monkeypatch):
    import os
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.diagnostics import record_event
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    original = os.replace
    calls = 0
    def replace(source, target):
        nonlocal calls
        if Path(target) == instance['registration']:
            calls += 1
            if calls == 2:
                raise OSError('receipt failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', replace)
        record_event(instance['home'], 'desktop_started', pid=123)
    folder = instance['home'] / 'diagnostics'
    assert not list(folder.iterdir())
    record_event(instance['home'], 'desktop_exit', pid=123)
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


def test_busy_inventory_drops_diagnostic_without_blocking_caller(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from common import file_lock
    from hostrun.cleanup import initialize_layout
    from hostrun.diagnostics import record_event
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    with ThreadPoolExecutor(max_workers=1) as pool:
        with file_lock(tmp_path / (instance['instance_id'] + '.inventory.lock')):
            pending = pool.submit(record_event, instance['home'], 'desktop_started', pid=123)
            assert pending.result(timeout=0.5) is None
    assert not (instance['home'] / 'diagnostics').exists()


def test_managed_agent_database_reopens_and_is_cleaned(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.agent_service import AgentService
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    service = AgentService(instance['home'], token='secret')
    task = service.create_task({'schema_version': 1, 'operation_id': 'create', 'title': 'TEST TITLE'}, token='secret')
    reopened = AgentService(instance['home'], token='secret')
    assert reopened.list_tasks(token='secret')[0]['task_id'] == task['task_id']
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'
    assert 'TEST TITLE' not in '\n'.join(p.read_text(encoding='utf-8') for p in Path(result['report']).iterdir() if p.is_file())


def test_real_second_desktop_start_enrolls_activation_and_lock(tmp_path):
    import os
    import subprocess
    import sys
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.managed_desktop import desktop_lock, consume_activation, signal_activation
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    with desktop_lock(instance['home']) as acquired:
        assert acquired
        for _ in range(2):
            process = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'hostrun.desktop',
                                      '--code', str(instance['code']), '--archive', str(instance['archive'])],
                                     env={**os.environ, 'HORIZON_DATA_HOME': str(instance['home'])},
                                     capture_output=True, timeout=10)
            assert process.returncode == 0, process.stderr
            assert consume_activation(instance['home'])
            assert not consume_activation(instance['home'])
        signal_activation(instance['home'])
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


@pytest.mark.parametrize('name', ['desktop.lock', 'desktop.activate'])
def test_real_desktop_refuses_replaced_lock_or_activation_without_writing(tmp_path, name):
    import os
    import subprocess
    import sys
    from contextlib import nullcontext
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.managed_desktop import desktop_lock, signal_activation, consume_activation
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    with desktop_lock(instance['home']):
        signal_activation(instance['home'])
    path = instance['home'] / name
    path.rename(path.with_name(name + '.retained'))
    path.write_text('USER FILE')
    with desktop_lock(instance['home']) if name == 'desktop.activate' else nullcontext():
        process = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'hostrun.desktop',
                                  '--code', str(instance['code']), '--archive', str(instance['archive'])],
                                 env={**os.environ, 'HORIZON_DATA_HOME': str(instance['home'])},
                                 capture_output=True, timeout=10)
        assert process.returncode == 2
        if name == 'desktop.activate':
            with pytest.raises(ValueError, match='identity'):
                consume_activation(instance['home'])
    assert path.read_text() == 'USER FILE'
    assert not (instance['home'] / 'diagnostics').exists()
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'partial'
    assert path.read_text() == 'USER FILE'


def test_activation_receipt_failure_releases_empty_file_and_can_retry(tmp_path, monkeypatch):
    import os
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.managed_desktop import signal_activation, consume_activation
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    original = os.replace
    def replace(source, target):
        if Path(target) == instance['registration']:
            raise OSError('receipt failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', replace)
        with pytest.raises(ValueError, match='unavailable'):
            signal_activation(instance['home'])
    assert not (instance['home'] / 'desktop.activate').exists()
    signal_activation(instance['home'])
    assert consume_activation(instance['home'])
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


def test_desktop_worker_streams_history_without_temporary_files(tmp_path):
    import json
    import queue
    import threading
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure
    from hostrun.desktop_worker import DesktopWorker
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for delta, finish in [({'role': 'assistant', 'content': 'Ready'}, None), ({}, 'stop')]:
                row = {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'test-model',
                       'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(row) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
        def log_message(self, *args): pass
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        configure(instance['home'], provider='horizon-test', model='test-model', api_key='SECRET',
                  base_url=f'http://127.0.0.1:{server.server_port}/v1')
        app = DesktopWorker()
        app.home = instance['home']
        app.resolved = {'code': str(Path.cwd())}
        app.events = queue.Queue()
        app.cancelled = threading.Event()
        app.process = None
        session = str(uuid.uuid4())
        history = [{'role': 'You', 'text': 'Legacy violet moon'}, {'role': 'Horizon', 'text': 'Legacy response'}]
        for prompt in ('First question', 'Continue'):
            app.run_agent(prompt, session, history)
            events = []
            while not app.events.empty():
                events.append(app.events.get())
            assert any(kind == 'agent' and event.get('type') == 'final' for kind, event in events)
        assert json.dumps(requests[0]['messages']).count('Legacy violet moon') == 1
        assert json.dumps(requests[1]['messages']).count('Legacy violet moon') == 1
        assert 'First question' in json.dumps(requests[1]['messages'])
        assert not (instance['home'] / 'agent' / 'history').exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


@pytest.mark.parametrize('case', ['eof', 'malformed', 'invalid_session'])
def test_history_pipe_failure_exits_without_hanging_or_echoing_content(tmp_path, case):
    import os
    import subprocess
    import sys
    import uuid
    from hostrun.cleanup import initialize_layout
    from hostrun.embedded_agent import configure, argv
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='horizon-test', model='test-model', api_key='SECRET',
              base_url='http://127.0.0.1:9/v1')
    args = argv(instance['home'], 'hello', kind='agent', code=Path.cwd())
    args += ['--history-stdin', '--session-id', 'invalid' if case == 'invalid_session' else str(uuid.uuid4())]
    content = '' if case == 'eof' else '{"type":"history","messages":"PRIVATE-HISTORY-MARKER"}\n'
    process = subprocess.run(args, input=content, env={**os.environ, 'HORIZON_PYTHON': sys.executable},
                             capture_output=True, text=True, encoding='utf-8', timeout=10)
    assert process.returncode != 0
    assert 'PRIVATE-HISTORY-MARKER' not in process.stdout + process.stderr
    assert 'SECRET' not in process.stdout + process.stderr
    assert 'Invalid session identity' in process.stdout if case == 'invalid_session' else 'Invalid conversation history' in process.stdout


def test_history_eof_rejects_later_delegate_without_pending_forever(tmp_path):
    import json
    import os
    import subprocess
    import sys
    import threading
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from hostrun.cleanup import initialize_layout
    from hostrun.embedded_agent import configure, argv
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            if len(requests) == 1:
                delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call', 'type': 'function',
                         'function': {'name': 'delegate', 'arguments': json.dumps({'role': 'development_reader', 'input': 'bounded'})}}]}
                finish = 'tool_calls'
            else:
                delta, finish = {'role': 'assistant', 'content': 'Owner unavailable'}, 'stop'
            for part, reason in [(delta, None), ({}, finish)]:
                row = {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'test-model',
                       'choices': [{'index': 0, 'delta': part, 'finish_reason': reason}]}
                self.wfile.write(('data: ' + json.dumps(row) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
        def log_message(self, *args): pass
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        configure(instance['home'], provider='horizon-test', model='test-model', api_key='SECRET',
                  base_url=f'http://127.0.0.1:{server.server_port}/v1')
        args = argv(instance['home'], 'hello', kind='agent', code=Path.cwd(), session_id=str(uuid.uuid4())) + ['--history-stdin']
        process = subprocess.run(args, input='{"type":"history","messages":[]}\n',
                                 env={**os.environ, 'HORIZON_PYTHON': sys.executable}, capture_output=True,
                                 text=True, encoding='utf-8', timeout=6)
        assert 'Delegation owner disconnected' in json.dumps(requests)
        assert 'SECRET' not in process.stdout + process.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_large_history_pipe_write_is_released_by_stop(tmp_path, monkeypatch):
    import queue
    import subprocess
    import threading
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from hostrun.cleanup import initialize_layout
    from hostrun.embedded_agent import configure
    from hostrun.desktop_worker import DesktopWorker
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='test', api_key='SECRET')
    app = DesktopWorker()
    app.home = instance['home']
    app.resolved = {'code': str(Path.cwd())}
    app.events = queue.Queue()
    app.cancelled = threading.Event()
    app.process = None
    original = subprocess.Popen
    started = threading.Event()
    children = []
    def suspended(*args, **kwargs):
        # Real Windows process/pipe, deliberately not reading. This injects at
        # CreateProcess, not at the worker or model-service collaborator.
        create_suspended = 0x00000004
        kwargs['creationflags'] = kwargs.get('creationflags', 0) | create_suspended
        child = original(*args, **kwargs)
        children.append(child)
        started.set()
        return child
    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=1) as pool:
        patch.setattr(subprocess, 'Popen', suspended)
        pending = pool.submit(app.run_agent, 'hello', str(uuid.uuid4()),
                              [{'role': 'You', 'text': 'PRIVATE-HISTORY-MARKER' * 200000}])
        try:
            assert started.wait(5)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.2)
        finally:
            app.stop()
        pending.result(timeout=5)
    assert children[0].poll() is not None
    assert not (instance['home'] / 'agent' / 'history').exists()
    while not app.events.empty():
        assert 'PRIVATE-HISTORY-MARKER' not in str(app.events.get())


@pytest.mark.parametrize('phase, expected', [('before_commit', 1), ('after_commit', 2)])
def test_managed_sqlite_real_transaction_crash_recovers_without_new_identity(tmp_path, phase, expected):
    import os
    import subprocess
    import sys
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.agent_service import AgentService
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    service = AgentService(instance['home'], token='s')
    service.create_task({'schema_version': 1, 'operation_id': 'original', 'title': 'original'}, token='s')
    paths = [instance['home'] / name for name in ('horizon.sqlite3', 'horizon.sqlite3-journal')]
    identities = [path.stat().st_ino for path in paths]
    script = '''
import os, sys
from hostrun.agent_service import AgentService
service = AgentService(sys.argv[1], token='s', transaction_observer=lambda phase: os._exit(71) if phase == sys.argv[2] else None)
service.create_task({'schema_version': 1, 'operation_id': 'interrupted', 'title': 'x' * (3 * 1024 * 1024)}, token='s')
'''
    child = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(instance['home']), phase],
                           env=os.environ.copy(), capture_output=True, timeout=15)
    assert child.returncode == 71, child.stderr
    from hostrun.desktop_upgrade import upgrade
    before_upgrade = [path.read_bytes() for path in paths]
    if phase == 'before_commit':
        refused = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'hot-report',
                                   confirmation=instance['instance_id'])
        assert refused['status'] == 'blocked'
        assert refused['reason'] == 'diagnostics_incomplete'
        assert [path.read_bytes() for path in paths] == before_upgrade
    upgrade_result = upgrade(instance['code'], instance['home'], from_source='absent.zip', sha256='0' * 64)
    assert upgrade_result == {'status': 'blocked', 'reason': 'managed_upgrade_inventory_pending'}
    assert [path.read_bytes() for path in paths] == before_upgrade
    reopened = AgentService(instance['home'], token='s')
    assert len(reopened.list_tasks(token='s')) == expected
    assert [path.stat().st_ino for path in paths] == identities
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


@pytest.mark.parametrize('name', ['horizon.sqlite3', 'horizon.sqlite3-journal', 'horizon.sqlite3-wal', 'horizon.sqlite3-shm'])
def test_managed_sqlite_rejects_replacements_and_unenrolled_sidecars(tmp_path, name):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.agent_service import AgentService
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    AgentService(instance['home'], token='s')
    path = instance['home'] / name
    if path.exists():
        path.rename(path.with_name(name + '.retained'))
    path.write_text('USER FILE')
    with pytest.raises(ValueError):
        AgentService(instance['home'], token='s')
    assert path.read_text() == 'USER FILE'
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == ('blocked' if name == 'horizon.sqlite3-journal' else 'partial')
    assert path.read_text() == 'USER FILE'


def test_agent_service_does_not_recreate_cleaned_home_while_cleanup_owns_lease(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance, lifecycle_lock
    from hostrun.agent_service import AgentService
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report', confirmation=instance['instance_id'])
    with lifecycle_lock(tmp_path / (instance['instance_id'] + '.lock'), exclusive=True) as acquired:
        assert acquired
        with pytest.raises((ValueError, RuntimeError)):
            AgentService(instance['home'], token='s')
        assert not instance['home'].exists()


def test_managed_database_exception_rolls_back_and_releases_connection(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.agent_service import AgentService
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    def interrupt(phase):
        if phase == 'before_commit':
            raise RuntimeError('controlled transaction failure')
    service = AgentService(instance['home'], token='s', transaction_observer=interrupt)
    with pytest.raises(RuntimeError, match='controlled transaction'):
        service.create_task({'schema_version': 1, 'operation_id': 'failed', 'title': 'failed'}, token='s')
    reopened = AgentService(instance['home'], token='s')
    assert reopened.list_tasks(token='s') == []
    reopened.create_task({'schema_version': 1, 'operation_id': 'success', 'title': 'success'}, token='s')
    for name in ('horizon.sqlite3', 'horizon.sqlite3-journal'):
        path = instance['home'] / name
        path.rename(path.with_name(name + '.moved'))
        path.with_name(name + '.moved').rename(path)
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


def test_managed_database_receipt_failure_retries_without_adopting_files(tmp_path, monkeypatch):
    import os
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.agent_service import AgentService
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    original = os.replace
    calls = 0
    def replace(source, target):
        nonlocal calls
        if Path(target) == instance['registration']:
            calls += 1
            if calls == 2:
                raise OSError('journal receipt failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', replace)
        with pytest.raises(OSError, match='journal receipt failure'):
            AgentService(instance['home'], token='s')
    assert not (instance['home'] / 'horizon.sqlite3-journal').exists()
    assert (instance['home'] / 'horizon.sqlite3').stat().st_size == 0
    AgentService(instance['home'], token='s')
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


def test_busy_database_snapshot_blocks_cleanup_before_deletion(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.agent_service import AgentService
    entered, release = threading.Event(), threading.Event()
    def pause(phase):
        if phase == 'before_commit':
            entered.set()
            assert release.wait(30)
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    service = AgentService(instance['home'], token='s', transaction_observer=pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(service.create_task, {'schema_version': 1, 'operation_id': 'held', 'title': 'x' * (3 * 1024 * 1024)}, token='s')
        try:
            assert entered.wait(5)
            result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                                      confirmation=instance['instance_id'])
            assert result['status'] == 'blocked'
            assert result['reason'] == 'diagnostics_incomplete'
            assert (instance['home'] / 'horizon.sqlite3').exists()
            assert (instance['home'] / 'horizon.sqlite3-journal').exists()
        finally:
            release.set()
        task.result(timeout=5)


def test_cleanup_failure_reports_files_already_removed(tmp_path):
    import json
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package, cleanup_instance
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    package = _build(tmp_path.parent / 'fixture')
    digest = json.loads(Path(package['digest']).read_text())['artifact']['sha256']
    install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    configure(instance['home'], provider='test', model='test', api_key='SECRET')
    model = instance['home'] / 'agent' / 'model.json'
    with model.open('rb'):
        result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                                  confirmation=instance['instance_id'])
    assert result['status'] == 'partial'
    assert result['removed']
    assert all(name.startswith('app/current/') and not (instance['root'] / name).exists()
               for name in result['removed'])
    assert model.exists()


def test_layout_can_retry_after_directory_creation_failure(tmp_path, monkeypatch):
    from hostrun.cleanup import initialize_layout
    instance = prepare_instance(tmp_path)
    original = Path.stat
    def fail_data(path, *args, **kwargs):
        if path.name == 'data':
            raise OSError('controlled filesystem failure')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'stat', fail_data)
        with pytest.raises(OSError):
            initialize_layout(tmp_path, instance['instance_id'])
    initialize_layout(tmp_path, instance['instance_id'])
    assert instance['home'].is_dir()


def test_layout_pins_root_before_enumeration(tmp_path, monkeypatch):
    from hostrun.cleanup import initialize_layout
    instance = prepare_instance(tmp_path)
    original = Path.iterdir
    attempts = []
    def swap_root(path):
        if path == instance['root'] and not attempts:
            try:
                path.rename(tmp_path / 'moved-original')
            except OSError:
                attempts.append('blocked')
            else:
                attempts.append('replaced')
                path.mkdir()
        return original(path)
    monkeypatch.setattr(Path, 'iterdir', swap_root)
    initialize_layout(tmp_path, instance['instance_id'])
    assert attempts == ['blocked']


def test_layout_resumes_after_final_registration_write_fails(tmp_path, monkeypatch):
    from hostrun.cleanup import initialize_layout, preview
    instance = prepare_instance(tmp_path)
    original = Path.replace
    writes = []
    def fail_final(source, target):
        if Path(target) == instance['registration']:
            writes.append(1)
            if len(writes) == 2:
                raise OSError('controlled final write failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', fail_final)
        with pytest.raises(OSError):
            initialize_layout(tmp_path, instance['instance_id'])
    initialize_layout(tmp_path, instance['instance_id'])
    assert preview(tmp_path, instance['instance_id'])['owned_children'] == ['app', 'archive', 'data']
    assert instance['home'].is_dir()


def test_new_stage_and_child_are_pinned_before_identity_is_read(tmp_path, monkeypatch):
    from hostrun.cleanup import initialize_layout
    instance = prepare_instance(tmp_path)
    original = Path.stat
    attempts = []
    def race(path, *args, **kwargs):
        if path.name == 'app' and '.stage-' in path.parent.name and not attempts:
            for candidate in (path.parent, path):
                try:
                    candidate.rename(candidate.with_name(candidate.name + '-replaced'))
                except OSError:
                    attempts.append('blocked')
                else:
                    attempts.append('replaced')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', race)
    initialize_layout(tmp_path, instance['instance_id'])
    assert attempts == ['blocked', 'blocked']


def test_resumed_layout_removes_its_empty_staging_directory(tmp_path, monkeypatch):
    from hostrun.cleanup import initialize_layout
    instance = prepare_instance(tmp_path)
    original = Path.stat
    def fail_mid_move(path, *args, **kwargs):
        if path.name == 'data' and '.stage-' in path.parent.name and (instance['code']).exists():
            raise OSError('controlled interrupted move')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'stat', fail_mid_move)
        with pytest.raises(OSError):
            initialize_layout(tmp_path, instance['instance_id'])
    initialize_layout(tmp_path, instance['instance_id'])
    assert not list(tmp_path.glob(instance['instance_id'] + '.stage-*'))


def test_clean_instance_can_be_cleaned_twice_and_initialized_again(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    for _ in range(2):
        result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                                  confirmation=instance['instance_id'])
        assert result['status'] == 'cleaned'
        assert not instance['home'].exists()
    initialize_layout(tmp_path, instance['instance_id'])
    assert instance['home'].is_dir()


def test_keep_data_uninstall_preserves_session_and_reports_success(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    session = instance['home'] / 'horizon.sqlite3'
    session.write_bytes(b'original session')
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'], mode='keep-data')
    assert result['status'] == 'uninstalled'
    assert session.read_bytes() == b'original session'
    assert not instance['code'].exists()


def test_installed_package_inventory_excludes_unknown_user_files(tmp_path):
    import json
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package, cleanup_instance
    package = _build(tmp_path.parent / 'fixture')
    digest = json.loads(Path(package['digest']).read_text(encoding='utf-8'))['artifact']['sha256']
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    result = install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    assert result['status'] == 'ok'
    code = instance['code'] / 'current'
    assert (code / 'README.md').exists()
    unknown = code / 'personal.txt'
    unknown.write_text('original user document')
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'], mode='keep-data')
    assert result['status'] == 'partial'
    assert not (code / 'README.md').exists()
    assert unknown.read_text() == 'original user document'


def test_package_uninstall_removes_registered_empty_directory_skeleton(tmp_path):
    import json
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package, cleanup_instance
    package = _build(tmp_path.parent / 'fixture')
    digest = json.loads(Path(package['digest']).read_text())['artifact']['sha256']
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'], mode='keep-data')
    assert result['status'] == 'uninstalled'
    assert not instance['code'].exists()


@pytest.mark.parametrize('replacement', ['# fixture horizon\n', 'USER REPLACEMENT'])
def test_package_inventory_commit_failure_preserves_unproven_files(tmp_path, monkeypatch, replacement):
    import json
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package
    package = _build(tmp_path.parent / 'fixture')
    digest = json.loads(Path(package['digest']).read_text())['artifact']['sha256']
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    original = Path.replace
    def fail_inventory(source, target):
        if Path(target) == instance['registration'] and (instance['code'] / 'current' / 'README.md').exists():
            raise OSError('controlled inventory commit failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', fail_inventory)
        with pytest.raises(OSError):
            install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    installed = instance['code'] / 'current' / 'README.md'
    installed.rename(installed.with_name('original-readme.md'))
    installed.write_text(replacement)
    original_id = installed.stat().st_ino
    result = install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    assert result['status'] == 'blocked'
    assert installed.read_text() == replacement
    assert installed.stat().st_ino == original_id


def test_package_install_refuses_current_junction_inserted_after_absence_check(tmp_path, monkeypatch):
    import json
    import _winapi
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package
    package = _build(tmp_path.parent / 'fixture')
    digest = json.loads(Path(package['digest']).read_text())['artifact']['sha256']
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    code = instance['code'] / 'current'
    external = tmp_path.parent / 'external'
    external.mkdir()
    original = Path.exists
    inserted = []
    def race(path):
        present = original(path)
        if path == code and not present and not inserted:
            _winapi.CreateJunction(str(external), str(code))
            inserted.append(True)
        return present
    monkeypatch.setattr(Path, 'exists', race)
    with pytest.raises((OSError, ValueError)):
        install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    assert inserted == [True]
    assert not list(external.iterdir())


def test_initial_install_journal_failure_leaves_no_unowned_current(tmp_path, monkeypatch):
    import json
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package
    package = _build(tmp_path.parent / 'fixture')
    digest = json.loads(Path(package['digest']).read_text())['artifact']['sha256']
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    original = Path.replace
    def fail_journal(source, target):
        if Path(target) == instance['registration']:
            raise OSError('controlled journal failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', fail_journal)
        with pytest.raises(OSError):
            install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    assert not (instance['code'] / 'current').exists()
    assert install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)['status'] == 'ok'


def test_interrupted_package_copy_preserves_unproven_replacement(tmp_path, monkeypatch):
    import json
    import shutil
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package
    package = _build(tmp_path.parent / 'fixture')
    digest = json.loads(Path(package['digest']).read_text())['artifact']['sha256']
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    code = instance['code'] / 'current'
    original = shutil.copyfile
    def interrupted(source, target, *args, **kwargs):
        value = original(source, target, *args, **kwargs)
        if Path(target) == code / 'common.py':
            raise KeyboardInterrupt('controlled abrupt copy interruption')
        return value
    with monkeypatch.context() as patch:
        patch.setattr(shutil, 'copyfile', interrupted)
        with pytest.raises(KeyboardInterrupt):
            install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    assert (code / '.horizon' / 'dist.pending.json').exists()

    (code / 'common.py').rename(code / 'original-partial.py')
    (code / 'common.py').write_text('USER REPLACEMENT MUST SURVIVE')
    result = install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    assert result['status'] == 'blocked'
    assert (code / 'common.py').read_text() == 'USER REPLACEMENT MUST SURVIVE'
    assert (code / '.horizon' / 'dist.pending.json').exists()


@pytest.mark.parametrize('report_failure', [False, True])
def test_cleanup_stops_exact_job_owner_and_preserves_unrelated_process(tmp_path, report_failure):
    import os
    import subprocess
    import sys
    import time
    from hostrun.cleanup import initialize_layout, cleanup_instance
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    ready = tmp_path.parent / 'owner-ready'
    script = (
        'import subprocess,sys,time; from pathlib import Path; '
        'from hostrun.desktop_processes import install; '
        'from hostrun.cleanup import register_instance_owner, instance_lock; '
        'install(); lease=instance_lock(Path(sys.argv[1])); lease.__enter__(); '
        'register_instance_owner(Path(sys.argv[1])); '
        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
        'Path(sys.argv[2]).write_text(str(child.pid)); time.sleep(60)'
    )
    owner = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', script, str(instance['home']), str(ready)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy())
    sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and owner.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), owner.communicate(timeout=2)
        if report_failure:
            output = tmp_path.parent / 'report'
            output.write_text('not a directory')
            with pytest.raises(OSError):
                cleanup_instance(tmp_path, instance['instance_id'], output, confirmation=instance['instance_id'])
            assert owner.poll() is None
            assert sentinel.poll() is None
            return
        result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                                  confirmation=instance['instance_id'])
        assert owner.wait(timeout=5) is not None
        assert sentinel.poll() is None
        assert result['status'] == 'cleaned'
        assert (Path(result['report']) / 'summary.md').is_file()
        assert result['processes']['status'] == 'stopped'
        assert result['processes']['observed_processes'] >= 2
        import ctypes
        from ctypes import wintypes as w
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        api.CloseHandle.argtypes = [w.HANDLE]
        child = api.OpenProcess(0x100000, False, int(ready.read_text()))
        if child:
            try:
                assert api.WaitForSingleObject(child, 0) == 0
            finally:
                api.CloseHandle(child)
        else:
            assert ctypes.get_last_error() == 87
    finally:
        for process in (owner, sentinel):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def test_real_model_configuration_is_owned_and_removed_without_secret_report(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='test-model', api_key='SECRET-CONFIG-MARKER')
    config = instance['home'] / 'agent' / 'model.json'
    assert config.exists()
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'
    assert not config.exists()
    report_text = '\n'.join(p.read_text(encoding='utf-8') for p in Path(result['report']).iterdir() if p.is_file())
    assert 'SECRET-CONFIG-MARKER' not in report_text


def test_model_cleanup_preserves_replaced_file_and_unknown_neighbor(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='test-model', api_key='SECRET')
    folder = instance['home'] / 'agent'
    config = folder / 'model.json'
    config.rename(folder / 'user-retained-original.json')
    config.write_text('USER REPLACEMENT')
    neighbor = folder / 'personal.txt'
    neighbor.write_text('USER DOCUMENT')
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'partial'
    assert config.read_text() == 'USER REPLACEMENT'
    assert neighbor.read_text() == 'USER DOCUMENT'


def test_model_reconfiguration_keeps_registered_identity(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    for key in ('FIRST SECRET', 'SECOND SECRET'):
        configure(instance['home'], provider='test', model='test-model', api_key=key)
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


def test_model_update_write_failure_preserves_previous_configuration(tmp_path, monkeypatch):
    import ctypes
    from hostrun.cleanup import initialize_layout
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='first', api_key='ORIGINAL SECRET')
    config = instance['home'] / 'agent' / 'model.json'
    before = config.read_bytes()
    original_dll = ctypes.WinDLL
    kernel = original_dll('kernel32', use_last_error=True)
    def partial_write(handle, buffer, size, written, overlap):
        kernel.WriteFile.argtypes = partial_write.argtypes
        kernel.WriteFile(handle, buffer, min(size, 55), written, overlap)
        ctypes.set_last_error(112)
        return 0
    class KernelProxy:
        def __getattr__(self, name):
            return partial_write if name == 'WriteFile' else getattr(kernel, name)
    def dll(name, *args, **kwargs):
        return KernelProxy() if name == 'kernel32' else original_dll(name, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(ctypes, 'WinDLL', dll)
        with pytest.raises(OSError):
            configure(instance['home'], provider='test', model='second', api_key='NEW SECRET')
    assert config.read_bytes() == before


def test_model_public_save_recovers_published_config_after_receipt_failure(tmp_path, monkeypatch):
    import os
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='first', api_key='SECRET')
    original = os.replace
    writes = 0
    def replace(source, target):
        nonlocal writes
        if Path(target) == instance['registration']:
            writes += 1
            if writes == 2:
                raise OSError('injected final receipt failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', replace)
        with pytest.raises(OSError):
            configure(instance['home'], provider='test', model='second', api_key='SECRET')
    configure(instance['home'], provider='test', model='third', api_key='SECRET')
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


def test_model_runtime_waits_for_configuration_publication(tmp_path, monkeypatch):
    import ctypes
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from hostrun.cleanup import initialize_layout
    from hostrun.embedded_agent import configure, runtime
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='first', api_key='SECRET')
    original_dll = ctypes.WinDLL
    kernel = original_dll('kernel32', use_last_error=True)
    moved = threading.Event()
    release = threading.Event()
    def set_info(handle, kind, buffer, size):
        kernel.SetFileInformationByHandle.argtypes = set_info.argtypes
        result = kernel.SetFileInformationByHandle(handle, kind, buffer, size)
        if kind == 3 and result and not moved.is_set():
            moved.set()
            assert release.wait(5)
        return result
    class KernelProxy:
        def __init__(self):
            self.api = original_dll('kernel32', use_last_error=True)
        def __getattr__(self, name):
            return set_info if name == 'SetFileInformationByHandle' else getattr(self.api, name)
    def dll(name, *args, **kwargs):
        return KernelProxy() if name == 'kernel32' else original_dll(name, *args, **kwargs)
    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=2) as pool:
        patch.setattr(ctypes, 'WinDLL', dll)
        writer = pool.submit(configure, instance['home'], provider='test', model='second', api_key='SECRET')
        assert moved.wait(5)
        reader = pool.submit(runtime, instance['home'])
        try:
            with pytest.raises(TimeoutError):
                reader.result(timeout=0.15)
        finally:
            release.set()
        writer.result(timeout=5)
        assert reader.result(timeout=5)['model'] == 'second'


def test_configure_cleanup_reinitialize_configure_round_trip(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    for _ in range(2):
        initialize_layout(tmp_path, instance['instance_id'])
        configure(instance['home'], provider='test', model='test', api_key='SECRET')
        result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                                  confirmation=instance['instance_id'])
        assert result['status'] == 'cleaned'


def test_gui_model_discovery_selection_refresh_are_owned(tmp_path):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun import embedded_agent
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'data': [{'id': 'chat-a'}, {'id': 'chat-b'}]}).encode())
        def log_message(self, *args):
            pass
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        embedded_agent.discover_and_configure(instance['home'], provider='custom', api_key='SECRET',
                                             base_url=f'http://127.0.0.1:{server.server_port}/v1')
        embedded_agent.select_model(instance['home'], 'chat-b')
        embedded_agent.refresh_models(instance['home'])
        assert embedded_agent.runtime(instance['home'])['model'] == 'chat-b'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


@pytest.mark.parametrize('crash_at, expected', [('written', 'first'), ('backup', 'first'), ('published', 'second')])
def test_model_runtime_recovers_real_process_interruption(tmp_path, crash_at, expected):
    import os
    import subprocess
    import sys
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure, runtime
    from hostrun.managed_upgrade_data import backup_owned_data, restore_owned_data
    from hostrun.cleanup import InstanceInvalid
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='first', api_key='SECRET')
    snapshot = backup_owned_data(instance['home'])
    script = '''
import ctypes, os, sys
from pathlib import Path
from hostrun.embedded_agent import configure
original = ctypes.WinDLL
kernel = original('kernel32', use_last_error=True)
count = 0
def set_info(handle, kind, buffer, size):
    global count
    kernel.SetFileInformationByHandle.argtypes = set_info.argtypes
    result = kernel.SetFileInformationByHandle(handle, kind, buffer, size)
    if kind == 3 and result:
        count += 1
        if (sys.argv[2] == 'backup' and count == 1) or (sys.argv[2] == 'published' and count == 2):
            os._exit(74)
    return result
def flush(handle):
    kernel.FlushFileBuffers.argtypes = flush.argtypes
    result = kernel.FlushFileBuffers(handle)
    if result and sys.argv[2] == 'written': os._exit(74)
    return result
class Proxy:
    def __getattr__(self, name):
        if name == 'SetFileInformationByHandle': return set_info
        if name == 'FlushFileBuffers': return flush
        return getattr(kernel, name)
ctypes.WinDLL = lambda name, *args, **kwargs: Proxy() if name == 'kernel32' else original(name, *args, **kwargs)
configure(Path(sys.argv[1]), provider='test', model='second', api_key='SECRET')
'''
    process = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(instance['home']), crash_at],
                             env=os.environ.copy(), capture_output=True, timeout=10)
    assert process.returncode == 74, process.stderr
    before = {path: path.read_bytes() for path in (instance['home'] / 'agent').iterdir() if path.is_file()}
    with pytest.raises(InstanceInvalid):
        restore_owned_data(instance['home'], snapshot['snapshot_id'])
    assert {path: path.read_bytes() for path in (instance['home'] / 'agent').iterdir() if path.is_file()} == before
    assert runtime(instance['home'])['model'] == expected
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


def test_node_agent_rejects_replaced_managed_credentials(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    from hostrun.cleanup import initialize_layout
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='first', api_key='SECRET')
    config = instance['home'] / 'agent' / 'model.json'
    config.rename(config.with_suffix('.retained'))
    config.write_text('{"provider":"test","model":"first","apiKey":"REPLACED"}')
    process = subprocess.run([shutil.which('node'), 'agent/dist/main.js', '--home', str(instance['home']),
                              '--task', 'hello', '--kind', 'intent_judgement'],
                             env={**os.environ, 'HORIZON_PYTHON': sys.executable}, capture_output=True,
                             text=True, encoding='utf-8', timeout=15)
    assert process.returncode != 0
    assert 'Model configuration identity validation failed' in process.stdout + process.stderr
    assert not (instance['home'] / 'agent' / 'pi').exists()


def test_managed_node_does_not_duplicate_credentials_in_pi_auth(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    from hostrun.cleanup import initialize_layout
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='first', api_key='SECRET')
    process = subprocess.run([shutil.which('node'), 'agent/dist/main.js', '--home', str(instance['home']),
                              '--task', 'hello', '--kind', 'intent_judgement'],
                             env={**os.environ, 'HORIZON_PYTHON': sys.executable}, capture_output=True,
                             text=True, encoding='utf-8', timeout=15)
    assert 'Unknown model' in process.stdout
    assert not (instance['home'] / 'agent' / 'pi' / 'auth.json').exists()
    assert 'SECRET' not in process.stdout + process.stderr


@pytest.mark.parametrize('residue', ['none', 'replacement', 'unknown'])
def test_managed_pi_session_resumes_and_cleanup_removes_only_owned_files(tmp_path, residue):
    import json
    import os
    import subprocess
    import sys
    import threading
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun import embedded_agent
    requests = []
    protected = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            path = instance['home'] / 'agent' / 'pi' / 'sessions' / session / 'session.jsonl'
            try:
                path.rename(path.with_suffix('.moved'))
            except PermissionError:
                protected.append(True)
            else:
                protected.append(False)
                path.with_suffix('.moved').rename(path)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for delta, finish in [({'role': 'assistant', 'content': 'Ready'}, None), ({}, 'stop')]:
                chunk = {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'test-model',
                         'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
        def log_message(self, *args): pass
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        embedded_agent.configure(instance['home'], provider='horizon-test', model='test-model', api_key='SECRET',
                                 base_url=f'http://127.0.0.1:{server.server_port}/v1')
        session = str(uuid.uuid4())
        for prompt in ('First unique question', 'Continue'):
            args = embedded_agent.argv(instance['home'], prompt, kind='intent_judgement', code=Path.cwd(), session_id=session)
            process = subprocess.run(args, env={**os.environ, 'HORIZON_PYTHON': sys.executable},
                                     capture_output=True, text=True, encoding='utf-8', timeout=20)
            assert process.returncode == 0, process.stdout + process.stderr
            assert 'SECRET' not in process.stdout + process.stderr
        assert 'First unique question' in json.dumps(requests[-1])
        assert protected == [True, True]
        if residue == 'none':
            from hostrun.managed_upgrade_data import backup_owned_data, restore_owned_data
            session_path = instance['home'] / 'agent/pi/sessions' / session / 'session.jsonl'
            before = session_path.read_bytes()
            snapshot = backup_owned_data(instance['home'])
            args = embedded_agent.argv(instance['home'], 'After snapshot', kind='intent_judgement', code=Path.cwd(), session_id=session)
            process = subprocess.run(args, env={**os.environ, 'HORIZON_PYTHON': sys.executable},
                                     capture_output=True, text=True, encoding='utf-8', timeout=20)
            assert process.returncode == 0, process.stdout + process.stderr
            assert session_path.read_bytes() != before
            assert restore_owned_data(instance['home'], snapshot['snapshot_id'])['status'] == 'restored'
            assert session_path.read_bytes() == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    session_path = instance['home'] / 'agent' / 'pi' / 'sessions' / session / 'session.jsonl'
    if residue == 'replacement':
        session_path.rename(session_path.with_suffix('.retained'))
        session_path.write_text('USER REPLACEMENT')
    elif residue == 'unknown':
        session_path.with_name('personal.txt').write_text('USER DOCUMENT')
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == ('cleaned' if residue == 'none' else 'partial')
    if residue == 'replacement':
        assert session_path.read_text() == 'USER REPLACEMENT'
    elif residue == 'unknown':
        assert session_path.with_name('personal.txt').read_text() == 'USER DOCUMENT'


def test_gui_refresh_cannot_overwrite_new_credentials(tmp_path):
    import json
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from hostrun.cleanup import initialize_layout
    from hostrun import embedded_agent
    entered, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            assert release.wait(5)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'data': [{'id': 'old-model'}]}).encode())
        def log_message(self, *args): pass
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    embedded_agent.configure(instance['home'], provider='custom', model='old-model', api_key='OLD',
                             base_url=f'http://127.0.0.1:{server.server_port}/v1')
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            refresh = pool.submit(embedded_agent.refresh_models, instance['home'])
            try:
                assert entered.wait(5)
                embedded_agent.configure(instance['home'], provider='new-provider', model='new-model', api_key='NEW')
            finally:
                release.set()
            with pytest.raises(ValueError, match='configuration changed'):
                refresh.result(timeout=5)
        assert embedded_agent.runtime(instance['home'])['provider'] == 'new-provider'
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('written', [0, 12])
def test_pi_interrupted_header_stays_blocked_without_identity_drift(tmp_path, written):
    import os
    import subprocess
    import sys
    import uuid
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun import embedded_agent
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    embedded_agent.configure(instance['home'], provider='horizon-test', model='test-model', api_key='SECRET',
                             base_url='http://127.0.0.1:9/v1')
    injection = tmp_path.parent / 'injection'
    injection.mkdir()
    (injection / 'sitecustomize.py').write_text('''
import ctypes, os
original = ctypes.WinDLL
kernel = original('kernel32', use_last_error=True)
def write(handle, buffer, size, written, overlap):
    kernel.WriteFile.argtypes = write.argtypes
    kernel.WriteFile(handle, buffer, int(os.environ['HEADER_BYTES']), written, overlap)
    os._exit(75)
class Proxy:
    def __getattr__(self, name):
        return write if name == 'WriteFile' else getattr(kernel, name)
ctypes.WinDLL = lambda name, *args, **kwargs: Proxy() if name == 'kernel32' else original(name, *args, **kwargs)
''')
    session = str(uuid.uuid4())
    args = embedded_agent.argv(instance['home'], 'hello', kind='intent_judgement', code=Path.cwd(), session_id=session)
    environment = {**os.environ, 'HORIZON_PYTHON': sys.executable}
    failed = subprocess.run(args, env={**environment, 'PYTHONPATH': str(injection), 'HEADER_BYTES': str(written)},
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert failed.returncode != 0
    path = instance['home'] / 'agent' / 'pi' / 'sessions' / session / 'session.jsonl'
    before = path.read_bytes()
    assert len(before) == written
    identity = path.stat().st_ino
    retry = subprocess.run(args, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert retry.returncode != 0
    assert 'Managed Pi session unavailable' in retry.stdout
    assert path.read_bytes() == before
    assert path.stat().st_ino == identity
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


def test_killed_node_releases_pi_guardian_without_polling_process(tmp_path):
    import os
    import subprocess
    import sys
    import threading
    import time
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun import embedded_agent
    entered, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            entered.set()
            release.wait(10)
        def log_message(self, *args): pass
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    process = None
    try:
        embedded_agent.configure(instance['home'], provider='horizon-test', model='test-model', api_key='SECRET',
                                 base_url=f'http://127.0.0.1:{server.server_port}/v1')
        session = str(uuid.uuid4())
        args = embedded_agent.argv(instance['home'], 'hello', kind='intent_judgement', code=Path.cwd(), session_id=session)
        process = subprocess.Popen(args, env={**os.environ, 'HORIZON_PYTHON': sys.executable},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert entered.wait(10)
        path = instance['home'] / 'agent' / 'pi' / 'sessions' / session / 'session.jsonl'
        with pytest.raises(PermissionError):
            path.rename(path.with_suffix('.moved'))
        process.kill()
        process.communicate(timeout=5)
        deadline = time.monotonic() + 5
        while True:
            try:
                path.rename(path.with_suffix('.moved'))
                path.with_suffix('.moved').rename(path)
                break
            except PermissionError:
                assert time.monotonic() < deadline, 'guardian retained file after Node exit'
                time.sleep(0.02)
        result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                                  confirmation=instance['instance_id'])
        assert result['status'] == 'cleaned'
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('damage', ['missing', 'tampered', 'lock_directory', 'junction'])
def test_invalid_instance_real_desktop_never_writes_home(tmp_path, damage):
    import os
    import subprocess
    import sys
    instance = prepare_instance(tmp_path)
    if damage == 'missing':
        instance['registration'].unlink()
    elif damage == 'tampered':
        instance['registration'].write_text('{}')
    elif damage == 'lock_directory':
        (tmp_path / (instance['instance_id'] + '.lock')).mkdir()
    else:
        import _winapi
        external = tmp_path.parent / 'external'
        external.mkdir()
        _winapi.CreateJunction(str(external), str(instance['home']))
    result = subprocess.run(
        [sys.executable, '-X', 'utf8', '-m', 'hostrun.desktop',
         '--code', str(instance['code']), '--archive', str(instance['archive'])],
        env={**os.environ, 'HORIZON_DATA_HOME': str(instance['home'])},
        capture_output=True, timeout=3)
    assert result.returncode != 0
    if damage == 'junction':
        assert not list(external.iterdir())
    else:
        assert not instance['home'].exists()


@pytest.mark.parametrize('mode', ['managed', 'report_unavailable', 'ordinary'])
def test_managed_real_desktop_failure_exports_category_without_raw_traceback(tmp_path, mode):
    import os
    import shutil
    import subprocess
    import sys
    from hostrun.cleanup import initialize_layout
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    home = instance['home'] if mode != 'ordinary' else tmp_path.parent / 'ordinary'
    if mode == 'report_unavailable':
        import _winapi
        external = tmp_path.parent / 'external-report'
        external.mkdir()
        _winapi.CreateJunction(str(external), str(tmp_path.parent / 'test-reports'))
    source = Path(__file__).resolve().parents[1]
    tool = tmp_path.parent / 'tool'
    shutil.copytree(source / 'hostrun', tool / 'hostrun', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(source / 'common.py', tool / 'common.py')
    # Only replace the OS dialog boundary so a real -m failure can finish unattended.
    (tool / 'sitecustomize.py').write_text(
        'import ctypes\nctypes.windll.user32.MessageBoxW = lambda owner, message, title, flags: print(message) or 1\n',
        encoding='utf-8')
    result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'hostrun.desktop',
                             '--code', str(instance['code']), '--archive', str(instance['archive'])],
                            cwd=tool, env={**os.environ, 'PYTHONPATH': os.pathsep.join((str(tool), str(source))),
                                           'HORIZON_DATA_HOME': str(home)},
                            capture_output=True, text=True, encoding='utf-8', timeout=10)
    if mode == 'ordinary':
        assert result.returncode == 1
        assert 'Traceback' in (tool / '.horizon/desktop-error.log').read_text(encoding='utf-8')
        return
    assert result.returncode == 2
    assert 'RuntimeError' in result.stdout
    assert 'Traceback' not in result.stdout + result.stderr
    assert not (tool / '.horizon/desktop-error.log').exists()
    if mode == 'report_unavailable':
        assert '诊断导出未完成' in result.stdout
        assert not list(external.iterdir())
        return
    reports = list((tmp_path.parent / 'test-reports' / instance['instance_id']).glob('horizon-report-*'))
    assert len(reports) == 1
    assert str(reports[0]) in result.stdout
    events = (reports[0] / 'events.jsonl').read_text(encoding='utf-8')
    assert 'RuntimeError' in events


@pytest.mark.parametrize('managed,connected', [(True, True), (False, True), (True, False)])
def test_managed_renderer_raw_stderr_is_not_persisted_and_exit_is_reported(tmp_path, managed, connected):
    import json
    import os
    import shutil
    import subprocess
    import sys
    from hostrun.cleanup import initialize_layout
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    home = instance['home'] if managed else tmp_path.parent / 'ordinary'
    source = Path(__file__).resolve().parents[1]
    tool = tmp_path.parent / 'tool'
    shutil.copytree(source / 'hostrun', tool / 'hostrun', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(source / 'common.py', tool / 'common.py')
    (tool / 'sitecustomize.py').write_text(
        'import ctypes\nctypes.windll.user32.MessageBoxW = lambda *args: 1\n', encoding='utf-8')
    desktop = instance['code'] / 'desktop'
    runtime = desktop / 'node_modules/electron/dist'
    runtime.mkdir(parents=True)
    shutil.copyfile(shutil.which('node'), runtime / 'electron.exe')
    (desktop / 'package.json').write_text(json.dumps({'main': 'actor.cjs'}))
    marker = 'SYNTHETIC-RENDERER-SECRET-DO-NOT-LOG'
    (desktop / 'actor.cjs').write_text('''
const fs = require('fs');
const net = require('net');
const secret = 'SYNTHETIC-RENDERER-SECRET-DO-NOT-LOG';
process.stderr.write(secret);
if (process.env.ELECTRON_LOG_FILE) fs.writeFileSync(process.env.ELECTRON_LOG_FILE, secret);
if (process.env.TEST_RENDERER_EARLY_EXIT === '1') process.exit(17);
const socket = net.connect(Number(process.env.HORIZON_DESKTOP_PORT), '127.0.0.1', () => {
  socket.write(process.env.HORIZON_DESKTOP_TOKEN + '\\n');
});
socket.once('data', () => process.exit(17));
setTimeout(() => process.exit(18), 8000);
''', encoding='utf-8')
    result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'hostrun.desktop',
                             '--code', str(instance['code']), '--archive', str(instance['archive'])],
                            cwd=tool, env={**os.environ, 'PYTHONPATH': os.pathsep.join((str(tool), str(source))),
                                           'HORIZON_DATA_HOME': str(home),
                                           'ELECTRON_LOG_FILE': str(tmp_path.parent / 'inherited.log'),
                                           'ELECTRON_ENABLE_LOGGING': '1',
                                           'TEST_RENDERER_EARLY_EXIT': '0' if connected else '1'},
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    if not managed:
        assert result.returncode == 1
        assert marker in (home / 'desktop-electron.log').read_text(encoding='utf-8')
        return
    assert result.returncode == 2, result.stderr
    assert not (instance['home'] / 'desktop-electron.log').exists()
    assert marker not in result.stdout + result.stderr
    report = next((tmp_path.parent / 'test-reports' / instance['instance_id']).glob('horizon-report-*'))
    events = [json.loads(line) for line in (report / 'events.jsonl').read_text().splitlines()]
    category = 'renderer_exit' if connected else 'renderer_start_failed'
    assert any(event.get('code') == category and event.get('exit_code') == 17 for event in events)
    assert all(marker not in path.read_text(encoding='utf-8') for path in report.iterdir() if path.is_file())


def test_experimental_code_transaction_preserves_home_and_cleans_enrolled_backups(tmp_path):
    import json
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package, cleanup_instance
    from hostrun.managed_upgrade import stage_code_transaction, rollback_code_transaction
    from hostrun.embedded_agent import configure, load_model_config
    from hostrun.agent_service import AgentService
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    first = _build(tmp_path.parent / 'first', version='0.1.0')
    second = _build(tmp_path.parent / 'second', version='0.2.0')
    install_test_package(tmp_path, instance['instance_id'], first['artifact']['path'], first['artifact']['sha256'])
    configure(instance['home'], provider='openai', api_key='synthetic-upgrade-key', model='gpt-4o')
    service = AgentService(instance['home'], token='test')
    task = service.create_task({'schema_version': 1, 'operation_id': 'create', 'title': 'kept'}, token='test')
    code = instance['code'] / 'current'
    previous_id = (code / 'README.md').stat().st_ino
    result = stage_code_transaction(code, instance['home'], from_source=second['artifact']['path'], sha256=second['artifact']['sha256'])
    assert result['status'] == 'ok', result
    assert json.loads((code / 'manifest.json').read_text())['version'] == '0.2.0'
    assert load_model_config(instance['home'])['apiKey'] == 'synthetic-upgrade-key'
    assert AgentService(instance['home'], token='test').list_tasks(token='test')[0]['task_id'] == task['task_id']
    assert result['data_migration'] == 'not_performed'
    assert rollback_code_transaction(code, instance['home'], result['transaction_id'])['status'] == 'restored'
    assert json.loads((code / 'manifest.json').read_text())['version'] == '0.1.0'
    assert (code / 'README.md').stat().st_ino == previous_id
    assert rollback_code_transaction(code, instance['home'], result['transaction_id'])['status'] == 'restored'
    assert cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                            confirmation=instance['instance_id'])['status'] == 'cleaned'


def test_startup_rechecks_pending_upgrade_after_acquiring_lease(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package, InstanceInvalid
    from hostrun.managed_upgrade import stage_code_transaction
    from hostrun.agent_service import AgentService
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    first = _build(tmp_path.parent / 'first')
    second = _build(tmp_path.parent / 'second', version='0.2.0')
    install_test_package(tmp_path, instance['instance_id'], first['artifact']['path'], first['artifact']['sha256'])
    entered, resume = threading.Event(), threading.Event()
    original = Path.open
    def open_file(path, *args, **kwargs):
        if path.name == instance['instance_id'] + '.gate' and threading.current_thread().name.startswith('startup') and not entered.is_set():
            entered.set()
            assert resume.wait(10)
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=1, thread_name_prefix='startup') as pool:
        patch.setattr(Path, 'open', open_file)
        future = pool.submit(AgentService, instance['home'], token='test')
        assert entered.wait(5)
        try:
            # An actual no-delete-sharing file handle interrupts publishing.
            with (instance['code'] / 'current/README.md').open('rb'):
                with pytest.raises(PermissionError):
                    stage_code_transaction(instance['code'] / 'current', instance['home'],
                        from_source=second['artifact']['path'], sha256=second['artifact']['sha256'])
        finally:
            resume.set()
        with pytest.raises(InstanceInvalid):
            future.result(timeout=5)
    from hostrun.cleanup import cleanup_instance
    assert cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                            confirmation=instance['instance_id'])['status'] == 'cleaned'
    initialize_layout(tmp_path, instance['instance_id'])
    assert AgentService(instance['home'], token='test').list_tasks(token='test') == []


def test_internal_upgrade_data_snapshot_restores_real_database_and_credentials(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.managed_upgrade_data import backup_owned_data, restore_owned_data
    from hostrun.embedded_agent import configure, load_model_config
    from hostrun.agent_service import AgentService
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    home = instance['home']
    configure(home, provider='openai', model='test', api_key='before-upgrade')
    service = AgentService(home, token='test')
    original = service.create_task({'schema_version': 1, 'operation_id': 'first', 'title': 'before'}, token='test')
    snapshot = backup_owned_data(home)
    assert snapshot['status'] == 'backed_up'
    configure(home, provider='openai', model='changed', api_key='after-upgrade')
    service.create_task({'schema_version': 1, 'operation_id': 'second', 'title': 'after'}, token='test')
    assert restore_owned_data(home, snapshot['snapshot_id'])['status'] == 'restored'
    assert load_model_config(home)['apiKey'] == 'before-upgrade'
    assert [task['task_id'] for task in AgentService(home, token='test').list_tasks(token='test')] == [original['task_id']]
    assert cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'reports',
                            confirmation=instance['instance_id'])['status'] == 'cleaned'


def test_data_restore_partial_write_blocks_startup_and_retries_from_snapshot(tmp_path, monkeypatch):
    import ctypes
    from hostrun.cleanup import initialize_layout, InstanceInvalid
    from hostrun.managed_upgrade_data import backup_owned_data, restore_owned_data
    from hostrun.embedded_agent import configure, load_model_config
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='first', api_key='before')
    snapshot = backup_owned_data(instance['home'])
    configure(instance['home'], provider='test', model='second', api_key='after')
    original = ctypes.WinDLL
    kernel = original('kernel32', use_last_error=True)
    def partial_write(handle, buffer, size, written, overlap):
        kernel.WriteFile.argtypes = partial_write.argtypes
        kernel.WriteFile(handle, buffer, min(size, 7), written, overlap)
        ctypes.set_last_error(112)
        return 0
    class Proxy:
        def __getattr__(self, name):
            return partial_write if name == 'WriteFile' else getattr(kernel, name)
    with monkeypatch.context() as patch:
        patch.setattr(ctypes, 'WinDLL', lambda name, *a, **kw: Proxy() if name == 'kernel32' else original(name, *a, **kw))
        with pytest.raises(OSError):
            restore_owned_data(instance['home'], snapshot['snapshot_id'])
    with pytest.raises(InstanceInvalid):
        load_model_config(instance['home'])
    assert restore_owned_data(instance['home'], snapshot['snapshot_id'])['status'] == 'restored'
    assert load_model_config(instance['home'])['apiKey'] == 'before'


@pytest.mark.parametrize('target', ['original', 'backup', 'backup_content'])
def test_data_restore_preserves_foreign_or_changed_snapshot_files(tmp_path, target):
    from hostrun.cleanup import initialize_layout, InstanceInvalid
    from hostrun.managed_upgrade_data import backup_owned_data, restore_owned_data
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='first', api_key='before')
    snapshot = backup_owned_data(instance['home'])
    original = instance['home'] / 'agent/model.json'
    backup = instance['home'] / 'upgrade-backups' / snapshot['snapshot_id'] / 'agent/model.json'
    changed = original if target == 'original' else backup
    if target != 'backup_content':
        changed.rename(changed.with_suffix('.retained'))
    changed.write_text('USER REPLACEMENT', encoding='utf-8')
    before = original.read_bytes()
    with pytest.raises(InstanceInvalid):
        restore_owned_data(instance['home'], snapshot['snapshot_id'])
    assert changed.read_text(encoding='utf-8') == 'USER REPLACEMENT'
    assert original.read_bytes() == before


def test_data_backup_refuses_external_wal_mode_without_creating_sidecars(tmp_path):
    import sqlite3
    from contextlib import closing
    from hostrun.cleanup import initialize_layout, InstanceInvalid
    from hostrun.managed_upgrade_data import backup_owned_data
    from hostrun.agent_service import AgentService
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    AgentService(instance['home'], token='test')
    path = instance['home'] / 'horizon.sqlite3'
    journal = path.with_name(path.name + '-journal')
    retained = path.with_name('saved-journal')
    journal.rename(retained)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    retained.rename(journal)
    before = path.read_bytes()
    with pytest.raises(InstanceInvalid):
        backup_owned_data(instance['home'])
    assert path.read_bytes() == before
    assert not (instance['home'] / 'upgrade-backups').exists()
    assert not path.with_name(path.name + '-wal').exists()
    assert not path.with_name(path.name + '-shm').exists()


def test_internal_code_rollback_interrupted_rename_blocks_then_retries(tmp_path, monkeypatch):
    import ctypes
    import json
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package, InstanceInvalid
    from hostrun.managed_upgrade import stage_code_transaction, rollback_code_transaction
    from hostrun.agent_service import AgentService
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    first = _build(tmp_path.parent / 'first')
    second = _build(tmp_path.parent / 'second', version='0.2.0')
    install_test_package(tmp_path, instance['instance_id'], first['artifact']['path'], first['artifact']['sha256'])
    code = instance['code'] / 'current'
    old_id = (code / 'README.md').stat().st_ino
    transaction = stage_code_transaction(code, instance['home'], from_source=second['artifact']['path'], sha256=second['artifact']['sha256'])
    original = ctypes.WinDLL
    kernel = original('kernel32', use_last_error=True)
    moves = 0
    def set_info(handle, kind, buffer, size):
        nonlocal moves
        kernel.SetFileInformationByHandle.argtypes = set_info.argtypes
        if kind == 3:
            moves += 1
            if moves == 2:
                ctypes.set_last_error(32)
                return 0
        return kernel.SetFileInformationByHandle(handle, kind, buffer, size)
    class Proxy:
        def __getattr__(self, name):
            return set_info if name == 'SetFileInformationByHandle' else getattr(kernel, name)
    with monkeypatch.context() as patch:
        patch.setattr(ctypes, 'WinDLL', lambda name, *a, **kw: Proxy() if name == 'kernel32' else original(name, *a, **kw))
        with pytest.raises(OSError):
            rollback_code_transaction(code, instance['home'], transaction['transaction_id'])
    with pytest.raises(InstanceInvalid):
        AgentService(instance['home'], token='test')
    assert rollback_code_transaction(code, instance['home'])['status'] == 'restored'
    assert json.loads((code / 'manifest.json').read_text())['version'] == '0.1.0'
    assert (code / 'README.md').stat().st_ino == old_id


@pytest.mark.parametrize('target', ['current', 'backup', 'backup_content'])
def test_internal_code_rollback_preserves_replacements(tmp_path, target):
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package, InstanceInvalid
    from hostrun.managed_upgrade import stage_code_transaction, rollback_code_transaction
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    first = _build(tmp_path.parent / 'first')
    second = _build(tmp_path.parent / 'second', version='0.2.0')
    install_test_package(tmp_path, instance['instance_id'], first['artifact']['path'], first['artifact']['sha256'])
    code = instance['code'] / 'current'
    transaction = stage_code_transaction(code, instance['home'], from_source=second['artifact']['path'], sha256=second['artifact']['sha256'])
    changed = code / 'README.md' if target == 'current' else code / '.horizon/upgrades' / transaction['transaction_id'] / 'old/README.md'
    if target != 'backup_content': changed.rename(changed.with_suffix('.retained'))
    changed.write_text('USER FILE', encoding='utf-8')
    before = (code / 'manifest.json').read_bytes()
    with pytest.raises(InstanceInvalid):
        rollback_code_transaction(code, instance['home'], transaction['transaction_id'])
    assert changed.read_text(encoding='utf-8') == 'USER FILE'
    assert (code / 'manifest.json').read_bytes() == before


@pytest.mark.parametrize('phase', ['before_rename', 'after_rename'])
def test_code_rollback_recovers_process_exit_between_alias_receipts(tmp_path, phase):
    import json
    import os
    import subprocess
    import sys
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package, InstanceInvalid
    from hostrun.managed_upgrade import stage_code_transaction, rollback_code_transaction
    from hostrun.agent_service import AgentService
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    first = _build(tmp_path.parent / 'first')
    second = _build(tmp_path.parent / 'second', version='0.2.0')
    install_test_package(tmp_path, instance['instance_id'], first['artifact']['path'], first['artifact']['sha256'])
    code = instance['code'] / 'current'
    old_id = (code / 'README.md').stat().st_ino
    transaction = stage_code_transaction(code, instance['home'], from_source=second['artifact']['path'], sha256=second['artifact']['sha256'])
    script = '''
import ctypes, os, sys
from hostrun.managed_upgrade import rollback_code_transaction
original = ctypes.WinDLL
kernel = original('kernel32', use_last_error=True)
def set_info(handle, kind, buffer, size):
    kernel.SetFileInformationByHandle.argtypes = set_info.argtypes
    if kind == 3 and sys.argv[4] == 'before_rename': os._exit(77)
    result = kernel.SetFileInformationByHandle(handle, kind, buffer, size)
    if kind == 3 and result: os._exit(77)
    return result
class Proxy:
    def __getattr__(self, name):
        return set_info if name == 'SetFileInformationByHandle' else getattr(kernel, name)
ctypes.WinDLL = lambda name, *a, **kw: Proxy() if name == 'kernel32' else original(name, *a, **kw)
rollback_code_transaction(sys.argv[1], sys.argv[2], sys.argv[3])
'''
    process = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(code), str(instance['home']),
                              transaction['transaction_id'], phase], env=os.environ.copy(), capture_output=True, timeout=10)
    assert process.returncode == 77, process.stderr
    with pytest.raises(InstanceInvalid): AgentService(instance['home'], token='test')
    assert rollback_code_transaction(code, instance['home'])['status'] == 'restored'
    assert (code / 'README.md').stat().st_ino == old_id
    assert json.loads((code / 'manifest.json').read_text())['version'] == '0.1.0'


@pytest.mark.parametrize('mode', ['normal', 'missing', 'tampered', 'failure', 'no_database_snapshot'])
def test_target_package_schema_entry_runs_verified_target_code(tmp_path, mode):
    import json
    from test_dist_install import _write_tree, PACKAGE_FILES, _host_compat
    from hostrun.distribution import build
    from hostrun.cleanup import initialize_layout, install_test_package, InstanceInvalid
    from hostrun.managed_upgrade_data import backup_owned_data, restore_owned_data
    from hostrun.managed_upgrade_initialize import initialize_target_schema
    from hostrun.managed_database import database_connection
    from hostrun.agent_service import AgentService
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    home = instance['home']
    if mode == 'no_database_snapshot':
        snapshot = backup_owned_data(home)
    service = AgentService(home, token='test')
    task = service.create_task({'schema_version': 1, 'operation_id': 'first', 'title': 'legacy'}, token='test')
    with database_connection(home) as db, db:
        data = json.loads(db.execute('SELECT result FROM agent_tasks').fetchone()[0])
        data.pop('session_id')
        db.execute('UPDATE agent_tasks SET result=?', (json.dumps(data),))
    if mode != 'no_database_snapshot':
        snapshot = backup_owned_data(home)
    original = (Path(__file__).resolve().parents[1] / 'hostrun/agent_service.py').read_text(encoding='utf-8')
    target = original.replace("task['session_id'] = str(uuid.uuid4())", "task['session_id'] = '11111111-1111-4111-8111-111111111111'")
    if mode == 'missing':
        target = target.replace('def initialize_database(', 'def unsupported_schema(')
    if mode == 'failure':
        target = target.replace("db.execute('BEGIN IMMEDIATE')", "db.execute('CREATE TABLE target_partial(value TEXT)')\n        raise RuntimeError('TARGET-SCHEMA-FAILURE')", 1)
    source = _write_tree(tmp_path.parent / 'target-source', {**PACKAGE_FILES, 'hostrun/agent_service.py': target})
    package = build(source=source, out=tmp_path.parent / 'package', version='0.2.0', channel='stable', commit='test', compat=_host_compat())
    install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], package['artifact']['sha256'])
    manifest_sha = json.loads(Path(package['digest']).read_text())['manifest_sha256']
    code = instance['code'] / 'current'
    database = home / 'horizon.sqlite3'
    before = database.read_bytes()
    if mode == 'no_database_snapshot':
        result = initialize_target_schema(code, home, manifest_sha256=manifest_sha, snapshot_id=snapshot['snapshot_id'])
        assert result == {'status': 'blocked', 'reason': 'schema_database_snapshot_unavailable'}
        assert database.read_bytes() == before
        AgentService(home, token='test')
        return
    if mode == 'tampered':
        with (code / 'hostrun/agent_service.py').open('ab') as stream:
            stream.write(b'\n# changed target bytes\n')
        with pytest.raises(InstanceInvalid):
            initialize_target_schema(code, home, manifest_sha256=manifest_sha, snapshot_id=snapshot['snapshot_id'])
        assert database.read_bytes() == before
        return
    if mode == 'failure':
        with pytest.raises(RuntimeError, match='TARGET-SCHEMA-FAILURE'):
            initialize_target_schema(code, home, manifest_sha256=manifest_sha, snapshot_id=snapshot['snapshot_id'])
        with pytest.raises(InstanceInvalid): AgentService(home, token='test')
        assert restore_owned_data(home, snapshot['snapshot_id'])['status'] == 'restored'
        with database_connection(home) as db:
            assert not db.execute("SELECT name FROM sqlite_master WHERE name='target_partial'").fetchall()
        assert AgentService(home, token='test').list_tasks(token='test')[0]['task_id'] == task['task_id']
        return
    result = initialize_target_schema(code, home, manifest_sha256=manifest_sha, snapshot_id=snapshot['snapshot_id'])
    if mode == 'missing':
        assert result == {'status': 'blocked', 'reason': 'target_schema_entry_unavailable'}
        assert database.read_bytes() == before
        return
    assert result['status'] == 'initialized'
    tasks = AgentService(home, token='test').list_tasks(token='test')
    assert tasks[0]['task_id'] == task['task_id']
    assert tasks[0]['session_id'] == '11111111-1111-4111-8111-111111111111'
    assert not (code / 'hostrun/__pycache__').exists()
