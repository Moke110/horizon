"""Install self-check: honest statuses, resumable state, no credential leakage."""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from hostrun import cli, install
from hostrun import workspace as workspace_mod
from hostrun.base import HostRunError, resolve_executable
from conftest import cli_locations


def _host(**over):
    payload = {
        "product": "WorkBuddy",
        "version": "1.6.110",
        "mode": "desktop",
        "local_execution": True,
        "permissions": ["shell", "file_write"],
    }
    payload.update(over)
    return payload


# ------------------------------------------------------------------ record
def test_ok_is_terminal_and_rechecks_do_not_erase_it(tmp_path):
    install.record(tmp_path, "host", "ok", note="首次确认")
    install.record(tmp_path, "host", "blocked", note="复检失败")

    entry = install.load_state(tmp_path)["steps"]["host"]
    assert entry["status"] == "ok"
    assert entry["note"] == "首次确认"
    assert entry["first_ok_at"]


def test_record_rejects_unknown_step_and_status(tmp_path):
    with pytest.raises(HostRunError):
        install.record(tmp_path, "nope", "ok")
    with pytest.raises(HostRunError):
        install.record(tmp_path, "host", "probably")


def test_state_is_resumable_across_calls(tmp_path):
    install.record(tmp_path, "host", "ok")
    install.record(tmp_path, "workspace", "ok")
    install.record(tmp_path, "dependencies", "ok")
    install.record(tmp_path, "skill", "ok")
    install.record(tmp_path, "lark_cli", "blocked", note="未安装")

    assert install.next_step(tmp_path) == "lark_cli"
    status = install.step_status(tmp_path)
    assert status["host"] == "ok"
    assert status["facilities"] == "pending"

    # a fresh call reads the same state from disk
    assert install.summary(tmp_path)["steps"]["lark_cli"] == "blocked"


def test_report_separates_working_blocked_untested(tmp_path):
    install.record(tmp_path, "host", "ok")
    install.record(tmp_path, "dependencies", "ok")
    install.record(tmp_path, "lark_cli", "blocked", note="飞书 CLI 未找到")
    install.record(tmp_path, "identity", "untested", note="未验证")

    report = install.report(tmp_path)
    assert report["ok"] is False
    assert report["partial"] is False
    assert report["working"] == ["host", "dependencies"]
    assert [row["step"] for row in report["blocked"]] == ["lark_cli"]
    assert [row["step"] for row in report["untested"]] == ["identity"]
    assert any("飞书 CLI" in line for line in report["limitations"])


def test_report_never_contains_credentials(tmp_path):
    install.record(
        tmp_path,
        "identity",
        "ok",
        note="user 与 bot 身份均可用",
        evidence={"user_token": "u-secret", "bot_secret": "b-secret", "user_ready": True},
    )
    report = install.report(tmp_path)
    blob = json.dumps(report, ensure_ascii=False)
    assert "u-secret" not in blob and "b-secret" not in blob
    assert report["steps"]["identity"]["evidence"]["user_ready"] is True


# ------------------------------------------------------------------ probes
def test_probe_host_asks_instead_of_assuming(tmp_path):
    pending = install.probe_host(None)
    assert pending["status"] == "pending"

    untested = install.probe_host({"product": "ChatGPT"})
    assert untested["status"] == "untested"

    ok = install.probe_host(_host())
    assert ok["status"] == "ok"
    assert ok["evidence"]["local_execution"] is True


def _seed_repo_files(repo: Path) -> None:
    for rel in (
        "common.py",
        "contracts/README.md",
        "skills/horizon-run/SKILL.md",
        "skills/horizon-run/references/installation.md",
        "skills/horizon-run/references/workflow.md",
    ):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {rel}\n", encoding="utf-8")


def test_probe_dependencies_never_falls_back_to_this_process(tmp_path):
    repo = tmp_path / "repo"
    _seed_repo_files(repo)

    result = install.probe_dependencies(repo)

    assert result["status"] == "blocked"
    assert "解释器" in result["note"]
    assert "install toolchain" in result["note"]
    assert "--clear" in result["note"]
    assert result["evidence"]["recorded"] is False


