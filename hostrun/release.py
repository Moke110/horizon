"""Release identity, the release index and artifact checks (spec #92, ticket #98).

The release pipeline keeps its decisions out of YAML and shell so they can be
tested offline:

- a tag names the package version and the release channel — ``vX.Y.Z`` is
  stable, ``vX.Y.Z-<prerelease>`` is prerelease;
- the release index ``releases/index.json`` is a deterministic, newest-first
  record of published artifacts, merged by version and validated strictly;
- a built artifact must contain the bundled runtime and the launcher wrapper,
  and rebuilding the same inputs must reproduce the same artifact digest;
- the bundled runtime directory has one expected CPython embeddable layout.

Everything here is stdlib-only.  One function reaches the network, and only for
an explicit ``https://`` release-artifact URL: ``resolve_release_source`` (the
rest of the module stays network-free).  The CI workflow calls the
``python -m hostrun.release`` CLI for the same functions the tests exercise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1
STABLE = "stable"
PRERELEASE = "prerelease"
CHANNELS = (STABLE, PRERELEASE)
TAG_PREFIX = "v"
ARTIFACT_TEMPLATE = "horizon-{version}-{channel}.zip"
DIGEST_TEMPLATE = "horizon-{version}-{channel}.digest.json"
MANIFEST_NAME = "manifest.json"
RUNTIME_DIRNAME = "runtime"
RUNTIME_PYTHON_NAME = "python.exe"
WRAPPER_REL = "bin/horizon.cmd"
RELEASE_INDEX_PATH = "releases/index.json"

#: Fields of one release-index entry, in canonical JSON order.  ``compat`` keeps
#: the same four fields as the distribution manifest.
ENTRY_FIELDS = (
    "version",
    "channel",
    "artifact_sha256",
    "artifact_name",
    "manifest_sha256",
    "compat",
    "published_at",
    "release_url",
)
COMPAT_FIELDS = ("os", "arch", "dsh", "python")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
#: Semver core.  Numeric components and numeric prerelease identifiers reject
#: leading zeroes, so ``v01.2.3`` and ``v1.2.3-rc.01`` are malformed.
_CORE = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
_PRERELEASE_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_PRERELEASE = rf"{_PRERELEASE_IDENTIFIER}(?:\.{_PRERELEASE_IDENTIFIER})*"
_VERSION_RE = re.compile(rf"^{_CORE}(?:-({_PRERELEASE}))?$")
_PYTHON_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")


class ReleaseError(Exception):
    """A release request that cannot produce a trustworthy result."""


# ------------------------------------------------------------------ versions
def parse_version(version: str) -> dict:
    """Split a distribution version into its semver parts and channel.

    The version never carries the tag's ``v`` prefix: ``v1.2.3-rc.1`` names the
    package version ``1.2.3-rc.1``.
    """
    text = "" if version is None else str(version)
    match = _VERSION_RE.match(text)
    if not match:
        raise ReleaseError(
            f"version {text!r} is not MAJOR.MINOR.PATCH or "
            "MAJOR.MINOR.PATCH-<prerelease> (e.g. 1.2.3 or 1.2.3-rc.1)"
        )
    major, minor, patch, suffix = match.groups()
    return {
        "version": text,
        "major": int(major),
        "minor": int(minor),
        "patch": int(patch),
        "prerelease": suffix or "",
        "channel": PRERELEASE if suffix else STABLE,
    }


def parse_tag(tag: str, channel: str | None = None) -> dict:
    """Resolve a Git tag to ``{tag, version, channel, prerelease}``.

    ``channel`` is optional; when supplied it must agree with the tag, so a
    dry-run input cannot silently publish a prerelease tag on the stable
    channel.
    """
    text = "" if tag is None else str(tag)
    if not text.startswith(TAG_PREFIX) or len(text) <= len(TAG_PREFIX):
        raise ReleaseError(
            f"tag {text!r} is not a release tag; expected "
            "vMAJOR.MINOR.PATCH or vMAJOR.MINOR.PATCH-<prerelease>"
        )
    try:
        info = parse_version(text[len(TAG_PREFIX):])
    except ReleaseError as exc:
        raise ReleaseError(f"tag {text!r} is not a release tag: {exc}") from exc

    resolved = info["channel"]
    if channel is not None:
        requested = str(channel).strip()
        if requested not in CHANNELS:
            raise ReleaseError(
                f"channel must be one of {', '.join(CHANNELS)}; got {channel!r}"
            )
        if requested != resolved:
            raise ReleaseError(
                f"tag {text!r} implies channel {resolved!r}, "
                f"but channel {requested!r} was requested"
            )
    return {
        "tag": text,
        "version": info["version"],
        "channel": resolved,
        "prerelease": resolved == PRERELEASE,
    }


def version_sort_key(version: str) -> tuple:
    """Newest-first sort key implementing semver precedence.

    A stable version sorts above its own prereleases; numeric prerelease
    identifiers sort below alphanumeric ones; a shorter identifier list sorts
    first when the shared prefix matches.
    """
    info = parse_version(version)
    if not info["prerelease"]:
        return (info["major"], info["minor"], info["patch"], 1, ())
    identifiers = []
    for identifier in info["prerelease"].split("."):
        if identifier.isdigit():
            identifiers.append((0, int(identifier)))
        else:
            identifiers.append((1, identifier))
    return (
        info["major"],
        info["minor"],
        info["patch"],
        0,
        tuple(identifiers),
    )


def expected_artifact_name(version: str, channel: str) -> str:
    """The only artifact name the pipeline accepts for a version and channel."""
    info = parse_version(version)
    if channel not in CHANNELS:
        raise ReleaseError(
            f"channel must be one of {', '.join(CHANNELS)}; got {channel!r}"
        )
    if info["channel"] != channel:
        raise ReleaseError(
            f"version {version!r} implies channel {info['channel']!r}, "
            f"but channel {channel!r} was given"
        )
    return ARTIFACT_TEMPLATE.format(version=version, channel=channel)


def _expected_digest_name(version: str, channel: str) -> str:
    expected_artifact_name(version, channel)
    return DIGEST_TEMPLATE.format(version=version, channel=channel)


# -------------------------------------------------------------- release index
def empty_index() -> dict:
    """A valid empty release index; ``schema_version`` is part of the contract."""
    return {"schema_version": SCHEMA_VERSION, "releases": []}


def _validate_compat(compat) -> None:
    if not isinstance(compat, dict):
        raise ReleaseError("release entry compat must be an object")
    missing = [field for field in COMPAT_FIELDS if field not in compat]
    unexpected = sorted(set(compat) - set(COMPAT_FIELDS))
    if missing:
        raise ReleaseError(
            f"release entry compat is missing fields: {', '.join(missing)}"
        )
    if unexpected:
        raise ReleaseError(
            f"release entry compat has unknown fields: {', '.join(unexpected)}"
        )
    for field in COMPAT_FIELDS:
        value = compat[field]
        if not isinstance(value, str) or not value.strip():
            raise ReleaseError(
                f"release entry compat.{field} must be a non-empty string"
            )


def _validate_published_at(value) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseError("release entry published_at must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseError(
            f"release entry published_at {value!r} is not ISO-8601"
        ) from exc
    if parsed.tzinfo is None:
        raise ReleaseError(
            "release entry published_at must include a timezone offset"
        )


def validate_entry(entry) -> None:
    """Reject anything that is not one complete, self-consistent release entry."""
    if not isinstance(entry, dict):
        raise ReleaseError("release entry must be an object")
    missing = [field for field in ENTRY_FIELDS if field not in entry]
    if missing:
        raise ReleaseError(
            f"release entry is missing fields: {', '.join(missing)}"
        )
    unexpected = sorted(set(entry) - set(ENTRY_FIELDS))
    if unexpected:
        raise ReleaseError(
            f"release entry has unknown fields: {', '.join(unexpected)}"
        )

    info = parse_version(entry["version"])
    channel = entry["channel"]
    if channel not in CHANNELS:
        raise ReleaseError(
            f"release entry channel must be one of {', '.join(CHANNELS)}; "
            f"got {channel!r}"
        )
    if info["channel"] != channel:
        raise ReleaseError(
            f"release entry channel {channel!r} disagrees with version "
            f"{entry['version']!r} ({info['channel']})"
        )

    expected_name = expected_artifact_name(entry["version"], channel)
    # Published pre-rename assets keep their original names and digests.
    legacy_name = (
        f"frontier-{entry['version']}-{channel}.zip"
        if entry["version"] in {f"0.1.0-rc.{n}" for n in range(1, 7)}
        else None
    )
    if entry["artifact_name"] != expected_name and (legacy_name is None or entry["artifact_name"] != legacy_name):
        raise ReleaseError(
            f"release entry artifact_name must be {expected_name!r}; "
            f"got {entry['artifact_name']!r}"
        )
    for field in ("artifact_sha256", "manifest_sha256"):
        value = entry[field]
        if not isinstance(value, str) or not _SHA256_RE.match(value):
            raise ReleaseError(
                f"release entry {field} must be 64 lowercase hex characters"
            )
    _validate_compat(entry["compat"])
    _validate_published_at(entry["published_at"])
    url = entry["release_url"]
    if (
        not isinstance(url, str)
        or not url.strip()
        or not url.startswith(("https://", "http://"))
        or any(character.isspace() for character in url)
    ):
        raise ReleaseError(
            "release entry release_url must be an http(s) URL without whitespace"
        )


def canonical_entry(entry) -> dict:
    """Validate an entry and return it with canonical key order."""
    validate_entry(entry)
    return {
        "version": entry["version"],
        "channel": entry["channel"],
        "artifact_sha256": entry["artifact_sha256"],
        "artifact_name": entry["artifact_name"],
        "manifest_sha256": entry["manifest_sha256"],
        "compat": {field: entry["compat"][field] for field in COMPAT_FIELDS},
        "published_at": entry["published_at"],
        "release_url": entry["release_url"],
    }


def validate_index(index) -> None:
    """Reject an index that is not schema version 1 with unique valid entries."""
    if not isinstance(index, dict):
        raise ReleaseError("release index must be an object")
    if index.get("schema_version") != SCHEMA_VERSION:
        raise ReleaseError(
            f"release index schema_version must be {SCHEMA_VERSION}; "
            f"got {index.get('schema_version')!r}"
        )
    unexpected = sorted(set(index) - {"schema_version", "releases"})
    if unexpected:
        raise ReleaseError(
            f"release index has unknown fields: {', '.join(unexpected)}"
        )
    releases = index.get("releases")
    if not isinstance(releases, list):
        raise ReleaseError("release index releases must be an array")
    seen: set[str] = set()
    for row in releases:
        validate_entry(row)
        version = row["version"]
        if version in seen:
            raise ReleaseError(
                f"release index contains version {version!r} more than once"
            )
        seen.add(version)


def merge_release_index(index, entry) -> dict:
    """Return ``index`` with ``entry`` merged by version, newest first.

    The same version replaces its earlier entry (a republished artifact keeps
    one row); every other entry is preserved, and the result is sorted by
    semver precedence descending so the index is deterministic.
    """
    validate_index(index)
    canonical = canonical_entry(entry)
    releases = [
        row for row in index["releases"] if row["version"] != canonical["version"]
    ]
    releases.append(canonical)
    releases.sort(key=lambda row: version_sort_key(row["version"]), reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "releases": [canonical_entry(row) for row in releases],
    }


def dump_index(index) -> str:
    """Render an index as canonical, byte-deterministic JSON plus one newline."""
    validate_index(index)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "releases": [canonical_entry(row) for row in index["releases"]],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def load_index(path: str | Path) -> dict:
    """Read and validate an index; a missing file means no releases yet."""
    index_path = Path(path)
    if not index_path.exists():
        return empty_index()
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"could not read release index {index_path}: {exc}") from exc
    validate_index(payload)
    return payload


def write_index(path: str | Path, index) -> Path:
    """Write the canonical index, creating its parent directory if needed."""
    index_path = Path(path)
    data = dump_index(index)
    try:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_bytes(data.encode("utf-8"))
    except OSError as exc:
        raise ReleaseError(f"could not write release index {index_path}: {exc}") from exc
    return index_path


def utc_now() -> str:
    """The index's only build-time timestamp, in UTC with an offset."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def entry_from_digest(
    digest,
    *,
    release_url: str,
    published_at: str | None = None,
) -> dict:
    """Build one index entry from a build's digest file (the single source)."""
    if not isinstance(digest, dict):
        raise ReleaseError("digest must be a JSON object")
    for field in ("version", "channel", "manifest_sha256", "compat"):
        if field not in digest:
            raise ReleaseError(f"digest is missing field {field!r}")
    artifact = digest.get("artifact")
    if not isinstance(artifact, dict):
        raise ReleaseError("digest is missing the artifact block")
    for field in ("name", "sha256"):
        if not artifact.get(field):
            raise ReleaseError(f"digest is missing artifact.{field}")
    entry = {
        "version": digest["version"],
        "channel": digest["channel"],
        "artifact_sha256": artifact["sha256"],
        "artifact_name": artifact["name"],
        "manifest_sha256": digest["manifest_sha256"],
        "compat": digest["compat"],
        "published_at": published_at or utc_now(),
        "release_url": release_url,
    }
    return canonical_entry(entry)


