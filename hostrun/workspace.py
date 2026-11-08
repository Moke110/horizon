"""Installation workspace: code, data root, private uv/Python and the run skill.

The host agent owns installation (ADR 0005).  This module makes the first
phase checkable instead of conversational:

    root/                         installation workspace (host project folder)
      .horizon/install.json      resolved code / data / interpreter / skill paths
      horizon/                   code checkout (never the data root)
      horizon-archive/           data root (never inside the code checkout)
        bin/uv(.exe)              private uv fallback, outside the code checkout
        .venv/                    private environment created and used by uv
      <host skills dir>/horizon-run/SKILL.md   small launcher, not a repo copy

Rules that the functions below keep:

- an existing checkout is inspected first and never overwritten or pulled when
  it has local changes or a different origin;
- uv is resolved from outside the workspace first (recorded or system uv), then
  a uv installed into the data root; nothing asks the user to edit PATH;
- the private environment and its interpreter live in the data root, never in
  the code checkout, and the absolute interpreter path is recorded in both the
  workspace pointer and the install configuration;
- no environment variable, PATH entry, registry value, service or OS task is
  written;
- a failed phase returns ``ok: false`` with an actionable reason and stays
  resumable: rerunning reuses everything already verified, and a broken
  interpreter is reported as blocked with the exact ``--clear`` repair command
  instead of being silently rebuilt.
"""

from __future__ import annotations

import io
import os
import platform as platform_mod
import re
import shlex
import stat
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

from common import ensure_dir, now, read_json, sha256_file, write_json
from hostrun import dist as dist_mod
from hostrun.base import HostRunError, resolve_executable, update_config

SCHEMA_VERSION = 1
RECORD_REL = Path(".horizon") / "install.json"
DEFAULT_CODE_DIRNAME = "horizon"
DEFAULT_DATA_DIRNAME = "horizon-archive"
DEFAULT_REPO_URL = "https://github.com/Moke110/horizon.git"
DEFAULT_PYTHON = "3.13"
ENV_DIRNAME = ".venv"
SKILL_NAME = "horizon-run"
SKILL_ENTRY = Path("skills") / SKILL_NAME / "SKILL.md"
SKILL_REFERENCES = (
    Path("skills") / SKILL_NAME / "references" / "installation.md",
    Path("skills") / SKILL_NAME / "references" / "workflow.md",
)
REPO_FILES = (
    Path("common.py"),
    Path("requirements.txt"),
    Path("contracts") / "README.md",
    SKILL_ENTRY,
    *SKILL_REFERENCES,
)
UV_ASSETS = {
    ("Windows", "AMD64"): "uv-x86_64-pc-windows-msvc.zip",
    ("Windows", "ARM64"): "uv-aarch64-pc-windows-msvc.zip",
    ("Linux", "x86_64"): "uv-x86_64-unknown-linux-gnu.tar.gz",
    ("Linux", "aarch64"): "uv-aarch64-unknown-linux-gnu.tar.gz",
    ("Darwin", "x86_64"): "uv-x86_64-apple-darwin.tar.gz",
    ("Darwin", "arm64"): "uv-aarch64-apple-darwin.tar.gz",
}
UV_RELEASE = "https://github.com/astral-sh/uv/releases/latest/download/{asset}"


# ------------------------------------------------------------------ helpers
def _run(
    argv: list[str],
    *,
    runner=subprocess.run,
    timeout: int = 300,
    cwd: str | Path | None = None,
    env: dict | None = None,
) -> dict:
    """One bounded process call; never prints credentials or raw stdout."""
    kwargs = {"capture_output": True, "text": True, "timeout": timeout, "cwd": cwd}
    if env is not None:
        kwargs["env"] = env
    try:
        proc = runner(argv, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "code": None, "out": "", "err": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "out": (proc.stdout or "").strip(),
        "err": (proc.stderr or "").strip(),
    }


def _tail(text: str, limit: int = 400) -> str:
    text = " ".join((text or "").split())
    return text[-limit:]


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def environment_venv(home: str | Path) -> Path:
    """The private environment directory inside the data root."""
    return Path(home).expanduser().resolve() / ENV_DIRNAME


def environment_python(home: str | Path) -> Path:
    """The absolute interpreter path installation records and later reuses."""
    return _venv_python(environment_venv(home))


def repair_command(root: str | Path, home: str | Path, *, code: str | Path | None = None) -> str:
    """The exact command that rebuilds a missing or broken interpreter.

    The command first changes into the code checkout, so the host can run it
    from any working directory.
    """
    root_path = Path(root).expanduser().resolve()
    home_path = Path(home).expanduser().resolve()
    flags = ""
    if code is not None:
        flags += f'--code "{Path(code).expanduser().resolve()}" '
    flags += f'--archive "{home_path}"'
    if os.name == "nt":
        change = f'cd /d "{root_path}" && '
    else:
        change = f"cd {shlex.quote(str(root_path))} && "
    return (
        change
        + f'"{sys.executable or "python"}" -m hostrun.cli '
        f"{flags} install toolchain "
        f'--root "{root_path}" --clear'
    )


def normalize_url(url: str) -> str:
    text = str(url or "").strip().lower()
    if text.endswith(".git"):
        text = text[:-4]
    return text.rstrip("/")


def resolve_layout(
    root: str | Path,
    *,
    code_dirname: str = DEFAULT_CODE_DIRNAME,
    data_dirname: str = DEFAULT_DATA_DIRNAME,
) -> dict:
    """Resolve and validate the two independent directories under ``root``."""
    root_path = Path(root).expanduser().resolve()
    code = Path(code_dirname).expanduser()
    data = Path(data_dirname).expanduser()
    repo = (code if code.is_absolute() else root_path / code).resolve()
    home = (data if data.is_absolute() else root_path / data).resolve()
    if repo == home:
        raise HostRunError("代码目录与资料目录不能是同一个目录")
    try:
        home.relative_to(repo)
    except ValueError:
        pass
    else:
        raise HostRunError("资料目录不能放在代码目录内，否则升级会覆盖研究数据")
    return {"root": str(root_path), "repo": str(repo), "home": str(home)}