def test_probe_dependencies_blocks_an_interpreter_inside_the_repo(tmp_path):
    repo = tmp_path / "repo"
    _seed_repo_files(repo)
    inside = repo / ".venv" / "python"
    inside.parent.mkdir(parents=True)
    inside.write_text("", encoding="utf-8")

    result = install.probe_dependencies(repo, python=str(inside))

    assert result["status"] == "blocked"
    assert result["evidence"]["outside_repo"] is False
    assert "代码目录" in result["note"]
    assert "--clear" in result["note"]


def test_probe_dependencies_accepts_the_bundled_runtime_of_a_package_install(tmp_path):
    repo = tmp_path / "repo"
    _seed_repo_files(repo)
    bundled = repo / "runtime" / "python.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("", encoding="utf-8")
    lock = repo / ".horizon" / "dist.lock.json"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        json.dumps({"schema_version": 1, "distribution": "package"}),
        encoding="utf-8",
    )

    result = install.probe_dependencies(
        repo,
        python=str(bundled),
        runner=_import_probe_runner(["3.13.1", "6.0.1", "1.24.0"]),
    )

    assert result["status"] == "ok"
    assert result["evidence"]["bundled_runtime"] is True
    # The interpreter is still inside the code directory; the package lock is
    # what makes that legitimate.
    assert result["evidence"]["outside_repo"] is False
    assert "内置运行时" in result["note"]


def test_probe_dependencies_blocks_an_in_repo_runtime_for_a_source_install(tmp_path):
    repo = tmp_path / "repo"
    _seed_repo_files(repo)
    bundled = repo / "runtime" / "python.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("", encoding="utf-8")

    result = install.probe_dependencies(repo, python=str(bundled))

    assert result["status"] == "blocked"
    assert result["evidence"]["bundled_runtime"] is False
    assert "代码目录" in result["note"]


def test_probe_dependencies_accepts_an_existing_outside_interpreter(tmp_path):
    import sys

    repo = tmp_path / "repo"
    _seed_repo_files(repo)

    result = install.probe_dependencies(repo, python=sys.executable)

    assert result["status"] == "ok"
    assert result["evidence"]["outside_repo"] is True
    assert result["evidence"]["recorded"] is True
    assert result["evidence"]["python_version"].count(".") == 2


def _fake_python(tmp_path, name="python.exe"):
    binary = tmp_path / "tools" / name
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("", encoding="utf-8")
    return binary


def _import_probe_runner(lines):
    class Proc:
        returncode = 0
        stderr = ""
        stdout = "\n".join(lines) + "\n"

    return lambda argv, **kwargs: Proc()


def _package_install_tree(tmp_path: Path) -> Path:
    repo = tmp_path / "code"
    _seed_repo_files(repo)
    (repo / "bin").mkdir(parents=True)
    (repo / "bin" / "horizon.cmd").write_text("@echo off\r\n", encoding="utf-8")
    (repo / "runtime").mkdir()
    (repo / "runtime" / "python.exe").write_bytes(b"MZ fake")
    (repo / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "1.2.3",
                "channel": "stable",
                "built_from_commit": "abc123",
                "compat": {"os": "windows", "arch": "x86_64", "dsh": "*", "python": "3.13"},
                "files": [],
            }
        ),
        encoding="utf-8",
    )
    (repo / ".horizon").mkdir()
    (repo / ".horizon" / "dist.lock.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "distribution": "package",
                "version": "1.2.3",
                "channel": "stable",
                "artifact_sha256": "a" * 64,
                "source_url": (tmp_path / "horizon-1.2.3-stable.zip").as_uri(),
                "installed_at": "2026-10-31T10:00:00+08:00",
                "requires": {"os": "windows", "arch": "x86_64", "dsh": "*", "python": "3.13"},
                "skill_digest": "",
                "package_file_count": 0,
                "manifest_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    assert workspace_mod.register_skill(
        repo, tmp_path / "host-skills" / "horizon-run"
    )["ok"] is True
    return repo