# ------------------------------------------------------------------ artifacts
#: Default read timeout for one ``https://`` release-artifact download.
RELEASE_URL_TIMEOUT = 30.0
#: A Windows drive path, which ``urlsplit`` would otherwise read as a scheme.
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _release_url_label(parsed: urllib.parse.SplitResult) -> str:
    """A URL label safe for logs: no query string or fragment (no tokens)."""
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _file_uri_path(parsed: urllib.parse.SplitResult) -> Path:
    path_text = urllib.request.url2pathname(parsed.path)
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        path_text = f"//{parsed.netloc}{path_text}"
    return Path(path_text)


def resolve_release_source(
    source: str | Path,
    *,
    download_dir: str | Path | None = None,
    timeout: float = RELEASE_URL_TIMEOUT,
    urlopen=None,
) -> Path:
    """Resolve a distribution-artifact source to one local file path.

    Accepted sources (spec #92: ``--from`` takes a local file or a URL):

    - a local path (relative or absolute, including a Windows drive/UNC path);
    - a ``file://`` URL;
    - an ``https://`` URL, downloaded into ``download_dir`` (a fresh temporary
      directory when omitted) with a read timeout.

    Plain ``http://`` and every other scheme are refused: a release artifact
    comes over TLS or from disk, never in clear text.  Every failure raises
    :class:`ReleaseError` with a reason that names the offending source, so a
    caller can report it as a blocked install instead of guessing.

    ``urlopen`` is injectable so tests (and offline callers) can supply a fake
    ``urllib.request.urlopen``; the default is the real one, called as
    ``urlopen(url, timeout=timeout)``.

    This is the one function in this module that may touch the network; the
    sibling ``hostrun/dist.py`` ``_source_path`` should delegate its URL branch
    here instead of refusing ``https://``.
    """
    raw = str(source or "").strip()
    if not raw:
        raise ReleaseError(
            "release source is empty; pass a local path, file:// URL or "
            "https:// URL"
        )

    if _DRIVE_RE.match(raw) or raw.startswith("\\\\"):
        return Path(raw).expanduser().resolve()

    parsed = urllib.parse.urlsplit(raw)
    scheme = parsed.scheme.lower()
    if not scheme:
        return Path(raw).expanduser().resolve()
    if scheme == "file":
        return _file_uri_path(parsed).expanduser().resolve()
    if scheme != "https":
        raise ReleaseError(
            f"unsupported release source scheme {parsed.scheme!r}; use a local "
            "path, file:// URL or https:// URL"
        )

    if any(character.isspace() for character in raw):
        raise ReleaseError(
            "release URL contains whitespace; percent-encode it or pass a "
            "local path"
        )
    if not parsed.hostname:
        raise ReleaseError("release URL must include a host")
    filename = PurePosixPath(urllib.parse.unquote(parsed.path)).name
    if not filename or filename in {".", ".."} or parsed.path.endswith("/"):
        raise ReleaseError(
            "release URL must name the artifact filename, e.g. "
            "https://host/horizon-0.1.0-stable.zip"
        )
    try:
        seconds = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ReleaseError(
            f"download timeout must be a number of seconds: {timeout!r}"
        ) from exc
    if seconds <= 0:
        raise ReleaseError("download timeout must be greater than zero")

    if download_dir is None:
        destination_dir = Path(tempfile.mkdtemp(prefix="horizon-release-"))
    else:
        destination_dir = Path(download_dir).expanduser().resolve()
    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReleaseError(
            f"could not create the download directory {destination_dir}: {exc}"
        ) from exc
    target = destination_dir / filename
    label = _release_url_label(parsed)
    opener = urlopen or urllib.request.urlopen

    try:
        response = opener(raw, timeout=seconds)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ReleaseError(
            f"could not download release artifact {label}: {exc}"
        ) from exc

    written = 0
    try:
        with target.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        target.unlink(missing_ok=True)
        raise ReleaseError(
            f"could not download release artifact {label}: {exc}"
        ) from exc
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()

    if written == 0:
        target.unlink(missing_ok=True)
        raise ReleaseError(f"downloaded release artifact is empty: {label}")
    return target.resolve()