def record_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / RECORD_REL


def load_record(root: str | Path) -> dict:
    return read_json(record_path(root)) or {}


def save_record(root: str | Path, record: dict) -> Path:
    path = record_path(root)
    write_json(path, {**record, "schema_version": SCHEMA_VERSION, "updated_at": now()})
    return path


def locate(start: str | Path | None = None) -> dict:
    """Find the nearest workspace record, so a moved skill can still locate code."""
    current = Path(start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        record = load_record(directory)
        if record:
            return record
    return {}


# ------------------------------------------------------------- code checkout
def git_facts(repo: str | Path, *, runner=subprocess.run) -> dict:
    repo = Path(repo)
    status = _run(["git", "-C", str(repo), "status", "--porcelain"], runner=runner)
    if not status["ok"]:
        return {"ok": False, "error": _tail(status["err"] or status["out"]) or "git status failed"}
    origin = _run(["git", "-C", str(repo), "remote", "get-url", "origin"], runner=runner)
    commit = _run(["git", "-C", str(repo), "rev-parse", "HEAD"], runner=runner)
    return {
        "ok": True,
        "dirty": bool(status["out"].strip()),
        "dirty_files": [line[3:] for line in status["out"].splitlines() if line.strip()][:20],
        "origin": origin["out"] if origin["ok"] else "",
        "commit": commit["out"] if commit["ok"] else "",
    }


def inspect_checkout(repo: str | Path, *, repo_url: str = DEFAULT_REPO_URL, runner=subprocess.run) -> dict:
    """What is already at the code path — without changing a single byte."""
    repo = Path(repo)
    if not repo.exists():
        return {"state": "absent", "path": str(repo)}
    if not repo.is_dir():
        return {"state": "not_a_directory", "path": str(repo)}
    entries = list(repo.iterdir())
    if not entries:
        return {"state": "empty", "path": str(repo)}
    if not (repo / ".git").exists():
        return {"state": "not_a_git_repo", "path": str(repo), "entries": len(entries)}
    facts = git_facts(repo, runner=runner)
    if not facts["ok"]:
        return {"state": "git_error", "path": str(repo), "error": facts["error"]}
    if repo_url and normalize_url(facts["origin"]) != normalize_url(repo_url):
        return {"state": "other_origin", **facts, "path": str(repo)}
    if facts["dirty"]:
        return {"state": "dirty", **facts, "path": str(repo)}
    return {"state": "reusable", **facts, "path": str(repo)}


def _clone(url: str, repo: Path, *, runner=subprocess.run, timeout: int = 900) -> dict:
    ensure_dir(repo.parent)
    result = _run(
        ["git", "clone", "--depth", "1", url, str(repo)],
        runner=runner,
        timeout=timeout,
    )
    return {"ok": result["ok"], "error": _tail(result["err"] or result["out"]), "result": result}


def _clone_marker(root: Path) -> Path:
    return Path(root) / ".horizon" / "clone-in-progress.json"


def _write_clone_marker(root: Path, repo: Path) -> None:
    path = _clone_marker(root)
    ensure_dir(path.parent)
    write_json(path, {"repo": str(repo), "started_at": now()})


def _clear_clone_marker(root: Path) -> None:
    _clone_marker(root).unlink(missing_ok=True)


def _recover_partial_clone(root: Path, repo: Path) -> dict | None:
    """Move an interrupted clone aside and retry, never deleting user files.

    Only a checkout left by this installer (marker present) and broken enough
    that git cannot run is moved; the original bytes stay under a new name.
    """
    marker = _clone_marker(root)
    if not marker.exists():
        return None
    if not repo.exists():
        _clear_clone_marker(root)
        return None
    archived = repo.with_name(f"{repo.name}.incomplete-{int(time.time())}")
    try:
        repo.rename(archived)
    except OSError as exc:
        return {
            "blocked": (
                f"检测到上次中断的代码副本 {repo}，但无法移到 {archived}：{exc}；"
                "请手动移走后重试"
            ),
            "archived": str(archived),
        }
    _clear_clone_marker(root)
    return {"archived": str(archived)}


def _candidate_path(root_path: Path, dirname: str) -> Path:
    candidate = Path(dirname).expanduser()
    return (candidate if candidate.is_absolute() else root_path / candidate).resolve()


def acquire(
    root: str | Path,
    *,
    repo_url: str = DEFAULT_REPO_URL,
    code_dirname: str = DEFAULT_CODE_DIRNAME,
    data_dirname: str = DEFAULT_DATA_DIRNAME,
    dry_run: bool = False,
    runner=subprocess.run,
) -> dict:
    """Acquire code safely and create the data root; never overwrite user work.

    A data root that would sit inside the code checkout is refused before any
    directory is created, and the refusal carries the reason plus the offending
    candidate so the caller can report it instead of writing anywhere.
    """
    root_path = Path(root).expanduser().resolve()
    try:
        layout = resolve_layout(root, code_dirname=code_dirname, data_dirname=data_dirname)
    except HostRunError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "dry_run": dry_run,
            "action": "rejected",
            "layout_valid": False,
            "root": str(root_path),
            "repo": str(_candidate_path(root_path, code_dirname)),
            "home": str(_candidate_path(root_path, data_dirname)),
            "code_dirname": code_dirname,
            "data_dirname": data_dirname,
            "blocked": str(exc),
        }
    repo, home = Path(layout["repo"]), Path(layout["home"])
    existing = inspect_checkout(repo, repo_url=repo_url, runner=runner)
    recovered = None
    if existing["state"] in {"git_error", "not_a_git_repo"}:
        recovery = _recover_partial_clone(Path(layout["root"]), repo)
        if recovery and recovery.get("blocked"):
            return {
                "schema_version": SCHEMA_VERSION,
                "ok": False,
                "dry_run": dry_run,
                "action": "blocked",
                "layout_valid": True,
                "root": layout["root"],
                "repo": layout["repo"],
                "home": layout["home"],
                "repo_url": repo_url,
                "blocked": recovery["blocked"],
            }
        if recovery:
            recovered = recovery
            existing = inspect_checkout(repo, repo_url=repo_url, runner=runner)

    blocked = None
    if existing["state"] in {"absent", "empty"}:
        action = "clone"
        if not dry_run:
            _write_clone_marker(Path(layout["root"]), repo)
            clone = _clone(repo_url, repo, runner=runner)
            if not clone["ok"]:
                blocked = f"代码获取失败：{clone['error'] or 'git clone 退出非零'}；重跑会移开不完整副本后重试"
                action = "clone_failed"
            else:
                _clear_clone_marker(Path(layout["root"]))
    elif existing["state"] == "reusable":
        action = "reuse"
    elif existing["state"] == "dirty":
        action = "blocked"
        blocked = (
            "已有代码副本存在未提交改动，未拉取或覆盖；请先提交/备份，"
            "或改用另一个工作区"
        )
    elif existing["state"] == "other_origin":
        action = "blocked"
        blocked = f"已有代码副本的 origin 是 {existing.get('origin') or '<空>'}，与 {repo_url} 不符"
    elif existing["state"] == "not_a_git_repo":
        action = "blocked"
        blocked = "代码目录已存在但不是 git 仓库；未覆盖，请改用空工作区或自行移走"
    else:
        action = "blocked"
        blocked = f"无法检查已有代码副本：{existing.get('error') or existing['state']}"

    data_created = False
    data_entries = 0
    if home.exists():
        data_entries = len(list(home.iterdir())) if home.is_dir() else 0
    elif not dry_run:
        ensure_dir(home)
        data_created = True
    elif dry_run:
        data_created = True

    facts = {}
    if not dry_run and (repo / ".git").exists():
        facts = git_facts(repo, runner=runner)

    result = {
        "schema_version": SCHEMA_VERSION,
        "ok": blocked is None,
        "dry_run": dry_run,
        "action": action,
        "layout_valid": True,
        "root": layout["root"],
        "repo": layout["repo"],
        "home": layout["home"],
        "repo_url": repo_url,
        "origin": facts.get("origin", ""),
        "commit": facts.get("commit", ""),
        "dirty": bool(facts.get("dirty")),
        "data_root_created": data_created,
        "data_root_entries": data_entries,
        "recovered_partial_clone": recovered,
        "existing": {k: v for k, v in existing.items() if k != "dirty_files"},
    }
    if blocked:
        result["blocked"] = blocked
        return result

    if not dry_run and facts:
        record = {
            **{k: result[k] for k in ("root", "repo", "home", "repo_url", "origin", "commit")},
            "code_dirname": code_dirname,
            "data_dirname": data_dirname,
        }
        previous = load_record(root)
        save_record(root, {**previous, **record})
    return result


