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

    def run(*arguments, expected=0, input_text=None):
        result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'hostrun.cleanup_cli', *arguments],
                                cwd=kit, env=env, capture_output=True, text=True, timeout=30, input=input_text)
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
    assert result['postverification']['status'] == 'verified'
    assert result['next_action'] == 'review_process_evidence'
    assert unknown.exists()
    unknown.unlink()  # Only the test user removes their original file.
    repeated = run('cleanup', identity, '--output', str(report), '--confirm', identity)
    assert repeated['status'] == 'cleaned'
    assert repeated['scope_complete'] is False
    assert json.loads((Path(repeated['report']) / 'cleanup.json').read_text()) == repeated
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


def test_cli_human_confirmation_cancels_then_accepts_exact_id(tmp_path, cli):
    instance = cli('prepare')
    identity = instance['instance_id']
    output = tmp_path / 'reports'
    arguments = ('cleanup', identity, '--output', str(output), '--interactive')
    assert cli(*arguments, input_text='no\n')['status'] == 'cancelled'
    assert Path(instance['home']).exists()
    assert not output.exists()
    assert cli(*arguments, input_text=identity + '\n')['status'] == 'cleaned'
