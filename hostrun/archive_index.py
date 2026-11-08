"""The archive index: a code-directory ledger of the material files.

ADR 0009 keeps the archive directory free of ledgers.  The index therefore lives
in the code directory runtime area and records, for every material file under
``originals/`` and ``pushes/``, its business id, size and content md5.  It is
append-only in effect: re-registering the same bytes is a no-op, so
``/horizon-update-kb`` can ask "which files are new?" without re-reading a
directory twice.

Nothing here writes into the archive directory and nothing touches Feishu.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from common import read_jsonl, write_jsonl

INDEX_DIRNAME = "archive-index"
INDEX_FILENAME = "index.jsonl"
MATERIAL_ROOTS = ("originals", "pushes")
# Directory names that group files rather than identify one business object.
GENERIC_PARENTS = frozenset({"text", "pages", "assets", "raw"})


def index_path(runtime: str | Path) -> Path:
    return Path(runtime).expanduser().resolve() / INDEX_DIRNAME / INDEX_FILENAME


def business_id(relative: str | Path) -> str:
    """The object a material file belongs to: a paper id, push id or card id."""
    parts = Path(relative).parts
    if not parts:
        return ""
    parent = parts[-2] if len(parts) >= 2 else ""
    if parent and parent not in GENERIC_PARENTS and parent not in MATERIAL_ROOTS:
        return parent
    return Path(parts[-1]).stem


def file_md5(path: str | Path, *, chunk: int = 1 << 20) -> str:
    digest = hashlib.md5()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def load(runtime: str | Path) -> list[dict]:
    return read_jsonl(index_path(runtime))


def lookup(runtime: str | Path, *, business: str | None = None, md5: str | None = None) -> list[dict]:
    rows = load(runtime)
    if business is not None:
        rows = [row for row in rows if row.get("business_id") == business]
    if md5 is not None:
        rows = [row for row in rows if row.get("md5") == md5]
    return rows


def material_files(archive: str | Path) -> list[tuple[Path, Path]]:
    """Every material file under the archive's two allowed roots, path-sorted."""
    root = Path(archive).expanduser().resolve()
    found: list[tuple[Path, Path]] = []
    for name in MATERIAL_ROOTS:
        directory = root / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                found.append((path, path.relative_to(root)))
    found.sort(key=lambda item: item[1].as_posix())
    return found


def register(runtime: str | Path, archive: str | Path, *, dry_run: bool = False) -> dict:
    """Add every not-yet-registered material file to the ledger.

    A file whose bytes already appear in the ledger is reported as a duplicate and
    not added again, which is what makes a repeated migration a no-op.
    """
    target = index_path(runtime)
    existing = load(runtime)
    known = {row.get("md5") for row in existing if row.get("md5")}
    scanned = 0
    duplicates = 0
    added: list[dict] = []
    for path, relative in material_files(archive):
        scanned += 1
        digest = file_md5(path)
        if digest in known:
            duplicates += 1
            continue
        known.add(digest)
        added.append(
            {
                "schema_version": 1,
                "business_id": business_id(relative),
                "path": relative.as_posix(),
                "md5": digest,
                "size": path.stat().st_size,
            }
        )
    if added and not dry_run:
        write_jsonl(target, [*existing, *added])
    return {
        "ok": True,
        "index": str(target),
        "scanned": scanned,
        "registered": len(added),
        "duplicates": duplicates,
        "existing": len(existing),
        "total": len(existing) + len(added),
        "entries": added,
    }


def status(runtime: str | Path, archive: str | Path) -> dict:
    """Ledger counts plus which of the two material roots actually exist."""
    rows = load(runtime)
    root = Path(archive).expanduser().resolve()
    return {
        "ok": True,
        "index": str(index_path(runtime)),
        "present": index_path(runtime).is_file(),
        "entries": len(rows),
        "business_ids": len({row.get("business_id") for row in rows}),
        "material_roots": {
            name: (root / name).is_dir() for name in MATERIAL_ROOTS
        },
    }