def record_source_install(result: dict) -> dict:
    """Write ``<code>/.horizon/dist.lock.json`` for a source installation.

    ``install workspace`` is where a source installation is established, so
    that CLI branch calls this with the :func:`acquire` result.  The lock records
    git evidence only (#97); a package installation is never overwritten.
    The helper is separate from :func:`acquire` so acquiring a checkout keeps
    its documented "touches no file inside the checkout" contract.
    """
    repo_text = str(result.get("repo") or "")
    if (
        not result.get("ok")
        or result.get("dry_run")
        or not result.get("layout_valid", True)
        or not repo_text
    ):
        return {
            "ok": False,
            "skipped": True,
            "reason": "workspace_not_established",
        }
    repo = Path(repo_text)
    if not (repo / ".git").exists():
        return {
            "ok": False,
            "skipped": True,
            "reason": "not_a_source_checkout",
            "lock": str(dist_mod.lock_path(repo)),
        }
    try:
        return dist_mod.write_source_lock(
            repo,
            commit=str(result.get("commit") or ""),
            dirty=bool(result.get("dirty")),
            source_url=str(result.get("origin") or result.get("repo_url") or ""),
        )
    except OSError as exc:
        return {
            "ok": False,
            "skipped": False,
            "reason": "source_lock_write_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "lock": str(dist_mod.lock_path(repo)),
        }


# ------------------------------------------------------------------ toolchain
def uv_asset(platform_name: str | None = None, machine: str | None = None) -> str:
    name = platform_name or platform_mod.system()
    arch = machine or platform_mod.machine()
    if (name, arch) not in UV_ASSETS:
        raise HostRunError(f"没有适用于 {name}/{arch} 的 uv 安装包映射")
    return UV_ASSETS[(name, arch)]


def _extract_uv(blob: bytes, asset: str, target_dir: Path) -> Path:
    ensure_dir(target_dir)
    binary = "uv.exe" if asset.endswith(".zip") else "uv"
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            member = next((n for n in archive.namelist() if n.endswith(binary)), None)
            if not member:
                raise HostRunError("uv 安装包中找不到可执行文件")
            data = archive.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
            member = next((m for m in archive.getmembers() if m.name.endswith("/" + binary) or m.name == binary), None)
            if not member:
                raise HostRunError("uv 安装包中找不到可执行文件")
            data = archive.extractfile(member).read()
    path = target_dir / binary
    path.write_bytes(data)
    if os.name != "nt":
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _private_uv_path(home: Path) -> Path:
    return Path(home).expanduser().resolve() / "bin" / ("uv.exe" if os.name == "nt" else "uv")


