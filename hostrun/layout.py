"""One resolution of the two top-level locations: code directory and archive.

ADR 0009 retired the single data root in two:

- the **code directory** holds the checkout, the private environment,
  configuration, run state, queue, proposals, logs, the executor lease, console
  state, installation records, initialization state and the private lark-cli;
- the **archive directory** holds only ``originals/`` (source documents) and
  ``pushes/`` (push artifacts).

Runtime state always lives in ``<code>/.horizon`` and the archive location is
the one last resolved for that code directory (``<code>/.horizon/layout.json``),
so no command or module derives a location on its own.  The legacy ``--home``
flag and its "data root" are gone: passing it is refused with a migration
pointer instead of silently mapping one old tree onto the new model.

Nothing here starts a process or reaches a network.  ``ensure`` is the only
function that creates directories, and it never writes a file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from common import RUNTIME_DIRNAME, RUNTIME_ENV, now, read_json, write_json
from hostrun.base import HostRunError

ARCHIVE_DIRNAME = "horizon-archive"
LAYOUT_FILENAME = "layout.json"

# Environment handoff for child processes started by a resolved command: the
# console and publisher spawn module commands without repeating the location
# flags, and the child must resolve exactly the same two directories.
ENV_CODE = "HORIZON_CODE"
ENV_ARCHIVE = "HORIZON_ARCHIVE"
ENV_RUNTIME = RUNTIME_ENV

RETIRED_HOME_FLAG = "--home"
RETIRED_HOME_MESSAGE = (
    "--home 已退役：运行时区固定为 <代码目录>/.horizon，资料目录用 --archive 指定。"
    "既有安装请先运行 python -m hostrun.cli --code <代码目录> --archive <资料目录> "
    "install migrate 迁移，再用 --code 与 --archive 指定位置"
)

# One relative path per runtime-state kind.  Config, state, installation
# records and initialization state physically share ``install/``; the category
# names, not the directory count, are the contract other modules use.
CATEGORY_RELS = {
    "config": Path("install") / "config.json",
    "state": Path("install") / "state.json",
    "queue": Path("queue"),
    "proposals": Path("proposals"),
    "logs": Path("logs"),
    "lease": Path("executor"),
    "console": Path("console"),
    "install": Path("install"),
    "init": Path("install") / "initial",
    "lark_cli": Path("lark-cli"),
}

# The archive directory admits exactly these two kinds of files.
ARCHIVE_RELS = {
    "originals": Path("originals"),
    "pushes": Path("pushes"),
}

# Directories the runtime area establishes; file categories (config, state)
# live in one of these.
RUNTIME_DIRS = (
    Path("install"),
    Path("install") / "initial",
    Path("queue"),
    Path("proposals"),
    Path("logs"),
    Path("executor"),
    Path("console"),
    Path("lark-cli"),
)

AREAS = ("runtime", "archive")


def repo_root() -> Path:
    """The checkout this module lives in; the default code directory."""
    return Path(__file__).resolve().parents[1]


def default_archive(code: str | Path) -> Path:
    """``<root>/horizon-archive``, the archive sibling of a code checkout."""
    return Path(code).expanduser().resolve().parent / ARCHIVE_DIRNAME


def record_path(code: str | Path) -> Path:
    """The remembered layout of a code directory (inside its ignored runtime area)."""
    return Path(code).expanduser().resolve() / RUNTIME_DIRNAME / LAYOUT_FILENAME


def remembered_archive(code: str | Path) -> Path | None:
    """The archive a code directory was last used with, if it was recorded.

    A record written by another checkout is ignored, so moving a repository cannot
    silently point it at someone else's archive.
    """
    code_dir = Path(code).expanduser().resolve()
    record = read_json(record_path(code_dir)) or {}
    recorded_code = str(record.get("code") or "").strip()
    archive = str(record.get("archive") or "").strip()
    if not archive or not recorded_code:
        return None
    try:
        if Path(recorded_code).expanduser().resolve() != code_dir:
            return None
    except OSError:
        return None
    return Path(archive).expanduser()


def archive_for_runtime(runtime: str | Path) -> Path | None:
    """The archive directory a runtime area's store must read and write.

    This is the single lookup every consumer (``Archive``, publisher, editor,
    trigger, library, pdf ingest) uses instead of deriving ``<runtime>/archive``
    on its own.  Order: the explicit child-process handoff, then the layout
    recorded for the code directory.  ``None`` means the runtime area has no
    recorded archive yet (uninitialized install or an isolated test runtime);
    in that case the legacy runtime-resident store under ``<runtime>/archive``
    is still what the caller has.
    """
    runtime_path = Path(runtime).expanduser().resolve()
    handed = str(os.environ.get(ENV_ARCHIVE) or "").strip()
    if handed:
        archive_path = Path(handed).expanduser()
        # A resolved-but-not-yet-created archive is the handoff for a fresh
        # command; if the runtime area still holds the legacy store and that
        # archive was never materialized, keep reading the legacy store rather
        # than silently hiding existing material (pre-migration installs).
        if archive_path.is_dir() or not (runtime_path / "archive").exists():
            return archive_path
        return None
    return remembered_archive(runtime_path.parent)


def remember(resolved: dict) -> Path | None:
    """Persist the resolved layout so a custom ``--archive`` survives later runs.

    A read-only checkout must not make a command fail, so an unwritable runtime
    area is ignored.
    """
    path = record_path(resolved["code"])
    try:
        write_json(
            path,
            {
                "schema_version": 1,
                "code": resolved["code"],
                "runtime": resolved["runtime"],
                "archive": resolved["archive"],
                "remembered_at": now(),
            },
        )
    except OSError:
        return None
    return path


def resolve(*, code: str | Path | None = None, archive: str | Path | None = None) -> dict:
    """Resolve the two locations and every category path.

    A code directory and its archive may not contain each other, otherwise a
    checkout update or archive cleanup could delete the other side.
    """
    code_dir = Path(code).expanduser().resolve() if code else repo_root()
    runtime = code_dir / RUNTIME_DIRNAME
    remembered = remembered_archive(code_dir) if not archive else None
    archive_root = (
        Path(archive).expanduser().resolve()
        if archive
        else (remembered.resolve() if remembered else default_archive(code_dir))
    )

    if archive_root == code_dir:
        raise HostRunError("资料目录不能就是代码目录")
    if _is_inside(archive_root, code_dir):
        raise HostRunError("资料目录不能放在代码目录内，否则更新代码会覆盖研究资料")
    if _is_inside(code_dir, archive_root):
        raise HostRunError("代码目录不能放在资料目录内")

    return {
        "code": str(code_dir),
        "runtime": str(runtime),
        "archive": str(archive_root),
        "paths": {key: str(runtime / rel) for key, rel in CATEGORY_RELS.items()},
        "archive_paths": {
            key: str(archive_root / rel) for key, rel in ARCHIVE_RELS.items()
        },
    }


def resolve_for_cli(args) -> dict:
    """Resolve from parsed ``--code``/``--archive``, else the child-run handoff.

    Explicit flags always win.  The environment is only the way a spawned module
    command inherits the directories its parent already resolved, so it is read
    solely when the command was given no location flag at all.
    """
    code = getattr(args, "code", None)
    archive = getattr(args, "archive", None)
    if code is None and archive is None:
        code = os.environ.get(ENV_CODE) or None
        archive = os.environ.get(ENV_ARCHIVE) or None
    return resolve(code=code, archive=archive)


def export(resolved: dict) -> None:
    """Publish the resolved locations to child processes and in-process lookups."""
    os.environ[ENV_CODE] = resolved["code"]
    os.environ[ENV_ARCHIVE] = resolved["archive"]
    os.environ[ENV_RUNTIME] = resolved["runtime"]


def handoff(runtime: str | Path) -> dict[str, str]:
    """The environment a parent hands a spawned module command.

    Runtime state is by contract ``<code>/.horizon``, so the runtime area's
    parent is the code directory; the archive is whatever that code directory
    resolved to.  Used by the Console's Agent runs and the publisher so a child
    process resolves exactly the same two locations without repeating the flags.
    """
    runtime_path = Path(runtime).expanduser().resolve()
    code = runtime_path.parent
    try:
        archive = resolve(code=code)["archive"]
    except HostRunError:
        archive = str(default_archive(code))
    return {
        ENV_CODE: str(code),
        ENV_ARCHIVE: str(archive),
        ENV_RUNTIME: str(runtime_path),
    }


def cli_argv(argv: list[str] | None = None) -> list[str]:
    """Raw argv, refusing the retired ``--home`` with a migration pointer.

    Both the hostrun CLI and the module CLIs call this before argparse so the
    old flag never falls through as a generic "unrecognized arguments" error.
    """
    raw = list(sys.argv[1:] if argv is None else argv)
    for arg in raw:
        if arg == RETIRED_HOME_FLAG or arg.startswith(RETIRED_HOME_FLAG + "="):
            raise HostRunError(RETIRED_HOME_MESSAGE)
    return raw


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def ensure(resolved: dict) -> dict:
    """Create the runtime categories and the two archive directories.  Idempotent."""
    created: list[str] = []
    targets = [Path(resolved["runtime"])]
    targets.extend(Path(resolved["runtime"]) / rel for rel in RUNTIME_DIRS)
    targets.extend(Path(path) for path in resolved["archive_paths"].values())
    try:
        for target in targets:
            if not target.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                created.append(str(target))
    except OSError as exc:
        raise HostRunError(
            f"无法建立运行时区或资料目录：{exc}；请检查 --code/--archive 的位置是否可写"
        ) from exc
    return {**resolved, "created": True, "created_paths": created}


def entry(resolved: dict, area: str, *parts: str) -> Path:
    """A path under one allowed area, refused if it would escape that area."""
    if area not in AREAS:
        raise HostRunError(f"未知区域 {area}；只允许 {', '.join(AREAS)}")
    root = Path(resolved[area]).resolve()
    target = root.joinpath(*parts).resolve()
    if target != root and not _is_inside(target, root):
        raise HostRunError(f"拒绝越界写入：{target} 不在 {root} 内")
    return target
