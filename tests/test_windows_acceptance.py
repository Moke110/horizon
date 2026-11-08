"""The no-runtime collector must never bypass the application's privacy filter."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows batch collector')
def test_collector_without_runtime_does_not_copy_raw_sensitive_events(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'installer/acceptance/3-collect.cmd'
    shutil.copyfile(source, tmp_path / source.name)
    diagnostics = tmp_path / 'home/diagnostics'
    diagnostics.mkdir(parents=True)
    (diagnostics / 'events.jsonl').write_text(
        '{"schema_version":1,"event":"error","message":"private-body-should-not-copy","api_key":"secret-should-not-copy"}\n',
        encoding='utf-8',
    )
    env = dict(os.environ, HORIZON_DATA_HOME=str(diagnostics.parent), LOCALAPPDATA=str(tmp_path / 'no-runtime'),
               PATH=os.environ['SystemRoot'] + r'\System32;' + os.environ['SystemRoot'])
    result = subprocess.run(['cmd.exe', '/d', '/c', '3-collect.cmd', '--quiet'],
                            cwd=tmp_path, env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    report = tmp_path / 'report'
    assert (report / 'SUMMARY.txt').exists()
    assert not (report / 'application-events').exists()
    combined = ''.join(p.read_text(encoding='utf-8-sig') for p in report.rglob('*') if p.is_file())
    assert 'private-body-should-not-copy' not in combined
    assert 'secret-should-not-copy' not in combined
