"""Public probe CLI evidence survives an unsupported host."""
import json
from pathlib import Path
import platform
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform == "darwin" and platform.machine() == "arm64", reason="unsupported-host contract")
def test_probe_writes_blocked_evidence_on_unsupported_host(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "mac_lifecycle_probe.py"
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    report = json.loads((tmp_path / "mac-lifecycle-report.json").read_text())
    assert report["status"] == "blocked"
    assert "native Apple Silicon" in report["error"]
    assert report["results"] == []
