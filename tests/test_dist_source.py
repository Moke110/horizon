"""Source installations: truthful lock, report, verify and rollback source.

Ticket #97 separates the two installation kinds.  A package install keeps the
full package lock (#94) and report block (#96); a source installation records
only git/source evidence and never invents a version, channel or artifact
digest.  ``install report`` and ``install verify`` must tell the two apart.

Every test works through the public seams: the ``install workspace`` CLI
branch (with ``acquire`` stubbed so no git process runs), ``install.report``,
``dist.verify_install`` and ``dist.rollback``.  Nothing reaches the network.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime
from pathlib import Path

from conftest import cli_locations
from hostrun import cli, dist, distribution, install, workspace

PACKAGE_FILES = {
    "common.py": "RUNTIME = True\n",
    "requirements.txt": "PyYAML>=6.0\n",
    "README.md": "# fixture horizon\n",
    "LICENSE": "MIT\n",
    "hostrun/__init__.py": "",
    "hostrun/cli.py": "CLI = 1\n",
    "hostrun/base.py": "VALUE = 1\n",
    "skills/horizon-run/SKILL.md": "---\nname: horizon-run\n---\n",
    "contracts/README.md": "contracts\n",
    "docs/adr/0010-distribution.md": "# decision\n",
}

REPO_URL = "https://github.com/Moke110/horizon.git"


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _host_compat() -> dict[str, str]:
    return {
        "os": distribution.normalize_os(sys.platform),
        "arch": distribution.normalize_arch(platform.machine()),
        "dsh": "*",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def _build(tmp_path: Path, *, name: str, version: str) -> dict:
    source = _write_tree(tmp_path / f"{name}-src", PACKAGE_FILES)
    return distribution.build(
        source=source,
        out=tmp_path / f"{name}-out",
        version=version,
        channel="stable",
        commit="deadbeefcafe",
        compat=_host_compat(),
    )


def _seed_checkout(repo: Path) -> Path:
    """A source checkout with the files installation must read."""
    (repo / ".git").mkdir(parents=True)
    for rel in (
        "common.py",
        "requirements.txt",
        "contracts/README.md",
        "skills/horizon-run/SKILL.md",
        "skills/horizon-run/references/installation.md",
        "skills/horizon-run/references/workflow.md",
    ):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {rel}\n", encoding="utf-8")
    return repo


def _acquire_result(workspace_root: Path, repo: Path, home: Path, *, commit="abc123", dirty=False) -> dict:
    return {
        "schema_version": 1,
        "ok": True,
        "dry_run": False,
        "action": "reuse",
        "layout_valid": True,
        "root": str(workspace_root),
        "repo": str(repo),
        "home": str(home),
        "repo_url": REPO_URL,
        "origin": REPO_URL,
        "commit": commit,
        "dirty": dirty,
        "data_root_created": False,
        "data_root_entries": 0,
        "recovered_partial_clone": None,
        "existing": {"state": "reusable"},
    }


def _source_lock(**overrides) -> dict:
    lock = {
        "schema_version": 1,
        "distribution": "source",
        "commit": "abc123",
        "dirty": False,
        "source_url": REPO_URL,
        "installed_at": "2026-10-31T10:00:00+08:00",
        "requires": {},
        "skill_digest": "",
    }
    lock.update(overrides)
    return lock


def _cli(capsys, home: Path, *args: str):
    exit_code = cli.main([*cli_locations(home), *args])
    captured = capsys.readouterr()
    raw = captured.out.strip() or captured.err.strip()
    return exit_code, json.loads(raw), captured


def _install(code: Path, result: dict) -> dict:
    return dist.install_package(code, from_source=str(result["artifact"]["path"]))


# --------------------------------------------------------- source install lock
def test_workspace_install_records_a_truthful_source_lock(tmp_path, capsys, monkeypatch):
    workspace_root = tmp_path / "ws"
    repo = _seed_checkout(workspace_root / "horizon")
    home = workspace_root / "horizon-archive"
    home.mkdir(parents=True)
    result = _acquire_result(workspace_root, repo, home)
    monkeypatch.setattr(cli.workspace_mod, "acquire", lambda *a, **k: result)
    cli_home = tmp_path / "cli-code" / ".horizon"

    exit_code, payload, captured = _cli(
        capsys, cli_home, "install", "workspace", "--root", str(workspace_root)
    )

    assert exit_code == 0
    assert "Traceback" not in captured.err
    assert payload["source_lock"]["distribution"] == "source"
    lock_path = repo / ".horizon" / "dist.lock.json"
    assert payload["source_lock"]["lock"] == str(lock_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["schema_version"] == 1
    assert lock["distribution"] == "source"
    assert lock["commit"] == "abc123"
    assert lock["dirty"] is False
    assert lock["source_url"] == REPO_URL
    assert lock["requires"] == {}
    assert lock["skill_digest"] == ""
    datetime.fromisoformat(lock["installed_at"])
    # A source install must never invent package evidence.
    for key in (
        "version",
        "channel",
        "artifact_sha256",
        "package_file_count",
        "manifest_sha256",
    ):
        assert key not in lock, key


def test_workspace_install_keeps_the_source_installed_at_on_a_rerun(tmp_path, capsys, monkeypatch):
    workspace_root = tmp_path / "ws"
    repo = _seed_checkout(workspace_root / "horizon")
    home = workspace_root / "horizon-archive"
    home.mkdir(parents=True)
    dist.write_lock(repo, _source_lock(installed_at="2026-01-01T00:00:00+08:00"))
    result = _acquire_result(workspace_root, repo, home, commit="def456")
    monkeypatch.setattr(cli.workspace_mod, "acquire", lambda *a, **k: result)
    cli_home = tmp_path / "cli-code" / ".horizon"

    exit_code, _, _ = _cli(
        capsys, cli_home, "install", "workspace", "--root", str(workspace_root)
    )

    assert exit_code == 0
    lock = json.loads(
        (repo / ".horizon" / "dist.lock.json").read_text(encoding="utf-8")
    )
    assert lock["commit"] == "def456"
    assert lock["installed_at"] == "2026-01-01T00:00:00+08:00"


# ---------------------------------------------------------------- install report
def test_report_for_a_source_lock_is_truthful(tmp_path):
    code = tmp_path / "code"
    home = code / ".horizon"
    home.mkdir(parents=True)
    dist.write_lock(code, _source_lock(commit="abc123", dirty=True))

    report = install.report(home)

    release = report["release"]
    assert release["distribution"] == "source"
    assert release["version"] is None
    assert release["channel"] == "source"
    assert release["source"] == REPO_URL
    assert release["installed_at"] == "2026-10-31T10:00:00+08:00"
    assert release["commit"] == "abc123"
    assert release["dirty"] is True
    assert release["retained"] == []
    assert release["lock_path"] == str(code / ".horizon" / "dist.lock.json")
    assert "recorded" not in release


def test_report_without_a_lock_keeps_the_unrecorded_source_shape(tmp_path):
    home = tmp_path / "code" / ".horizon"
    home.mkdir(parents=True)

    release = install.report(home)["release"]

    assert release["distribution"] == "source"
    assert release["recorded"] is False
    assert release["detail"]
    assert "version" not in release


def test_report_for_a_package_install_does_not_gain_source_fields(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    code = tmp_path / "code"
    assert _install(code, v1)["ok"] is True

    release = install.report(code / ".horizon")["release"]

    assert release["distribution"] == "package"
    assert release["version"] == "1.0.0"
    assert "commit" not in release
    assert "dirty" not in release


# ---------------------------------------------------------------- install verify
def test_verify_reports_a_source_install_instead_of_a_mismatch(tmp_path):
    code = tmp_path / "code"
    (code / ".horizon").mkdir(parents=True)
    dist.write_lock(code, _source_lock())

    payload = dist.verify_install(code)

    assert payload["ok"] is True
    assert payload["status"] == "source_install"
    assert payload["distribution"] == "source"
    assert payload["commit"] == "abc123"
    assert payload["lock"] == str(code / ".horizon" / "dist.lock.json")
    assert "发行包清单" in payload["note"]


def test_cli_verify_of_a_source_install_exits_zero(tmp_path, capsys):
    code = tmp_path / "cli-code"
    (code / ".horizon").mkdir(parents=True)
    dist.write_lock(code, _source_lock())
    home = code / ".horizon"

    exit_code, payload, captured = _cli(capsys, home, "install", "verify")

    assert exit_code == 0
    assert captured.err == ""
    assert payload["status"] == "source_install"


# ------------------------------------------------------- rollback source_url fix
def test_rollback_restores_the_retained_release_source_url_and_installed_at(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    for result in (v1, v2):
        assert _install(code, result)["ok"] is True
    before = json.loads(
        (code / ".horizon" / "dist.lock.json").read_text(encoding="utf-8")
    )

    payload = dist.rollback(code)

    assert payload["ok"] is True
    lock = json.loads(
        (code / ".horizon" / "dist.lock.json").read_text(encoding="utf-8")
    )
    assert lock["source_url"] == Path(v1["artifact"]["path"]).resolve().as_uri()
    assert lock["source_url"] != Path(v2["artifact"]["path"]).resolve().as_uri()
    assert lock["installed_at"] == before["installed_at"]

    release = install.report(code / ".horizon")["release"]
    assert release["version"] == "1.0.0"
    assert release["source"] == Path(v1["artifact"]["path"]).resolve().as_uri()


def test_install_lock_schema_documents_both_distributions():
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "contracts" / "install-lock.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is True
    assert schema["properties"]["distribution"]["enum"] == ["package", "source"]
    assert schema["properties"]["channel"]["enum"] == ["stable", "prerelease"]
    assert set(schema["required"]) == {
        "schema_version",
        "distribution",
        "source_url",
        "installed_at",
        "requires",
        "skill_digest",
    }
    assert {"commit", "dirty"} <= set(schema["properties"])
    branches = json.dumps(schema, ensure_ascii=False)
    assert "artifact_sha256" in branches
    assert "package_file_count" in branches
    assert "manifest_sha256" in branches
    # Package-only fields stay package requirements; the source branch requires
    # the git evidence instead and must not demand invented package fields.
    source_properties = schema["properties"]
    assert "version" not in schema["required"]
    assert "channel" not in schema["required"]
    assert "artifact_sha256" not in schema["required"]
    assert source_properties["commit"]["type"] == "string"
    assert source_properties["dirty"]["type"] == "boolean"