def install_uv(
    home: str | Path,
    *,
    asset: str | None = None,
    fetch=None,
    platform_name: str | None = None,
    machine: str | None = None,
) -> dict:
    """Install uv privately under ``<home>/bin`` (no PATH write)."""
    asset = asset or uv_asset(platform_name, machine)
    target_dir = Path(home).expanduser().resolve() / "bin"
    url = UV_RELEASE.format(asset=asset)
    if fetch is None:
        import urllib.request

        def fetch(address: str) -> bytes:  # pragma: no cover - network path
            with urllib.request.urlopen(address, timeout=120) as response:
                return response.read()

    try:
        blob = fetch(url)
        path = _extract_uv(blob, asset, target_dir)
    except HostRunError as exc:
        return {"ok": False, "reason": "uv_install_failed", "error": str(exc), "url": url}
    except Exception as exc:  # noqa: BLE001 - download/extract failures are results
        return {"ok": False, "reason": "uv_install_failed", "error": _tail(str(exc)), "url": url}
    return {"ok": True, "path": str(path), "source": "private", "url": url}


def _uv_blocked(note: str, path: str | Path | None = None) -> dict:
    result = {"ok": False, "status": "blocked", "note": note}
    if path is not None:
        result["evidence"] = {"source": "explicit", "path": str(path)}
    return result


def ensure_uv(
    home: str | Path,
    *,
    uv_path: str | None = None,
    recorded: str | None = None,
    fetch=None,
    runner=subprocess.run,
    platform_name: str | None = None,
    machine: str | None = None,
) -> dict:
    """Resolve uv from outside the workspace first, then the data-root private copy.

    Order: explicit ``--uv``, the recorded uv, a read-only PATH lookup, then
    ``<home>/bin``; only when none is usable is uv downloaded into the data
    root. A candidate that cannot even answer ``--version`` is skipped, so one
    stale recorded path cannot mask a working system uv.
    """
    home_path = Path(home).expanduser().resolve()
    private = _private_uv_path(home_path)
    candidates: list[tuple[str, str]] = []
    if uv_path:
        found = resolve_executable(uv_path)
        if not found["found"]:
            return _uv_blocked(
                f"指定的 uv 不存在：{uv_path}；请给出 uv 可执行文件的绝对路径或省略 --uv",
                uv_path,
            )
        candidates.append(("explicit", found["path"]))
    else:
        if recorded and Path(recorded).expanduser().is_file():
            candidates.append(("recorded", str(Path(recorded).expanduser())))
        system = resolve_executable(None, names=["uv", "uv.exe"])
        if system["found"]:
            candidates.append((system["source"], system["path"]))
    if private.is_file():
        candidates.append(("private", str(private)))

    for source, path in candidates:
        version = _run([path, "--version"], runner=runner, timeout=60)
        if version["ok"]:
            return {
                "ok": True,
                "status": "ok",
                "path": path,
                "source": source,
                "version": version["out"].splitlines()[0] if version["out"] else "",
                "installed": None,
            }
    if uv_path:
        return _uv_blocked(
            f"指定的 uv 无法运行：{uv_path}；请修复该可执行文件或改用系统 uv",
            uv_path,
        )

    installed = install_uv(
        home_path, fetch=fetch, platform_name=platform_name, machine=machine
    )
    if not installed["ok"]:
        return {
            "ok": False,
            "status": "blocked",
            "note": "未找到 uv，私有安装也失败：" + installed["error"],
            "evidence": {"source": "missing", "install": installed},
        }
    version = _run([installed["path"], "--version"], runner=runner, timeout=60)
    return {
        "ok": True,
        "status": "ok",
        "path": installed["path"],
        "source": "private",
        "version": version["out"].splitlines()[0] if version["ok"] and version["out"] else "",
        "installed": installed,
    }


def check_runtime(python_path: str | Path, *, runner=subprocess.run) -> dict:
    """The installed interpreter must already import what installation needs."""
    probe = _run(
        [str(python_path), "-c", "import yaml, fitz"],
        runner=runner,
        timeout=120,
    )
    return {
        "ok": probe["ok"],
        "error": _tail(probe["err"] or probe["out"]),
        "python": str(python_path),
    }


