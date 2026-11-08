"""Build the installable distribution package, its manifest and its digest.

ADR 0010 splits development from installation: the Git repository holds the
development inputs, while an installed horizon is an extracted distribution
package.  This module is the single build entry both a developer and CI use:

    python -m hostrun.distribution --source . --version 0.1.0 \
        --channel stable --out dist --runtime-dir <prepared-runtime>

It reads a source tree, copies only runtime code and agent-readable assets into
``<out>/horizon-<version>-<channel>/``, then writes:

- ``manifest.json`` *inside* the package: every packaged file's path, byte count,
  sha256 and executable flag, plus version, channel, build commit and the
  compatibility range.  The manifest never lists itself.
- ``<out>/horizon-<version>-<channel>.digest.json``: the artifact summary — the
  sha256 of the manifest bytes, file count, total bytes, the same identity
  facts, and an ``artifact`` block naming the zip, its byte length and sha256.
  This is what a release index can publish without unpacking.
- ``<out>/horizon-<version>-<channel>.zip``: a deterministic zip of the package
  directory.  Entries are path-sorted, timestamps are fixed at 1980-01-01, and
  every entry declares its POSIX mode, so building the same source twice yields
  byte-identical zip, manifest and digest files.

The build is offline and deterministic: no network, no timestamp, stable path
sorting, and no reads from the installation's runtime state.  Building the same
source tree twice yields byte-identical manifest, digest and artifact files.

Exclusions are an allowlist plus explicit rules (see the constants below): only
runtime packages, ``skills/``, ``contracts/``, ``docs/`` and a few root files
ship.  Hidden entries, caches, runtime state, private environments, tests,
fixtures, drafts and other development-only directories never enter the
package.

Bundled runtime (``--runtime-dir``, #95)
----------------------------------------
A release build passes ``--runtime-dir`` naming a prepared CPython embeddable
tree.  The builder never downloads or synthesises an interpreter: CI (#98)
assembles the tree, this entry copies it verbatim into ``<package>/runtime/``
with a sorted walk (skipping ``__pycache__`` and ``*.pyc``/``*.pyo``) and
preserves every file's bytes.  The expected layout is::

    runtime/python.exe                       # interpreter the wrapper runs
    runtime/python<major><minor>.dll         # core DLL
    runtime/python<major><minor>._pth        # module search path (stdlib + Lib/site-packages)
    runtime/python<major><minor>.zip         # stdlib (or a Lib/ tree)
    runtime/DLLs/*.pyd                       # extension modules (or flat *.pyd next to python.exe)
    runtime/Lib/site-packages/...            # PyYAML, PyMuPDF and their dependencies

A bundled runtime also generates ``<package>/bin/horizon.cmd``, the launcher
wrapper described in :mod:`hostrun.launcher` and ``contracts/distribution-runtime.md``.
Both the runtime tree and the wrapper are written *before* the manifest is
assembled, so the existing manifest schema covers them automatically.  A build
without ``--runtime-dir`` keeps the #93 layout (no ``runtime/``, no wrapper) so
development builds and their tests stay unchanged; a release build always passes
it.  A ``--runtime-dir`` that is missing or lacks ``python.exe`` fails the build
before any package directory is created.
"""

from __future__ import annotations

import argparse
import os
import platform as platform_mod
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

from common import emit, sha256_bytes, sha256_file, write_json_bytes
from hostrun import launcher

SCHEMA_VERSION = 1
CHANNELS = ("stable", "prerelease")
MANIFEST_FILENAME = "manifest.json"
DIGEST_SUFFIX = ".digest.json"
ARTIFACT_SUFFIX = ".zip"
#: Package-relative layout of a bundled runtime and its wrapper (#95).
RUNTIME_DIRNAME = "runtime"
RUNTIME_PYTHON_NAME = "python.exe"
RUNTIME_SKIP_DIRS = {"__pycache__"}
RUNTIME_SKIP_SUFFIXES = {".pyc", ".pyo"}
#: Fixed DOS epoch timestamp for every zip entry.  A timestamp from the build
#: host would make two builds of the same source differ.
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ZIP_COMPRESSLEVEL = 9
#: Manifest value when no clean Git checkout can provide a commit.  It is an
#: explicit field, not an omitted key, so consumers can tell "unknown evidence"
#: apart from "this schema version has no such field".
UNKNOWN_COMMIT = "unknown"