def artifact_sha256(path: str | Path) -> str:
    """SHA-256 of a file, read in bounded chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(
    zip_path: str | Path, *, expected_sha256: str | None = None
) -> dict:
    """Check an artifact's digest and that it carries the runtime and wrapper."""
    path = Path(zip_path)
    if not path.is_file():
        raise ReleaseError(f"artifact is missing: {path}")
    actual = artifact_sha256(path)
    if expected_sha256 is not None:
        expected = str(expected_sha256).strip().lower()
        if actual != expected:
            raise ReleaseError(
                f"artifact sha256 mismatch for {path.name}: "
                f"expected {expected}, got {actual}"
            )
    if not zipfile.is_zipfile(path):
        raise ReleaseError(f"artifact is not a zip archive: {path}")
    with zipfile.ZipFile(path) as archive:
        names = {
            name.replace("\\", "/")
            for name in archive.namelist()
            if not name.endswith("/")
        }
    required = (MANIFEST_NAME, f"{RUNTIME_DIRNAME}/{RUNTIME_PYTHON_NAME}", WRAPPER_REL)
    missing = [name for name in required if name not in names]
    if missing:
        raise ReleaseError(
            f"artifact {path.name} is missing required entries: "
            + ", ".join(missing)
        )
    return {
        "ok": True,
        "artifact": str(path.resolve()),
        "name": path.name,
        "sha256": actual,
        "bytes": path.stat().st_size,
        "entries": len(names),
        "required": list(required),
    }


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"could not read {path}: {exc}") from exc