def ensure_environment(
    repo: str | Path,
    home: str | Path,
    *,
    uv: str,
    python_version: str = DEFAULT_PYTHON,
    runner=subprocess.run,
    verify=None,
    clear: bool = False,
    repair: str = "",
    expected: str | Path | None = None,
) -> dict:
    """Create the private environment in ``<home>/.venv``, then verify imports.

    An environment recorded by an earlier run that no longer passes the import
    probe — or whose recorded interpreter disappeared — is *not* rebuilt
    silently: the call returns ``blocked`` with the repair command unless the
    caller explicitly asked for ``clear=True``.
    """
    repo = Path(repo)
    home = Path(home).expanduser().resolve()
    venv = environment_venv(home)
    python = _venv_python(venv)
    verify = verify or (lambda path: check_runtime(path, runner=runner))
    requirements = repo / "requirements.txt"

    existing_ok = python.is_file() and verify(python).get("ok")
    if existing_ok:
        return {
            "ok": True,
            "status": "ok",
            "python": str(python),
            "venv": str(venv),
            "uv": str(uv),
            "created": False,
            "repaired": False,
            "probe": {"ok": True, "error": ""},
            "note": "已有私有环境通过依赖自检",
        }

    recorded_missing = bool(expected) and Path(expected) == python
    broken = venv.exists() or python.exists() or recorded_missing
    if broken and not clear:
        hint = repair or "以 --clear 重跑 install toolchain"
        reason = "interpreter_missing" if recorded_missing and not python.exists() else "interpreter_broken"
        if python.is_file():
            probe_error = verify(python).get("error", "") or "解释器导入自检失败"
        else:
            probe_error = f"解释器文件缺失：{python}"
        return {
            "ok": False,
            "status": "blocked",
            "reason": reason,
            "python": str(python),
            "venv": str(venv),
            "uv": str(uv),
            "created": False,
            "repaired": False,
            "probe": {"ok": False, "error": probe_error},
            "note": f"私有解释器不可用：{python}；修复：{hint}",
        }

    ensure_dir(home)
    repaired = broken
    child_env = {
        **os.environ,
        "UV_PYTHON_INSTALL_DIR": str(home / "python"),
        "UV_CACHE_DIR": str(home / "uv-cache"),
        "UV_LINK_MODE": "copy",
    }
    create = [str(uv), "venv", "--python", str(python_version), str(venv)]
    if repaired:
        create.insert(4, "--clear")
    made = _run(create, runner=runner, timeout=900, cwd=str(repo), env=child_env)
    if not made["ok"]:
        return {
            "ok": False,
            "status": "blocked",
            "note": "创建私有环境失败：" + (_tail(made["err"] or made["out"]) or "uv venv 退出非零"),
            "python": str(python),
            "venv": str(venv),
            "uv": str(uv),
            "repaired": repaired,
        }
    if not requirements.exists():
        return {
            "ok": False,
            "status": "blocked",
            "note": f"缺少依赖声明：{requirements}",
            "python": str(python),
            "venv": str(venv),
            "uv": str(uv),
        }
    installed = _run(
        [
            str(uv),
            "pip",
            "install",
            "--python",
            str(python),
            "-r",
            str(requirements),
        ],
        runner=runner,
        timeout=1800,
        cwd=str(repo),
        env=child_env,
    )
    if not installed["ok"]:
        return {
            "ok": False,
            "status": "blocked",
            "note": "安装依赖失败：" + (_tail(installed["err"] or installed["out"]) or "uv pip install 退出非零"),
            "python": str(python),
            "venv": str(venv),
            "uv": str(uv),
            "repaired": repaired,
        }
    checked = verify(python)
    ok = bool(checked.get("ok"))
    note = "" if ok else "依赖装好后仍无法导入 yaml/fitz：" + checked.get("error", "")
    if not ok and repair:
        note += f"；修复：{repair}"
    return {
        "ok": ok,
        "status": "ok" if ok else "blocked",
        "python": str(python),
        "venv": str(venv),
        "uv": str(uv),
        "created": not repaired,
        "repaired": repaired,
        "probe": {"ok": ok, "error": "" if ok else checked.get("error", "")},
        "note": note,
    }


def toolchain(
    root: str | Path,
    *,
    uv_path: str | None = None,
    python_version: str = DEFAULT_PYTHON,
    fetch=None,
    runner=subprocess.run,
    clear: bool = False,
) -> dict:
    """Resolve uv outside the workspace, then build the data-root environment."""
    root_path = Path(root).expanduser().resolve()
    record = load_record(root_path)
    if not record.get("repo") or not record.get("home"):
        raise HostRunError("先执行 install workspace，工作区尚未记录代码与数据目录")
    repo = Path(record["repo"])
    home = Path(record["home"])
    repair = repair_command(root_path, home, code=repo)
    uv = ensure_uv(
        home,
        uv_path=uv_path,
        recorded=record.get("uv"),
        fetch=fetch,
        runner=runner,
    )
    if not uv["ok"]:
        return {
            "ok": False,
            "status": "blocked",
            "uv": uv,
            "note": uv["note"],
            "repair": repair,
        }
    env = ensure_environment(
        repo,
        home,
        uv=uv["path"],
        python_version=python_version,
        runner=runner,
        clear=clear,
        repair=repair,
        expected=record.get("python"),
    )
    result = {
        "ok": env["ok"],
        "status": env["status"],
        "uv": uv,
        "environment": env,
        "repair": repair,
    }
    if not env["ok"]:
        result["note"] = env["note"]
        return result
    save_record(
        root_path,
        {
            **record,
            "uv": uv["path"],
            "uv_source": uv["source"],
            "uv_version": uv.get("version", ""),
            "python": env["python"],
            "python_version": python_version,
            "venv": env["venv"],
            "environment": {
                "created": env.get("created", False),
                "repaired": env.get("repaired", False),
                "imports_ok": bool(env.get("probe", {}).get("ok")),
                "checked_at": now(),
            },
        },
    )
    update_config(
        home,
        paths={
            "uv": uv["path"],
            "python": env["python"],
            "venv": env["venv"],
        },
    )
    result["note"] = env["note"]
    return result


# ---------------------------------------------------------------------- skill
POINTER_DISTRIBUTION = "发行来源"
POINTER_VERSION = "发行版本"
POINTER_CHANNEL = "发行渠道"
POINTER_ARTIFACT = "制品摘要"
POINTER_PACKAGE_SOURCE = "发行包来源"
POINTER_COMMIT = "源码提交"
POINTER_DIRTY = "工作区改动"
POINTER_SOURCE = "源码来源"
POINTER_SKILL_DIGEST = "技能摘要"
POINTER_LOCK = "安装锁"
POINTER_UNRECORDED = "<未记录>"
_POINTER_KEYS = {
    POINTER_DISTRIBUTION: "distribution",
    POINTER_VERSION: "version",
    POINTER_CHANNEL: "channel",
    POINTER_ARTIFACT: "artifact_sha256",
    POINTER_PACKAGE_SOURCE: "source",
    POINTER_COMMIT: "commit",
    POINTER_DIRTY: "dirty",
    POINTER_SOURCE: "source",
    POINTER_SKILL_DIGEST: "skill_digest",
    POINTER_LOCK: "lock",
}
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def entry_digest(repo: str | Path) -> str:
    """SHA-256 of the checkout's ``skills/horizon-run/SKILL.md`` (#97)."""
    return sha256_file(Path(repo) / SKILL_ENTRY)


