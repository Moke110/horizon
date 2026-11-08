"""One-time migration from the legacy single data root to code + archive.

ADR 0009 keeps run state in the code directory and only originals/pushes in the
archive directory.  An existing installation still has one data root that mixes
both.  ``install migrate`` moves the run-state halves (config, state, installation
records, initialization state, queue, proposals, logs, lease, console state,
private lark-cli) into ``<code>/.horizon``, splits the old ``archive/`` subtree
into the two allowed archive roots plus code-directory knowledge state, rebuilds
the private environment under the code directory and replaces the old listener
scripts with the console start script.

The migration is local-only: Feishu Base, Drive and the knowledge base are never
contacted.  Every transfer is idempotent, so a second run reports
``already-migrated`` instead of duplicating files.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
import sys
from pathlib import Path

from common import ensure_dir, now, read_json, write_json
from hostrun import archive_index, launcher, layout, workspace
from hostrun.base import HostRunError, default_config, load_config

CONSOLE_START_SCRIPT = "horizon-console.cmd"
CONSOLE_WINDOW_TITLE = launcher.CONSOLE_TITLE

# Legacy top-level entries that hold run state, not research material.
LEGACY_RUNTIME_DIRS = (
    "install",
    "queue",
    "proposals",
    "logs",
    "state",
    "executor",
    "console",
    "lark-cli",
    "knowledge",
    "library",
    "publisher",
    "cache",
    "outbox",
    "hostrun",
    "lark-workspace",
    "bin",
)
LEGACY_RUNTIME_FILES = (
    "ratings.jsonl",
    "auth-login-device.json",
    "auth-login-qr.png",
    "lark-login-device.json",
    "lark-login-qr.png",
    "lark-status.err",
    "lark-status.json",
)
# Old ``<home>/archive/<name>``: materials move to the archive's two roots,
# knowledge state moves into the code directory's archive store root.
LEGACY_MATERIALS = {"raw": "originals", "pushes": "pushes"}
LEGACY_ARCHIVE_STATE = ("cards", "propositions", "index", "applied", "legacy", ".tmp")
LEGACY_SCRIPTS = ("horizon-listener.cmd", "horizon-listener-stop.cmd")
# Missing in both source and destination means the old install was incomplete.
REQUIRED_RUNTIME = ("install/config.json", "install/state.json")


class _Report:
    def __init__(self) -> None:
        self.moved: list[str] = []
        self.already = 0
        self.conflicts: list[dict] = []


def _same_file(left: Path, right: Path) -> bool:
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        return archive_index.file_md5(left) == archive_index.file_md5(right)
    except OSError:
        return False


def _transfer(src: Path, dst: Path, report: _Report) -> None:
    """Move ``src`` to ``dst`` without ever overwriting different content."""
    try:
        if src.resolve() == dst.resolve():
            return
    except OSError:
        pass
    if src.is_file():
        if dst.exists():
            if dst.is_file() and _same_file(src, dst):
                src.unlink()
                report.already += 1
            else:
                report.conflicts.append(
                    {"from": str(src), "to": str(dst), "reason": "destination_differs"}
                )
            return
        ensure_dir(dst.parent)
        shutil.move(str(src), str(dst))
        report.moved.append(str(dst))
        return
    if not src.is_dir():
        return
    ensure_dir(dst)
    for child in sorted(src.iterdir()):
        _transfer(child, dst / child.name, report)
    try:
        src.rmdir()
    except OSError:
        pass


def _infer_legacy_home(code: Path) -> Path | None:
    """Read the old data root from the installation workspace record."""
    runtime = code / layout.RUNTIME_DIRNAME
    for root in (code, code.parent):
        record = workspace.load_record(root)
        home = str(record.get("home") or "").strip()
        if home:
            candidate = Path(home).expanduser().resolve()
            # The record may already be the migrated one; never migrate the
            # runtime area onto itself and call it a legacy installation.
            if candidate != code and candidate != runtime:
                return candidate
    return None


def _split_archive(legacy: Path, archive: Path, runtime: Path, report: _Report) -> None:
    """Split ``<legacy>/archive`` into archive materials and code-side state."""
    old = legacy / "archive"
    if old.is_dir():
        for name, target in LEGACY_MATERIALS.items():
            if (old / name).exists():
                _transfer(old / name, archive / target, report)
        for name in LEGACY_ARCHIVE_STATE:
            if (old / name).exists():
                _transfer(old / name, runtime / "archive" / name, report)
        try:
            old.rmdir()
        except OSError:
            pass
    # A half-migrated install may already have the new material roots in place.
    for name in ("originals", "pushes"):
        if (legacy / name).exists():
            _transfer(legacy / name, archive / name, report)


def _find_workspace_record(legacy: Path, code: Path) -> dict:
    """The old installation record (workspace root) or the new code-side one."""
    candidates = [
        workspace.record_path(code),
        legacy.parent / ".horizon" / "install.json",
        code.parent / ".horizon" / "install.json",
        legacy / ".horizon" / "install.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            loaded = read_json(candidate) or {}
            if loaded.get("repo") or loaded.get("home"):
                return loaded
    return {}


def _rebuild_environment(
    code: Path,
    runtime: Path,
    *,
    record: dict | None = None,
    uv_path=None,
    runner=subprocess.run,
    fetch=None,
) -> dict:
    """Build the private environment under the code runtime area and verify imports."""
    record = record if record is not None else workspace.load_record(code)
    config = load_config(runtime)
    uv = str(uv_path or record.get("uv") or (config.get("paths") or {}).get("uv") or "").strip()
    if not uv or not Path(uv).expanduser().is_file():
        ensured = workspace.ensure_uv(
            runtime,
            uv_path=uv or None,
            recorded=record.get("uv"),
            fetch=fetch,
            runner=runner,
        )
        if not ensured.get("ok"):
            return {
                "ok": False,
                "status": "blocked",
                "note": ensured.get("note") or "uv 不可用，无法重建私有环境",
                "uv": ensured,
            }
        uv = ensured["path"]
    return workspace.ensure_environment(
        code,
        runtime,
        uv=uv,
        runner=runner,
        clear=True,
        expected=record.get("python"),
    )


def _remap_inside(value: str, old_root: Path, new_root: Path) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    try:
        relative = Path(text).expanduser().resolve().relative_to(old_root)
    except (OSError, ValueError):
        return text
    return str(new_root / relative)


def _write_if_changed(path: Path, text: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")
    return True


def _console_script_text(code: Path, runtime: Path, archive: Path, python: str) -> str:
    """The migrated console launcher, generated by the shared generator (#95).

    Migration only runs on source installations, so this stays the absolute
    interpreter form; ``%*`` and the title line are unchanged.
    """
    return launcher.console_script_text(code, archive, python)


def _migrate_config(
    legacy: Path,
    code: Path,
    runtime: Path,
    environment: dict,
) -> Path:
    path = runtime / "install" / "config.json"
    existing = read_json(path) or {}
    config = copy.deepcopy(existing) if existing else default_config(runtime, repo=code)
    paths = {**(config.get("paths") or {})}
    env_ok = bool(environment.get("ok")) and bool(environment.get("python"))
    paths["repo"] = str(code)
    paths["home"] = str(runtime)
    paths["lark_cli"] = _remap_inside(paths.get("lark_cli", ""), legacy, runtime)
    if env_ok:
        paths["python"] = str(environment["python"])
        paths["venv"] = str(environment.get("venv") or "")
    config["paths"] = paths
    # The listener section is gone: keep only the Agent-run settings the Console
    # still consumes, and drop the retired start/stop commands and host tasks.
    legacy_listener = config.pop("listener", {}) or {}
    agent_run = {**(config.get("agent_run") or {})}
    for key in ("feedback_lease_minutes", "derived_timeout_seconds", "dsh_args"):
        if key in legacy_listener and key not in agent_run:
            agent_run[key] = legacy_listener[key]
    # Recorded per-run arguments can carry absolute paths (e.g. the credentials
    # ``--patch`` overlay).  Those files move out of the legacy data root, so a
    # stale path would make every real agent run fail; remap them with the rest
    # of the installation.
    if isinstance(agent_run.get("dsh_args"), list):
        agent_run["dsh_args"] = [
            _remap_inside(item, legacy, runtime) if isinstance(item, str) else item
            for item in agent_run["dsh_args"]
        ]
    if agent_run:
        config["agent_run"] = agent_run
    config.pop("schedules", None)
    workspace_section = config.get("workspace")
    if isinstance(workspace_section, dict):
        config["workspace"] = {
            **workspace_section,
            "repo": str(code),
            "home": str(runtime),
        }
    if config != existing:
        # Write the migrated record as-is instead of going through
        # ``base.save_config``: its credential redaction also rewrites the
        # informative ``workspace.code_dirname`` key, which is not a secret.
        write_json(path, config)
    return path


def _migrate_workspace_record(
    code: Path,
    runtime: Path,
    environment: dict,
    record: dict,
) -> Path | None:
    if not record:
        return None
    updated = {
        **record,
        "root": record.get("root") or str(code.parent),
        "repo": str(code),
        "home": str(runtime),
    }
    if environment.get("ok") and environment.get("python"):
        updated["python"] = str(environment["python"])
        updated["venv"] = str(environment.get("venv") or "")
    current = {
        key: value
        for key, value in (workspace.load_record(code) or {}).items()
        if key not in {"updated_at", "schema_version"}
    }
    wanted = {key: value for key, value in updated.items() if key not in {"updated_at", "schema_version"}}
    if current == wanted and workspace.record_path(code).is_file():
        return workspace.record_path(code)
    return workspace.save_record(code, updated)


def _replace_scripts(legacy: Path, code: Path, runtime: Path, archive: Path, python: str) -> dict:
    removed: list[str] = []
    for base in (runtime / "bin", legacy / "bin", code / "bin"):
        for name in LEGACY_SCRIPTS:
            path = base / name
            if path.is_file():
                path.unlink()
                removed.append(str(path))
    console = code / "bin" / CONSOLE_START_SCRIPT
    _write_if_changed(console, _console_script_text(code, runtime, archive, python))
    return {"console": str(console), "removed": removed, "window_title": CONSOLE_WINDOW_TITLE}


def migrate(
    *,
    code: str | Path,
    archive: str | Path,
    legacy_home: str | Path | None = None,
    environment=None,
    uv_path: str | None = None,
    runner=subprocess.run,
    fetch=None,
    dry_run: bool = False,
) -> dict:
    """Migrate one legacy installation; safe to run again."""
    code_path = Path(code).expanduser().resolve()
    archive_path = Path(archive).expanduser().resolve()
    if legacy_home is None:
        legacy = _infer_legacy_home(code_path)
        if legacy is None:
            raise HostRunError(
                "找不到旧安装位置；请用 --from 指定原安装目录（例如 <workspace>/horizon-archive）"
            )
    else:
        legacy = Path(legacy_home).expanduser().resolve()
    if not legacy.is_dir():
        raise HostRunError(f"旧安装位置不存在或不是目录：{legacy}")
    if legacy == code_path:
        raise HostRunError("旧安装位置不能就是代码目录")

    resolved = layout.resolve(code=str(code_path), archive=str(archive_path))
    runtime = Path(resolved["runtime"])
    if legacy == runtime:
        raise HostRunError("旧安装位置不能就是代码目录的运行时区")
    if not dry_run:
        layout.ensure(resolved)
        layout.remember(resolved)

    missing = [
        rel
        for rel in REQUIRED_RUNTIME
        if not (legacy / rel).exists() and not (runtime / rel).exists()
    ]

    report = _Report()
    if not dry_run:
        for name in LEGACY_RUNTIME_DIRS:
            if (legacy / name).exists():
                _transfer(legacy / name, runtime / name, report)
        for name in LEGACY_RUNTIME_FILES:
            if (legacy / name).exists():
                _transfer(legacy / name, runtime / name, report)
        _split_archive(legacy, archive_path, runtime, report)

    record = _find_workspace_record(legacy, code_path)
    if environment is None:
        environment = (
            _rebuild_environment(
                code_path,
                runtime,
                record=record,
                uv_path=uv_path,
                runner=runner,
                fetch=fetch,
            )
            if not dry_run
            else {"ok": True, "status": "untested", "skipped": True}
        )
    elif callable(environment):
        environment = environment(code=code_path, runtime=runtime)
    environment = dict(environment)
    env_ok = bool(environment.get("ok"))

    config_path = None
    workspace_path = None
    scripts = {"console": "", "removed": []}
    index = {"ok": True, "scanned": 0, "registered": 0, "duplicates": 0, "existing": 0, "total": 0}
    if not dry_run:
        existing_paths = load_config(runtime).get("paths") or {}
        python = (
            str(environment.get("python"))
            if env_ok and environment.get("python")
            else str(existing_paths.get("python") or sys.executable)
        )
        config_path = _migrate_config(legacy, code_path, runtime, environment)
        workspace_path = _migrate_workspace_record(code_path, runtime, environment, record)
        scripts = _replace_scripts(legacy, code_path, runtime, archive_path, python)
        index = archive_index.register(runtime, archive_path)

    changed = len(report.moved) + int(index.get("registered") or 0)
    leftovers = []
    if not dry_run and legacy.is_dir():
        leftovers = sorted(
            path.name
            for path in legacy.iterdir()
            if path.name not in {"originals", "pushes"}
        )
    if report.conflicts or not env_ok:
        status = "blocked"
        ok = False
    elif missing:
        status = "partial"
        ok = True
    elif changed:
        status = "migrated"
        ok = True
    else:
        status = "already-migrated"
        ok = True

    result = {
        "schema_version": 1,
        "ok": ok,
        "status": status,
        "code": str(code_path),
        "runtime": str(runtime),
        "archive": str(archive_path),
        "from": str(legacy),
        "changed": changed,
        "moved": report.moved,
        "already_present": report.already,
        "conflicts": report.conflicts,
        "missing": missing,
        "leftovers": leftovers,
        "environment": environment,
        "archive_index": index,
        "scripts": scripts,
        "config": str(config_path) if config_path else "",
        "workspace_record": str(workspace_path) if workspace_path else "",
        "layout_record": str(layout.record_path(code_path)) if not dry_run else "",
        "feishu_touched": False,
    }
    if not dry_run:
        ensure_dir(runtime / "install")
        write_json(
            runtime / "install" / "migrate.json",
            {
                "schema_version": 1,
                "at": now(),
                "status": status,
                "from": str(legacy),
                "code": str(code_path),
                "archive": str(archive_path),
                "changed": changed,
                "missing": missing,
                "conflicts": report.conflicts,
                "leftovers": leftovers,
            },
        )
    return result
