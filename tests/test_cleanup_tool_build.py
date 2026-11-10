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
    # Corrupt installed code: the independent report/cleanup must still run.
    (Path(instance['code']) / 'current/README.md').write_text('broken installation')
    report = run('report', identity, '--output', str(tmp_path / 'reports'))
    assert Path(report['report']).is_dir()
    args = ('cleanup', identity, '--output', str(tmp_path / 'reports'), '--interactive')
    assert run(*args, input_text='cancel\n')['status'] == 'cancelled'
    assert Path(instance['home']).is_dir()
    assert run(*args, input_text=identity + '\n')['status'] == 'cleaned'
    assert Path(report['report']).is_dir()