def test_install_check_for_a_package_reports_workspace_and_bundled_dependencies(tmp_path):
    repo = _package_install_tree(tmp_path)
    runtime_python = repo / "runtime" / "python.exe"
    wrapper = repo / "bin" / "horizon.cmd"

    result = install.check(
        repo / ".horizon",
        workspace_root=repo,
        host=_host(),
        runner=_import_probe_runner(["3.13.1", "6.0.1", "1.24.0"]),
    )

    assert result["steps"]["workspace"] == "ok"
    assert result["steps"]["dependencies"] == "ok"
    evidence = install.load_state(repo / ".horizon")["steps"]["dependencies"]["evidence"]
    assert evidence["bundled_runtime"] is True
    assert evidence["python"] == str(runtime_python)
    config = json.loads(
        (repo / ".horizon" / "install" / "config.json").read_text(encoding="utf-8")
    )
    assert config["paths"]["python"] == str(runtime_python)
    assert config["paths"]["python"] != str(wrapper)
    assert (wrapper).is_file()


def test_probe_dependencies_blocks_an_interpreter_without_pymupdf(tmp_path):
    # yaml imports, fitz does not: PyMuPDF is a required dependency, not an
    # optional extra (workspace.check_runtime asserts both).
    repo = tmp_path / "repo"
    _seed_repo_files(repo)
    python = _fake_python(tmp_path)

    result = install.probe_dependencies(
        repo,
        python=str(python),
        runner=_import_probe_runner(["3.13.1", "6.0.1"]),
    )

    assert result["status"] == "blocked"
    assert "PyMuPDF" in result["note"]
    assert result["evidence"]["yaml"] is True
    assert result["evidence"]["fitz"] is False


def test_probe_dependencies_accepts_yaml_and_fitz(tmp_path):
    repo = tmp_path / "repo"
    _seed_repo_files(repo)
    python = _fake_python(tmp_path)

    result = install.probe_dependencies(
        repo,
        python=str(python),
        runner=_import_probe_runner(["3.13.1", "6.0.1", "1.24.0"]),
    )

    assert result["status"] == "ok"
    assert result["evidence"]["yaml"] is True
    assert result["evidence"]["fitz"] is True


def test_probe_identity_reads_booleans_only():
    payload = {
        "identities": {
            "user": {"available": True, "tokenStatus": "valid", "userOpenId": "ou_x"},
            "bot": {"available": True, "tokenStatus": "expired"},
        }
    }

    class Proc:
        returncode = 0
        stdout = json.dumps(payload)

    result = install.probe_identity("lark-cli", runner=lambda *a, **k: Proc())
    assert result["status"] == "blocked"
    assert "bot" in result["note"]
    assert result["evidence"]["user_ready"] is True
    assert result["evidence"]["bot_ready"] is False


def test_probe_identity_handles_unreadable_output():
    class Proc:
        returncode = 1
        stdout = "not json"

    result = install.probe_identity("lark-cli", runner=lambda *a, **k: Proc())
    assert result["status"] == "blocked"
    assert "auth status" in result["note"]


def test_resolve_executable_prefers_explicit_then_private(tmp_path, monkeypatch):
    private = tmp_path / "bin"
    private.mkdir()
    (private / "lark-cli").write_text("#!/bin/sh\n", encoding="utf-8")
    explicit = tmp_path / "explicit-cli"
    explicit.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setenv("PATH", "")
    assert resolve_executable(str(explicit), private_dirs=[private])["source"] == "explicit"
    assert resolve_executable(None, private_dirs=[private], names=["lark-cli"])["source"] == "private"
    assert resolve_executable(str(tmp_path / "missing"), private_dirs=[], names=["nope"])["found"] is False


