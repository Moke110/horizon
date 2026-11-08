"""Install a distribution package and keep the installation's lock and evidence.

ADR 0010 makes the distribution package the user-facing installation unit.  This
module is the runtime side of that decision:

- :func:`install_package` takes a local artifact (path or ``file://`` URL), or
  downloads an ``https://`` artifact URL, and its digest, verifies the artifact
  *before* unpacking it, reads and validates ``manifest.json`` from inside the
  zip, extracts into ``<code>/.horizon/staging/<id>``, verifies every staged
  byte, and only then copies the package into the code directory and writes
  ``<code>/.horizon/dist.lock.json``.
- :func:`verify_install` checks that the lock, manifest and on-disk files still
  agree without needing the original zip.
- :func:`apply_staged` is deliberately small so ticket #96 (retention and
  rollback) can wrap it; this ticket does not delete or replace the runtime area.

The only network access is an explicit ``https://`` artifact source, delegated
to :func:`hostrun.release.resolve_release_source`; plain ``http://`` and every
other scheme are refused.  The build side lives in :mod:`hostrun.distribution`.
"""

from __future__ import annotations

import filecmp
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from common import (
    ensure_dir,
    now,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json,
    write_text_bytes,
)
from hostrun import distribution

SCHEMA_VERSION = 1
DISTRIBUTION = "package"
SOURCE_DISTRIBUTION = "source"
#: A source installation has no distribution manifest, so it declares no
#: compatibility requirements; the empty object is recorded explicitly (#97).
SOURCE_REQUIRES: dict = {}
LOCK_REL = Path(".horizon") / "dist.lock.json"
STAGING_REL = Path(".horizon") / "staging"
RELEASES_REL = Path(".horizon") / "releases"
CURRENT_REL = Path(".horizon") / "current"
PENDING_REL = Path(".horizon") / "dist.pending.json"
RELEASE_FILENAME = "release.json"
RELEASE_SCHEMA_VERSION = 1
MANIFEST_FILENAME = distribution.MANIFEST_FILENAME
CHANNELS = distribution.CHANNELS
#: Files an installation generates after extraction, so they are not in the
#: shipped manifest.  Only fixed paths are tolerated: ``console install`` writes
#: ``<code>/bin/horizon-console.cmd`` next to the package's ``bin/horizon.cmd``
#: (#95).  Any other unlisted file is still reported as an extra file.
VERIFY_IGNORED_PATHS = frozenset({"bin/horizon-console.cmd"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+~-]*$")
_MANIFEST_LIMIT = 16 * 1024 * 1024
_MANIFEST_KEYS = frozenset(
    {"schema_version", "version", "channel", "built_from_commit", "compat", "files"}
)
_COMPAT_KEYS = frozenset({"os", "arch", "dsh", "python"})
_FILE_KEYS = frozenset({"path", "bytes", "sha256", "executable"})
# Two release versions exist at any time: the active tree and one retained
# previous tree.  Installing a third version prunes the oldest retained tree.
RETAINED_LIMIT = 1


class DistError(Exception):
    """A direct API misuse (the CLI reports domain failures as blocked payloads)."""


class _InstallBlocked(Exception):
    def __init__(self, payload: dict):
        super().__init__(payload.get("reason", "blocked"))
        self.payload = payload


def lock_path(code: str | Path) -> Path:
    return Path(code).expanduser().resolve() / LOCK_REL


def read_lock(code: str | Path) -> dict | None:
    """Read the install lock, or ``None`` when absent/unreadable."""
    try:
        lock = read_json(lock_path(code))
    except (OSError, ValueError):
        return None
    return lock if isinstance(lock, dict) else None


def write_lock(code: str | Path, lock: dict) -> Path:
    """Write the install lock directly; never route it through ``save_config``.

    ``base.save_config`` redacts any key containing ``code``/``key``; a lock has
    no secret but must keep its literal field names and digests.
    """
    path = lock_path(code)
    write_json(path, lock)
    return path


def source_lock(
    *,
    commit: str,
    dirty: bool,
    source_url: str,
    installed_at: str | None = None,
    skill_digest: str = "",
) -> dict:
    """The truthful lock for a source installation (#97).

    It records only evidence a source checkout has: the git commit, whether the
    checkout was dirty, the repository URL, the install time and the skill
    pointer digest.  Package-only fields (version, channel, artifact/manifest
    digests, file count) are deliberately absent; a source install never
    pretends to be a distribution package.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "distribution": SOURCE_DISTRIBUTION,
        "commit": str(commit or ""),
        "dirty": bool(dirty),
        "source_url": str(source_url or ""),
        "installed_at": str(installed_at or now()),
        "requires": dict(SOURCE_REQUIRES),
        "skill_digest": str(skill_digest or ""),
    }


def write_source_lock(
    code: str | Path,
    *,
    commit: str,
    dirty: bool,
    source_url: str,
    installed_at: str | None = None,
    skill_digest: str = "",
) -> dict:
    """Write (or refresh) a source install lock for the code directory.

    A package installation is never overwritten by a source install: the
    returned payload is skipped instead.  Re-running the workspace install
    keeps the original ``installed_at`` and any digest a skill registration
    already recorded, so the lock stays idempotent and truthful.
    """
    code_path = Path(code).expanduser().resolve()
    existing = read_lock(code_path)
    if existing is not None and existing.get("distribution") != SOURCE_DISTRIBUTION:
        return {
            "ok": False,
            "skipped": True,
            "reason": "package_install",
            "distribution": existing.get("distribution"),
            "lock": str(lock_path(code_path)),
            "note": "当前代码目录是发行包安装；未用源码安装锁覆盖它",
        }
    previous = existing or {}
    lock = source_lock(
        commit=commit,
        dirty=dirty,
        source_url=source_url,
        installed_at=installed_at
        or str(previous.get("installed_at") or "")
        or now(),
        skill_digest=skill_digest or str(previous.get("skill_digest") or ""),
    )
    path = write_lock(code_path, lock)
    return {
        "ok": True,
        "skipped": False,
        "distribution": SOURCE_DISTRIBUTION,
        "commit": lock["commit"],
        "dirty": lock["dirty"],
        "source_url": lock["source_url"],
        "installed_at": lock["installed_at"],
        "skill_digest": lock["skill_digest"],
        "lock": str(path),
    }


def releases_path(code: str | Path) -> Path:
    return Path(code).expanduser().resolve() / RELEASES_REL


def current_path(code: str | Path) -> Path:
    return Path(code).expanduser().resolve() / CURRENT_REL


def read_current(code: str | Path) -> str | None:
    """Read the ``.horizon/current`` pointer, or ``None`` when absent."""
    try:
        text = current_path(code).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text or None


def _write_current(code: str | Path, version: str) -> Path:
    path = current_path(code)
    write_text_bytes(path, version)
    return path


def _usable_version(version: object) -> bool:
    """Versions name a directory under ``.horizon/releases``; keep them inert."""
    return isinstance(version, str) and bool(_VERSION_RE.fullmatch(version))


def _manifest_at(tree: Path) -> tuple[bytes, dict] | None:
    """Read and validate ``manifest.json`` from a tree, or ``None``."""
    path = tree / MANIFEST_FILENAME
    try:
        data = path.read_bytes()
    except OSError:
        return None
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if _validate_manifest(manifest):
        return None
    return data, manifest


def _tree_disagreements(tree: Path, manifest: dict) -> tuple[list[str], list[str]]:
    """Return ``(missing, mismatched)`` paths for a manifest against a tree."""
    missing: list[str] = []
    mismatch: list[str] = []
    for row in manifest["files"]:
        rel = row["path"]
        target = tree / Path(rel)
        if not target.is_file():
            missing.append(rel)
            continue
        try:
            data = target.read_bytes()
        except OSError:
            missing.append(rel)
            continue
        if len(data) != row["bytes"] or sha256_bytes(data) != row["sha256"]:
            mismatch.append(rel)
    return missing, mismatch


def _release_records(code: str | Path) -> list[dict]:
    """Retained release records (``release.json``), oldest first."""
    root = releases_path(code)
    if not root.is_dir():
        return []
    records: list[dict] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            record = read_json(entry / RELEASE_FILENAME)
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        if str(record.get("version") or "") != entry.name:
            continue
        record["_path"] = str(entry)
        records.append(record)
    records.sort(
        key=lambda row: (str(row.get("installed_at") or ""), str(row.get("version") or ""))
    )
    return records


def retained_releases(code: str | Path) -> list[dict]:
    """Public summary of retained releases, newest first."""
    records = _release_records(code)
    records.reverse()
    return [
        {
            "version": str(record.get("version") or ""),
            "channel": str(record.get("channel") or ""),
            "artifact_sha256": str(record.get("artifact_sha256") or ""),
        }
        for record in records
    ]


def _retain_tree(
    code: Path,
    manifest: dict,
    manifest_bytes: bytes,
    *,
    artifact_sha256: str,
    channel: str,
    source_url: str,
    installed_at: str,
) -> Path:
    """Copy one verified tree into ``.horizon/releases/<version>``.

    The retained tree is a plain copy (no re-extraction): every manifest file
    plus the original ``manifest.json`` and a ``release.json`` metadata record.
    """
    version = manifest["version"]
    if not _usable_version(version):
        _stop(
            "invalid_version",
            f"版本号不能用作保留目录名：{version!r}",
            version=version,
        )
    target = releases_path(code) / version
    if target.exists():
        shutil.rmtree(target)
    ensure_dir(target)
    for row in manifest["files"]:
        rel = row["path"]
        source = code / Path(rel)
        if not source.is_file():
            _stop(
                "retention_failed",
                f"待保留版本缺少文件：{rel}",
                version=version,
                files=[rel],
            )
        destination = target / Path(rel)
        ensure_dir(destination.parent)
        shutil.copyfile(source, destination)
        try:
            shutil.copymode(source, destination)
        except OSError:
            pass
    (target / MANIFEST_FILENAME).write_bytes(manifest_bytes)
    record = {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "version": version,
        "channel": channel,
        "artifact_sha256": artifact_sha256,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "package_file_count": len(manifest["files"]),
        "source_url": source_url,
        "installed_at": installed_at,
    }
    write_json(target / RELEASE_FILENAME, record)
    return target


def _prune_releases(code: Path, keep: str | None) -> list[str]:
    """Keep at most ``RETAINED_LIMIT`` retained trees, always including ``keep``.

    The active tree is not in this directory, so ``RETAINED_LIMIT = 1`` means the
    active version plus one previous version.  Keeping this decision here stops
    the retention count and the pruning walk from drifting apart.
    """
    root = releases_path(code)
    if not root.is_dir():
        return []
    keep = keep or ""
    records = {str(record.get("version") or ""): record for record in _release_records(code)}
    entries = [entry for entry in root.iterdir() if entry.is_dir()]
    if keep:
        entries.sort(
            key=lambda entry: (
                entry.name != keep,
                str((records.get(entry.name) or {}).get("installed_at") or ""),
                entry.name,
            )
        )
    else:
        entries.sort(
            key=lambda entry: (
                str((records.get(entry.name) or {}).get("installed_at") or ""),
                entry.name,
            ),
            reverse=True,
        )
    removed: list[str] = []
    for entry in entries[RETAINED_LIMIT:]:
        shutil.rmtree(entry)
        removed.append(entry.name)
    return removed


def _remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    stop = stop.resolve()
    while current != stop and current.is_dir():
        try:
            next(current.iterdir())
        except StopIteration:
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent
            continue
        except OSError:
            return
        return


def _remove_stale_files(code: Path, old_manifest: dict, new_manifest: dict) -> list[str]:
    """Delete files the previous manifest listed but the new one does not.

    Never touches the runtime area: ``.horizon`` is both rejected as a
    relative path and excluded by the resolved-path containment check.
    """
    new_paths = {row["path"] for row in new_manifest["files"]}
    code_root = code.resolve()
    removed: list[str] = []
    for row in old_manifest["files"]:
        rel = row["path"]
        if rel in new_paths or rel == MANIFEST_FILENAME:
            continue
        if not _safe_relpath(rel) or _reserved_relpath(rel):
            continue
        target = (code / Path(rel)).resolve()
        try:
            target.relative_to(code_root)
        except ValueError:
            continue
        if target.is_file():
            target.unlink()
            removed.append(rel)
            _remove_empty_parents(target.parent, code_root)
    return removed


def _remove_applied_files(code: Path, files: list[str]) -> list[str]:
    """Best-effort removal of the package files a failed fresh apply created."""
    removed: list[str] = []
    for rel in files:
        if not _safe_relpath(rel) or _reserved_relpath(rel) or rel == MANIFEST_FILENAME:
            continue
        target = (code / Path(rel)).resolve()
        try:
            target.relative_to(code.resolve())
        except ValueError:
            continue
        try:
            if target.is_file():
                target.unlink()
                removed.append(rel)
                _remove_empty_parents(target.parent, code.resolve())
        except OSError:
            continue
    try:
        (code / MANIFEST_FILENAME).unlink(missing_ok=True)
    except OSError:
        pass
    return removed


def _blocked(reason: str, note: str, **extra) -> dict:
    return {
        "ok": False,
        "status": "blocked",
        "distribution": DISTRIBUTION,
        "reason": reason,
        "note": note,
        **extra,
    }


def _stop(reason: str, note: str, **extra):
    raise _InstallBlocked(_blocked(reason, note, **extra))


# ------------------------------------------------------------------- artifact
def _source_path(source: str) -> Path:
    """Resolve a local path, ``file://`` URL or ``https://`` artifact URL.

    URL downloads are delegated to :func:`hostrun.release.resolve_release_source`
    (imported lazily so importing this installer never pulls in the release
    pipeline).  Plain ``http://`` and every other scheme stay refused, and a
    download failure becomes a :class:`DistError` so the installer reports a
    blocked payload instead of a traceback.
    """
    raw = str(source or "").strip()
    if not raw:
        raise DistError("缺少发行包来源")
    if _DRIVE_RE.match(raw) or raw.startswith("\\\\"):
        return Path(raw).expanduser().resolve()
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme == "file":
        path_text = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            path_text = f"//{parsed.netloc}{path_text}"
        return Path(url2pathname(path_text)).expanduser().resolve()
    if scheme == "https":
        from hostrun import release

        try:
            return release.resolve_release_source(raw)
        except release.ReleaseError as exc:
            raise DistError(str(exc)) from exc
    if scheme:
        raise DistError(
            "不支持的发行包来源（只接受本地路径、file:// 或 https:// URL）："
            f"{raw}"
        )
    return Path(raw).expanduser().resolve()


def _digest_candidates(artifact: Path) -> list[Path]:
    if artifact.suffix.lower() == ".zip":
        return [
            artifact.with_suffix(".digest.json"),
            Path(str(artifact) + ".digest.json"),
        ]
    return [Path(str(artifact) + ".digest.json")]


def _load_digest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _stop(
            "invalid_digest",
            f"无法读取摘要文件：{path}（{exc}）",
            digest=str(path),
        )
    if not isinstance(data, dict):
        _stop("invalid_digest", f"摘要文件不是 JSON 对象：{path}", digest=str(path))
    return data


def _resolve_expected(
    artifact: Path,
    digest_path: str | Path | None,
    sha256: str | None,
) -> tuple[str, dict | None, Path | None]:
    """Resolve the expected artifact sha256 and the digest metadata.

    Explicit ``--sha256`` wins, then an explicit ``--digest``, then a sibling
    ``<artifact>.digest.json`` (for a zip also ``<stem>.digest.json``).
    """
    explicit_sha = str(sha256 or "").strip().lower()
    if explicit_sha and not _SHA256_RE.fullmatch(explicit_sha):
        _stop(
            "invalid_sha256",
            "--sha256 需要 64 位小写十六进制 SHA-256",
            sha256=explicit_sha,
        )

    digest_file: Path | None = None
    digest: dict | None = None
    if digest_path:
        digest_file = Path(digest_path).expanduser().resolve()
        if not digest_file.is_file():
            _stop(
                "digest_not_found",
                f"找不到摘要文件：{digest_file}",
                digest=str(digest_file),
            )
        digest = _load_digest(digest_file)
    elif not explicit_sha:
        for candidate in _digest_candidates(artifact):
            if candidate.is_file():
                digest_file = candidate
                break
        if digest_file is not None:
            digest = _load_digest(digest_file)

    digest_sha = ""
    if digest is not None:
        artifact_block = digest.get("artifact")
        if artifact_block is not None:
            if not isinstance(artifact_block, dict):
                _stop("invalid_digest", "摘要文件的 artifact 字段必须是对象", digest=str(digest_file))
            digest_sha = str(artifact_block.get("sha256") or "").strip().lower()
            if digest_sha and not _SHA256_RE.fullmatch(digest_sha):
                if not explicit_sha:
                    _stop(
                        "invalid_digest",
                        "摘要文件的 artifact.sha256 不是合法 SHA-256",
                        digest=str(digest_file),
                    )
                digest_sha = ""

    expected = explicit_sha or digest_sha
    if not expected:
        _stop(
            "digest_not_found",
            "未找到可用的制品摘要；请提供 --sha256 或 --digest，或把摘要文件放在制品旁边",
            artifact=str(artifact),
        )
    return expected, digest, digest_file


def _safe_relpath(name: str) -> bool:
    """Reject absolute paths, drive letters, backslashes and ``..`` components."""
    if not isinstance(name, str) or not name or "\x00" in name:
        return False
    if name.startswith(("/", "\\")) or _DRIVE_RE.match(name):
        return False
    if "\\" in name:
        return False
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return True


def _reserved_relpath(name: str) -> bool:
    return name.split("/", 1)[0] == ".horizon"


# ------------------------------------------------------------------- manifest
def _validate_manifest(manifest: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest 必须是 JSON 对象"]
    unknown = sorted(set(manifest) - _MANIFEST_KEYS)
    if unknown:
        errors.append(f"manifest 含未知字段：{', '.join(unknown)}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version 必须为 1")
    for key in ("version", "channel", "built_from_commit"):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            errors.append(f"缺少字符串字段 {key}")
    if manifest.get("channel") not in CHANNELS:
        errors.append(f"channel 必须是 {'/'.join(CHANNELS)}")
    compat = manifest.get("compat")
    if not isinstance(compat, dict):
        errors.append("缺少 compat 对象")
    else:
        unknown_compat = sorted(set(compat) - _COMPAT_KEYS)
        if unknown_compat:
            errors.append(f"compat 含未知字段：{', '.join(unknown_compat)}")
        for key in ("os", "arch", "dsh", "python"):
            if not isinstance(compat.get(key), str) or not compat[key].strip():
                errors.append(f"缺少 compat.{key}")
    files = manifest.get("files")
    if not isinstance(files, list):
        errors.append("files 必须是数组")
        return errors
    seen: set[str] = set()
    for index, row in enumerate(files):
        prefix = f"files[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        unknown_file = sorted(set(row) - _FILE_KEYS)
        if unknown_file:
            errors.append(f"{prefix} 含未知字段：{', '.join(unknown_file)}")
        path = row.get("path")
        if not isinstance(path, str) or not _safe_relpath(path):
            errors.append(f"{prefix}.path 不安全或为空：{path!r}")
        else:
            if _reserved_relpath(path):
                errors.append(f"{prefix}.path 不得写入 .horizon：{path}")
            if path == MANIFEST_FILENAME:
                errors.append(f"{prefix}.path 不得是清单自身")
            if path in seen:
                errors.append(f"{prefix}.path 重复：{path}")
            seen.add(path)
        size = row.get("bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            errors.append(f"{prefix}.bytes 必须是非负整数")
        sha = row.get("sha256")
        if not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha):
            errors.append(f"{prefix}.sha256 不是合法 SHA-256")
        if not isinstance(row.get("executable"), bool):
            errors.append(f"{prefix}.executable 必须是布尔值")
    return errors


def _resolve_channel(
    explicit_channel: str | None,
    digest_channel: str,
    manifest_channel: str | None,
) -> str:
    """Resolve the effective channel once for install and archive validation.

    Precedence is explicit ``--channel`` → digest → manifest.  A conflict is a
    ``blocked`` result, not a silent override, so the two paths cannot drift.
    """
    explicit = str(explicit_channel or "").strip()
    from_digest = str(digest_channel or "").strip()
    from_manifest = str(manifest_channel or "").strip()
    if explicit and explicit not in CHANNELS:
        _stop("invalid_channel", f"channel 必须是 {'/'.join(CHANNELS)}：{explicit}")
    if from_digest and from_digest not in CHANNELS:
        _stop("invalid_digest", f"摘要文件的 channel 不受支持：{from_digest}")
    if explicit and from_digest and explicit != from_digest:
        _stop(
            "channel_conflict",
            f"显式 channel {explicit} 与摘要文件 channel {from_digest} 冲突",
            expected=from_digest,
            actual=explicit,
        )
    channel = explicit or from_digest or from_manifest
    if channel not in CHANNELS:
        _stop("invalid_channel", f"channel 必须是 {'/'.join(CHANNELS)}：{channel}")
    if from_manifest and channel != from_manifest:
        _stop(
            "channel_mismatch",
            f"安装渠道 {channel} 与发行包清单渠道 {from_manifest} 不一致",
            expected=from_manifest,
            actual=channel,
        )
    return channel


def _read_archive(
    artifact: Path,
    digest: dict | None,
    explicit_channel: str | None,
) -> tuple[zipfile.ZipFile, bytes, dict, str, str]:
    """Open the zip and validate its manifest and entry names before extraction.

    Returns ``(archive, manifest_bytes, manifest, manifest_sha, channel)``.  The
    archive is left open for the caller to extract from; every failure raises
    :class:`_InstallBlocked`.
    """
    try:
        archive = zipfile.ZipFile(artifact, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        _stop("invalid_zip", f"发行包不是可读取的 zip：{exc}", artifact=str(artifact))

    try:
        entries = archive.infolist()
        names: list[str] = []
        seen: set[str] = set()
        for info in entries:
            name = info.filename
            if name.endswith("/"):
                if not _safe_relpath(name.rstrip("/")):
                    _stop("unsafe_path", f"zip 中包含不安全路径：{name}", path=name)
                continue
            if not _safe_relpath(name):
                _stop("unsafe_path", f"zip 中包含不安全路径：{name}", path=name)
            if name in seen:
                _stop("duplicate_zip_entry", f"zip 中条目重复：{name}", path=name)
            seen.add(name)
            names.append(name)
        if MANIFEST_FILENAME not in seen:
            _stop("missing_manifest", "发行包内缺少 manifest.json", artifact=str(artifact))
        try:
            manifest_bytes = archive.read(MANIFEST_FILENAME)
        except (KeyError, OSError, RuntimeError) as exc:
            _stop("invalid_manifest", f"无法读取 manifest.json：{exc}")
        if len(manifest_bytes) > _MANIFEST_LIMIT:
            _stop("invalid_manifest", "manifest.json 过大")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            _stop("invalid_manifest", f"manifest.json 不是合法 UTF-8 JSON：{exc}")
        if isinstance(manifest, dict) and isinstance(manifest.get("files"), list):
            for row in manifest["files"]:
                if isinstance(row, dict):
                    candidate = row.get("path")
                    if not isinstance(candidate, str) or not _safe_relpath(candidate):
                        _stop(
                            "unsafe_path",
                            f"manifest.json 中包含不安全路径：{candidate!r}",
                            path=candidate,
                        )
                    if _reserved_relpath(candidate):
                        _stop(
                            "unsafe_path",
                            f"manifest.json 试图写入运行时区：{candidate}",
                            path=candidate,
                        )
        errors = _validate_manifest(manifest)
        if errors:
            _stop(
                "invalid_manifest",
                "manifest.json 校验失败：" + "；".join(errors[:5]),
                errors=errors,
            )

        file_paths = [row["path"] for row in manifest["files"]]
        expected_names = {MANIFEST_FILENAME, *file_paths}
        actual_names = set(names)
        extra = sorted(actual_names - expected_names)
        missing = sorted(expected_names - actual_names)
        if extra:
            _stop(
                "unexpected_zip_entries",
                f"zip 中有清单未列出的条目：{', '.join(extra[:5])}",
                entries=extra,
            )
        if missing:
            _stop(
                "missing_zip_entries",
                f"zip 缺少清单列出的条目：{', '.join(missing[:5])}",
                entries=missing,
            )

        manifest_sha = sha256_bytes(manifest_bytes)
        if digest is not None:
            expected_manifest_sha = str(digest.get("manifest_sha256") or "").strip().lower()
            if expected_manifest_sha and expected_manifest_sha != manifest_sha:
                _stop(
                    "manifest_digest_mismatch",
                    "manifest.json 的 sha256 与摘要文件不一致",
                    expected=expected_manifest_sha,
                    actual=manifest_sha,
                )
            digest_version = str(digest.get("version") or "").strip()
            if digest_version and digest_version != manifest["version"]:
                _stop(
                    "version_mismatch",
                    f"摘要文件版本 {digest_version} 与清单版本 {manifest['version']} 不一致",
                    expected=digest_version,
                    actual=manifest["version"],
                )
            digest_files = digest.get("files")
            if isinstance(digest_files, int) and digest_files != len(file_paths):
                _stop(
                    "file_count_mismatch",
                    f"摘要文件记录 {digest_files} 个文件，清单列出 {len(file_paths)} 个",
                    expected=digest_files,
                    actual=len(file_paths),
                )

        digest_channel = str((digest or {}).get("channel") or "").strip()
        channel = _resolve_channel(
            explicit_channel, digest_channel, manifest["channel"]
        )
        return archive, manifest_bytes, manifest, manifest_sha, channel
    except Exception:
        archive.close()
        raise


def _compat_host(field: str) -> str:
    if field == "os":
        return distribution.normalize_os(platform.system())
    if field == "arch":
        return distribution.normalize_arch(platform.machine())
    if field == "python":
        return f"{sys.version_info.major}.{sys.version_info.minor}"
    return ""


def _compat_value_matches(value: str, host: str) -> bool:
    value = value.strip()
    if value == "*":
        return True
    if value.lower() == host.lower():
        return True
    if host == f"{sys.version_info.major}.{sys.version_info.minor}":
        match = re.match(r"^(\d+)\.(\d+)", value)
        if match and host == f"{int(match.group(1))}.{int(match.group(2))}":
            return True
        match = re.match(r"^>=\s*(\d+)\.(\d+)", value)
        if match:
            return (sys.version_info.major, sys.version_info.minor) >= (
                int(match.group(1)),
                int(match.group(2)),
            )
    return False


def _check_compat(manifest: dict) -> None:
    compat = manifest["compat"]
    for field in ("os", "arch", "python"):
        required = str(compat[field]).strip()
        host = _compat_host(field)
        canonical = required
        if field == "os":
            canonical = distribution.normalize_os(required)
        elif field == "arch":
            canonical = distribution.normalize_arch(required)
        if not _compat_value_matches(canonical, host):
            _stop(
                f"compat_{field}_mismatch",
                f"发行包要求 compat.{field}={required}，当前主机为 {host}",
                field=f"compat.{field}",
                required=required,
                host=host,
            )


_VERSION_COMPONENT_RE = re.compile(r"^(?P<op>>=|<=|==|=|>|<)?\s*v?(?P<version>\d+(?:\.\d+){0,2})(?:[-+].*)?$")
_VERSION_NUMBER_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")


def _version_tuple(text: str) -> tuple[int, int, int]:
    parts = [int(part) for part in text.split(".")]
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


def _parse_host_version(output: str) -> str:
    match = _VERSION_NUMBER_RE.search(output or "")
    return match.group(0) if match else ""


def _dsh_requirement_matches(requirement: str, version: str) -> bool | None:
    """Return True/False for a parsable requirement, ``None`` when unparsable."""
    requirement = str(requirement or "").strip()
    if requirement == "*":
        return True
    match = _VERSION_COMPONENT_RE.fullmatch(requirement)
    if not match:
        return None
    wanted = _version_tuple(match.group("version"))
    actual = _version_tuple(version)
    operator = match.group("op") or "=="
    if operator in ("==", "="):
        return actual == wanted
    if operator == ">=":
        return actual >= wanted
    if operator == ">":
        return actual > wanted
    if operator == "<=":
        return actual <= wanted
    if operator == "<":
        return actual < wanted
    return None


def dsh_compatibility(
    code: str | Path,
    requirement: str,
    *,
    runner=subprocess.run,
) -> dict:
    """Check the optional ``compat.dsh`` requirement against the recorded dsh.

    A wildcard passes.  A recorded ``paths.dsh`` that runs and prints a parsable
    version yields ``ok`` or ``mismatch``.  Anything that prevents a trustworthy
    answer yields ``unverified`` with a note: the install is not silently
    reported as compatible.
    """
    requirement = str(requirement or "").strip() or "*"
    if requirement == "*":
        return {
            "status": "ok",
            "required": requirement,
            "host": "",
            "note": "compat.dsh=*；不做宿主版本限制",
            "evidence": {"checked": False},
        }
    code_path = Path(code).expanduser().resolve()
    config = read_json(code_path / ".horizon" / "install" / "config.json") or {}
    dsh = str(((config.get("paths") or {}).get("dsh") or "")).strip()
    if not dsh:
        return {
            "status": "unverified",
            "required": requirement,
            "host": "",
            "note": "compat.dsh 非 *，但未记录 paths.dsh；宿主 dsh 版本未验证",
            "evidence": {"checked": False, "dsh": ""},
        }
    if not Path(dsh).is_absolute() or not Path(dsh).is_file():
        return {
            "status": "unverified",
            "required": requirement,
            "host": "",
            "note": f"compat.dsh 非 *，但记录的 paths.dsh 不存在或不是绝对路径：{dsh}；宿主版本未验证",
            "evidence": {"checked": False, "dsh": dsh},
        }
    try:
        proc = runner(
            [dsh, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "unverified",
            "required": requirement,
            "host": "",
            "note": f"compat.dsh 非 *，但运行 {dsh} --version 失败：{exc}；宿主版本未验证",
            "evidence": {"checked": False, "dsh": dsh, "error": str(exc)},
        }
    if proc.returncode != 0:
        return {
            "status": "unverified",
            "required": requirement,
            "host": "",
            "note": f"compat.dsh 非 *，但 {dsh} --version 退出码非零；宿主版本未验证",
            "evidence": {"checked": False, "dsh": dsh, "returncode": proc.returncode},
        }
    output = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    host = _parse_host_version(output)
    if not host:
        return {
            "status": "unverified",
            "required": requirement,
            "host": "",
            "note": f"compat.dsh 非 *，但无法从 {dsh} --version 解析版本；宿主版本未验证",
            "evidence": {"checked": False, "dsh": dsh, "output": output.strip()[:200]},
        }
    matched = _dsh_requirement_matches(requirement, host)
    if matched is None:
        return {
            "status": "unverified",
            "required": requirement,
            "host": host,
            "note": f"compat.dsh 要求 {requirement!r} 无法解析；宿主 dsh {host} 未判定",
            "evidence": {"checked": False, "dsh": dsh, "host": host},
        }
    return {
        "status": "ok" if matched else "mismatch",
        "required": requirement,
        "host": host,
        "note": (
            f"宿主 dsh {host} 满足 compat.dsh {requirement}"
            if matched
            else f"发行包要求 compat.dsh {requirement}，当前宿主 dsh 为 {host}"
        ),
        "evidence": {"checked": True, "dsh": dsh, "host": host},
    }


def _check_dsh_compat(code: Path, manifest: dict, *, runner=None) -> dict:
    result = dsh_compatibility(
        code,
        manifest.get("compat", {}).get("dsh", "*"),
        runner=runner or subprocess.run,
    )
    if result["status"] == "mismatch":
        _stop(
            "compat_dsh_mismatch",
            result["note"],
            field="compat.dsh",
            required=result["required"],
            host=result["host"],
        )
    return result


# -------------------------------------------------------------- pending marker
def pending_path(code: str | Path) -> Path:
    return Path(code).expanduser().resolve() / PENDING_REL


def read_pending(code: str | Path) -> dict | None:
    """Read the destructive-operation marker, or ``None`` when absent."""
    path = pending_path(code)
    if not path.is_file():
        return None
    try:
        marker = read_json(path)
    except (OSError, ValueError):
        return {"schema_version": 1, "operation": "unknown", "unreadable": True}
    return marker if isinstance(marker, dict) else {"operation": "unknown"}


def _write_pending_marker(code: Path, marker: dict) -> dict:
    payload = {"schema_version": 1, "started_at": now(), **marker}
    write_json(pending_path(code), payload)
    return payload


def _clear_pending_marker(code: Path) -> None:
    try:
        pending_path(code).unlink(missing_ok=True)
    except OSError:
        pass


def _pending_blocked(code: Path, marker: dict) -> dict:
    return _blocked(
        "pending_operation_incomplete",
        (
            "检测到未完成的破坏性安装操作留下的 .horizon/dist.pending.json；"
            "为避免把两个版本的文件混在一起，本次安装已停止。"
            "请先检查 pending_marker 与 recovery 字段：能恢复时按记录手工恢复，"
            "确认现有代码树完整后再删除该标记并重试；不要直接覆盖代码目录。"
        ),
        code=str(code),
        pending_marker=str(pending_path(code)),
        operation=marker.get("operation", "unknown"),
        target_version=marker.get("target_version"),
        pre_operation_version=marker.get("pre_operation_version"),
        retained=marker.get("retained"),
        recovery=marker.get("recovery"),
        guidance="检查标记指向的保留版本/恢复快照；确认树完整后删除 dist.pending.json 再重试",
    )


def _remove_paths(code: Path, relpaths: list[str]) -> list[str]:
    removed: list[str] = []
    code_root = code.resolve()
    for rel in relpaths:
        if not _safe_relpath(rel) or _reserved_relpath(rel) or rel == MANIFEST_FILENAME:
            continue
        target = (code / Path(rel)).resolve()
        try:
            target.relative_to(code_root)
        except ValueError:
            continue
        try:
            if target.is_file():
                target.unlink()
                removed.append(rel)
                _remove_empty_parents(target.parent, code_root)
        except OSError:
            continue
    return removed


def _restore_pre_operation(code: Path, marker: dict) -> dict:
    """Restore the tree, lock and current pointer recorded by the marker.

    Fresh installs only have package files to remove.  Upgrades and rollbacks
    keep the pre-operation tree in ``.horizon/releases/<version>`` (or a
    same-version recovery snapshot) and restore it file by file; the lock and
    ``current`` bytes are restored from the marker's snapshot.
    """
    operation = str(marker.get("operation") or "")
    target_files = [str(rel) for rel in (marker.get("target_files") or [])]
    if operation == "install":
        removed = _remove_applied_files(code, target_files)
        # A fresh install may have written the lock/current pointer before the
        # failure; they did not exist before this operation, so remove them.
        if marker.get("pre_lock") is None:
            try:
                lock_path(code).unlink(missing_ok=True)
            except OSError:
                pass
        if marker.get("pre_current") is None:
            try:
                current_path(code).unlink(missing_ok=True)
            except OSError:
                pass
        return {"restored": True, "restored_version": None, "removed": removed}

    retained_text = str(marker.get("retained") or "")
    retained = Path(retained_text).expanduser().resolve() if retained_text else None
    if retained is None or not retained.is_dir():
        raise DistError(f"恢复快照不存在：{retained_text or '<未记录>'}")
    current = _manifest_at(retained)
    if current is None:
        raise DistError(f"恢复快照的 manifest.json 缺失或无效：{retained}")
    old_bytes, old_manifest = current
    old_paths = {row["path"] for row in old_manifest["files"]}
    _remove_paths(code, [rel for rel in target_files if rel not in old_paths])
    for row in old_manifest["files"]:
        source = retained / Path(row["path"])
        if not source.is_file():
            raise DistError(f"恢复快照缺少文件：{row['path']}")
        destination = code / Path(row["path"])
        ensure_dir(destination.parent)
        _copy_unless_identical(source, destination)
    (code / MANIFEST_FILENAME).write_bytes(old_bytes)
    pre_lock = marker.get("pre_lock")
    if isinstance(pre_lock, dict):
        write_lock(code, pre_lock)
    else:
        raise DistError("恢复标记中没有 pre_lock 快照")
    pre_current = marker.get("pre_current")
    if isinstance(pre_current, str) and pre_current.strip():
        _write_current(code, pre_current.strip())
    else:
        try:
            current_path(code).unlink(missing_ok=True)
        except OSError:
            pass
    return {
        "restored": True,
        "restored_version": str(old_manifest.get("version") or ""),
        "removed": [],
    }


def _failed_operation(
    code: Path,
    payload: dict,
    marker: dict,
) -> dict:
    """Try to restore; if restoring fails keep the marker and say so."""
    try:
        recovery = _restore_pre_operation(code, marker)
    except (OSError, DistError) as exc:
        return {
            **payload,
            "ok": False,
            "status": "blocked",
            "restored": False,
            "recovery_incomplete": True,
            "pending_marker": str(pending_path(code)),
            "recovery_note": (
                f"回退失败：{exc}；已保留 {pending_path(code)} 与 "
                f"{marker.get('retained') or '恢复快照'}，请按该标记手工恢复后再删除它"
            ),
        }
    _clear_pending_marker(code)
    return {
        **payload,
        "ok": False,
        "status": "blocked",
        "restored": True,
        "recovery_incomplete": False,
        "recovery": recovery,
        "pending_marker": str(pending_path(code)),
        "recovery_note": (
            f"写入失败后已恢复到 {recovery.get('restored_version') or '操作前状态'}，"
            "临时标记已清除"
        ),
    }


def _snapshot_for_repair(code: Path, manifest: dict, backup: Path) -> Path:
    """Copy the existing files (even a partially broken tree) for same-version repair."""
    if backup.exists():
        shutil.rmtree(backup)
    ensure_dir(backup)
    for row in manifest["files"]:
        source = code / Path(row["path"])
        if source.is_file():
            destination = backup / Path(row["path"])
            ensure_dir(destination.parent)
            shutil.copyfile(source, destination)
    source_manifest = code / MANIFEST_FILENAME
    if source_manifest.is_file():
        (backup / MANIFEST_FILENAME).write_bytes(source_manifest.read_bytes())
    return backup


# ------------------------------------------------------------------- install
def _foreign_entries(code: Path) -> list[str]:
    if not code.is_dir():
        return []
    return sorted(
        path.name
        for path in code.iterdir()
        if path.name != ".horizon"
    )


def _extract_and_verify(
    archive: zipfile.ZipFile,
    manifest: dict,
    manifest_bytes: bytes,
    stage: Path,
) -> list[str]:
    if stage.exists():
        shutil.rmtree(stage)
    ensure_dir(stage)
    bad: list[str] = []
    for row in manifest["files"]:
        target = stage / Path(row["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = archive.read(row["path"])
        except (KeyError, OSError, RuntimeError):
            bad.append(row["path"])
            continue
        target.write_bytes(data)
        try:
            target.chmod(0o755 if row["executable"] else 0o644)
        except OSError:
            pass
    (stage / MANIFEST_FILENAME).write_bytes(manifest_bytes)

    for row in manifest["files"]:
        target = stage / Path(row["path"])
        if not target.is_file():
            if row["path"] not in bad:
                bad.append(row["path"])
            continue
        data = target.read_bytes()
        if len(data) != row["bytes"] or sha256_bytes(data) != row["sha256"]:
            if row["path"] not in bad:
                bad.append(row["path"])
    if sha256_bytes((stage / MANIFEST_FILENAME).read_bytes()) != sha256_bytes(manifest_bytes):
        bad.append(MANIFEST_FILENAME)
    if bad:
        shutil.rmtree(stage, ignore_errors=True)
        _stop(
            "file_verification_failed",
            "暂存文件与清单不一致：" + "，".join(bad[:5]),
            files=bad,
        )
    return [row["path"] for row in manifest["files"]]


def _copy_unless_identical(source: Path, destination: Path) -> bool:
    """Copy ``source`` over ``destination`` unless the bytes already match.

    Returns True when a write happened.  An in-place upgrade must not rewrite a
    file whose content is already correct: the running bundled interpreter keeps
    ``python.exe``/``python3XX.dll``/loaded ``.pyd`` files locked on Windows, and
    pinned runtimes ship identical bytes, so skipping identical files is what
    makes self-upgrade work without a separate installer process.
    """
    if destination.is_file():
        try:
            if (
                destination.stat().st_size == source.stat().st_size
                and filecmp.cmp(source, destination, shallow=False)
            ):
                return False
        except OSError:
            pass
    shutil.copyfile(source, destination)
    try:
        shutil.copymode(source, destination)
    except OSError:
        pass
    return True

def apply_staged(stage: str | Path, code: str | Path) -> list[str]:
    """Copy verified staged files into the code directory, manifest last.

    This function never deletes anything and refuses to write the runtime area.
    Tickets #95-#97 can wrap it; rollback/retention is intentionally *not* here.
    """
    stage_path = Path(stage).expanduser().resolve()
    code_path = Path(code).expanduser().resolve()
    if not stage_path.is_dir():
        raise DistError(f"暂存目录不存在：{stage_path}")
    if code_path == stage_path:
        raise DistError("暂存目录不能是代码目录")
    files = sorted(
        (path for path in stage_path.rglob("*") if path.is_file()),
        key=lambda path: (
            path.relative_to(stage_path).as_posix() == MANIFEST_FILENAME,
            path.relative_to(stage_path).as_posix(),
        ),
    )
    if not files:
        raise DistError(f"暂存目录为空：{stage_path}")
    code_path.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []
    for source in files:
        rel = source.relative_to(stage_path)
        rel_posix = rel.as_posix()
        if _reserved_relpath(rel_posix):
            raise DistError(f"拒绝把暂存文件写入运行时区：{rel_posix}")
        target = code_path / rel
        ensure_dir(target.parent)
        _copy_unless_identical(source, target)
        applied.append(rel_posix)
    return applied


def _success(
    code: Path,
    manifest: dict,
    artifact_sha: str,
    artifact: Path,
    source_url: str,
    channel: str,
    extracted: bool,
    installed_at: str,
    previous_version: str = "",
    dsh_compat: dict | None = None,
) -> dict:
    manifest_files = manifest["files"]
    total_bytes = sum(row["bytes"] for row in manifest_files)
    if extracted:
        note = (
            f"已从 {artifact.name} 安装 horizon {manifest['version']}"
            f"（{channel}），安装锁已写入；可用 install verify 复核磁盘内容"
        )
    else:
        note = "同一版本且校验通过，本次未重复解压，安装锁与磁盘均未改动"
    if dsh_compat and dsh_compat.get("status") == "unverified":
        note = f"{note}；宿主 dsh 兼容性未验证：{dsh_compat.get('note', '')}"
    return {
        "ok": True,
        "status": "ok",
        "distribution": DISTRIBUTION,
        "version": manifest["version"],
        "channel": channel,
        "artifact": artifact.name,
        "artifact_sha256": artifact_sha,
        "source_url": source_url,
        "code": str(code),
        "lock": str(lock_path(code)),
        "manifest": str(code / MANIFEST_FILENAME),
        "current": str(current_path(code)),
        "previous_version": previous_version,
        "retained": retained_releases(code),
        "files": len(manifest_files),
        "package_file_count": len(manifest_files),
        "bytes": total_bytes,
        "extracted": extracted,
        "installed_at": installed_at,
        "compat_dsh": dsh_compat or {},
        "note": note,
    }


def install_package(
    code: str | Path,
    *,
    from_source: str,
    digest_path: str | Path | None = None,
    sha256: str | None = None,
    channel: str | None = None,
    runner=subprocess.run,
) -> dict:
    """Install a distribution package and write its install lock.

    Failure is a structured ``status=blocked`` payload; the code directory and
    the lock are not modified before the artifact, manifest and compatibility
    checks all pass.  Before any destructive write a ``dist.pending.json`` marker
    records how to undo it; a later install refuses to mix trees while it exists.
    """
    code_path = Path(code).expanduser().resolve()
    try:
        pending = read_pending(code_path)
        if pending is not None:
            return _pending_blocked(code_path, pending)
        try:
            artifact = _source_path(from_source)
        except DistError as exc:
            _stop("unsupported_source", str(exc), source=str(from_source))
        if not artifact.is_file():
            _stop(
                "source_not_found",
                f"发行包不存在或不是文件：{artifact}",
                source=str(from_source),
            )
        source_url = artifact.as_uri()
        expected_sha, digest, _digest_file = _resolve_expected(artifact, digest_path, sha256)

        actual_sha = sha256_file(artifact)
        if actual_sha != expected_sha:
            _stop(
                "digest_mismatch",
                "制品摘要不符，未解压、未写安装锁；请重新下载发行包",
                expected=expected_sha,
                actual=actual_sha,
                artifact=str(artifact),
            )
        if digest is not None:
            digest_bytes = (digest.get("artifact") or {}).get("bytes")
            if isinstance(digest_bytes, int) and digest_bytes != artifact.stat().st_size:
                _stop(
                    "artifact_size_mismatch",
                    "制品字节数与摘要文件不一致",
                    expected=digest_bytes,
                    actual=artifact.stat().st_size,
                )
        explicit_channel = str(channel or "").strip() or None
        archive, manifest_bytes, manifest, manifest_sha, effective_channel = _read_archive(
            artifact, digest, explicit_channel
        )
        try:
            _check_compat(manifest)
            dsh_compat = _check_dsh_compat(code_path, manifest, runner=runner)

            lock = read_lock(code_path)
            if (
                lock
                and lock.get("version") == manifest["version"]
                and lock.get("artifact_sha256") == actual_sha
            ):
                verification = verify_install(code_path)
                if verification.get("status") == "verified":
                    return _success(
                        code_path,
                        manifest,
                        actual_sha,
                        artifact,
                        source_url,
                        effective_channel,
                        False,
                        str(lock.get("installed_at") or ""),
                        dsh_compat=dsh_compat,
                    )

            conflicts = _foreign_entries(code_path)
            if lock is None and conflicts:
                _stop(
                    "foreign_code_dir",
                    "代码目录非空且没有安装锁，拒绝覆盖：" + "，".join(conflicts[:5]),
                    conflicts=conflicts[:20],
                )

            stage = code_path / STAGING_REL / actual_sha[:16]
            _extract_and_verify(archive, manifest, manifest_bytes, stage)
        finally:
            archive.close()

        previous_manifest: dict | None = None
        previous_version = ""
        retained_tree: Path | None = None
        if lock is not None:
            lock_version = str(lock.get("version") or "")
            if manifest["version"] != lock_version:
                if lock.get("distribution") != DISTRIBUTION:
                    _stop(
                        "unsupported_distribution",
                        "当前安装不是发行包安装，拒绝按发行包升级",
                        distribution=lock.get("distribution"),
                    )
                previous = _manifest_at(code_path)
                if previous is None:
                    _stop(
                        "previous_manifest_unreadable",
                        "代码目录现有 manifest.json 缺失或无效；无法保留可回滚的旧版本",
                        version=lock_version,
                    )
                previous_bytes, previous_manifest = previous
                if previous_manifest["version"] != lock_version:
                    _stop(
                        "previous_manifest_unreadable",
                        "代码目录的 manifest.json 版本与安装锁不一致，无法保留旧版本",
                        expected=lock_version,
                        actual=previous_manifest["version"],
                    )
                if sha256_bytes(previous_bytes) != str(
                    lock.get("manifest_sha256") or ""
                ):
                    _stop(
                        "previous_install_unverified",
                        "现有安装的 manifest.json 摘要与安装锁不一致；拒绝在没有可回滚副本时升级",
                        version=lock_version,
                    )
                missing, mismatch = _tree_disagreements(code_path, previous_manifest)
                if missing or mismatch:
                    _stop(
                        "previous_install_unverified",
                        "现有安装的文件与清单不一致；请先重新安装当前版本修复，再升级",
                        version=lock_version,
                        missing=missing[:20],
                        mismatch=mismatch[:20],
                    )
                retained_tree = _retain_tree(
                    code_path,
                    previous_manifest,
                    previous_bytes,
                    artifact_sha256=str(lock.get("artifact_sha256") or ""),
                    channel=str(lock.get("channel") or ""),
                    source_url=str(lock.get("source_url") or ""),
                    installed_at=str(lock.get("installed_at") or ""),
                )
                previous_version = lock_version
                _prune_releases(code_path, keep=previous_version)
            else:
                # Same-version repair: preserve whatever tree exists (it failed
                # verify, so it may be incomplete) before overwriting it.
                previous_version = lock_version
                backup = (
                    code_path
                    / ".horizon"
                    / "pending"
                    / actual_sha[:16]
                    / "pre-repair"
                )
                retained_tree = _snapshot_for_repair(code_path, manifest, backup)

        pre_current = None
        try:
            pre_current = current_path(code_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            pre_current = None
        pending = _write_pending_marker(
            code_path,
            {
                "operation": "install" if lock is None else "upgrade",
                "target_version": manifest["version"],
                "pre_operation_version": (
                    str(lock.get("version") or "") if lock is not None else None
                ),
                "retained": str(retained_tree) if retained_tree is not None else None,
                "target_files": [row["path"] for row in manifest["files"]],
                "pre_lock": dict(lock) if lock is not None else None,
                "pre_current": pre_current,
            },
        )

        def cleanup_stage() -> None:
            shutil.rmtree(stage, ignore_errors=True)
            try:
                stage.parent.rmdir()
            except OSError:
                pass

        installed_at = now()
        new_lock = {
            "schema_version": SCHEMA_VERSION,
            "distribution": DISTRIBUTION,
            "version": manifest["version"],
            "channel": effective_channel,
            "artifact_sha256": actual_sha,
            "source_url": source_url,
            "installed_at": installed_at,
            "requires": dict(manifest["compat"]),
            "skill_digest": "",
            "package_file_count": len(manifest["files"]),
            "manifest_sha256": manifest_sha,
        }
        if previous_version:
            new_lock["previous_version"] = previous_version
        try:
            apply_staged(stage, code_path)
            if previous_manifest is not None:
                _remove_stale_files(code_path, previous_manifest, manifest)
            write_lock(code_path, new_lock)
            _write_current(code_path, manifest["version"])
        except _InstallBlocked as exc:
            cleanup_stage()
            return _failed_operation(code_path, exc.payload, pending)
        except (OSError, DistError) as exc:
            cleanup_stage()
            return _failed_operation(
                code_path,
                _blocked("apply_failed", f"写入代码目录失败：{exc}"),
                pending,
            )
        cleanup_stage()
        _clear_pending_marker(code_path)
        return _success(
            code_path,
            manifest,
            actual_sha,
            artifact,
            source_url,
            effective_channel,
            True,
            installed_at,
            previous_version,
            dsh_compat=dsh_compat,
        )
    except _InstallBlocked as exc:
        return exc.payload
    except OSError as exc:
        return _blocked(
            "install_failed",
            f"安装过程遇到文件系统错误：{exc}",
            code=str(code_path),
        )


# ------------------------------------------------------------------ rollback
def _default_rollback_target(code: Path, lock: dict) -> str:
    """The retained release a bare ``install rollback`` should switch to."""
    previous = str(lock.get("previous_version") or "")
    if previous and (releases_path(code) / previous).is_dir():
        return previous
    active = str(lock.get("version") or "")
    candidates = [
        record
        for record in _release_records(code)
        if str(record.get("version") or "") != active
    ]
    if not candidates:
        return ""
    return str(candidates[-1].get("version") or "")


def _valid_release_record(record: dict, version: str) -> str:
    """Return an empty string when the record is usable, else a reason."""
    if not _usable_version(version):
        return "invalid_version"
    if str(record.get("version") or "") != version:
        return "release_metadata_invalid"
    if str(record.get("channel") or "") not in CHANNELS:
        return "release_metadata_invalid"
    if not _SHA256_RE.fullmatch(str(record.get("artifact_sha256") or "")):
        return "release_metadata_invalid"
    if not _SHA256_RE.fullmatch(str(record.get("manifest_sha256") or "")):
        return "release_metadata_invalid"
    if not isinstance(record.get("package_file_count"), int) or isinstance(
        record.get("package_file_count"), bool
    ):
        return "release_metadata_invalid"
    if not str(record.get("source_url") or "").strip():
        return "release_metadata_invalid"
    if not str(record.get("installed_at") or "").strip():
        return "release_metadata_invalid"
    return ""


def rollback(code: str | Path, *, to_version: str | None = None) -> dict:
    """Switch the active tree to a retained release without re-extracting it.

    Every failure that happens before the swap returns ``status=blocked`` and
    leaves the active tree, lock, ``current`` pointer and retained releases
    untouched.  The outgoing active tree is retained first, so a second
    rollback can return to the version that was active.
    """
    code_path = Path(code).expanduser().resolve()
    try:
        pending = read_pending(code_path)
        if pending is not None:
            return _pending_blocked(code_path, pending)
        lock = read_lock(code_path)
        if lock is None:
            return _blocked(
                "no_install_lock",
                "代码目录中没有 .horizon/dist.lock.json，无法回滚",
                code=str(code_path),
                lock=str(lock_path(code_path)),
            )
        if str(lock.get("distribution") or "") != DISTRIBUTION:
            return _blocked(
                "unsupported_distribution",
                "当前安装不是发行包安装，无法按发行包回滚",
                distribution=lock.get("distribution"),
                code=str(code_path),
            )
        active_version = str(lock.get("version") or "")

        target_version = str(to_version or "").strip()
        if target_version:
            if not _usable_version(target_version):
                return _blocked(
                    "invalid_version",
                    f"目标版本号不合法：{target_version!r}",
                    code=str(code_path),
                )
        else:
            target_version = _default_rollback_target(code_path, lock)
            if not target_version:
                return _blocked(
                    "no_retained_release",
                    "没有可回滚的保留版本；先安装一个新版本，再回滚",
                    code=str(code_path),
                    version=active_version,
                )
        if target_version == active_version:
            return _blocked(
                "already_active",
                f"目标版本 {target_version} 已是当前版本",
                code=str(code_path),
                version=active_version,
            )

        target_dir = releases_path(code_path) / target_version
        if not target_dir.is_dir():
            return _blocked(
                "release_not_found",
                f"找不到保留版本目录：.horizon/releases/{target_version}",
                code=str(code_path),
                version=target_version,
            )
        try:
            release = read_json(target_dir / RELEASE_FILENAME)
        except (OSError, ValueError):
            release = None
        if not isinstance(release, dict):
            return _blocked(
                "release_metadata_invalid",
                f"保留版本 {target_version} 缺少可读的 release.json",
                code=str(code_path),
                version=target_version,
            )
        record_reason = _valid_release_record(release, target_version)
        if record_reason:
            return _blocked(
                record_reason,
                f"保留版本 {target_version} 的 release.json 字段不完整或不合法",
                code=str(code_path),
                version=target_version,
            )

        target_manifest = _manifest_at(target_dir)
        if target_manifest is None:
            return _blocked(
                "release_manifest_invalid",
                f"保留版本 {target_version} 的 manifest.json 缺失或无效",
                code=str(code_path),
                version=target_version,
            )
        target_bytes, target = target_manifest
        target_manifest_sha = sha256_bytes(target_bytes)
        if target_manifest_sha != release["manifest_sha256"]:
            return _blocked(
                "release_manifest_mismatch",
                f"保留版本 {target_version} 的 manifest.json 摘要与 release.json 不一致（保留副本已损坏）",
                code=str(code_path),
                version=target_version,
                expected=release["manifest_sha256"],
                actual=target_manifest_sha,
            )
        if str(target.get("channel") or "") != release["channel"]:
            return _blocked(
                "release_metadata_invalid",
                f"保留版本 {target_version} 的渠道与 manifest.json 不一致",
                code=str(code_path),
                version=target_version,
            )
        missing, mismatch = _tree_disagreements(target_dir, target)
        if missing or mismatch:
            return _blocked(
                "release_corrupt",
                f"保留版本 {target_version} 的文件与清单不一致，拒绝回滚",
                code=str(code_path),
                version=target_version,
                missing=missing[:20],
                mismatch=mismatch[:20],
            )

        outgoing_version = active_version
        outgoing_info = _manifest_at(code_path)
        if outgoing_info is None:
            return _blocked(
                "rollback_not_recoverable",
                "当前活动树缺少可读的 manifest.json；无法在替换后恢复，拒绝回滚",
                code=str(code_path),
                version=outgoing_version,
            )
        outgoing_bytes, outgoing_manifest = outgoing_info
        if outgoing_manifest.get("version") != outgoing_version:
            return _blocked(
                "rollback_not_recoverable",
                "当前活动树的 manifest.json 版本与安装锁不一致；无法在替换后恢复，拒绝回滚",
                code=str(code_path),
                expected=outgoing_version,
                actual=outgoing_manifest.get("version"),
            )
        outgoing_missing, outgoing_mismatch = _tree_disagreements(
            code_path, outgoing_manifest
        )
        retained_outgoing = False
        retained_tree: Path | None = None
        if (
            not outgoing_missing
            and not outgoing_mismatch
            and _usable_version(outgoing_version)
        ):
            retained_tree = _retain_tree(
                code_path,
                outgoing_manifest,
                outgoing_bytes,
                artifact_sha256=str(lock.get("artifact_sha256") or ""),
                channel=str(lock.get("channel") or ""),
                source_url=str(lock.get("source_url") or ""),
                installed_at=str(lock.get("installed_at") or ""),
            )
            retained_outgoing = True
        else:
            # The active tree already differs from its manifest; a full retained
            # release cannot be built, but a repair snapshot still makes the
            # destructive swap reversible.
            backup = (
                code_path
                / ".horizon"
                / "pending"
                / str(target_version)
                / "pre-rollback"
            )
            retained_tree = _snapshot_for_repair(
                code_path, outgoing_manifest, backup
            )

        pre_current = None
        try:
            pre_current = current_path(code_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            pre_current = None
        pending = _write_pending_marker(
            code_path,
            {
                "operation": "rollback",
                "target_version": target_version,
                "pre_operation_version": outgoing_version,
                "retained": str(retained_tree) if retained_tree is not None else None,
                "target_files": [row["path"] for row in target["files"]],
                "pre_lock": dict(lock),
                "pre_current": pre_current,
            },
        )
        rolled_back_at = now()
        new_lock = dict(lock)
        new_lock.update(
            {
                "schema_version": SCHEMA_VERSION,
                "distribution": DISTRIBUTION,
                "version": target_version,
                "channel": release["channel"],
                "artifact_sha256": release["artifact_sha256"],
                # Keep the source URL of the version that is now active, not the
                # outgoing artifact's URL (#96 review fix, #97).
                "source_url": release["source_url"],
                "manifest_sha256": release["manifest_sha256"],
                "package_file_count": release["package_file_count"],
                "requires": dict(target["compat"]),
                "previous_version": outgoing_version if retained_outgoing else "",
                "rolled_back_at": rolled_back_at,
            }
        )
        try:
            for row in target["files"]:
                rel = row["path"]
                source = target_dir / Path(rel)
                destination = code_path / Path(rel)
                ensure_dir(destination.parent)
                _copy_unless_identical(source, destination)
            (code_path / MANIFEST_FILENAME).write_bytes(target_bytes)
            if outgoing_manifest is not None:
                _remove_stale_files(code_path, outgoing_manifest, target)
            _prune_releases(
                code_path, keep=outgoing_version if retained_outgoing else None
            )
            write_lock(code_path, new_lock)
            _write_current(code_path, target_version)
        except _InstallBlocked as exc:
            return _failed_operation(code_path, exc.payload, pending)
        except (OSError, DistError) as exc:
            return _failed_operation(
                code_path,
                _blocked("rollback_failed", f"回滚过程遇到文件系统错误：{exc}"),
                pending,
            )
        _clear_pending_marker(code_path)
        return {
            "ok": True,
            "status": "ok",
            "distribution": DISTRIBUTION,
            "version": target_version,
            "channel": release["channel"],
            "artifact_sha256": release["artifact_sha256"],
            "source_url": new_lock.get("source_url"),
            "code": str(code_path),
            "lock": str(lock_path(code_path)),
            "manifest": str(code_path / MANIFEST_FILENAME),
            "current": str(current_path(code_path)),
            "files": len(target["files"]),
            "package_file_count": release["package_file_count"],
            "previous_version": new_lock["previous_version"],
            "rolled_back_at": rolled_back_at,
            "retained": retained_releases(code_path),
            "note": (
                f"已回滚到保留版本 horizon {target_version}"
                f"（{release['channel']}）；运行状态与知识库未改动"
            ),
        }
    except _InstallBlocked as exc:
        return exc.payload
    except OSError as exc:
        return _blocked(
            "rollback_failed",
            f"回滚过程遇到文件系统错误：{exc}",
            code=str(code_path),
        )


# -------------------------------------------------------------------- verify
def _mismatch_payload(
    code: Path,
    manifest: dict | None,
    *,
    manifest_path: Path,
    lock: dict,
    missing: list[str] | None = None,
    mismatch: list[dict] | None = None,
    extra: list[str] | None = None,
    lock_mismatch: list[dict] | None = None,
    reason: str = "install_mismatch",
    note: str = "安装锁与磁盘内容不一致；请按 missing/mismatch 明细修复或重新安装",
) -> dict:
    return {
        "ok": False,
        "status": "mismatch",
        "distribution": DISTRIBUTION,
        "reason": reason,
        "version": (manifest or {}).get("version") or lock.get("version"),
        "channel": (manifest or {}).get("channel") or lock.get("channel"),
        "artifact_sha256": lock.get("artifact_sha256"),
        "code": str(code),
        "lock": str(lock_path(code)),
        "manifest": str(manifest_path),
        "missing": sorted(missing or []),
        "mismatch": mismatch or [],
        "extra_manifest_files": sorted(extra or []),
        "lock_mismatch": lock_mismatch or [],
        "note": note,
    }


def _lock_comparisons(manifest: dict, manifest_sha: str, lock: dict) -> list[dict]:
    comparisons = [
        ("distribution", DISTRIBUTION, lock.get("distribution")),
        ("version", manifest["version"], lock.get("version")),
        ("channel", manifest["channel"], lock.get("channel")),
        ("manifest_sha256", manifest_sha, lock.get("manifest_sha256")),
        (
            "package_file_count",
            len(manifest["files"]),
            lock.get("package_file_count"),
        ),
        ("requires", manifest["compat"], lock.get("requires")),
    ]
    out: list[dict] = []
    for field, expected, actual in comparisons:
        if actual != expected:
            out.append({"field": field, "expected": expected, "actual": actual})
    artifact_sha = str(lock.get("artifact_sha256") or "")
    if not _SHA256_RE.fullmatch(artifact_sha):
        out.append(
            {
                "field": "artifact_sha256",
                "expected": "64 lowercase hex characters",
                "actual": artifact_sha,
            }
        )
    return out


def verify_install(code: str | Path) -> dict:
    """Check the lock, manifest and every file under the code directory."""
    code_path = Path(code).expanduser().resolve()
    lock = read_lock(code_path)
    if lock is None:
        return _blocked(
            "no_install_lock",
            "代码目录中没有 .horizon/dist.lock.json，无法按发行包安装校验",
            code=str(code_path),
            lock=str(lock_path(code_path)),
        )
    if str(lock.get("distribution") or "") == SOURCE_DISTRIBUTION:
        # A source installation has no manifest and no artifact to check.  The
        # lock itself is the evidence; reporting a mismatch would be wrong.
        return {
            "ok": True,
            "status": "source_install",
            "distribution": SOURCE_DISTRIBUTION,
            "version": None,
            "channel": "source",
            "commit": lock.get("commit"),
            "dirty": bool(lock.get("dirty")),
            "source_url": lock.get("source_url"),
            "installed_at": lock.get("installed_at"),
            "skill_digest": lock.get("skill_digest"),
            "code": str(code_path),
            "lock": str(lock_path(code_path)),
            "manifest": None,
            "missing": [],
            "mismatch": [],
            "extra_manifest_files": [],
            "lock_mismatch": [],
            "note": (
                "源码安装没有发行包清单可校验；安装锁记录的是源码来源，"
                "不是发行包制品"
            ),
        }
    manifest_path = code_path / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return _mismatch_payload(
            code_path,
            None,
            manifest_path=manifest_path,
            lock=lock,
            missing=[MANIFEST_FILENAME],
            reason="manifest_missing",
            note="安装锁存在，但代码目录缺少 manifest.json；无法证明安装内容",
        )
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return _mismatch_payload(
            code_path,
            None,
            manifest_path=manifest_path,
            lock=lock,
            mismatch=[{"path": MANIFEST_FILENAME, "field": "json", "error": str(exc)}],
            reason="invalid_manifest",
            note="manifest.json 无法解析，无法校验安装",
        )
    errors = _validate_manifest(manifest)
    if errors:
        return _mismatch_payload(
            code_path,
            manifest if isinstance(manifest, dict) else None,
            manifest_path=manifest_path,
            lock=lock,
            mismatch=[{"path": MANIFEST_FILENAME, "field": "schema", "errors": errors}],
            reason="invalid_manifest",
        )

    missing: list[str] = []
    mismatch: list[dict] = []
    listed: set[str] = set()
    for row in manifest["files"]:
        rel = row["path"]
        listed.add(rel)
        if _reserved_relpath(rel):
            mismatch.append(
                {
                    "path": rel,
                    "field": "path",
                    "expected": "outside .horizon",
                    "actual": rel,
                }
            )
            continue
        target = code_path / Path(rel)
        if not target.is_file():
            missing.append(rel)
            continue
        try:
            data = target.read_bytes()
        except OSError as exc:
            mismatch.append(
                {
                    "path": rel,
                    "field": "read",
                    "expected": "readable file",
                    "actual": str(exc),
                }
            )
            continue
        actual_sha = sha256_bytes(data)
        if len(data) != row["bytes"] or actual_sha != row["sha256"]:
            fields = []
            if len(data) != row["bytes"]:
                fields.append("bytes")
            if actual_sha != row["sha256"]:
                fields.append("sha256")
            mismatch.append(
                {
                    "path": rel,
                    "field": ",".join(fields),
                    "expected": {"bytes": row["bytes"], "sha256": row["sha256"]},
                    "actual": {"bytes": len(data), "sha256": actual_sha},
                }
            )

    extra: list[str] = []
    for dirpath, dirnames, filenames in os.walk(code_path):
        # The runtime area contains the lock, staging and the private/bundled
        # environment; it is never package content and may be very large.
        # ``__pycache__`` is written by the installed interpreter on every run,
        # so it is runtime state too: verifying a live installation must not
        # report its own bytecode caches as tampering.
        dirnames[:] = sorted(
            name for name in dirnames if name not in (".horizon", "__pycache__")
        )
        current = Path(dirpath)
        for name in sorted(filenames):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = current / name
            rel = path.relative_to(code_path).as_posix()
            if rel == MANIFEST_FILENAME or _reserved_relpath(rel):
                continue
            if rel in VERIFY_IGNORED_PATHS:
                continue
            if rel == 'Horizon.lnk' and not path.is_symlink() and path.resolve().parent == code_path:
                continue
            if rel not in listed:
                extra.append(rel)

    lock_mismatch = _lock_comparisons(manifest, sha256_bytes(manifest_bytes), lock)
    if missing or mismatch or extra or lock_mismatch:
        return _mismatch_payload(
            code_path,
            manifest,
            manifest_path=manifest_path,
            lock=lock,
            missing=missing,
            mismatch=mismatch,
            extra=extra,
            lock_mismatch=lock_mismatch,
        )
    return {
        "ok": True,
        "status": "verified",
        "distribution": DISTRIBUTION,
        "version": manifest["version"],
        "channel": manifest["channel"],
        "artifact_sha256": lock.get("artifact_sha256"),
        "code": str(code_path),
        "lock": str(lock_path(code_path)),
        "manifest": str(manifest_path),
        "files": len(manifest["files"]),
        "bytes": sum(row["bytes"] for row in manifest["files"]),
        "missing": [],
        "mismatch": [],
        "extra_manifest_files": [],
        "lock_mismatch": [],
        "note": f"安装锁与磁盘内容一致：horizon {manifest['version']}（{manifest['channel']}）",
    }