# ---------------------------------------------------------------- package scope
# Top-level directories that are program code or agent-readable assets.  Only
# these can ship: an unknown directory is treated as development-only rather
# than silently copied, so local scratch cannot leak into a release.
PACKAGED_DIRS = (
    "agent",
    "desktop",
    "archive",
    "contracts",
    "docs",
    "editor",
    "horizon",
    "hostrun",
    "initial_build",
    "knowledge",
    "library",
    "pdf_ingest",
    "publisher",
    "question_queue",
    "retriever",
    "skills",
    "trigger",
    "writer",
)
# Top-level regular files the runtime or its users need.  Development-only root
# docs (AGENTS.md, GLOSSARY.md) stay in the repository.
PACKAGED_FILES = (
    "LICENSE",
    "README.md",
    "common.py",
    "requirements.txt",
)

# Known top-level exclusions, with the reason each one is not runtime content.
# The ticket's inventory is recorded here; hidden entries are additionally
# excluded by the generic rule below so unknown private state cannot ship.
EXCLUDED_TOP_LEVEL = {
    ".git": "version-control metadata; history is development evidence, not runtime content",
    ".worktrees": "other development worktrees are separate checkouts, not this package",
    ".venv": "private environment of a source installation; #95 ships a bundled runtime instead",
    ".horizon": "runtime state (install records, config, queue, logs, archive index)",
    ".horizon-archive": "legacy archive/data root; research data is never package content",
    ".dev": "developer-local scratch directory",
    ".pytest_cache": "pytest cache, recreated on demand",
    "__pycache__": "interpreter bytecode cache, recreated on demand",
    "AGENTS.md": "development guide for repository contributors",
    "GLOSSARY.md": "domain glossary for repository contributors",
    "bin": "generated console launcher scripts belong to an installation, not to source",
    "deploy": "legacy host service templates; the console is the only resident entry (ADR 0009)",
    "drafts": "authoring drafts, not runtime data",
    "fixtures": "test fixtures only",
    "outbox": "generated push artifacts, runtime output",
    "pytest-of-*": "pytest temporary roots",
    "scantest": "local permission-diagnosis scratch directory",
    "scripts": "development-only build, demo and data scripts",
    "tests": "development test suite is not shipped to users",
    "tmpgap": "local permission-diagnosis scratch directory",
}
_EXCLUDED_TOP_LEVEL_EXACT = {name for name in EXCLUDED_TOP_LEVEL if "*" not in name}
_EXCLUDED_TOP_LEVEL_PATTERNS = tuple(
    name for name in EXCLUDED_TOP_LEVEL if "*" in name
)

# Names pruned anywhere below a packaged directory.  Hidden entries are pruned
# by prefix, which covers .git/.venv/.horizon/.dev/.pytest-* wherever they
# appear; only the visible caches need naming here.
EXCLUDED_ENTRY_NAMES = {
    "__pycache__": "interpreter bytecode cache",
    "pytest-of-*": "pytest temporary roots",
}
EXCLUDED_FILE_SUFFIXES = {
    ".pyc": "compiled Python bytecode",
    ".pyo": "optimised Python bytecode",
}

# How the manifest's ``executable`` flag is decided.  A POSIX execute bit is
# authoritative; the suffix list makes the answer stable when a Windows build
# host has no POSIX mode bits (CI runs on Windows).
EXECUTABLE_SUFFIXES = {".exe", ".cmd", ".bat", ".ps1", ".sh"}

_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]*$")


class BuildError(Exception):
    """A build request that cannot produce a trustworthy package."""


# --------------------------------------------------------------------- helpers
def _matches_pattern(name: str, pattern: str) -> bool:
    if pattern.endswith("*"):
        return name.startswith(pattern[:-1])
    return name == pattern