def reproducibility_report(
    first_dir: str | Path,
    second_dir: str | Path,
    version: str,
    channel: str,
) -> dict:
    """Compare two builds' artifact and manifest digests for the same inputs."""
    artifact_name = expected_artifact_name(version, channel)
    digest_name = _expected_digest_name(version, channel)
    first_artifact = Path(first_dir) / artifact_name
    second_artifact = Path(second_dir) / artifact_name
    for path in (first_artifact, second_artifact):
        if not path.is_file():
            raise ReleaseError(f"reproducibility build artifact is missing: {path}")
    first_sha = artifact_sha256(first_artifact)
    second_sha = artifact_sha256(second_artifact)
    first_manifest = _read_json(Path(first_dir) / digest_name).get("manifest_sha256")
    second_manifest = _read_json(Path(second_dir) / digest_name).get("manifest_sha256")
    return {
        "ok": first_sha == second_sha and first_manifest == second_manifest,
        "artifact_name": artifact_name,
        "first_sha256": first_sha,
        "second_sha256": second_sha,
        "first_manifest_sha256": first_manifest,
        "second_manifest_sha256": second_manifest,
    }


# --------------------------------------------------------------- runtime tree
def _python_tag(python_version: str) -> str:
    match = _PYTHON_VERSION_RE.match(str(python_version or "").strip())
    if not match:
        raise ReleaseError(
            f"python version must be MAJOR.MINOR (e.g. 3.13); got {python_version!r}"
        )
    return f"{match.group(1)}{match.group(2)}"


