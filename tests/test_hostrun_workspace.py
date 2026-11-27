"""Installation workspace: safe reuse, data-root private environment."""

import io
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from hostrun import dist, workspace
from hostrun.base import HostRunError
from conftest import cli_locations

ROOT = Path(__file__).resolve().parents[1]
PYTHON_REL = (
    Path("Scripts") / "python.exe" if os.name == "nt" else Path("bin") / "python"
)

def _package_lock(repo: Path, **overrides) -> dict:
    lock = {
        "schema_version": 1,
        "distribution": "package",
        "version": "1.2.3",
        "channel": "stable",
        "artifact_sha256": "a" * 64,
        "source_url": (repo.parent / "horizon-1.2.3-stable.zip").as_uri(),
        "installed_at": "2026-10-31T10:00:00+08:00",
        "requires": {
            "os": "windows",
            "arch": "x86_64",
            "dsh": "*",
            "python": "3.13",
        },
        "skill_digest": "",
        "package_file_count": 10,
        "manifest_sha256": "b" * 64,
    }
    lock.update(overrides)
    dist.write_lock(repo, lock)
    return lock


def _seed_repo(repo: Path) -> Path:
    """A checkout with the files installation must read, plus a .git marker."""
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


def _files_under(directory: Path) -> set[Path]:
    return {path for path in directory.rglob("*") if path.is_file()}


class Runner:
    """Records argv and answers the git/uv calls the module makes."""

    def __init__(self, *, status="", origin=workspace.DEFAULT_REPO_URL, commit="abc123"):
        self.calls = []
        self.status = status
        self.origin = origin
        self.commit = commit

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        joined = " ".join(argv)

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        proc = Proc()
        if argv[:2] == ["git", "-C"] and "status" in argv:
            proc.stdout = self.status
        elif argv[:2] == ["git", "-C"] and "get-url" in argv:
            proc.stdout = self.origin
        elif argv[:2] == ["git", "-C"] and "rev-parse" in argv:
            proc.stdout = self.commit
        elif "clone" in joined:
            target = Path(argv[-1])
            _seed_repo(target)
            proc.stdout = "cloned"
        elif "--version" in joined:
            proc.stdout = "uv 0.11.19"
        return proc


class ToolchainRunner:
    """Fakes uv and the import probe; ``uv venv`` creates the interpreter stub."""

    def __init__(self, *, import_ok=True):
        self.calls = []
        self.import_ok = import_ok

    def __call__(self, argv, **kwargs):
        argv = [str(item) for item in argv]
        self.calls.append(argv)

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        proc = Proc()
        if argv[-1] == "--version":
            proc.stdout = "uv 0.11.19"
        elif argv[1:2] == ["venv"]:
            venv = Path(argv[-1])
            python = venv / PYTHON_REL
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("fake-interpreter", encoding="utf-8")
            proc.stdout = "venv created"
        elif argv[1:2] == ["pip"]:
            proc.stdout = "dependencies installed"
        elif "-c" in argv:
            if not self.import_ok:
                proc.returncode = 1
                proc.stderr = "ModuleNotFoundError: No module named 'yaml'"
            else:
                proc.stdout = "imports ok"
        return proc

    def ran(self, subcommand: str) -> list[list[str]]:
        return [call for call in self.calls if call[1:2] == [subcommand]]


def _uv_binary(tmp_path: Path, name: str | None = None) -> Path:
    path = tmp_path / (name or ("uv.exe" if os.name == "nt" else "uv"))
    path.write_bytes(b"fake-uv")
    return path


def _record(root: Path, repo: Path, home: Path) -> dict:
    record = {
        "schema_version": 1,
        "root": str(root),
        "repo": str(repo),
        "home": str(home),
        "repo_url": workspace.DEFAULT_REPO_URL,
        "commit": "abc123",
    }
    workspace.save_record(root, record)
    return record


def _workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "ws"
    repo = _seed_repo(root / "horizon")
    home = root / "horizon-archive"
    home.mkdir(parents=True)
    _record(root, repo, home)
    return root, repo, home