def _top_level_exclusion(name: str, *, is_dir: bool) -> str | None:
    """Why a top-level entry is not packaged, or ``None`` when it may ship."""
    if name in _EXCLUDED_TOP_LEVEL_EXACT:
        return EXCLUDED_TOP_LEVEL[name]
    for pattern in _EXCLUDED_TOP_LEVEL_PATTERNS:
        if _matches_pattern(name, pattern):
            return EXCLUDED_TOP_LEVEL[pattern]
    if name.startswith("."):
        return "hidden VCS/tooling/private state"
    if is_dir:
        if name not in PACKAGED_DIRS:
            return "top-level directory is not runtime code or an agent-readable asset"
        return None
    if name not in PACKAGED_FILES:
        return "top-level file is not part of the runtime or its user documentation"
    return None


def _nested_exclusion(name: str) -> str | None:
    if name.startswith("."):
        return "hidden VCS/tooling/private state"
    for pattern, reason in EXCLUDED_ENTRY_NAMES.items():
        if _matches_pattern(name, pattern):
            return reason
    suffix = Path(name).suffix.lower()
    if suffix in EXCLUDED_FILE_SUFFIXES:
        return EXCLUDED_FILE_SUFFIXES[suffix]
    return None


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _source_relpaths(source: Path, out: Path | None) -> tuple[list[Path], dict[str, str]]:
    """Every file to package, relative to ``source``, plus top-level exclusions.

    ``out`` is pruned when it sits inside the source tree, so building into
    ``<source>/dist`` cannot package the previous build.
    """
    files: list[Path] = []
    excluded: dict[str, str] = {}
    out_resolved = out.resolve() if out is not None else None
    for entry in sorted(source.iterdir(), key=lambda path: path.name):
        reason = _top_level_exclusion(entry.name, is_dir=entry.is_dir())
        if reason:
            excluded[entry.name] = reason
            continue
        if entry.is_symlink():
            excluded[entry.name] = "symbolic links are not followed"
            continue
        if entry.is_dir():
            for dirpath, dirnames, filenames in os.walk(entry):
                current = Path(dirpath)
                kept: list[str] = []
                for name in sorted(dirnames):
                    if _nested_exclusion(name):
                        continue
                    candidate = current / name
                    if candidate.is_symlink():
                        continue
                    if out_resolved is not None:
                        resolved = candidate.resolve()
                        if resolved == out_resolved or _is_within(resolved, out_resolved):
                            continue
                    kept.append(name)
                dirnames[:] = kept
                for name in sorted(filenames):
                    candidate = current / name
                    # Pi imports its catalog manifest at runtime. Keep this exact
                    # dependency asset while still excluding private dotfiles.
                    pi_catalog = candidate.relative_to(source).as_posix() == (
                        "agent/node_modules/@earendil-works/pi-ai/dist/providers/data/.manifest.json"
                    )
                    if _nested_exclusion(name) and not pi_catalog:
                        continue
                    if candidate.is_symlink() or not candidate.is_file():
                        continue
                    files.append(candidate.relative_to(source))
        elif entry.is_file():
            files.append(entry.relative_to(source))
    return files, excluded


def _runtime_relpaths(runtime_dir: Path) -> list[Path]:
    """Every runtime file to copy, relative to ``runtime_dir``, sorted.

    The walk is deterministic and skips only interpreter caches: a prepared
    runtime is otherwise copied verbatim, including the extension modules
    (``DLLs/`` or flat ``*.pyd``) and ``Lib/site-packages/``.
    """
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(runtime_dir):
        dirnames[:] = sorted(
            name for name in dirnames if name not in RUNTIME_SKIP_DIRS
        )
        current = Path(dirpath)
        for name in sorted(filenames):
            if Path(name).suffix.lower() in RUNTIME_SKIP_SUFFIXES:
                continue
            candidate = current / name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            files.append(candidate.relative_to(runtime_dir))
    return sorted(files, key=lambda path: path.as_posix())