def _module_present(site_packages: Path, runtime: Path, name: str) -> bool:
    if not name or "/" in name or "\\" in name:
        return False
    candidates = [
        site_packages / name,
        site_packages / f"{name}.py",
        site_packages / f"{name}.pyd",
        runtime / f"{name}.pyd",
    ]
    if (site_packages / name).is_dir():
        return True
    return any(candidate.is_file() for candidate in candidates)


def validate_runtime_dir(
    runtime_dir: str | Path,
    *,
    python_version: str = "3.13",
    required_modules=(),
) -> dict:
    """Check the prepared CPython embeddable layout the builder copies verbatim."""
    root = Path(runtime_dir)
    if not root.is_dir():
        raise ReleaseError(f"runtime directory is not a directory: {root}")
    tag = _python_tag(python_version)
    missing: list[str] = []
    for relative in (
        RUNTIME_PYTHON_NAME,
        f"python{tag}.dll",
        f"python{tag}._pth",
    ):
        if not (root / relative).is_file():
            missing.append(relative)
    if not (root / f"python{tag}.zip").is_file() and not (root / "Lib").is_dir():
        missing.append(f"python{tag}.zip or Lib/")
    # The official embeddable zip ships its extension modules flat next to
    # python.exe (select.pyd, _ctypes.pyd, ...), while a full distribution
    # layout keeps them under DLLs/.  Accept either; the embeddable zip itself
    # is hash-pinned, so this check only catches an obviously wrong input.
    if not (root / "DLLs").is_dir() and not any(root.glob("*.pyd")):
        missing.append("extension modules (DLLs/ or *.pyd next to python.exe)")
    site_packages = root / "Lib" / "site-packages"
    if not site_packages.is_dir() or not any(site_packages.iterdir()):
        missing.append("Lib/site-packages/ (non-empty)")
    if missing:
        raise ReleaseError(
            f"runtime directory {root} is missing: {', '.join(missing)}"
        )

    modules = [str(name).strip() for name in required_modules if str(name).strip()]
    absent = [
        name for name in modules if not _module_present(site_packages, root, name)
    ]
    if absent:
        raise ReleaseError(
            f"runtime directory {root} is missing required modules: "
            f"{', '.join(absent)} (looked in Lib/site-packages)"
        )
    files = [path for path in root.rglob("*") if path.is_file()]
    return {
        "ok": True,
        "runtime": str(root.resolve()),
        "python": python_version,
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
        "modules": modules,
    }


