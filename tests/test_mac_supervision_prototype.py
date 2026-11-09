import json
from pathlib import Path
import subprocess
import sys


def test_unsupported_host_writes_report(tmp_path):
    if sys.platform == 'darwin':
        return
    script = Path(__file__).resolve().parents[1] / 'scripts/mac_supervision_prototype/run.py'
    result = subprocess.run([sys.executable, str(script), '--output', str(tmp_path / 'report')], capture_output=True, timeout=15)
    assert result.returncode == 1
    report = json.loads((tmp_path / 'report/report.json').read_text())
    assert report['status'] == 'blocked'
    assert report['results'] == []
