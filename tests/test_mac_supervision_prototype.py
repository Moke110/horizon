import json
from pathlib import Path
import subprocess
import sys
import pytest


def test_unsupported_host_writes_report(tmp_path):
    if sys.platform == 'darwin':
        pytest.skip('unsupported-host test')
    script = Path(__file__).resolve().parents[1] / 'scripts/mac_supervision_prototype/run.py'
    result = subprocess.run([sys.executable, str(script), '--output', str(tmp_path / 'report')], capture_output=True, timeout=15)
    assert result.returncode == 1
    report = json.loads((tmp_path / 'report/report.json').read_text())
    assert report['status'] == 'blocked'
    assert report['results'] == []


def test_existing_output_is_preserved(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'scripts/mac_supervision_prototype/run.py'
    marker = tmp_path / 'report.json'
    marker.write_text('keep')
    result = subprocess.run([sys.executable, str(script), '--output', str(tmp_path)], capture_output=True, timeout=15)
    assert result.returncode != 0
    assert marker.read_text() == 'keep'
