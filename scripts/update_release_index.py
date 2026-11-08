#!/usr/bin/env python3
"""Merge one release into ``releases/index.json`` deterministically.

The workflow calls this after a successful build, passing the build's digest
file (the single source for version, channel, artifact name/digest, manifest
digest and compat) plus the GitHub Release URL:

    python scripts/update_release_index.py \
        --index releases/index.json \
        --digest dist/horizon-0.1.0-stable.digest.json \
        --release-url https://github.com/Moke110/horizon/releases/tag/v0.1.0

``published_at`` is the only build-time timestamp; when omitted it is recorded
as the current UTC time.  The merge replaces the same version, keeps every other
entry, sorts newest first (semver precedence) and writes canonical JSON, so two
identical inputs produce identical index bytes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hostrun import release  # noqa: E402


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def update_release_index(
    *,
    index_path: str | Path,
    digest_path: str | Path,
    release_url: str,
    published_at: str | None = None,
    out_path: str | Path | None = None,
) -> dict:
    """Read ``index_path``, merge the digest's release, write canonical JSON."""
    digest_file = Path(digest_path)
    try:
        digest = json.loads(digest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise release.ReleaseError(
            f"could not read digest {digest_file}: {exc}"
        ) from exc
    entry = release.entry_from_digest(
        digest, release_url=release_url, published_at=published_at
    )
    current = release.load_index(index_path)
    merged = release.merge_release_index(current, entry)
    target = Path(out_path) if out_path is not None else Path(index_path)
    release.write_index(target, merged)
    return {
        "ok": True,
        "index": str(target.resolve()),
        "entry": entry,
        "releases": len(merged["releases"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/update_release_index.py",
        description="Merge one release entry into the deterministic release index.",
    )
    parser.add_argument(
        "--index", default="releases/index.json", help="release index to read and update"
    )
    parser.add_argument("--digest", required=True, help="same-build .digest.json")
    parser.add_argument("--release-url", required=True, help="GitHub Release URL")
    parser.add_argument(
        "--published-at",
        default=None,
        help="ISO-8601 publication time; defaults to now (UTC)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="write the merged index here instead of replacing --index",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = update_release_index(
            index_path=args.index,
            digest_path=args.digest,
            release_url=args.release_url,
            published_at=args.published_at,
            out_path=args.out,
        )
    except release.ReleaseError as exc:
        _emit({"ok": False, "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 1
    _emit(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
