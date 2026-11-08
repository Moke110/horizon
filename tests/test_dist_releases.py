"""Retention, rollback and the install report's release block.

Ticket #96 builds on the #94 install seam: the active tree stays in the code
root, one previous release is retained under ``<code>/.horizon/releases/<v>``
and ``<code>/.horizon/current`` names the active version.  Every test builds
real distribution packages and works only through the public install/rollback
report seams; nothing reaches the network.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path

import pytest

from conftest import cli_locations
from hostrun import cli, dist, distribution, install

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


def _build(
    tmp_path: Path,
    *,
    name: str,
    version: str,
    channel: str = "stable",
    files: dict[str, str] | None = None,
    runtime_dir: Path | None = None,
) -> dict:
    source = _write_tree(tmp_path / f"{name}-src", files or PACKAGE_FILES)
    return distribution.build(
        source=source,
        out=tmp_path / f"{name}-out",
        version=version,
        channel=channel,
        commit="deadbeefcafe",
        compat=_host_compat(),
        runtime_dir=runtime_dir,
    )


def _install(code: Path, result: dict, **kwargs) -> dict:
    return dist.install_package(
        code,
        from_source=str(result["artifact"]["path"]),
        **kwargs,
    )


def _manifest(result: dict) -> dict:
    return json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))


def _lock(code: Path) -> dict:
    return json.loads((code / ".horizon" / "dist.lock.json").read_text(encoding="utf-8"))


def _current(code: Path) -> str:
    return (code / ".horizon" / "current").read_text(encoding="utf-8").strip()


def _release_record(code: Path, version: str) -> dict:
    return json.loads(
        (code / ".horizon" / "releases" / version / "release.json").read_text(
            encoding="utf-8"
        )
    )


def _tree_files(root: Path, manifest: dict) -> dict[str, bytes]:
    return {
        row["path"]: (root / row["path"]).read_bytes() for row in manifest["files"]
    }


def _cli(capsys, home: Path, *args: str):
    code = cli.main([*cli_locations(home), *args])
    captured = capsys.readouterr()
    raw = captured.out.strip() or captured.err.strip()
    return code, json.loads(raw), captured


# --------------------------------------------------------------- retention
def test_upgrade_retains_the_outgoing_version_and_points_at_the_new_one(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    assert _install(code, v1)["ok"] is True

    payload = _install(code, v2)

    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert payload["version"] == "2.0.0"
    assert payload["extracted"] is True

    assert _current(code) == "2.0.0"
    lock = _lock(code)
    assert lock["version"] == "2.0.0"
    assert lock["previous_version"] == "1.0.0"
    assert lock["artifact_sha256"] == v2["artifact"]["sha256"]
    assert not (code / ".horizon" / "releases" / "2.0.0").exists()

    retained = code / ".horizon" / "releases" / "1.0.0"
    v1_manifest = _manifest(v1)
    for row in v1_manifest["files"]:
        assert (retained / row["path"]).read_bytes() == (
            Path(v1["package"]) / row["path"]
        ).read_bytes(), row["path"]
    assert (retained / "manifest.json").read_bytes() == Path(v1["manifest"]).read_bytes()

    record = _release_record(code, "1.0.0")
    assert record["schema_version"] == 1
    assert record["version"] == "1.0.0"
    assert record["channel"] == "stable"
    assert record["artifact_sha256"] == v1["artifact"]["sha256"]
    assert record["manifest_sha256"] == hashlib.sha256(
        (retained / "manifest.json").read_bytes()
    ).hexdigest()
    assert record["package_file_count"] == len(v1_manifest["files"])
    assert record["source_url"] == Path(v1["artifact"]["path"]).resolve().as_uri()
    assert record["installed_at"]


def test_upgrading_removes_files_only_the_previous_manifest_listed(tmp_path):
    v1_files = {**PACKAGE_FILES, "hostrun/legacy_only.py": "OLD = True\n"}
    v1 = _build(tmp_path, name="v1", version="1.0.0", files=v1_files)
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    assert _install(code, v1)["ok"] is True
    assert (code / "hostrun" / "legacy_only.py").is_file()

    payload = _install(code, v2)

    assert payload["ok"] is True
    assert not (code / "hostrun" / "legacy_only.py").exists()
    assert dist.verify_install(code)["status"] == "verified"


def _write_runtime_fixture(root: Path) -> Path:
    files = {
        "python.exe": b"MZ fake interpreter",
        "python313.dll": b"core dll",
        "python313._pth": b"python313.zip\r\n.\r\n..\r\nLib\\site-packages\r\n",
        "python313.zip": b"PK fake stdlib",
        "select.pyd": b"fake extension",
        "Lib/site-packages/.keep": b"",
    }
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def test_upgrade_leaves_identical_locked_runtime_files_untouched(tmp_path):
    """A running bundled runtime locks its own binaries on Windows.

    An in-place upgrade is executed by the installed runtime, so
    ``python.exe``/``python3XX.dll``/loaded ``.pyd`` files are locked.  Pinned
    runtime bytes are identical between versions, so the upgrade must skip them
    instead of trying to rewrite the locked files (which would fail with
    ``PermissionError`` and leave a half-applied tree).
    """
    runtime = _write_runtime_fixture(tmp_path / "runtime-fixture")
    v1 = _build(tmp_path, name="v1", version="1.0.0", runtime_dir=runtime)
    v2 = _build(tmp_path, name="v2", version="2.0.0", runtime_dir=runtime)
    code = tmp_path / "code"
    assert _install(code, v1)["ok"] is True
    locked = [
        code / "runtime" / "python.exe",
        code / "runtime" / "select.pyd",
        code / "runtime" / "python313.dll",
        code / "runtime" / "Lib" / "site-packages" / ".keep",
    ]
    for path in locked:
        path.chmod(0o444)  # read-only stands in for the running process's lock

    payload = _install(code, v2)

    assert payload["ok"] is True, payload
    assert payload["status"] == "ok"
    assert payload["version"] == "2.0.0"
    assert (code / "runtime" / "python.exe").read_bytes() == b"MZ fake interpreter"
    assert dist.verify_install(code)["status"] == "verified"
    for path in locked:
        path.chmod(0o666)


def test_runtime_state_and_knowledge_survive_an_upgrade(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    assert _install(code, v1)["ok"] is True

    runtime = code / ".horizon"
    (runtime / "install").mkdir(parents=True, exist_ok=True)
    (runtime / "knowledge").mkdir(parents=True, exist_ok=True)
    state_bytes = b'{"schema_version": 1, "steps": {}}\n'
    knowledge_bytes = b'{"schema_version": 1, "cards": ["k1"]}\n'
    (runtime / "install" / "state.json").write_bytes(state_bytes)
    (runtime / "knowledge" / "base.json").write_bytes(knowledge_bytes)

    assert _install(code, v2)["ok"] is True

    assert (runtime / "install" / "state.json").read_bytes() == state_bytes
    assert (runtime / "knowledge" / "base.json").read_bytes() == knowledge_bytes


def test_installing_a_third_version_prunes_the_oldest_retained_release(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    v3 = _build(tmp_path, name="v3", version="3.0.0")
    code = tmp_path / "code"
    for result in (v1, v2, v3):
        assert _install(code, result)["ok"] is True

    releases = code / ".horizon" / "releases"
    assert sorted(path.name for path in releases.iterdir()) == ["2.0.0"]
    assert not (releases / "1.0.0").exists()
    record = _release_record(code, "2.0.0")
    assert record["version"] == "2.0.0"
    assert _current(code) == "3.0.0"
    assert _lock(code)["previous_version"] == "2.0.0"
    assert dist.verify_install(code)["status"] == "verified"


def test_idempotent_reinstall_does_not_rewrite_lock_current_or_retention(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    for result in (v1, v2):
        assert _install(code, result)["ok"] is True

    lock_path = code / ".horizon" / "dist.lock.json"
    current_path = code / ".horizon" / "current"
    release_path = code / ".horizon" / "releases" / "1.0.0" / "release.json"
    lock_bytes = lock_path.read_bytes()
    current_bytes = current_path.read_bytes()
    release_bytes = release_path.read_bytes()
    installed_at = json.loads(lock_bytes)["installed_at"]

    payload = _install(code, v2)

    assert payload["ok"] is True
    assert payload["extracted"] is False
    assert payload["installed_at"] == installed_at
    assert lock_path.read_bytes() == lock_bytes
    assert current_path.read_bytes() == current_bytes
    assert release_path.read_bytes() == release_bytes


# ---------------------------------------------------------------- rollback
def _snapshot(code: Path) -> dict:
    root = code / ".horizon"
    return {
        "lock": (root / "dist.lock.json").read_bytes(),
        "current": (root / "current").read_bytes(),
        "manifest": (code / "manifest.json").read_bytes(),
        "files": {
            path.relative_to(code).as_posix(): path.read_bytes()
            for path in sorted(code.rglob("*"))
            if path.is_file() and ".horizon" not in path.relative_to(code).parts
        },
        "retained": sorted(path.name for path in (root / "releases").iterdir()),
    }


def test_rollback_restores_the_retained_version_and_its_lock(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    for result in (v1, v2):
        assert _install(code, result)["ok"] is True

    runtime = code / ".horizon"
    (runtime / "install").mkdir(parents=True, exist_ok=True)
    (runtime / "knowledge").mkdir(parents=True, exist_ok=True)
    state_bytes = b'{"schema_version": 1, "steps": {"host": "ok"}}\n'
    knowledge_bytes = b'{"schema_version": 1, "base_token": "t"}\n'
    (runtime / "install" / "state.json").write_bytes(state_bytes)
    (runtime / "knowledge" / "base.json").write_bytes(knowledge_bytes)

    payload = dist.rollback(code)

    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert payload["version"] == "1.0.0"
    assert payload["previous_version"] == "2.0.0"
    assert payload["rolled_back_at"]
    assert _current(code) == "1.0.0"

    lock = _lock(code)
    assert lock["version"] == "1.0.0"
    assert lock["channel"] == "stable"
    assert lock["artifact_sha256"] == v1["artifact"]["sha256"]
    assert lock["manifest_sha256"] == hashlib.sha256(
        (code / "manifest.json").read_bytes()
    ).hexdigest()
    assert lock["package_file_count"] == len(_manifest(v1)["files"])
    assert lock["previous_version"] == "2.0.0"
    assert lock["rolled_back_at"] == payload["rolled_back_at"]

    assert (code / "manifest.json").read_bytes() == Path(v1["manifest"]).read_bytes()
    v1_manifest = _manifest(v1)
    assert _tree_files(code, v1_manifest) == _tree_files(
        Path(v1["package"]), v1_manifest
    )
    assert dist.verify_install(code)["status"] == "verified"
    assert (runtime / "install" / "state.json").read_bytes() == state_bytes
    assert (runtime / "knowledge" / "base.json").read_bytes() == knowledge_bytes

    releases = runtime / "releases"
    assert sorted(path.name for path in releases.iterdir()) == ["2.0.0"]
    record = _release_record(code, "2.0.0")
    assert record["artifact_sha256"] == v2["artifact"]["sha256"]


def test_second_rollback_returns_to_the_later_version(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    for result in (v1, v2):
        assert _install(code, result)["ok"] is True

    assert dist.rollback(code)["version"] == "1.0.0"
    payload = dist.rollback(code)

    assert payload["ok"] is True
    assert payload["version"] == "2.0.0"
    assert payload["previous_version"] == "1.0.0"
    assert _current(code) == "2.0.0"
    assert _lock(code)["version"] == "2.0.0"
    assert (
        (code / "manifest.json").read_bytes() == Path(v2["manifest"]).read_bytes()
    )
    v2_manifest = _manifest(v2)
    assert _tree_files(code, v2_manifest) == _tree_files(
        Path(v2["package"]), v2_manifest
    )
    assert dist.verify_install(code)["status"] == "verified"
    assert sorted(
        path.name for path in (code / ".horizon" / "releases").iterdir()
    ) == ["1.0.0"]


def test_rollback_to_an_unknown_version_blocks_without_changes(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    for result in (v1, v2):
        assert _install(code, result)["ok"] is True
    before = _snapshot(code)

    payload = dist.rollback(code, to_version="9.9.9")

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == "release_not_found"
    assert _snapshot(code) == before


def test_rollback_with_a_corrupt_retained_manifest_blocks_without_changes(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    for result in (v1, v2):
        assert _install(code, result)["ok"] is True
    before = _snapshot(code)
    retained_manifest = code / ".horizon" / "releases" / "1.0.0" / "manifest.json"
    retained_manifest.write_bytes(retained_manifest.read_bytes() + b"\n")

    payload = dist.rollback(code)

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == "release_manifest_mismatch"
    assert _snapshot(code) == before


def test_rollback_with_a_tampered_retained_file_blocks_without_changes(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    for result in (v1, v2):
        assert _install(code, result)["ok"] is True
    before = _snapshot(code)
    (code / ".horizon" / "releases" / "1.0.0" / "common.py").write_text(
        "TAMPERED\n", encoding="utf-8"
    )

    payload = dist.rollback(code)

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == "release_corrupt"
    assert payload["mismatch"] == ["common.py"]
    assert _snapshot(code) == before


def test_rollback_without_an_install_lock_is_blocked(tmp_path):
    code = tmp_path / "code"
    code.mkdir()

    payload = dist.rollback(code)

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == "no_install_lock"


def test_cli_rollback_defaults_to_the_previous_version(tmp_path, capsys):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    home = tmp_path / "cli-code" / ".horizon"
    code = cli.main([*cli_locations(home), "install", "package", "--from", str(v1["artifact"]["path"])])
    assert code == 0
    capsys.readouterr()
    code = cli.main([*cli_locations(home), "install", "package", "--from", str(v2["artifact"]["path"])])
    assert code == 0
    capsys.readouterr()

    exit_code, payload, captured = _cli(capsys, home, "install", "rollback")

    assert exit_code == 0
    assert captured.err == ""
    assert payload["ok"] is True
    assert payload["version"] == "1.0.0"
    assert _current(tmp_path / "cli-code") == "1.0.0"


def test_cli_rollback_to_unknown_version_exits_one(tmp_path, capsys):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    home = tmp_path / "cli-code" / ".horizon"
    for result in (v1, v2):
        code = cli.main(
            [
                *cli_locations(home),
                "install",
                "package",
                "--from",
                str(result["artifact"]["path"]),
            ]
        )
        assert code == 0
        capsys.readouterr()

    exit_code, payload, captured = _cli(
        capsys, home, "install", "rollback", "--to", "9.9.9"
    )

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == "release_not_found"
    assert "Traceback" not in captured.err
    assert _current(tmp_path / "cli-code") == "2.0.0"


# ----------------------------------------------------------------- report
def test_report_without_a_lock_reports_an_unrecorded_source_install(tmp_path):
    home = tmp_path / "code" / ".horizon"
    home.mkdir(parents=True)

    report = install.report(home)

    assert report["release"]["distribution"] == "source"
    assert report["release"]["recorded"] is False
    assert report["release"]["detail"]
    assert "version" not in report["release"]
    # The existing report structure stays intact for #97 and older consumers.
    assert report["schema_version"] == 1
    assert {"steps", "working", "blocked", "untested", "pending"} <= set(report)


def test_report_for_a_package_install_shows_versions_source_and_retained(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0", channel="prerelease")
    code = tmp_path / "code"
    assert _install(code, v1, channel="stable")["ok"] is True
    assert _install(code, v2, channel="prerelease")["ok"] is True
    lock = _lock(code)

    report = install.report(code / ".horizon")

    release = report["release"]
    assert release["distribution"] == "package"
    assert release["version"] == "2.0.0"
    assert release["channel"] == "prerelease"
    assert release["source"] == lock["source_url"]
    assert release["installed_at"] == lock["installed_at"]
    assert release["previous_version"] == "1.0.0"
    assert release["rolled_back_at"] is None
    assert release["retained"] == [
        {
            "version": "1.0.0",
            "channel": "stable",
            "artifact_sha256": v1["artifact"]["sha256"],
        }
    ]
    assert release["lock_path"] == str(code / ".horizon" / "dist.lock.json")


def test_report_after_rollback_shows_the_restored_version_and_rolled_back_at(tmp_path):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    v2 = _build(tmp_path, name="v2", version="2.0.0")
    code = tmp_path / "code"
    for result in (v1, v2):
        assert _install(code, result)["ok"] is True
    rolled = dist.rollback(code)

    release = install.report(code / ".horizon")["release"]

    assert release["version"] == "1.0.0"
    assert release["previous_version"] == "2.0.0"
    assert release["rolled_back_at"] == rolled["rolled_back_at"]
    assert release["retained"] == [
        {
            "version": "2.0.0",
            "channel": "stable",
            "artifact_sha256": v2["artifact"]["sha256"],
        }
    ]


def test_cli_report_includes_the_release_block(tmp_path, capsys):
    v1 = _build(tmp_path, name="v1", version="1.0.0")
    home = tmp_path / "cli-code" / ".horizon"
    exit_code, payload, _ = _cli(
        capsys, home, "install", "package", "--from", str(v1["artifact"]["path"])
    )
    assert exit_code == 0

    _, report, captured = _cli(capsys, home, "install", "report")

    assert "Traceback" not in captured.err
    assert report["release"]["distribution"] == "package"
    assert report["release"]["version"] == "1.0.0"
    assert report["release"]["retained"] == []


def test_release_record_schema_declares_the_contract():
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "contracts"
            / "release-record.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is True
    assert set(schema["required"]) == {
        "schema_version",
        "version",
        "channel",
        "artifact_sha256",
        "manifest_sha256",
        "package_file_count",
        "source_url",
        "installed_at",
    }
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["properties"]["channel"]["enum"] == ["stable", "prerelease"]

    lock_schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "contracts" / "install-lock.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert "previous_version" in lock_schema["properties"]
    assert "rolled_back_at" in lock_schema["properties"]
