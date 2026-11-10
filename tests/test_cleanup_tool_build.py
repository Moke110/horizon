"""Public offline builder refuses untrusted runtime inputs before producing a kit."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest


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

    def run(*args, input_text=None):
        result = subprocess.run(['cmd.exe', '/d', '/c', str(output / 'cleanup-tool.cmd'), *args],
                                cwd=tmp_path, env=env, capture_output=True, text=True,
                                input=input_text, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout.splitlines()[-1])

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
    upgrade_script = "import json,sys;from hostrun.desktop_upgrade import upgrade;print(json.dumps(upgrade(sys.argv[1],sys.argv[2],from_source=sys.argv[3],sha256=sys.argv[4])))"
    assert python(upgrade_script, code, home, second['artifact']['path'], second['artifact']['sha256'])['status'] == 'ok'
    before = (home / 'version.json').read_bytes()
    original_id = (code / 'README.md').stat().st_ino
    third = target_package('third', target.replace("db.execute('BEGIN IMMEDIATE')", "__import__('os')._exit(77)", 1), '0.3.0')
    python(upgrade_script, code, home, third['artifact']['path'], third['artifact']['sha256'], expected=77)
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