# ------------------------------------------------------------------- check
def test_check_works_with_chinese_and_space_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    layout = tmp_path / "我的 工作 目录"
    home = layout / "我的 数据" / "home"
    home.mkdir(parents=True)
    # A seeded code checkout under a Chinese/space path: the running
    # interpreter (repo .venv) is genuinely outside it, so the recorded
    # interpreter probe succeeds no matter which python runs the suite.
    repo = layout / "代码 checkout"
    _seed_repo_files(repo)

    result = install.check(
        home, repo=repo, host=_host(), store_dir=tmp_path / "lark-cli-home"
    )

    assert result["steps"]["host"] == "ok"
    assert result["steps"]["dependencies"] == "ok"
    evidence = install.load_state(home)["steps"]["dependencies"]["evidence"]
    assert evidence["outside_repo"] is True
    assert evidence["repo"] == str(repo)
    assert evidence["in_data_root"] is False
    assert result["steps"]["lark_cli"] == "blocked"
    assert result["steps"]["permissions"] == "blocked"
    config = json.loads((home / "install" / "config.json").read_text(encoding="utf-8"))
    assert config["host"]["product"] == "WorkBuddy"
    assert config["timezone"]
    # The Chinese/space layout round-trips into the persisted config and the
    # report without being mangled or crashing anything.
    assert config["paths"]["repo"] == str(repo)
    assert config["paths"]["home"] == str(home)
    report = install.report(home)
    assert report["paths"]["repo"] == str(repo)
    assert report["paths"]["home"] == str(home)
    assert "dependencies" in report["working"]
    assert "dependencies" not in [row["step"] for row in report["blocked"]]


