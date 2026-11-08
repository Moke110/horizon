"""Destructive install/rollback operations are marked and recoverable (#92)."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

from hostrun import dist, distribution

PACKAGE_FILES = {
    "common.py": "RUNTIME = True\n",
    "requirements.txt": "PyYAML>=6.0\n",
    "README.md": "# fixture horizon\n",
    "LICENSE": "MIT\n",
    "hostrun/__init__.py": "",
    "hostrun/cli.py": "CLI = 1\n",
    "skills/horizon-run/SKILL.md": "---\nname: horizon-run\n---\n",
    "contracts/README.md": "contracts\n",
}


def _host_compat() -> dict[str, str]:
    return {
        "os": distribution.normalize_os(sys.platform),
        "arch": distribution.normalize_arch(platform.machine()),
        "dsh": "*",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def _build(tmp_path: Path, *, name: str, version: str) -> dict:
    source = tmp_path / f"{name}-src"
    for rel, text in PACKAGE_FILES.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return distribution.build(
        source=source,
        out=tmp_path / f"{name}-out",
        version=version,
        channel="stable",
        commit="deadbeefcafe",
        compat=_host_compat(),
    )


def _install(code: Path, result: dict) -> dict:
    return dist.install_package(
        code, from_source=str(result["artifact"]["path"])
    )


def _lock(code: Path) -> dict:
    return json.loads((code / ".horizon" / "dist.lock.json").read_text(encoding="utf-8"))


def _marker(code: Path) -> Path:
    return code / ".horizon" / "dist.pending.json"


def _fail_on_staging_copy(monkeypatch):
    original = dist.shutil.copyfile

    def copyfile(source, target):
        if "staging" in Path(source).parts:
            raise OSError("simulated mid-apply failure")
        return original(source, target)

    monkeypatch.setattr(dist.shutil, "copyfile", copyfile)


def _fail_on_retained_rollback_copy(monkeypatch, version: str):
    # Rollback now skips files whose bytes already match (so an in-place
    # upgrade never rewrites the running bundled runtime), so a pure
    # ``shutil.copyfile`` patch would not fire for identical fixture files.
    # Patch the copy decision point instead: it runs for every target file.
    original = dist._copy_unless_identical

    def copy(source, target):
        if "releases" in Path(source).parts and version in Path(source).parts:
            raise OSError("simulated mid-rollback failure")
        return original(source, target)

    monkeypatch.setattr(dist, "_copy_unless_identical", copy)


def test_fresh_install_failure_removes_applied_files_and_marker(tmp_path, monkeypatch):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    code = tmp_path / "code"
    _fail_on_staging_copy(monkeypatch)

    payload = _install(code, v1)

    assert payload["status"] == "blocked"
    assert payload["restored"] is True
    assert payload["recovery_incomplete"] is False
    assert not _marker(code).exists()
    assert not (code / "manifest.json").exists()
    assert not (code / "common.py").exists()
    assert not (code / ".horizon" / "dist.lock.json").exists()


def test_fresh_install_failure_after_lock_write_removes_lock_and_current(tmp_path, monkeypatch):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    code = tmp_path / "code"
    monkeypatch.setattr(
        dist,
        "_write_current",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("current denied")),
    )

    payload = _install(code, v1)

    assert payload["status"] == "blocked"
    assert payload["restored"] is True
    assert not _marker(code).exists()
    assert not (code / ".horizon" / "dist.lock.json").exists()
    assert not (code / ".horizon" / "current").exists()
    assert not (code / "manifest.json").exists()


def test_upgrade_failure_restores_retained_tree_lock_and_current(tmp_path, monkeypatch):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    assert _install(code, v1)["ok"] is True
    before_lock = (code / ".horizon" / "dist.lock.json").read_bytes()
    before_current = (code / ".horizon" / "current").read_bytes()
    before_common = (code / "common.py").read_bytes()
    _fail_on_staging_copy(monkeypatch)

    payload = _install(code, v2)

    assert payload["status"] == "blocked"
    assert payload["restored"] is True
    assert payload["recovery"]["restored_version"] == "1.0.0"
    assert not _marker(code).exists()
    assert (code / ".horizon" / "dist.lock.json").read_bytes() == before_lock
    assert (code / ".horizon" / "current").read_bytes() == before_current
    assert (code / "common.py").read_bytes() == before_common
    assert dist.verify_install(code)["status"] == "verified"
    assert _lock(code)["version"] == "1.0.0"


def test_rollback_failure_restores_the_outgoing_tree(tmp_path, monkeypatch):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    assert _install(code, v1)["ok"] is True
    assert _install(code, v2)["ok"] is True
    before_lock = (code / ".horizon" / "dist.lock.json").read_bytes()
    before_current = (code / ".horizon" / "current").read_bytes()
    before_common = (code / "common.py").read_bytes()
    _fail_on_retained_rollback_copy(monkeypatch, "1.0.0")

    payload = dist.rollback(code)

    assert payload["status"] == "blocked"
    assert payload["restored"] is True
    assert not _marker(code).exists()
    assert (code / ".horizon" / "dist.lock.json").read_bytes() == before_lock
    assert (code / ".horizon" / "current").read_bytes() == before_current
    assert (code / "common.py").read_bytes() == before_common
    assert dist.verify_install(code)["status"] == "verified"
    assert _lock(code)["version"] == "2.0.0"


def test_successful_install_upgrade_and_rollback_leave_no_marker(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"

    assert _install(code, v1)["ok"] is True
    assert not _marker(code).exists()
    assert _install(code, v2)["ok"] is True
    assert not _marker(code).exists()
    assert dist.rollback(code)["ok"] is True
    assert not _marker(code).exists()


def test_a_stale_marker_blocks_a_later_install_with_guidance(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    code = tmp_path / "code"
    marker = _marker(code)
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation": "upgrade",
                "target_version": "2.0.0",
                "started_at": "2026-10-31T10:00:00+08:00",
                "pre_operation_version": "1.0.0",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = _install(code, v1)

    assert payload["status"] == "blocked"
    assert payload["reason"] == "pending_operation_incomplete"
    assert payload["pending_marker"] == str(marker)
    assert payload["guidance"]
    assert not (code / ".horizon" / "dist.lock.json").exists()
    assert not (code / "manifest.json").exists()
    assert marker.exists()


def test_failed_recovery_keeps_the_marker_and_reports_incomplete(tmp_path, monkeypatch):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    assert _install(code, v1)["ok"] is True
    _fail_on_staging_copy(monkeypatch)
    monkeypatch.setattr(
        dist,
        "_restore_pre_operation",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("restore denied")),
    )

    payload = _install(code, v2)

    assert payload["status"] == "blocked"
    assert payload["restored"] is False
    assert payload["recovery_incomplete"] is True
    assert "回退失败" in payload["recovery_note"]
    assert _marker(code).exists()
