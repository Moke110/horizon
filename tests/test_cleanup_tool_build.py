"""Public offline builder refuses untrusted runtime inputs before producing a kit."""
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import pytest


@pytest.mark.skipif(os.name != 'nt' or not os.environ.get('HORIZON_CLEANUP_TEST_INPUTS'),
                    reason='requires explicit pinned offline Windows runtime inputs')
@pytest.mark.parametrize('entry', ['tool', 'package', 'console', 'pi', 'shortcut', 'collector'])
def test_independent_tool_entry_does_not_create_unowned_bytecode(tmp_path, entry):
    output = tmp_path / 'tool'
    built = subprocess.run([sys.executable, '-X', 'utf8', 'scripts/prepare_cleanup_tool.py',
                            '--inputs', os.environ['HORIZON_CLEANUP_TEST_INPUTS'], '--output', str(output)],
                           capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr
    (tmp_path / 'local').mkdir()
    env = dict({key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE') if key in os.environ},
               PATH=os.environ['SystemRoot'] + r'\System32',
               LOCALAPPDATA=str(tmp_path / 'local'), PYTHONHOME='invalid-python',
               PYTHONPATH='invalid-code', PYTHONDONTWRITEBYTECODE='')
    command = [str(output / 'cleanup-tool.cmd'), 'prepare']
    if entry != 'tool':
        from hostrun.launcher import write_wrapper, console_script_text
        repository = Path(__file__).resolve().parents[1]
        shutil.copytree(repository / 'skills/horizon-run', output / 'skills/horizon-run')
        for package in repository.iterdir():
            if (package / '__init__.py').is_file():
                shutil.copytree(package, output / package.name,
                                dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
        wrapper = output / 'bin/horizon.cmd'
        write_wrapper(wrapper)
        if entry == 'console':
            wrapper = output / 'console.cmd'
            wrapper.write_text(console_script_text(output, tmp_path / 'archive', str(output / 'runtime/python.exe')))
        command = [str(wrapper), '--help']
    if entry == 'pi':
        _run_managed_pi_without_bytecode(output, tmp_path, env)
        assert not list(output.rglob('*.pyc'))
        return
    if entry == 'shortcut':
        scripts = output / 'scripts'
        scripts.mkdir()
        shutil.copyfile(repository / 'scripts/install-desktop.ps1', scripts / 'install-desktop.ps1')
        # A bounded actor observes the actual .lnk interpreter startup, without GUI claims.
        (output / 'hostrun/desktop.py').write_text(
            "import common\nfrom pathlib import Path\nPath('shortcut-started').write_text('ok')\n")
        powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        installed = subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                    '-File', str(scripts / 'install-desktop.ps1')],
                                   cwd=output, env=env, capture_output=True, text=True, timeout=30)
        assert installed.returncode == 0, installed.stdout + installed.stderr
        started = subprocess.run([str(powershell), '-NoProfile', '-Command',
                                  "$link=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path (Get-Location) 'Horizon.lnk')); "
                                  "$p=Start-Process -FilePath $link.TargetPath -ArgumentList $link.Arguments -WorkingDirectory $link.WorkingDirectory -WindowStyle Hidden -PassThru; "
                                  "if(-not $p.WaitForExit(10000)){throw 'Shortcut actor timeout'}; exit $p.ExitCode"],
                                 cwd=output, env=env, capture_output=True, text=True, timeout=30)
        assert started.returncode == 0, started.stderr
        assert (output / 'shortcut-started').read_text() == 'ok'
        assert not list(output.rglob('*.pyc'))
        return
    if entry == 'collector':
        installed = tmp_path / 'local/Programs/Horizon'
        installed.parent.mkdir()
        output.rename(installed)
        output = installed
        collector = tmp_path / '3-collect.cmd'
        shutil.copyfile(repository / 'installer/acceptance/3-collect.cmd', collector)
        env['HORIZON_DATA_HOME'] = str(tmp_path / 'home')
        command = [str(collector), '--quiet']
    result = subprocess.run(['cmd.exe', '/d', '/c', *command],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    if entry == 'tool':
        assert json.loads(result.stdout)['status'] == 'prepared'
    elif entry == 'collector':
        assert (tmp_path / 'report/application').is_dir()
    else:
        assert 'usage:' in result.stdout
    assert not list(output.rglob('*.pyc'))


def _run_managed_pi_without_bytecode(output, tmp_path, env):
    import threading
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            chunks = [({'role': 'assistant', 'content': 'Ready'}, None), ({}, 'stop')]
            if len(requests) == 1:
                chunks = [({'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'bytecode-test', 'type': 'function',
                    'function': {'name': 'horizon_cli', 'arguments': json.dumps({'module': 'archive.cli', 'args': ['--help']})}}]}, None), ({}, 'tool_calls')]
            for delta, finish in chunks:
                chunk = {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'test-model',
                         'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    python = output / 'runtime/python.exe'
    try:
        setup = '''
import json,sys
from hostrun.cleanup import control_directory,prepare_instance,initialize_layout
from hostrun.embedded_agent import configure
control=control_directory();control.mkdir(parents=True)
instance=prepare_instance(control);initialize_layout(control,instance['instance_id'])
configure(instance['home'],provider='horizon-test',model='test-model',api_key='BYTECODE_TEST_SECRET',base_url=sys.argv[1])
print(json.dumps({'home':str(instance['home'])}))
'''
        prepared = subprocess.run([str(python), '-B', '-X', 'utf8', '-c', setup, f'http://127.0.0.1:{server.server_port}/v1'],
                                  cwd=output, env=env, capture_output=True, text=True, timeout=30)
        assert prepared.returncode == 0, prepared.stderr
        home = json.loads(prepared.stdout)['home']
        session = str(uuid.uuid4())
        repository = Path(__file__).resolve().parents[1]
        for prompt in ('Remember bytecode continuity', 'Continue'):
            result = subprocess.run([shutil.which('node'), str(repository / 'agent/dist/main.js'), '--home', home,
                                     '--task', prompt, '--kind', 'agent', '--session-id', session],
                                    cwd=output, env={**env, 'HORIZON_PYTHON': str(python)},
                                    capture_output=True, text=True, encoding='utf-8', timeout=30)
            assert result.returncode == 0, result.stdout + result.stderr
            assert 'BYTECODE_TEST_SECRET' not in result.stdout + result.stderr
        assert 'Remember bytecode continuity' in json.dumps(requests[-1])
        tool_messages = [m for m in requests[1]['messages'] if m['role'] == 'tool']
        assert tool_messages and 'usage:' in json.dumps(tool_messages)
    finally:
        server.shutdown(); server.server_close(); worker.join()


def test_builder_rejects_tampered_pinned_runtime_before_output(tmp_path):
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    (inputs / 'python-3.13.7-embed-amd64.zip').write_bytes(b'not the pinned interpreter')
    output = tmp_path / 'tool'
    result = subprocess.run([sys.executable, '-X', 'utf8', 'scripts/prepare_cleanup_tool.py',
                             '--inputs', str(inputs), '--output', str(output)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert json.loads(result.stdout)['reason'] == 'input_digest_mismatch'
    assert not output.exists()


@pytest.mark.skipif(os.name != 'nt' or not os.environ.get('HORIZON_BYTECODE_PACKAGE'),
                    reason='requires an explicit complete development distribution')
def test_runtime_smoke_does_not_modify_package_with_bytecode(tmp_path):
    from installer.smoke import check
    package = tmp_path / 'package'
    shutil.copytree(os.environ['HORIZON_BYTECODE_PACKAGE'], package,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    assert check(package)['ok'] is True
    assert not list(package.rglob('*.pyc'))


@pytest.mark.skipif(os.name != 'nt' or not os.environ.get('HORIZON_CLEANUP_TEST_INPUTS'),
                    reason='requires explicit pinned offline Windows runtime inputs')
def test_bundled_tool_runs_offline_with_minimal_path(tmp_path):
    output = tmp_path / 'independent-tool'
    built = subprocess.run([sys.executable, '-X', 'utf8', 'scripts/prepare_cleanup_tool.py',
                            '--inputs', os.environ['HORIZON_CLEANUP_TEST_INPUTS'], '--output', str(output)],
                           capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr
    manifest = json.loads((output / 'tool-manifest.json').read_text())
    assert manifest['scope_complete'] is False
    assert 'runtime/python.exe' in manifest['files']
    local = tmp_path / 'local'
    local.mkdir()
    env = dict(os.environ, PATH=os.environ['SystemRoot'] + r'\System32',
               LOCALAPPDATA=str(local), PYTHONHOME='invalid-developer-python', PYTHONPATH='invalid-developer-code')

    def run(*args, input_text=None, expected=0):
        result = subprocess.run(['cmd.exe', '/d', '/c', str(output / 'cleanup-tool.cmd'), *args],
                                cwd=tmp_path, env=env, capture_output=True, text=True,
                                input=input_text, timeout=30)
        assert result.returncode == expected, result.stdout + result.stderr
        return json.loads(result.stdout.splitlines()[-1]) if result.stdout else None

    instance = run('prepare')
    identity = instance['instance_id']
    from test_dist_install import _build
    package = _build(tmp_path / 'fixture')
    digest = json.loads(Path(package['digest']).read_text())['artifact']['sha256']
    assert run('install', identity, '--package', str(package['artifact']['path']), '--sha256', digest)['status'] == 'ok'
    def python(script, *args, expected=0):
        result = subprocess.run([str(output / 'runtime/python.exe'), '-X', 'utf8', '-c', script, *map(str, args)],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == expected, result.stdout + result.stderr
        return json.loads(result.stdout) if result.stdout else None
    home, code = Path(instance['home']), Path(instance['code']) / 'current'
    python("import sys;from hostrun.agent_service import AgentService;AgentService(sys.argv[1],token='test').create_task({'schema_version':1,'operation_id':'seed','title':'preserved'},token='test')", home)
    from test_dist_install import _write_tree, PACKAGE_FILES, _host_compat
    from hostrun.distribution import build
    target = (Path(__file__).resolve().parents[1] / 'hostrun/agent_service.py').read_text(encoding='utf-8')
    def target_package(name, module, version):
        source = _write_tree(tmp_path / (name + '-source'), {**PACKAGE_FILES, 'hostrun/agent_service.py': module})
        return build(source=source, out=tmp_path / name, version=version, channel='stable', commit='test', compat=_host_compat())
    second = target_package('second', target, '0.2.0')
    registration = Path(instance['registration'])
    unchanged = registration.read_bytes()
    original_manifest = (code / 'manifest.json').read_bytes()
    import hashlib
    def instance_files():
        return {path.relative_to(Path(instance['root'])).as_posix():
                (path.stat().st_ino, hashlib.sha256(path.read_bytes()).hexdigest())
                for path in Path(instance['root']).rglob('*') if path.is_file()}
    before_files = instance_files()
    assert run('upgrade', identity, '--package', str(second['artifact']['path']), '--sha256', '0' * 64, expected=2)['status'] == 'blocked'
    assert run('upgrade', '../foreign', '--package', str(second['artifact']['path']), '--sha256', second['artifact']['sha256'], expected=2)['status'] == 'blocked'
    assert registration.read_bytes() == unchanged
    assert instance_files() == before_files
    assert not (registration.parent.parent / 'foreign').exists()
    assert (code / 'manifest.json').read_bytes() == original_manifest
    assert not (home / 'version.json').exists()
    upgraded = run('upgrade', identity, '--package', str(second['artifact']['path']), '--sha256', second['artifact']['sha256'])
    assert upgraded['status'] == 'ok' and upgraded['scope_complete'] is False
    before = (home / 'version.json').read_bytes()
    original_id = (code / 'README.md').stat().st_ino
    third = target_package('third', target.replace("db.execute('BEGIN IMMEDIATE')", "__import__('os')._exit(77)", 1), '0.3.0')
    run('upgrade', identity, '--package', str(third['artifact']['path']), '--sha256', third['artifact']['sha256'], expected=77)
    pending = registration.read_bytes()
    refused = run('upgrade', identity, '--package', str(third['artifact']['path']), '--sha256', third['artifact']['sha256'], expected=2)
    assert refused['reason'] == 'managed_upgrade_recovery_pending'
    assert registration.read_bytes() == pending
    assert run('recover', identity, '--sha256', third['artifact']['sha256'])['status'] == 'restored'
    assert (home / 'version.json').read_bytes() == before
    assert (code / 'README.md').stat().st_ino == original_id
    tasks = python("import json,sys;from hostrun.agent_service import AgentService;print(json.dumps(AgentService(sys.argv[1],token='test').list_tasks(token='test')))", home)
    assert len(tasks) == 1 and tasks[0]['title'] == 'preserved'
    # Corrupt installed code: the independent report/cleanup must still run.
    (Path(instance['code']) / 'current/README.md').write_text('broken installation')
    report = run('report', identity, '--output', str(tmp_path / 'reports'))
    assert Path(report['report']).is_dir()
    args = ('cleanup', identity, '--output', str(tmp_path / 'reports'), '--interactive')
    assert run(*args, input_text='cancel\n')['status'] == 'cancelled'
    assert Path(instance['home']).is_dir()
    assert run(*args, input_text=identity + '\n')['status'] == 'cleaned'
    assert Path(report['report']).is_dir()