def test_check_never_falls_back_to_the_running_interpreter(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    root, repo, home = _fake_workspace(tmp_path)
    _seed_repo_files(repo)
    workspace_mod.save_record(
        root,
        {
            **workspace_mod.load_record(root),
            "python": "",
        },
    )
    from hostrun.base import update_config

    update_config(home, paths={"python": ""})

    result = install.check(
        home,
        host=_host(),
        workspace_root=root,
        store_dir=tmp_path / "lark-cli-home",
    )

    assert result["steps"]["dependencies"] == "blocked"
    note = install.load_state(home)["steps"]["dependencies"]["note"]
    assert "install toolchain" in note
    assert "--clear" in note
    assert str(root) in note
    assert install.load_state(home)["steps"]["dependencies"]["evidence"]["recorded"] is False


def test_check_merges_host_and_lark_permissions_once(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    home = tmp_path / "home"
    binary = home / "bin" / "lark-cli"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    _seed_repo_files(tmp_path / "repo")

    def runner(argv, **kwargs):
        if "auth" in argv:
            return _Proc(stdout=json.dumps(_auth_payload()))
        return _Proc(stdout="{}")

    install.check(
        home,
        repo=tmp_path / "repo",
        host=_host(),
        python=__import__("sys").executable,
        runner=runner,
        store_dir=tmp_path / "lark-cli-home",
    )

    config = json.loads((home / "install" / "config.json").read_text(encoding="utf-8"))
    permissions = config["permissions"]
    assert permissions["scope"] == sorted(GRANULAR_SCOPES)
    assert permissions["host_scope"] == ["shell", "file_write"]
    assert permissions["granted_at"]


def test_cli_rejected_layout_records_workspace_blocked(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    home = tmp_path / "code" / ".horizon"

    code = cli.main(
        [
            *cli_locations(home),
            "install",
            "workspace",
            "--root",
            str(ws),
            "--data-dir",
            "horizon/archive",
        ]
    )

    assert code == 1
    entry = install.load_state(home)["steps"]["workspace"]
    assert entry["status"] == "blocked"
    assert "资料目录不能放在代码目录内" in entry["note"]
    assert entry["evidence"]["layout_valid"] is False
    report = install.report(home)
    assert [row["step"] for row in report["blocked"]] == ["workspace"]
    assert report["next"] == "workspace"
    assert any("资料目录不能放在代码目录内" in line for line in report["limitations"])
    assert not (ws / "horizon").exists()


def test_stale_evidence_flags_old_steps(tmp_path):
    install.record(tmp_path, "host", "ok")
    state = install.load_state(tmp_path)
    state["steps"]["host"]["at"] = "2020-01-01T00:00:00+00:00"
    install.save_state(tmp_path, state)

    assert "host" in install.stale_evidence(tmp_path, days=7)


def test_cli_install_check_accepts_explicit_read_only_tokens(tmp_path, monkeypatch):
    captured = {}

    def fake_check(home, **kwargs):
        captured["home"] = Path(home)
        captured.update(kwargs)
        return {"ok": True, "steps": {}, "next": None}

    monkeypatch.setattr(install, "check", fake_check)

    code = cli.main(
        [
            *cli_locations(tmp_path / "home"),
            "install",
            "check",
            "--base-token",
            "bas_probe",
            "--folder",
            "fld_one",
            "--folder",
            "fld_two",
        ]
    )

    assert code == 0
    assert captured["base_token"] == "bas_probe"
    assert captured["folders"] == ["fld_one", "fld_two"]


# ------------------------------------------------ write-permission preflight
def test_probe_writes_creates_and_removes_unique_probes(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    store = tmp_path / "lark-cli-home"

    result = install.probe_writes(home, store_dir=store)

    assert result["status"] == "ok"
    assert result["data_root"]["status"] == "ok"
    assert result["lark_store"]["status"] == "ok"
    assert result["lark_store"]["path"] == str(store)
    # A missing store dir is probed in its nearest existing ancestor; the probe
    # must not create the credential store as a side effect.
    assert not store.exists()
    assert result["lark_store"]["probe_root"] == str(tmp_path)
    assert not list(home.glob(".horizon-write-probe-*"))
    assert not list(tmp_path.glob(".horizon-write-probe-*"))


def test_probe_writes_uses_lark_cli_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    store = tmp_path / "custom-store"
    monkeypatch.setenv("LARK_CLI_HOME", str(store))

    result = install.probe_writes(home)

    assert result["lark_store"]["path"] == str(store)


def test_probe_writes_reports_the_exact_denied_store_path(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    store = tmp_path / "locked-store"
    store.mkdir()

    def writer(path):
        if path.parent.resolve() == store.resolve():
            raise PermissionError(13, "access denied", str(path))
        path.write_text("probe", encoding="utf-8")

    result = install.probe_writes(home, store_dir=store, writer=writer)

    assert result["status"] == "blocked"
    assert result["data_root"]["status"] == "ok"
    assert result["lark_store"]["status"] == "blocked"
    assert result["lark_store"]["path"] == str(store)
    assert "denied" in result["lark_store"]["error"]


def test_check_blocks_permissions_when_a_write_probe_is_denied(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    store = tmp_path / "lark-cli-home"

    def writer(path):
        raise PermissionError(13, "access denied", str(path))

    result = install.check(
        home,
        repo=Path(__file__).resolve().parents[1],
        host=_host(),
        # Offline: never let the probe pick up a PATH ``lark-cli`` from the
        # developer machine and run it for real. A stale explicit path is
        # enough to exercise the write-probe branch under test.
        lark_cli=str(tmp_path / "missing-lark-cli"),
        store_dir=store,
        writer=writer,
    )

    assert result["steps"]["permissions"] == "blocked"
    entry = install.load_state(home)["steps"]["permissions"]
    assert "写入权限" in entry["note"]
    assert str(home) in entry["note"]
    assert str(store) in entry["note"]
    assert entry["evidence"]["writes"]["data_root"]["status"] == "blocked"
    assert entry["evidence"]["writes"]["lark_store"]["status"] == "blocked"


def test_check_reads_explicit_tokens_without_attaching_the_library(tmp_path):
    home = tmp_path / "home"
    (home / "install").mkdir(parents=True)
    facilities = home / "install" / "facilities.json"
    original = json.dumps(
        {"base": {"token": "bas_recorded"}, "folders": {"source": "fld_old", "push": "fld_old_push"}},
        ensure_ascii=False,
    )
    facilities.write_text(original, encoding="utf-8")
    binary = home / "bin" / "lark-cli"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    calls = []

    def runner(argv, **kwargs):
        calls.append([str(item) for item in argv])
        if "auth" in argv:
            return _Proc(stdout=json.dumps(_auth_payload()))
        if "+table-list" in argv:
            return _Proc(stdout=json.dumps({"ok": True, "data": {"tables": []}}))
        if "files" in argv and "list" in argv:
            return _Proc(stdout=json.dumps({"ok": True, "data": {"files": []}}))
        return _Proc(stdout="{}")

    result = install.check(
        home,
        repo=Path(__file__).resolve().parents[1],
        host=_host(),
        store_dir=tmp_path / "lark-cli-home",
        base_token="bas_probe",
        folders=["fld_probe_one", "fld_probe_two"],
        runner=runner,
    )

    assert result["steps"]["permissions"] == "ok"
    entry = install.load_state(home)["steps"]["permissions"]
    assert entry["evidence"]["base"]["status"] == "ok"
    assert entry["evidence"]["drive"]["status"] == "ok"
    joined = [" ".join(call) for call in calls]
    assert any("bas_probe" in call for call in joined)
    assert any("fld_probe_one" in call for call in joined)
    assert any("fld_probe_two" in call for call in joined)
    assert not any("bas_recorded" in call for call in joined)
    # Read-only: the recorded facilities and the absent library config are untouched.
    assert facilities.read_text(encoding="utf-8") == original
    assert not (home / "knowledge" / "base.json").exists()



# -------------------------------------------------- install toolchain step
def _fake_workspace(tmp_path):
    root = tmp_path / "ws"
    repo = root / "horizon"
    (repo / ".git").mkdir(parents=True)
    home = root / "horizon-archive"
    home.mkdir()
    workspace_mod.save_record(
        root,
        {
            "schema_version": 1,
            "root": str(root),
            "repo": str(repo),
            "home": str(home),
            "repo_url": workspace_mod.DEFAULT_REPO_URL,
            "commit": "abc123",
        },
    )
    return root, repo, home


def test_toolchain_step_blocks_with_a_repair_command_then_recovers(
    tmp_path, monkeypatch
):
    root, repo, home = _fake_workspace(tmp_path)
    repair = (
        f'python -m hostrun.cli --home "{home}" install toolchain '
        f'--root "{root}" --clear'
    )
    blocked = {
        "ok": False,
        "status": "blocked",
        "note": "数据根私有解释器不可用；修复：" + repair,
    }
    monkeypatch.setattr(workspace_mod, "toolchain", lambda *a, **k: blocked)

    assert cli.main([*cli_locations(home), "install", "toolchain", "--root", str(root)]) == 1

    entry = install.load_state(home)["steps"]["dependencies"]
    assert entry["status"] == "blocked"
    assert "--clear" in entry["note"]
    report = install.report(home)
    assert [row["step"] for row in report["blocked"]] == ["dependencies"]
    assert any("--clear" in line for line in report["limitations"])

    python = home / ".venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    ok = {
        "ok": True,
        "status": "ok",
        "note": "",
        "uv": {"path": "uv"},
        "environment": {"python": str(python), "probe": {"ok": True}},
    }
    monkeypatch.setattr(workspace_mod, "toolchain", lambda *a, **k: ok)

    assert (
        cli.main(
            [
                *cli_locations(home),
                "install",
                "toolchain",
                "--root",
                str(root),
                "--clear",
            ]
        )
        == 0
    )
    assert install.step_status(home)["dependencies"] == "ok"
    config = json.loads((home / "install" / "config.json").read_text(encoding="utf-8"))
    assert config["paths"]["python"] == str(python)
    assert config["paths"]["uv"] == "uv"


# --------------------------------------------- Lark CLI / identity / permissions
GRANULAR_SCOPES = [
    "base:app:read",
    "base:table:read",
    "base:field:read",
    "base:record:read",
    "base:record:create",
    "base:record:update",
    "base:record:delete",
    "drive:file:upload",
    "drive:file:download",
    "space:folder:create",
    "space:document:delete",
    "im:message",
]


class _Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _auth_payload(**over):
    payload = {
        "identities": {
            "user": {
                "available": True,
                "tokenStatus": "valid",
                "userOpenId": "ou_x",
                "accessToken": "secret-access",
            },
            "bot": {"available": True, "tokenStatus": "valid", "refreshToken": "secret-refresh"},
            "tenant": {"matched": True},
        },
        "scopes": list(GRANULAR_SCOPES),
    }
    payload.update(over)
    return payload


def test_probe_lark_cli_blocks_a_stale_recorded_path_without_path_fallback(
    tmp_path, monkeypatch
):
    path_dir = tmp_path / "pathbin"
    path_dir.mkdir()
    (path_dir / ("lark-cli.cmd" if os.name == "nt" else "lark-cli")).write_text(
        "", encoding="utf-8"
    )
    monkeypatch.setenv("PATH", str(path_dir))
    stale = tmp_path / "retired" / "lark-cli"

    result = install.probe_lark_cli(tmp_path / "home", str(stale))

    assert result["status"] == "blocked"
    assert result["evidence"]["stale"] is True
    assert result["evidence"]["path"] == str(stale)
    assert "discover --cli" in result["note"]
    assert "重装" in result["note"]


def test_probe_identity_requests_verify(tmp_path):
    calls = []

    def runner(argv, **kwargs):
        calls.append([str(item) for item in argv])
        return _Proc(stdout=json.dumps(_auth_payload()))

    result = install.probe_identity("lark-cli", runner=runner)

    assert result["status"] == "ok"
    assert "--verify" in calls[0]
    assert result["scopes"] == GRANULAR_SCOPES
    assert "ou_x" not in json.dumps(result)


def test_check_records_the_permissions_step_from_the_real_preflight(tmp_path):
    home = tmp_path / "home"
    binary = home / "bin" / "lark-cli"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    calls = []

    def runner(argv, **kwargs):
        calls.append([str(item) for item in argv])
        if "auth" in argv:
            return _Proc(stdout=json.dumps(_auth_payload()))
        return _Proc(stdout="{}")

    result = install.check(
        home,
        repo=Path(__file__).resolve().parents[1],
        host=_host(),
        runner=runner,
        store_dir=tmp_path / "lark-cli-home",
    )

    assert result["steps"]["permissions"] == "untested"
    entry = install.load_state(home)["steps"]["permissions"]
    assert entry["note"]
    assert entry["evidence"]["granted"] == sorted(GRANULAR_SCOPES)
    assert entry["evidence"]["missing"] == []
    assert entry["evidence"]["base"]["status"] == "untested"
    assert entry["evidence"]["drive"]["status"] == "untested"
    assert entry["evidence"]["writes"]["data_root"]["status"] == "ok"
    assert entry["evidence"]["writes"]["lark_store"]["status"] == "ok"
    assert any("--verify" in call for call in calls)
    report = install.report(home)
    assert "permissions" in [row["step"] for row in report["untested"]]
    blob = json.dumps(report, ensure_ascii=False)
    assert "secret-access" not in blob
    assert "secret-refresh" not in blob


def test_check_reads_facilities_folders_for_the_resource_preflight(tmp_path):
    home = tmp_path / "home"
    binary = home / "bin" / "lark-cli"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    (home / "install").mkdir(parents=True)
    (home / "install" / "facilities.json").write_text(
        json.dumps(
            {
                "base": {"token": "bas_x"},
                "folders": {"source": "fld_src", "push": "fld_push"},
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def runner(argv, **kwargs):
        calls.append([str(item) for item in argv])
        joined = " ".join(str(item) for item in argv)
        if "auth" in argv:
            return _Proc(stdout=json.dumps(_auth_payload()))
        if "+table-list" in argv:
            return _Proc(stdout=json.dumps({"ok": True, "data": {"tables": []}}))
        if "files" in argv and "list" in argv:
            return _Proc(stdout=json.dumps({"ok": True, "data": {"files": []}}))
        return _Proc(stdout="{}")

    result = install.check(
        home,
        repo=Path(__file__).resolve().parents[1],
        host=_host(),
        runner=runner,
        store_dir=tmp_path / "lark-cli-home",
    )

    assert result["steps"]["permissions"] == "ok"
    entry = install.load_state(home)["steps"]["permissions"]
    assert entry["evidence"]["base"]["status"] == "ok"
    assert entry["evidence"]["drive"]["status"] == "ok"
    assert entry["evidence"]["messages"]["status"] == "untested"
    drive_calls = [call for call in calls if "files" in call and "list" in call]
    assert drive_calls
    assert any("fld_src" in " ".join(call) for call in drive_calls)
    assert any("fld_push" in " ".join(call) for call in drive_calls)


def test_check_without_a_cli_records_permissions_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    home = tmp_path / "home"
    home.mkdir()

    result = install.check(
        home,
        repo=Path(__file__).resolve().parents[1],
        host=_host(),
        store_dir=tmp_path / "lark-cli-home",
    )

    assert result["steps"]["lark_cli"] == "blocked"
    assert result["steps"]["permissions"] == "blocked"
    entry = install.load_state(home)["steps"]["permissions"]
    assert "CLI" in entry["note"]


def test_check_normalizes_a_missing_runner_for_the_resource_preflight(tmp_path, monkeypatch):
    """A caller that leaves ``runner`` unset must still get a working preflight."""
    home = tmp_path / "home"
    (home / "install").mkdir(parents=True)
    binary = home / "bin" / "lark-cli"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    captured = {}

    monkeypatch.setattr(
        install,
        "probe_identity",
        lambda path, runner=None: {
            "status": "ok",
            "note": "",
            "scopes": [],
            "evidence": {"user_ready": True, "bot_ready": True},
        },
    )
    monkeypatch.setattr(
        install,
        "probe_writes",
        lambda home_, store_dir=None, writer=None: {
            "data_root": {"status": "ok", "path": str(home_), "error": ""},
            "lark_store": {"status": "ok", "path": str(tmp_path / "lark"), "error": ""},
        },
    )
    monkeypatch.setattr(
        install.lark_mod,
        "authorization",
        lambda scopes, required=None: {
            "status": "ok",
            "note": "",
            "required": list(required or []),
            "granted": [],
            "missing": [],
        },
    )

    def fake_preflight(home_, cli, *, base_token=None, folders=None, runner=None):
        captured["runner"] = runner
        captured["base_token"] = base_token
        captured["folders"] = list(folders or [])
        return {
            "status": "ok",
            "base": {"status": "ok", "note": ""},
            "drive": {"status": "ok", "note": ""},
            "messages": {"status": "untested", "note": ""},
        }

    monkeypatch.setattr(install.lark_mod, "resource_preflight", fake_preflight)

    result = install.check(
        home,
        repo=Path(__file__).resolve().parents[1],
        host=_host(),
        base_token="bas_probe",
        folders=["fld_probe"],
    )

    assert captured["runner"] is install.subprocess.run
    assert captured["base_token"] == "bas_probe"
    assert captured["folders"] == ["fld_probe"]
    assert result["steps"]["permissions"] == "ok"


# ------------------------------------------------------------ retired paths
def _empty_runner(calls=None):
    def runner(argv, **kwargs):
        if calls is not None:
            calls.append([str(item) for item in argv])
        return _Proc(stdout="{}")

    return runner


def test_retired_steps_are_gone_and_cannot_be_recorded(tmp_path):
    """#91 retires the host tasks, listener and the smoke/cleanup gate."""
    for name in ("schedules", "scheduled_run", "listener", "listener_health"):
        assert name not in install.STEPS
        assert name not in install.STEP_NOTES
        with pytest.raises(HostRunError):
            install.record(tmp_path, name, "ok")
    for name in ("test_loop", "cleanup"):
        with pytest.raises(HostRunError):
            install.record(tmp_path, name, "blocked")


def test_install_steps_end_at_knowledge(tmp_path):
    assert install.STEPS[-1] == "knowledge"
    for name in install.STEPS:
        install.record(tmp_path, name, "ok")
    report = install.report(tmp_path)
    assert report["ok"] is True
    assert report["next"] is None


def test_report_rules_name_the_console(tmp_path):
    rules = install.report(tmp_path)["rules"]

    assert any("唯一常驻组件是用户手动启停的控制台" in rule for rule in rules)
    assert not any("listener" in rule for rule in rules)


def test_check_no_longer_records_listener_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    home = tmp_path / "home"
    home.mkdir()

    result = install.check(
        home,
        repo=Path(__file__).resolve().parents[1],
        host=_host(),
        runner=_empty_runner(),
        store_dir=tmp_path / "lark-cli-home",
    )

    assert "listener" not in result["steps"]
    assert "listener_health" not in result["steps"]
    assert "scheduled_run" not in result["steps"]
    assert "schedules" not in install.load_state(home)["steps"]