def _pointer_values(record: dict, lock: dict | None = None) -> dict:
    """The distribution facts the pointer and its verifier agree on."""
    lock = lock if isinstance(lock, dict) else {}
    distribution = str(lock.get("distribution") or "source")
    repo = Path(record.get("repo") or "")
    values = {
        "distribution": distribution,
        "lock": (
            str(dist_mod.lock_path(repo))
            if record.get("repo")
            else POINTER_UNRECORDED
        ),
    }
    if distribution == "package":
        values.update(
            {
                "version": str(lock.get("version") or POINTER_UNRECORDED),
                "channel": str(lock.get("channel") or POINTER_UNRECORDED),
                "artifact_sha256": str(
                    lock.get("artifact_sha256") or POINTER_UNRECORDED
                ),
                "source": str(
                    lock.get("source_url")
                    or record.get("repo_url")
                    or POINTER_UNRECORDED
                ),
            }
        )
    else:
        dirty = lock.get("dirty") if lock else record.get("dirty")
        values.update(
            {
                "version": "source",
                "commit": str(
                    lock.get("commit") or record.get("commit") or ""
                )
                or POINTER_UNRECORDED,
                "dirty": "有" if dirty else "无",
                "source": str(
                    lock.get("source_url")
                    or record.get("repo_url")
                    or record.get("origin")
                    or POINTER_UNRECORDED
                ),
            }
        )
    return values


def _parse_pointer(text: str) -> dict:
    """Read the pointer's ``- key：value`` bullets, ignoring every other line."""
    values: dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("- ") or "：" not in line:
            continue
        key, value = line[2:].split("：", 1)
        field = _POINTER_KEYS.get(key.strip())
        if field:
            values[field] = value.strip()
    return values


def _launcher_text(record: dict, lock: dict | None = None, digest: str = "") -> str:
    values = _pointer_values(record, lock)
    package = values["distribution"] == "package"
    wrapper = Path(record["repo"]) / "bin" / "horizon.cmd"
    lines = [
        f"- 仓库：{record['repo']}",
        f"- 运行时区：{record['home']}",
        f"- 入口点：{wrapper if package else record.get('python') or '<由 install toolchain 记录>'}",
        f"- 解释器：{record.get('python') or '<内置运行时解释器未记录>'}",
        f"- 安装记录：{Path(record['root']) / RECORD_REL}",
        f"- {POINTER_DISTRIBUTION}：{values['distribution']}",
        f"- {POINTER_VERSION}：{values['version']}",
    ]
    if package:
        lines += [
            f"- {POINTER_CHANNEL}：{values['channel']}",
            f"- {POINTER_ARTIFACT}：{values['artifact_sha256']}",
            f"- {POINTER_PACKAGE_SOURCE}：{values['source']}",
        ]
    else:
        lines += [
            f"- {POINTER_COMMIT}：{values['commit']}",
            f"- {POINTER_DIRTY}：{values['dirty']}",
            f"- {POINTER_SOURCE}：{values['source']}",
        ]
    lines += [
        f"- {POINTER_SKILL_DIGEST}：{digest or POINTER_UNRECORDED}",
        f"- {POINTER_LOCK}：{values['lock']}",
    ]
    bullets = "\n".join(lines)
    if package:
        run_step = (
            f"3. 使用上面记录的入口点 `{wrapper}` 执行 horizon 子命令"
            f"（例如 `{wrapper} --code \"{record['repo']}\" --archive \"<资料目录>\" "
            "install verify`）；不要自行拼接 `-m hostrun.cli`。"
        )
    else:
        run_step = "3. 以仓库根为工作目录，用上面记录的解释器执行 `python -m hostrun.cli ...`。"
    return f"""---
name: {SKILL_NAME}
description: "Install, run, pause or recover horizon on this host. Read the repository entry and references before acting."
---

# {SKILL_NAME}

本文件是安装时生成的**入口指针**，不是仓库副本。运行与安装规则以仓库内文件为准。

{bullets}

## 每次唤醒

1. 读取 `{Path(record["repo"]) / SKILL_ENTRY}`。
2. 读取它引用的 `references/installation.md` 与 `references/workflow.md`。
{run_step}
4. 不要根据本文件所在位置推断仓库；路径变化时重新运行 `install skill`。

本文件不含凭据，也不修改环境变量、PATH 或注册表。
"""


def _package_workspace_record(root: str | Path) -> dict:
    """Derive a truthful workspace record from an installed package, or ``{}``.

    A distribution package is installed by ``install package`` and never runs
    ``install workspace``: its evidence is the install lock plus the package
    manifest, not a source checkout.  The record points at the code directory
    itself (which carries ``skills/``), keeps the runtime area as the data root,
    advertises the package's bundled wrapper as the entry point, and records the
    real bundled interpreter in ``python`` when the package carries one.  Nothing
    here invents a git commit or a source lock.

    A derived record carries ``derived_from``: ``register_skill`` uses it to
    refresh the version/commit after an upgrade or rollback without ever
    overwriting a user/source record.
    """
    root_path = Path(root).expanduser().resolve()
    lock = dist_mod.read_lock(root_path)
    if not isinstance(lock, dict) or lock.get("distribution") != "package":
        return {}
    wrapper = root_path / "bin" / "horizon.cmd"
    if not wrapper.is_file():
        return {
            "error": "package_wrapper_missing",
            "root": str(root_path),
            "wrapper": str(wrapper),
            "lock": str(dist_mod.lock_path(root_path)),
        }
    try:
        manifest = read_json(root_path / dist_mod.MANIFEST_FILENAME)
    except (OSError, ValueError):
        manifest = None
    manifest = manifest if isinstance(manifest, dict) else {}
    source_url = str(lock.get("source_url") or "")
    runtime_python = root_path / "runtime" / "python.exe"
    return {
        "root": str(root_path),
        "repo": str(root_path),
        "home": str(root_path / ".horizon"),
        "repo_url": source_url,
        "origin": source_url,
        "commit": str(manifest.get("built_from_commit") or ""),
        "dirty": False,
        "python": str(runtime_python) if runtime_python.is_file() else "",
        "entry": str(wrapper),
        "distribution": "package",
        "version": str(lock.get("version") or ""),
        "channel": str(lock.get("channel") or ""),
        "derived_from": str(dist_mod.lock_path(root_path)),
    }