# ------------------------------------------------------------ release writing
def release_title(meta: dict) -> str:
    """Human title for the GitHub Release."""
    return f"horizon {meta['version']} ({meta['channel']})"


def release_notes(
    meta: dict,
    *,
    artifact_name: str,
    artifact_sha256: str,
    manifest_sha256: str,
    compat: dict,
    built_from_commit: str,
    repository: str = "Moke110/horizon",
) -> str:
    """Release notes: identity, asset digests, compatibility and install pointer."""
    lines = [
        f"## horizon {meta['version']} ({meta['channel']})",
        "",
        f"Built from commit `{built_from_commit}` on the "
        f"**{meta['channel']}** channel.",
        "",
        "### Assets",
        "",
        f"- `{artifact_name}` — sha256 `{artifact_sha256}`",
        f"- manifest sha256 `{manifest_sha256}` (see the sibling digest JSON)",
        "",
        "### Compatibility",
        "",
        "| os | arch | dsh | python |",
        "| --- | --- | --- | --- |",
        f"| {compat['os']} | {compat['arch']} | {compat['dsh']} | {compat['python']} |",
        "",
        "### Install",
        "",
        "1. Download the `.zip` and its `.digest.json` from this release.",
        "2. Verify the zip's SHA-256 against the digest before extracting.",
        "3. Install it as a distribution package with the host agent; users do "
        "not need git, uv or a system Python.",
        "",
        f"The machine-readable release index is `releases/index.json` in "
        f"[{repository}](https://github.com/{repository}).",
        "",
    ]
    return "\n".join(lines)