def _config(home: Path) -> dict:
    return json.loads((home / "install" / "config.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- workspace
def test_fresh_workspace_clones_code_and_creates_separate_data_root(tmp_path):
    root = tmp_path / "研究 项目"
    root.mkdir()
    runner = Runner()

    result = workspace.acquire(root, runner=runner)

    assert result["ok"] is True
    assert result["action"] == "clone"
    repo, home = Path(result["repo"]), Path(result["home"])
    assert repo.name == "horizon"
    assert home.name == "horizon-archive"
    assert (repo / ".git").is_dir()
    assert home.is_dir()
    assert not home.is_relative_to(repo)
    record = workspace.load_record(root)
    assert record["repo"] == str(repo)
    assert record["commit"] == "abc123"


def test_acquiring_a_workspace_leaves_the_code_checkout_untouched(tmp_path):
    root = tmp_path / "ws"
    repo = _seed_repo(root / "horizon")
    before = _files_under(repo)

    result = workspace.acquire(root, runner=Runner())

    assert result["ok"] is True
    assert result["action"] == "reuse"
    assert _files_under(repo) == before
    assert not (repo / ".venv").exists()
    assert not (repo / ".horizon").exists()


def test_existing_checkout_with_changes_is_not_pulled_or_overwritten(tmp_path):
    root = tmp_path / "ws"
    (root / "horizon").mkdir(parents=True)
    _seed_repo(root / "horizon")
    runner = Runner(status=" M writer/tasks.py\n")

    result = workspace.acquire(root, runner=runner)

    assert result["ok"] is False
    assert "未提交改动" in result["blocked"]
    assert not any("clone" in " ".join(call) for call in runner.calls)


def test_existing_checkout_with_another_origin_is_blocked(tmp_path):
    root = tmp_path / "ws"
    (root / "horizon").mkdir(parents=True)
    _seed_repo(root / "horizon")
    runner = Runner(origin="https://example.com/other.git")

    result = workspace.acquire(root, runner=runner)

    assert result["ok"] is False
    assert result["existing"]["state"] == "other_origin"
    assert "origin" in result["blocked"]


def test_data_root_inside_the_code_checkout_is_rejected(tmp_path):
    with pytest.raises(HostRunError):
        workspace.resolve_layout(tmp_path, data_dirname="horizon/horizon-archive")
    with pytest.raises(HostRunError):
        workspace.resolve_layout(tmp_path, code_dirname="same", data_dirname="same")


def test_acquire_rejects_a_data_root_inside_the_code_checkout(tmp_path):
    root = tmp_path / "ws"
    repo = _seed_repo(root / "horizon")
    before = _files_under(repo)

    result = workspace.acquire(root, data_dirname="horizon/archive")

    assert result["ok"] is False
    assert result["action"] == "rejected"
    assert result["layout_valid"] is False
    assert "资料目录不能放在代码目录内" in result["blocked"]
    assert _files_under(repo) == before
    assert not (repo / "archive").exists()
    assert not workspace.record_path(root).exists()


def test_cli_workspace_rejection_reports_the_reason_and_writes_nothing(tmp_path, capsys):
    from hostrun import cli, install

    root = tmp_path / "ws"
    repo = _seed_repo(root / "horizon")
    before = _files_under(repo)
    data_root = tmp_path / "code" / ".horizon"

    code = cli.main(
        [
            *cli_locations(data_root),
            "install",
            "workspace",
            "--root",
            str(root),
            "--data-dir",
            "horizon/archive",
        ]
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["action"] == "rejected"
    assert "资料目录不能放在代码目录内" in payload["blocked"]
    assert _files_under(repo) == before
    assert not workspace.record_path(root).exists()
    assert not (root / "horizon" / "archive").exists()
    entry = install.load_state(data_root)["steps"]["workspace"]
    assert entry["status"] == "blocked"
    assert "资料目录不能放在代码目录内" in entry["note"]


def test_reusable_checkout_is_not_cloned_again(tmp_path):
    root = tmp_path / "ws"
    repo = root / "horizon"
    repo.mkdir(parents=True)
    _seed_repo(repo)
    runner = Runner()

    result = workspace.acquire(root, runner=runner)

    assert result["ok"] is True
    assert result["action"] == "reuse"
    assert not any("clone" in " ".join(call) for call in runner.calls)


def test_interrupted_clone_is_moved_aside_and_retried(tmp_path):
    root = tmp_path / "ws"
    repo = root / "horizon"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "partial.txt").write_text("leftover", encoding="utf-8")
    (root / ".horizon").mkdir()
    (root / ".horizon" / "clone-in-progress.json").write_text(
        json.dumps({"repo": str(repo), "started_at": "2026-10-27T10:00:00+08:00"}),
        encoding="utf-8",
    )

    def runner(argv, **kwargs):
        if argv[:2] == ["git", "-C"] and "status" in argv:
            if not (repo / "common.py").exists():
                return type("P", (), {"returncode": 128, "stdout": "", "stderr": "not a git repository"})()
            return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if argv[:2] == ["git", "-C"] and "get-url" in argv:
            return type("P", (), {"returncode": 0, "stdout": workspace.DEFAULT_REPO_URL, "stderr": ""})()
        if argv[:2] == ["git", "-C"] and "rev-parse" in argv:
            return type("P", (), {"returncode": 0, "stdout": "abc123", "stderr": ""})()
        if "clone" in " ".join(argv):
            _seed_repo(repo)
            return type("P", (), {"returncode": 0, "stdout": "cloned", "stderr": ""})()
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    result = workspace.acquire(root, runner=runner)

    assert result["ok"] is True
    assert result["recovered_partial_clone"]["archived"]
    assert Path(result["recovered_partial_clone"]["archived"]).exists()
    assert (repo / "common.py").exists()
    assert not (root / ".horizon" / "clone-in-progress.json").exists()


# ---------------------------------------------------------------------- uv
def _zip_with_uv(binary="uv.exe") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"uv-x86_64-pc-windows-msvc/{binary}", b"fake-uv")
    return buffer.getvalue()


def test_missing_uv_is_installed_privately_in_the_data_root(tmp_path):
    home = tmp_path / "horizon-archive"
    blob = _zip_with_uv()
    result = workspace.install_uv(
        home,
        asset="uv-x86_64-pc-windows-msvc.zip",
        fetch=lambda url: blob,
    )

    assert result["ok"] is True
    installed = Path(result["path"])
    assert installed.parent == home / "bin"
    assert installed.read_bytes() == b"fake-uv"


def test_missing_uv_reports_blocked_when_download_fails(tmp_path):
    def broken(url):
        raise OSError("no network")

    result = workspace.install_uv(
        tmp_path / "home",
        asset="uv-x86_64-pc-windows-msvc.zip",
        fetch=broken,
    )

    assert result["ok"] is False
    assert result["reason"] == "uv_install_failed"


def test_uv_is_reused_when_already_present(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    home = tmp_path / "home"
    binary = home / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fake")
    runner = Runner()

    result = workspace.ensure_uv(home, runner=runner)

    assert result["ok"] is True
    assert Path(result["path"]) == binary
    assert result["source"] == "private"


def test_uv_is_resolved_from_outside_the_workspace_first(tmp_path, monkeypatch):
    home = tmp_path / "home"
    private = home / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    private.parent.mkdir(parents=True)
    private.write_bytes(b"private-uv")
    recorded = tmp_path / "tools" / ("uv.exe" if os.name == "nt" else "uv")
    recorded.parent.mkdir(parents=True)
    recorded.write_bytes(b"recorded-uv")
    system_dir = tmp_path / "system"
    system_dir.mkdir()
    system = system_dir / ("uv.exe" if os.name == "nt" else "uv")
    system.write_bytes(b"system-uv")
    runner = ToolchainRunner()

    found = workspace.ensure_uv(home, recorded=str(recorded), runner=runner)
    assert found["source"] == "recorded"
    assert Path(found["path"]) == recorded

    monkeypatch.setenv("PATH", str(system_dir))
    found = workspace.ensure_uv(home, runner=runner)
    assert found["source"] == "path"
    assert Path(found["path"]) == system

    monkeypatch.setenv("PATH", "")
    found = workspace.ensure_uv(home, runner=runner)
    assert found["source"] == "private"
    assert Path(found["path"]) == private


# --------------------------------------------------------------- toolchain
def test_toolchain_creates_the_environment_in_the_data_root(tmp_path):
    root, repo, home = _workspace(tmp_path)
    uv = _uv_binary(tmp_path)
    runner = ToolchainRunner()

    result = workspace.toolchain(root, uv_path=str(uv), runner=runner)

    assert result["ok"] is True, result.get("note")
    python = workspace.environment_python(home)
    venv = workspace.environment_venv(home)
    assert python.is_file()
    assert python == venv / PYTHON_REL
    assert python.is_relative_to(home)
    assert not python.is_relative_to(repo)
    assert result["environment"]["probe"]["ok"] is True
    record = workspace.load_record(root)
    assert record["python"] == str(python)
    assert record["venv"] == str(venv)
    config = _config(home)
    assert config["paths"]["python"] == str(python)
    assert config["paths"]["uv"] == str(uv)
    assert not (repo / ".venv").exists()


def test_toolchain_reuses_an_existing_environment_without_rebuilding(tmp_path):
    root, repo, home = _workspace(tmp_path)
    uv = _uv_binary(tmp_path)
    first = workspace.toolchain(root, uv_path=str(uv), runner=ToolchainRunner())
    assert first["ok"] is True, first.get("note")
    sentinel = workspace.environment_venv(home) / "keep-me.txt"
    sentinel.write_text("user state", encoding="utf-8")

    runner = ToolchainRunner()
    second = workspace.toolchain(root, uv_path=str(uv), runner=runner)

    assert second["ok"] is True
    assert second["environment"]["created"] is False
    assert second["environment"]["repaired"] is False
    assert sentinel.exists()
    assert not runner.ran("venv")
    assert workspace.load_record(root)["environment"]["created"] is False


@pytest.mark.parametrize("breakage", ["missing_python", "failing_probe"])
def test_toolchain_blocks_a_broken_interpreter_with_a_repair_command(
    tmp_path, breakage
):
    root, repo, home = _workspace(tmp_path)
    venv = workspace.environment_venv(home)
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = retired\n", encoding="utf-8")
    runner = ToolchainRunner(import_ok=breakage != "failing_probe")
    if breakage == "failing_probe":
        python = workspace.environment_python(home)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("broken", encoding="utf-8")

    result = workspace.toolchain(root, uv_path=str(_uv_binary(tmp_path)), runner=runner)

    assert result["ok"] is False
    assert result["status"] == "blocked"
    note = result["note"]
    assert "install toolchain" in note
    assert "--clear" in note
    assert str(root) in note
    repair = note.split("修复：", 1)[1]
    assert repair.startswith(("cd /d ", "cd "))
    assert not runner.ran("venv")
    assert not (home / "install" / "config.json").exists()
    if breakage == "failing_probe":
        assert "yaml" in result["environment"]["probe"]["error"]


def test_repair_command_changes_directory_before_running(tmp_path):
    root = tmp_path / "ws"
    home = root / "horizon-archive"
    command = workspace.repair_command(root, home)

    if os.name == "nt":
        assert command.startswith(f'cd /d "{root}" && ')
    else:
        assert command.startswith(f"cd {root} && ")
    assert "install toolchain" in command
    assert "--clear" in command
    assert str(home) in command


def test_toolchain_clear_rebuilds_a_broken_interpreter_and_rerun_is_ok(tmp_path):
    root, repo, home = _workspace(tmp_path)
    venv = workspace.environment_venv(home)
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = retired\n", encoding="utf-8")
    uv = _uv_binary(tmp_path)

    broken = workspace.toolchain(root, uv_path=str(uv), runner=ToolchainRunner())
    assert broken["ok"] is False

    repaired = workspace.toolchain(
        root, uv_path=str(uv), runner=ToolchainRunner(), clear=True
    )

    assert repaired["ok"] is True, repaired.get("note")
    assert repaired["environment"]["repaired"] is True
    assert workspace.environment_python(home).is_file()
    assert workspace.load_record(root)["python"] == str(
        workspace.environment_python(home)
    )

    again = workspace.toolchain(root, uv_path=str(uv), runner=ToolchainRunner())
    assert again["ok"] is True
    assert again["environment"]["created"] is False
    assert again["environment"]["repaired"] is False


def test_recorded_interpreter_that_disappeared_blocks_until_cleared(tmp_path):
    root, repo, home = _workspace(tmp_path)
    uv = _uv_binary(tmp_path)
    first = workspace.toolchain(root, uv_path=str(uv), runner=ToolchainRunner())
    assert first["ok"] is True, first.get("note")
    shutil.rmtree(workspace.environment_venv(home))

    runner = ToolchainRunner()
    blocked = workspace.toolchain(root, uv_path=str(uv), runner=runner)

    assert blocked["ok"] is False
    assert blocked["status"] == "blocked"
    assert "--clear" in blocked["note"]
    assert not runner.ran("venv")

    repaired = workspace.toolchain(
        root, uv_path=str(uv), runner=ToolchainRunner(), clear=True
    )
    assert repaired["ok"] is True
    assert repaired["environment"]["repaired"] is True
    assert workspace.environment_python(home).is_file()


# --------------------------------------------------- environment building
def test_fresh_environment_is_created_by_uv(tmp_path):
    repo = _seed_repo(tmp_path / "repo")
    home = tmp_path / "horizon-archive"
    calls = []

    def runner(argv, **kwargs):
        calls.append(list(argv))

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        if argv[1:2] == ["venv"]:
            python = Path(argv[-1]) / PYTHON_REL
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
        return Proc()

    result = workspace.ensure_environment(
        repo,
        home,
        uv="uv",
        python_version="3.13",
        runner=runner,
        verify=lambda path: {"ok": path.exists()},
    )

    assert result["ok"] is True
    assert result["created"] is True
    assert result["repaired"] is False
    assert Path(result["python"]) == workspace.environment_python(home)
    assert Path(result["python"]).is_relative_to(home)
    assert not Path(result["python"]).is_relative_to(repo)
    assert any(call[1:2] == ["venv"] for call in calls)
    assert not any("--clear" in call for call in calls)
    assert any(call[1:2] == ["pip"] for call in calls)


def test_broken_environment_is_repaired_with_clear(tmp_path):
    repo = _seed_repo(tmp_path / "repo")
    home = tmp_path / "horizon-archive"
    venv = workspace.environment_venv(home)
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = retired\n", encoding="utf-8")
    calls = []

    def runner(argv, **kwargs):
        calls.append(list(argv))

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    result = workspace.ensure_environment(
        repo,
        home,
        uv="uv",
        runner=runner,
        verify=lambda path: {"ok": True},
        clear=True,
    )

    assert result["ok"] is True
    assert result["repaired"] is True
    assert any("--clear" in call for call in calls)


def test_failed_uv_venv_reports_blocked(tmp_path):
    repo = _seed_repo(tmp_path / "repo")
    home = tmp_path / "horizon-archive"

    def runner(argv, **kwargs):
        class Proc:
            returncode = 2
            stdout = ""
            stderr = "download failed"

        return Proc()

    result = workspace.ensure_environment(
        repo, home, uv="uv", runner=runner, verify=lambda path: {"ok": True}
    )

    assert result["ok"] is False
    assert "创建私有环境失败" in result["note"]


def test_import_probe_failure_is_blocked(tmp_path):
    repo = _seed_repo(tmp_path / "repo")
    home = tmp_path / "horizon-archive"

    def runner(argv, **kwargs):
        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    result = workspace.ensure_environment(
        repo,
        home,
        uv="uv",
        runner=runner,
        verify=lambda path: {"ok": False, "error": "No module named 'yaml'"},
    )

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "无法导入" in result["note"]


# -------------------------------------------------------------------- skill


# ------------------------------------------------ #97 pointer identity/digest


def _package_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "pkgws"
    repo = _seed_repo(root / "code")
    shutil.rmtree(repo / ".git")
    (repo / "bin").mkdir()
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
    _package_lock(repo)
    home = repo / ".horizon"
    return repo, home, root / "host-skills" / "horizon-run"

def test_workspace_reports_current_package_without_external_skill(tmp_path):
    repo, home, former_pointer = _package_tree(tmp_path)
    workspace.save_record(repo, {
        "root": str(repo), "repo": str(repo), "home": str(home),
        "distribution": "package", "version": "1.0.0",
        "derived_from": str(dist.lock_path(repo)), "skill": str(former_pointer / "SKILL.md"),
    })

    status = workspace.status(repo)

    assert status["ok"]
    assert status["version"] == "1.2.3"
    assert "skill" not in status
    assert not former_pointer.exists()
    assert workspace.load_record(repo)["version"] == "1.0.0"


def test_workspace_reports_missing_package_wrapper(tmp_path):
    repo, _, _ = _package_tree(tmp_path)
    wrapper = repo / "bin" / "horizon.cmd"
    wrapper.unlink()

    status = workspace.status(repo)

    assert not status["ok"] and status["status"] == "blocked"
    assert "package_wrapper_missing" == status["error"]
    assert any(str(wrapper) in problem for problem in status["problems"])


def test_source_workspace_reports_missing_interpreter_and_uv(tmp_path):
    root, repo, home = _workspace(tmp_path)
    missing_python = home / "missing-python.exe"
    missing_uv = home / "missing-uv.exe"
    workspace.save_record(root, {
        **workspace.load_record(root), "python": str(missing_python), "uv": str(missing_uv)
    })

    status = workspace.status(root)

    assert not status["ok"] and status["status"] == "blocked"
    assert any(str(missing_python) in problem for problem in status["problems"])
    assert any(str(missing_uv) in problem for problem in status["problems"])