def register_skill(
    root: str | Path,
    target: str | Path,
    *,
    dry_run: bool = False,
) -> dict:
    """Write a small launcher skill that points at the real repository.

    The pointer records the installation it points at: a package version and
    artifact digest, or the source commit and dirty state (#97).  The digest of
    the checkout's runtime ``SKILL.md`` is written into the pointer and into the
    install lock, so ``verify_skill`` can prove they still agree.
    """
    record = load_record(root)
    derived = _package_workspace_record(root) if not record or record.get("derived_from") else {}
    if derived.get("error") == "package_wrapper_missing":
        return {
            "ok": False,
            "status": "blocked",
            "reason": "package_wrapper_missing",
            "code": derived["root"],
            "wrapper": derived["wrapper"],
            "lock": derived["lock"],
            "note": (
                f"发行包安装缺少启动包装器：{derived['wrapper']}；"
                "请重新执行 install package 修复安装，再注册技能入口"
            ),
        }
    if derived:
        # Refresh every derived package fact (version/commit/python) after an
        # upgrade or rollback, but keep the registered skill paths.
        preserved = {
            key: record[key]
            for key in ("skill", "skill_target")
            if record.get(key)
        }
        record = {**derived, **preserved}
    if not record.get("repo") or not record.get("home"):
        if record:
            # A partial workspace record is authoritative evidence that a
            # source install happened; never paper over it with package fields.
            raise HostRunError("工作区记录不完整；先运行 install workspace 或修复安装记录")
        raise HostRunError("先执行 install workspace，工作区尚未记录代码与数据目录")
    target_dir = Path(target).expanduser().resolve()
    launcher = target_dir / "SKILL.md"
    repo = Path(record["repo"])
    entry = repo / SKILL_ENTRY
    references = [repo / rel for rel in SKILL_REFERENCES]
    missing = [str(path) for path in [entry, *references] if not path.is_file()]
    if missing:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "repo_missing_skill_files",
            "missing": missing,
            "note": "仓库缺少运行技能或参考文件；先更新代码副本",
        }
    if target_dir == entry.parent:
        raise HostRunError("目标目录就是技能源目录；请注册到宿主的技能目录，不要覆盖仓库文件")
    digest = entry_digest(repo)
    lock = dist_mod.read_lock(repo)
    lock_dirty = False
    if lock is None and not (repo / dist_mod.MANIFEST_FILENAME).exists():
        # Backfill a source lock for a pre-#97 source installation so the
        # pointer digest has a lock to live in.  A package tree whose lock went
        # missing is left alone: recreating it as "source" would be a lie.
        lock = dist_mod.source_lock(
            commit=str(record.get("commit") or ""),
            dirty=bool(record.get("dirty")),
            source_url=str(record.get("repo_url") or record.get("origin") or ""),
            skill_digest=digest,
        )
        lock_dirty = True
    elif lock is not None and lock.get("skill_digest") != digest:
        # Record the pointer's digest before the pointer references it, so a
        # verified pointer can never get ahead of the lock it claims.
        lock = {**lock, "skill_digest": digest}
        lock_dirty = True
    distribution = str((lock or {}).get("distribution") or "source")
    lock_file = str(dist_mod.lock_path(repo))
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "skill": str(launcher),
            "target": str(target_dir),
            "entry": str(entry),
            "references": [str(path) for path in references],
            "distribution": distribution,
            "skill_digest": digest,
            "lock": lock_file,
        }
    if lock_dirty:
        try:
            dist_mod.write_lock(repo, lock)
        except OSError as exc:
            return {
                "ok": False,
                "status": "blocked",
                "reason": "skill_lock_not_writable",
                "skill": str(launcher),
                "target": str(target_dir),
                "lock": lock_file,
                "error": f"{type(exc).__name__}: {exc}",
                "note": f"无法写入安装锁 {lock_file}：{exc}",
            }
    try:
        ensure_dir(target_dir)
        launcher.write_text(
            _launcher_text(record, lock, digest), encoding="utf-8"
        )
    except OSError as exc:
        # A host that does not allow writing its skills directory is a missing
        # capability, not a crash: report it as a blocked step with the exact
        # path so the install report stays actionable.
        return {
            "ok": False,
            "status": "blocked",
            "reason": "skill_target_not_writable",
            "skill": str(launcher),
            "target": str(target_dir),
            "error": f"{type(exc).__name__}: {exc}",
            "note": (
                f"无法写入宿主技能目录 {target_dir}：{exc}；"
                "需要宿主允许该路径写入，或把入口指针注册到宿主可写的技能目录"
            ),
        }
    record = {**record, "skill": str(launcher), "skill_target": str(target_dir)}
    save_record(root, record)
    return {
        "ok": True,
        "skill": str(launcher),
        "target": str(target_dir),
        "entry": str(entry),
        "references": [str(path) for path in references],
        "registered": True,
        "distribution": distribution,
        "skill_digest": digest,
        "lock": lock_file,
    }


