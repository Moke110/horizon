"""One-time migration: drop references to test-phase pushes (ADR 0003, Q13).

Evidence entries may carry ``push_id`` values that point at the 15 test pushes
which are not migrated into the push library. Clearing the id keeps the
``paper_id`` and page/quote anchors; the 报道 row keeps linking to 原文.

Idempotent. Evidence updates deliberately do not enter a proposition changelog.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archive.store import Archive
from hostrun import layout
from hostrun.base import HostRunError


TEST_PUSH = "2026-10-21_card_virtual_cell_01"


def run(data_home: Path, push_id: str = TEST_PUSH) -> dict:
    store = Archive(Path(data_home))
    changed = []
    for prop in store.list_props():
        touched = False
        for evidence in prop.get("evidence", []):
            for paper in evidence.get("papers", []):
                if paper.get("push_id") == push_id:
                    paper["push_id"] = None
                    touched = True
                    changed.append(
                        {"proposition": prop["id"],
                         "evidence": evidence.get("id"),
                         "paper": paper.get("paper_id")}
                    )
        if touched:
            store.put_prop(prop)
    return {"ok": True, "push_id": push_id, "changed": changed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="migrate_drop_test_push_refs")
    parser.add_argument("--code", help="code directory (default: this checkout)")
    parser.add_argument("--archive", help="archive directory (default: <code>/../horizon-archive)")
    parser.add_argument("--push-id", default=TEST_PUSH)
    try:
        args = parser.parse_args(layout.cli_argv(argv))
        resolved = layout.resolve_for_cli(args)
    except HostRunError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    layout.export(resolved)
    import json

    print(
        json.dumps(
            run(Path(resolved["runtime"]), args.push_id), ensure_ascii=False, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