def build_argv(
    *,
    version: str,
    channel: str,
    commit: str,
    runtime_dir: str | Path,
    source: str | Path = ".",
    out: str | Path = "dist",
    compat: dict | None = None,
    python: str = "python",
) -> list[str]:
    """The exact ``python -m hostrun.distribution`` argv a release build uses."""
    info = parse_version(version)
    if channel not in CHANNELS:
        raise ReleaseError(
            f"channel must be one of {', '.join(CHANNELS)}; got {channel!r}"
        )
    if info["channel"] != channel:
        raise ReleaseError(
            f"version {version!r} implies channel {info['channel']!r}, "
            f"but channel {channel!r} was given"
        )
    evidence = str(commit or "").strip()
    if not evidence:
        raise ReleaseError("built-from-commit must be a non-empty string")
    runtime = str(runtime_dir or "").strip()
    if not runtime:
        raise ReleaseError("runtime-dir must be a non-empty path")
    merged = {
        "os": "windows",
        "arch": "x86_64",
        "dsh": "*",
        "python": "3.13",
    }
    for key, value in (compat or {}).items():
        if key not in merged:
            raise ReleaseError(
                f"unknown compat field {key!r}; expected {', '.join(merged)}"
            )
        text = str(value or "").strip()
        if not text:
            raise ReleaseError(f"compat.{key} must be a non-empty string")
        merged[key] = text
    return [
        str(python),
        "-m",
        "hostrun.distribution",
        "--source",
        str(source),
        "--out",
        str(out),
        "--version",
        version,
        "--channel",
        channel,
        "--built-from-commit",
        evidence,
        "--runtime-dir",
        runtime,
        "--os",
        merged["os"],
        "--arch",
        merged["arch"],
        "--python",
        merged["python"],
        "--dsh",
        merged["dsh"],
    ]


# ----------------------------------------------------------------------- CLI
def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _error(message: str) -> None:
    _emit({"ok": False, "error": message})
    print(message, file=sys.stderr)