def verify_skill(record: dict) -> dict:
    """Prove the pointer still names the skill and installation it points at.

    A pre-#97 pointer has none of the new bullets; it is treated as a source
    installation and stays ``ok`` (``legacy_pointer: True``).  A new pointer
    must match the checkout's ``SKILL.md`` digest and the install lock's
    distribution/version or commit; a mismatch is ``blocked`` with specific
    reasons.  A missing lock only blocks a pointer that claims a package
    install; source pointers tolerate it.
    """
    launcher = Path(record.get("skill") or "")
    repo = Path(record.get("repo") or "")
    entry = repo / SKILL_ENTRY
    references = [repo / rel for rel in SKILL_REFERENCES]
    problems: list[str] = []
    text = ""
    if not launcher.is_file():
        problems.append(f"缺少入口指针：{launcher}")
    else:
        text = launcher.read_text(encoding="utf-8", errors="replace")
        if record.get("repo") and record["repo"] not in text:
            problems.append("入口指针未指向记录的仓库")
    for path in [entry, *references]:
        if not path.is_file():
            problems.append(f"缺少参考文件：{path}")
    digest = sha256_file(entry) if entry.is_file() else ""
    lock = dist_mod.read_lock(repo) if record.get("repo") else None
    lock_file = str(dist_mod.lock_path(repo)) if record.get("repo") else ""
    pointer = _parse_pointer(text) if text else {}
    base = {
        "skill": str(launcher),
        "entry": str(entry),
        "references": [str(path) for path in references],
        "problems": problems,
        "lock": lock_file,
        "lock_missing": lock is None,
        "skill_digest": digest,
    }
    if not pointer.get("distribution"):
        # The old pointer format: no distribution/version/digest fields at all.
        return {
            **base,
            "ok": not problems,
            "status": "ok" if not problems else "blocked",
            "distribution": "source",
            "version": "source",
            "legacy_pointer": True,
            "reasons": [],
            "note": "旧入口指针没有版本与摘要字段；按源码安装处理，不阻断安装检查",
        }
    reasons: list[str] = []
    distribution = pointer.get("distribution", "")
    if distribution not in ("package", "source"):
        reasons.append("pointer_distribution_invalid")
    recorded_digest = pointer.get("skill_digest", "")
    if not _DIGEST_RE.fullmatch(recorded_digest):
        reasons.append("skill_digest_invalid")
    elif digest and recorded_digest != digest:
        reasons.append("skill_digest_mismatch")
    if lock is None:
        if distribution == "package":
            reasons.append("lock_missing")
    else:
        lock_distribution = str(lock.get("distribution") or "")
        if lock_distribution != distribution:
            reasons.append("lock_distribution_mismatch")
        elif distribution == "package":
            if str(lock.get("version") or "") != pointer.get("version"):
                reasons.append("lock_version_mismatch")
            if str(lock.get("artifact_sha256") or "") != pointer.get("artifact_sha256"):
                reasons.append("lock_artifact_mismatch")
        else:
            lock_commit = str(lock.get("commit") or "")
            pointer_commit = pointer.get("commit") or ""
            if (
                lock_commit
                and pointer_commit
                and pointer_commit != POINTER_UNRECORDED
                and pointer_commit != lock_commit
            ):
                reasons.append("lock_commit_mismatch")
        lock_digest = str(lock.get("skill_digest") or "")
        if lock_digest and digest and lock_digest != digest:
            if "skill_digest_mismatch" not in reasons:
                reasons.append("skill_digest_mismatch")
    return {
        **base,
        "ok": not problems and not reasons,
        "status": "ok" if not problems and not reasons else "blocked",
        "distribution": distribution,
        "version": pointer.get("version"),
        "channel": pointer.get("channel"),
        "commit": pointer.get("commit"),
        "legacy_pointer": False,
        "reasons": reasons,
        "recorded_skill_digest": recorded_digest,
        "note": (
            "入口指针与安装锁、技能文件摘要一致"
            if not problems and not reasons
            else "入口指针与安装锁或技能文件摘要不一致"
        ),
    }
# --------------------------------------------------------------------- status
def status(root: str | Path) -> dict:
    """Read-only workspace snapshot for ``install report``."""
    root = Path(root).expanduser().resolve()
    record = load_record(root)
    if not record:
        return {"ok": False, "status": "pending", "note": "工作区尚未安装"}
    repo = Path(record.get("repo") or "")
    home = Path(record.get("home") or "")
    lock = dist_mod.read_lock(repo) if repo.is_dir() else None
    package = bool(
        record.get("distribution") == "package"
        or (isinstance(lock, dict) and lock.get("distribution") == "package")
    )
    python = Path(record.get("python") or "")
    uv = Path(record.get("uv") or "")
    venv = Path(record.get("venv") or "")
    problems = []
    if package:
        wrapper = repo / "bin" / "horizon.cmd"
        runtime_python = repo / "runtime" / "python.exe"
        if not repo.is_dir():
            problems.append(f"代码目录不可用：{repo}")
        if not home.is_dir():
            problems.append(f"运行时区不可用：{home}")
        if not wrapper.is_file():
            problems.append(f"发行包启动包装器不可用：{wrapper}")
        if not runtime_python.is_file():
            problems.append(f"内置运行时解释器不可用：{runtime_python}")
    else:
        if not (repo / ".git").exists():
            problems.append(f"代码目录不可用：{repo}")
        if not home.is_dir():
            problems.append(f"运行时区不可用：{home}")
        if venv and not venv.is_dir():
            problems.append(f"私有环境不可用：{venv}")
        if not python.is_file():
            problems.append(f"解释器不可用：{python}")
        if not uv.is_file():
            problems.append(f"uv 不可用：{uv}")
    skill = verify_skill(record)
    problems.extend(skill["problems"])
    return {
        "ok": not problems,
        "status": "ok" if not problems else "blocked",
        **record,
        "distribution": "package" if package else record.get("distribution", "source"),
        "problems": problems,
    }