def _validate_runtime_dir(runtime_dir: str | Path) -> Path:
    """Resolve and validate ``--runtime-dir`` before any package write."""
    path = Path(runtime_dir).expanduser().resolve()
    if not path.is_dir():
        raise BuildError(f"runtime directory is not a directory: {path}")
    if not (path / RUNTIME_PYTHON_NAME).is_file():
        raise BuildError(
            f"runtime directory must contain {RUNTIME_PYTHON_NAME}: {path}"
        )
    return path


def _copy_runtime(runtime_dir: Path, package_dir: Path) -> dict:
    """Copy the prepared runtime verbatim and generate its launcher wrapper."""
    destination_root = package_dir / RUNTIME_DIRNAME
    relpaths = _runtime_relpaths(runtime_dir)
    total = 0
    for rel in relpaths:
        origin = runtime_dir / rel
        destination = destination_root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination)
        total += destination.stat().st_size
    launcher.write_wrapper(package_dir / launcher.WRAPPER_REL)
    return {"files": len(relpaths), "bytes": total, "source": str(runtime_dir)}


def _is_executable(rel: Path, path: Path) -> bool:
    if rel.suffix.lower() in EXECUTABLE_SUFFIXES:
        return True
    try:
        return bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    except OSError:
        return False


def normalize_version(version: str) -> str:
    """Strip the tag's leading ``v``; the prefix never enters a version/name.

    ``contracts/release.md`` §1 makes ``v`` tag syntax only, so the build entry
    accepts the same string a maintainer types for a tag and normalizes it once.
    """
    text = str(version or "").strip()
    if text.startswith("v"):
        text = text[1:]
    return text


def _write_json(path: Path, payload: dict) -> bytes:
    return write_json_bytes(path, payload)


def _zip_mode(executable: bool) -> int:
    return stat.S_IFREG | (0o755 if executable else 0o644)


def _write_artifact(package_dir: Path, artifact_path: Path) -> None:
    """Zip ``package_dir`` deterministically, including its manifest."""
    files = sorted(
        (path for path in package_dir.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(package_dir).as_posix(),
    )
    executable = {
        path.relative_to(package_dir).as_posix(): _is_executable(
            path.relative_to(package_dir), path
        )
        for path in files
    }
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        artifact_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=ZIP_COMPRESSLEVEL,
    ) as archive:
        for path in files:
            rel = path.relative_to(package_dir).as_posix()
            info = zipfile.ZipInfo(rel, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # POSIX modes are meaningful in external_attr
            info.external_attr = _zip_mode(executable[rel]) << 16
            with path.open("rb") as source, archive.open(info, "w") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)


