"""The independent tool uses its own code, never the installed application's code."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows cleanup tool')


@pytest.fixture
def cli(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    kit = tmp_path / 'kit'
    shutil.copytree(repository / 'hostrun', kit / 'hostrun', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(repository / 'common.py', kit / 'common.py')
    local = tmp_path / 'local'
    local.mkdir()
    env = dict(os.environ, LOCALAPPDATA=str(local), PYTHONPATH=str(kit))

    def run(*arguments, expected=0):
        result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'hostrun.cleanup_cli', *arguments],
                                cwd=kit, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == expected, result.stderr + result.stdout
        return json.loads(result.stdout.splitlines()[-1])
    return run


def test_cli_prepare_preview_confirmation_and_repeated_cleanup(tmp_path, cli):
    run = cli
    prepared = run('prepare')
    identity = prepared['instance_id']
    home = Path(prepared['home'])
    assert home.is_dir()
    unknown = home / 'user-original.txt'
    unknown.write_text('preserve me', encoding='utf-8')
    plan = run('preview', identity)
    assert plan['status'] == 'confirmation_required'
    assert plan['scope_complete'] is False
    report = tmp_path / 'reports'
    assert run('cleanup', identity, '--output', str(report))['status'] == 'confirmation_required'
    assert not report.exists()
    assert unknown.read_text(encoding='utf-8') == 'preserve me'
    from test_dist_install import _build
    package = _build(tmp_path / 'package')
    digest = json.loads(Path(package['digest']).read_text())['artifact']['sha256']
    assert run('install', identity, '--package', str(package['artifact']['path']), '--sha256', digest)['status'] == 'ok'
    plan = run('preview', identity)
    assert str(Path(prepared['code']) / 'current/README.md') in plan['registered_file_candidates']
    assert 'delete' not in plan  # Containers are not promises of recursive deletion.
    assert str(unknown) not in plan['registered_file_candidates']
    collected = run('report', identity, '--output', str(report))
    assert (Path(collected['report']) / 'instance.json').exists()
    result = run('cleanup', identity, '--output', str(report), '--mode', 'keep-data', '--confirm', identity)
    assert result['status'] == 'uninstalled'
    assert unknown.exists()
    unknown.unlink()  # Only the test user removes their original file.
    assert run('cleanup', identity, '--output', str(report), '--confirm', identity)['status'] == 'cleaned'
    assert run('cleanup', identity, '--output', str(report), '--confirm', identity)['status'] == 'cleaned'
    assert Path(collected['report']).is_dir()


def test_cli_wrong_confirmation_and_unknown_files_are_preserved(tmp_path, cli):
    prepared = cli('prepare')
    identity = prepared['instance_id']
    original = Path(prepared['home']) / 'private.txt'
    original.write_text('secret-content', encoding='utf-8')
    output = tmp_path / 'reports'
    result = cli('cleanup', identity, '--output', str(output), '--confirm', 'wrong', expected=2)
    assert result['status'] == 'blocked'
    assert not output.exists()
    result = cli('cleanup', identity, '--output', str(output), '--confirm', identity, expected=2)
    assert result['status'] == 'partial'
    assert original.read_text(encoding='utf-8') == 'secret-content'
    assert 'secret-content' not in json.dumps(result)


def test_cli_report_rejects_instance_destination(tmp_path, cli):
    prepared = cli('prepare')
    result = cli('report', prepared['instance_id'], '--output', str(Path(prepared['home']) / 'report'), expected=2)
    assert result['status'] == 'blocked'
    assert not (Path(prepared['home']) / 'report').exists()