def _append_github_output(path: str, values: dict) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m hostrun.release",
        description="Release identity, index and artifact checks (offline).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    tag = subparsers.add_parser("tag", help="resolve a release tag")
    tag.add_argument("--tag", required=True)
    tag.add_argument("--channel", default=None)
    tag.add_argument(
        "--github-output",
        default=None,
        help="append version/channel/prerelease/tag to a GitHub Actions output file",
    )

    name = subparsers.add_parser("artifact-name", help="derive the artifact name")
    name.add_argument("--version", required=True)
    name.add_argument("--channel", required=True)

    verify = subparsers.add_parser("verify-artifact", help="verify a built zip")
    verify.add_argument("--zip", required=True)
    verify.add_argument("--sha256", default=None)

    reproducible = subparsers.add_parser(
        "check-reproducible", help="compare two builds of the same inputs"
    )
    reproducible.add_argument("--first-dir", required=True)
    reproducible.add_argument("--second-dir", required=True)
    reproducible.add_argument("--version", required=True)
    reproducible.add_argument("--channel", required=True)

    notes = subparsers.add_parser("notes", help="render release notes")
    notes.add_argument("--tag", required=True)
    notes.add_argument("--digest", required=True)
    notes.add_argument("--repository", default="Moke110/horizon")
    notes.add_argument("--out", default=None)

    argv = subparsers.add_parser("build-argv", help="print the build argv as JSON")
    argv.add_argument("--version", required=True)
    argv.add_argument("--channel", required=True)
    argv.add_argument("--commit", required=True)
    argv.add_argument("--runtime-dir", required=True)
    argv.add_argument("--source", default=".")
    argv.add_argument("--out", default="dist")
    argv.add_argument("--python", default="python")
    return parser


def _dispatch(args) -> dict | str:
    if args.command == "tag":
        meta = parse_tag(args.tag, args.channel)
        if args.github_output:
            _append_github_output(
                args.github_output,
                {
                    "version": meta["version"],
                    "channel": meta["channel"],
                    "prerelease": "true" if meta["prerelease"] else "false",
                    "tag": meta["tag"],
                },
            )
        return {"ok": True, **meta}

    if args.command == "artifact-name":
        return {
            "ok": True,
            "artifact_name": expected_artifact_name(args.version, args.channel),
            "version": args.version,
            "channel": args.channel,
        }

    if args.command == "verify-artifact":
        return verify_artifact(args.zip, expected_sha256=args.sha256)

    if args.command == "check-reproducible":
        report = reproducibility_report(
            args.first_dir, args.second_dir, args.version, args.channel
        )
        if not report["ok"]:
            raise ReleaseError(
                "reproducibility check failed: "
                f"first sha256 {report['first_sha256']}, "
                f"second sha256 {report['second_sha256']}"
            )
        return report

    if args.command == "notes":
        meta = parse_tag(args.tag)
        digest = _read_json(Path(args.digest))
        if (
            digest.get("version") != meta["version"]
            or digest.get("channel") != meta["channel"]
        ):
            raise ReleaseError(
                f"digest {args.digest} is for {digest.get('version')!r}/"
                f"{digest.get('channel')!r}, not {meta['version']!r}/"
                f"{meta['channel']!r}"
            )
        artifact = digest.get("artifact") or {}
        text = release_notes(
            meta,
            artifact_name=artifact.get("name", ""),
            artifact_sha256=artifact.get("sha256", ""),
            manifest_sha256=digest.get("manifest_sha256", ""),
            compat=digest.get("compat") or {},
            built_from_commit=digest.get("built_from_commit", "unknown"),
            repository=args.repository,
        )
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8", newline="\n")
            return {"ok": True, "notes": str(Path(args.out).resolve())}
        return text

    if args.command == "build-argv":
        return {
            "ok": True,
            "argv": build_argv(
                version=args.version,
                channel=args.channel,
                commit=args.commit,
                runtime_dir=args.runtime_dir,
                source=args.source,
                out=args.out,
                python=args.python,
            ),
        }
    raise ReleaseError(f"unknown command {args.command!r}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = _dispatch(args)
    except ReleaseError as exc:
        _error(str(exc))
        return 1
    if isinstance(result, str):
        print(result)
        return 0
    _emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
