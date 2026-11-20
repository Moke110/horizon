#!/usr/bin/env python3
"""Assemble the bundled CPython runtime a release package ships.

CI downloads the pinned CPython embeddable zip and the pinned dependency wheels
(``python -m pip download --only-binary=:all:``), then calls this script:

    python scripts/prepare_runtime.py \
        --embed-zip python-embed.zip \
        --wheels wheels \
        --out runtime \
        --python-version 3.13 \
        --require-module yaml --require-module pypdf

The result is the layout ``hostrun.distribution --runtime-dir`` copies verbatim
(see ``contracts/distribution-runtime.md``)::

    runtime/python.exe
    runtime/python<major><minor>.dll
    runtime/python<major><minor>._pth    # gains .. (code root) and Lib\\site-packages
    runtime/python<major><minor>.zip     # stdlib from the embeddable zip
    runtime/*.pyd                        # extension modules shipped flat by the
                                         # embeddable zip (a full distribution
                                         # keeps them under DLLs/ instead)
    runtime/Lib/site-packages/           # wheel contents, extracted as zips

Wheels are zips, so they are extracted directly instead of trusting
``pip install --target`` on the runner: the runner's site-packages is never part
of the bundled runtime.  Extraction is sorted and byte-for-byte; every input is
validated first, and a missing/malformed input fails with the reason instead of
producing a partial runtime.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hostrun import release  # noqa: E402

SITE_PACKAGES = Path("Lib") / "site-packages"
#: Entries the assembled runtime must expose in addition to the embeddable zip's
#: own ``python3XX.zip`` / ``.`` lines.  ``..`` is the code root: the wrapper
#: ``cd``s there and runs ``runtime\python.exe -m hostrun.cli``, but an isolated
#: embeddable interpreter ignores ``sys.path[0]``, so the code root must be an
#: explicit ``._pth`` entry or ``import hostrun``/``common`` fails in a real
#: install.  Order is the order they are appended when absent.
REQUIRED_PTH_ENTRIES = ("..", "Lib\\site-packages")
DATA_DIR_RE = re.compile(r"^[^/]+\.data/([^/]+)/")
_PYTHON_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _python_tag(python_version: str) -> str:
    match = _PYTHON_VERSION_RE.match(str(python_version or "").strip())
    if not match:
        raise release.ReleaseError(
            f"python version must be MAJOR.MINOR (e.g. 3.13); got {python_version!r}"
        )
    return f"{match.group(1)}{match.group(2)}"


def _output_dir(out: str | Path) -> Path:
    path = Path(out).expanduser().resolve()
    if path.exists():
        if not path.is_dir():
            raise release.ReleaseError(f"runtime output is not a directory: {path}")
        if any(path.iterdir()):
            raise release.ReleaseError(
                f"runtime output directory is not empty: {path}; remove it first "
                "so a stale tree is never bundled"
            )
    return path


def _require_zip(path: Path, what: str) -> Path:
    if not path.is_file():
        raise release.ReleaseError(f"{what} is missing: {path}")
    if not zipfile.is_zipfile(path):
        raise release.ReleaseError(f"{what} is not a valid zip archive: {path}")
    return path


def _safe_relative(name: str, source: str) -> PurePosixPath:
    text = str(name).replace("\\", "/")
    relative = PurePosixPath(text)
    if (
        not relative.parts
        or relative.is_absolute()
        or ".." in relative.parts
        or ":" in relative.parts[0]
    ):
        raise release.ReleaseError(f"{source} contains an unsafe path: {name!r}")
    return relative


def _extract_archive(archive_path: Path, destination: Path, source: str) -> int:
    extracted = 0
    with zipfile.ZipFile(archive_path) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                continue
            relative = _safe_relative(info.filename, source)
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
            extracted += 1
    return extracted


def _extract_wheel(wheel: Path, site_packages: Path) -> int:
    """Extract one wheel as a zip, mapping .data/purelib and .data/platlib.

    Wheels are zips; ``pip install --target`` would also consult the runner's
    environment, which the bundled runtime must not depend on.  ``.data``
    scripts/headers/data are not importable runtime content, so they are skipped.
    """
    extracted = 0
    with zipfile.ZipFile(wheel) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                continue
            relative = _safe_relative(info.filename, wheel.name)
            match = DATA_DIR_RE.match(relative.as_posix())
            if match:
                kind = match.group(1)
                if kind not in ("purelib", "platlib"):
                    continue
                relative = PurePosixPath(*relative.parts[2:])
                if not relative.parts:
                    continue
            target = site_packages.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
            extracted += 1
    return extracted


def _ensure_pth(runtime: Path, python_version: str) -> Path:
    """Expose the code root and ``Lib\\site-packages`` exactly once in ``._pth``.

    The embeddable zip ships ``python3XX.zip`` and ``.`` (and sometimes
    ``Lib\\site-packages``).  Those original entries are kept as-is; the entries
    this build requires are added in canonical spelling/order when missing and
    deduplicated (case- and slash-insensitively) when already present, so
    re-assembling a pre-built runtime cannot accumulate duplicates.
    """
    tag = _python_tag(python_version)
    pth = runtime / f"python{tag}._pth"
    if not pth.is_file():
        raise release.ReleaseError(
            f"embeddable zip did not contain python{tag}._pth: {runtime}"
        )
    text = pth.read_text(encoding="utf-8", errors="replace")
    canonical = {entry.lower(): entry for entry in REQUIRED_PTH_ENTRIES}
    kept: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        entry = line.rstrip("\r\n").strip()
        key = entry.replace("/", "\\").lower()
        if key in canonical:
            if key in seen:
                continue
            seen.add(key)
            kept.append(canonical[key])
            continue
        kept.append(line.rstrip("\r\n"))
    for entry in REQUIRED_PTH_ENTRIES:
        if entry.lower() not in seen:
            kept.append(entry)
    pth.write_text(
        "\r\n".join(kept).rstrip("\r\n") + "\r\n",
        encoding="utf-8",
        newline="",
    )
    return pth


def prepare_runtime(
    embed_zip: str | Path,
    wheels_dir: str | Path,
    out: str | Path,
    *,
    python_version: str = "3.13",
    required_modules=(),
) -> dict:
    """Assemble ``out`` from one embeddable zip and every wheel in ``wheels_dir``."""
    embed = Path(embed_zip).expanduser()
    wheels = Path(wheels_dir).expanduser().resolve()
    output = _output_dir(out)
    _python_tag(python_version)
    _require_zip(embed, "embeddable zip")
    if not wheels.is_dir():
        raise release.ReleaseError(f"wheels directory is missing: {wheels}")
    wheel_files = sorted(wheels.glob("*.whl"), key=lambda path: path.name)
    if not wheel_files:
        raise release.ReleaseError(f"wheels directory has no .whl files: {wheels}")
    for wheel in wheel_files:
        _require_zip(wheel, f"wheel {wheel.name}")

    try:
        output.mkdir(parents=True, exist_ok=True)
        _extract_archive(embed, output, embed.name)
        site_packages = output / SITE_PACKAGES
        site_packages.mkdir(parents=True, exist_ok=True)
        for wheel in wheel_files:
            _extract_wheel(wheel, site_packages)
    except OSError as exc:
        raise release.ReleaseError(
            f"could not assemble the runtime at {output}: {exc}"
        ) from exc

    pth = _ensure_pth(output, python_version)
    validated = release.validate_runtime_dir(
        output,
        python_version=python_version,
        required_modules=required_modules,
    )
    return {
        "ok": True,
        "runtime": validated["runtime"],
        "python": validated["python"],
        "files": validated["files"],
        "bytes": validated["bytes"],
        "wheels": [wheel.name for wheel in wheel_files],
        "pth": str(pth),
        "modules": validated["modules"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/prepare_runtime.py",
        description="Assemble a CPython embeddable runtime from pinned downloads.",
    )
    parser.add_argument("--embed-zip", required=True, help="CPython embeddable amd64 zip")
    parser.add_argument("--wheels", required=True, help="directory of dependency .whl files")
    parser.add_argument("--out", required=True, help="empty directory to assemble (e.g. runtime)")
    parser.add_argument("--python-version", default="3.13", help="MAJOR.MINOR of the embeddable zip")
    parser.add_argument(
        "--require-module",
        action="append",
        default=[],
        dest="require_modules",
        help="module that must exist under Lib/site-packages (repeatable)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = prepare_runtime(
            args.embed_zip,
            args.wheels,
            args.out,
            python_version=args.python_version,
            required_modules=args.require_modules,
        )
    except release.ReleaseError as exc:
        _emit({"ok": False, "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 1
    _emit(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