def _git(source: Path, args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def resolve_commit(source: str | Path, explicit: str | None = None) -> tuple[str, str, str]:
    """Resolve ``built_from_commit`` and say where the value came from.

    Order: an explicit value (CI extraction, or a maintainer building outside
    Git) wins; otherwise a clean Git checkout contributes its HEAD; otherwise
    the value is the explicit :data:`UNKNOWN_COMMIT` sentinel.  A dirty or
    unreadable checkout never claims HEAD, because that would be false evidence.
    """
    source = Path(source).expanduser().resolve()
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip(), "explicit", ""
    if (source / ".git").exists():
        head = _git(source, ["rev-parse", "HEAD"])
        status = _git(source, ["status", "--porcelain"])
        if head and status is not None and not status:
            return head, "git", ""
        if head and status:
            return UNKNOWN_COMMIT, "unknown", "source checkout has uncommitted changes; pass --built-from-commit to record an explicit evidence value"
        return UNKNOWN_COMMIT, "unknown", "git checkout could not be read; pass --built-from-commit to record an explicit evidence value"
    return UNKNOWN_COMMIT, "unknown", "source tree has no .git; pass --built-from-commit to record an explicit evidence value"


def _default_compat() -> dict[str, str]:
    return {
        "os": normalize_os(sys.platform),
        "arch": normalize_arch(platform_mod.machine()),
        "dsh": "*",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def normalize_os(platform_name: str | None) -> str:
    name = (platform_name or sys.platform).lower()
    if name.startswith("win"):
        return "windows"
    if name.startswith("darwin"):
        return "darwin"
    if name.startswith("linux"):
        return "linux"
    return name


def normalize_arch(machine: str | None) -> str:
    name = (machine or platform_mod.machine()).lower()
    mapping = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    return mapping.get(name, name)


def _compat(overrides: dict | None) -> dict[str, str]:
    compat = _default_compat()
    for key, value in (overrides or {}).items():
        if key not in compat:
            raise BuildError(f"unknown compat field {key!r}; expected {', '.join(compat)}")
        text = str(value or "").strip()
        if not text:
            raise BuildError(f"compat.{key} must be a non-empty string")
        compat[key] = text
    return compat


def _validate_request(version: str, channel: str) -> None:
    if channel not in CHANNELS:
        raise BuildError(f"channel must be one of {', '.join(CHANNELS)}; got {channel!r}")
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise BuildError(
            "version must be a non-empty filename-safe string such as 0.1.0 "
            f"(without the tag's leading 'v'); got {version!r}"
        )


# ----------------------------------------------------------------------- build
def build(
    *,
    source: str | Path,
    out: str | Path,
    version: str,
    channel: str,
    commit: str | None = None,
    compat: dict | None = None,
    runtime_dir: str | Path | None = None,
) -> dict:
    """Build a distribution package directory, its manifest and its digest.

    ``runtime_dir`` is optional: when given it must contain ``python.exe`` and is
    copied verbatim into ``runtime/`` together with the generated wrapper; when
    omitted the #93 layout is produced unchanged.

    Returns a JSON-serialisable summary with the three output paths (the
    manifest path points inside the package).  Raises :class:`BuildError` for an
    invalid request and leaves no package behind when validation fails.
    """
    requested_version = str(version or "").strip()
    version = normalize_version(requested_version)
    _validate_request(version, channel)
    source_path = Path(source).expanduser().resolve()
    out_path = Path(out).expanduser().resolve()
    if not source_path.is_dir():
        raise BuildError(f"source tree is not a directory: {source_path}")
    runtime_path = _validate_runtime_dir(runtime_dir) if runtime_dir is not None else None
    if (source_path / "agent" / "package.json").is_file():
        for required in ("agent/dist/main.js", "agent/node_modules/@earendil-works/pi-coding-agent/package.json"):
            if not (source_path / required).is_file():
                raise BuildError(f"bundled Pi agent is not built: {required}; run npm ci and npm run build in agent/")
        if runtime_path is not None and not (runtime_path / "node.exe").is_file():
            raise BuildError("runtime/node.exe is required for the bundled Pi agent")

    package_name = f"horizon-{version}-{channel}"
    package_dir = out_path / package_name
    manifest_path = package_dir / MANIFEST_FILENAME
    digest_path = out_path / f"{package_name}{DIGEST_SUFFIX}"
    artifact_path = out_path / f"{package_name}{ARTIFACT_SUFFIX}"

    try:
        relpaths, excluded = _source_relpaths(source_path, out_path)
    except OSError as exc:
        raise BuildError(f"could not read the source tree at {source_path}: {exc}") from exc
    if not relpaths:
        raise BuildError(
            f"source tree has no runtime code or agent-readable assets to package: {source_path}"
        )

    try:
        out_path.mkdir(parents=True, exist_ok=True)
        if package_dir.is_symlink() or (package_dir.exists() and not package_dir.is_dir()):
            raise BuildError(f"refusing to replace a non-directory package path: {package_dir}")
        if package_dir.exists():
            shutil.rmtree(package_dir)
        package_dir.mkdir(parents=True)
        for rel in relpaths:
            origin = source_path / rel
            destination = package_dir / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, destination)
            mode = 0o755 if _is_executable(rel, origin) else 0o644
            try:
                destination.chmod(mode)
            except OSError:
                pass
        runtime_summary = (
            _copy_runtime(runtime_path, package_dir)
            if runtime_path is not None
            else None
        )
    except OSError as exc:
        raise BuildError(f"could not write the package at {package_dir}: {exc}") from exc

    built_from_commit, commit_source, warning = resolve_commit(source_path, commit)
    compatibility = _compat(compat)

    package_files = sorted(
        (
            path
            for path in package_dir.rglob("*")
            if path.is_file()
            and path.relative_to(package_dir).as_posix() != MANIFEST_FILENAME
        ),
        key=lambda path: path.relative_to(package_dir).as_posix(),
    )
    files = [
        {
            "path": path.relative_to(package_dir).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "executable": _is_executable(path.relative_to(package_dir), path),
        }
        for path in package_files
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "channel": channel,
        "built_from_commit": built_from_commit,
        "compat": compatibility,
        "files": files,
    }
    manifest_bytes = _write_json(manifest_path, manifest)

    try:
        _write_artifact(package_dir, artifact_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BuildError(f"could not write the package artifact at {artifact_path}: {exc}") from exc
    artifact_sha256 = sha256_file(artifact_path)
    artifact_bytes = artifact_path.stat().st_size

    digest = {
        "schema_version": SCHEMA_VERSION,
        "package": package_name,
        "version": version,
        "channel": channel,
        "built_from_commit": built_from_commit,
        "compat": compatibility,
        "manifest": MANIFEST_FILENAME,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "files": len(files),
        "bytes": sum(row["bytes"] for row in files),
        "artifact": {
            "name": artifact_path.name,
            "bytes": artifact_bytes,
            "sha256": artifact_sha256,
        },
    }
    _write_json(digest_path, digest)

    result = {
        "ok": True,
        "package": str(package_dir),
        "manifest": str(manifest_path),
        "digest": str(digest_path),
        "artifact": {
            "path": str(artifact_path),
            "name": artifact_path.name,
            "bytes": artifact_bytes,
            "sha256": artifact_sha256,
        },
        "version": version,
        "requested_version": requested_version,
        "version_normalized": requested_version != version,
        "channel": channel,
        "built_from_commit": built_from_commit,
        "commit_source": commit_source,
        "compat": compatibility,
        "files": len(files),
        "bytes": digest["bytes"],
        "manifest_sha256": digest["manifest_sha256"],
        "runtime": runtime_summary,
        "launcher": (
            str(package_dir / launcher.WRAPPER_REL)
            if runtime_summary is not None
            else None
        ),
        "excluded": excluded,
    }
    if warning:
        result["warning"] = warning
    return result


# ------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m hostrun.distribution",
        description="Build a horizon distribution package, manifest and digest (offline).",
    )
    parser.add_argument("--source", required=True, help="source tree to package")
    parser.add_argument("--out", required=True, help="directory that receives the package and digest")
    parser.add_argument(
        "--version",
        required=True,
        help="package version without the tag prefix, e.g. 0.1.0 (a leading 'v' is stripped)",
    )
    parser.add_argument("--channel", required=True, choices=CHANNELS)
    parser.add_argument(
        "--built-from-commit",
        dest="commit",
        help=(
            "evidence commit when the source has no clean .git "
            f"(default: clean Git HEAD, else {UNKNOWN_COMMIT!r})"
        ),
    )
    parser.add_argument("--os", dest="os_name", help="compat.os (default: this host)")
    parser.add_argument("--arch", help="compat.arch (default: this host)")
    parser.add_argument("--dsh", dest="dsh", help="compat.dsh minimum (default: *)")
    parser.add_argument("--python", dest="python_version", help="compat.python (default: this interpreter)")
    parser.add_argument(
        "--runtime-dir",
        dest="runtime_dir",
        help=(
            "prepared CPython runtime to bundle verbatim under runtime/; it must "
            f"contain {RUNTIME_PYTHON_NAME} and also generates bin/horizon.cmd "
            "(release builds always pass it)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    compat = {
        key: value
        for key, value in {
            "os": args.os_name,
            "arch": args.arch,
            "dsh": args.dsh,
            "python": args.python_version,
        }.items()
        if value
    }
    try:
        result = build(
            source=args.source,
            out=args.out,
            version=args.version,
            channel=args.channel,
            commit=args.commit,
            compat=compat,
            runtime_dir=args.runtime_dir,
        )
    except BuildError as exc:
        emit({"ok": False, "error": str(exc)})
        return 1
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
