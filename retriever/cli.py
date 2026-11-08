"""retriever CLI: plan -> candidates.jsonl."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import emit, paper_id, read_json, write_jsonl
from hostrun import layout
from hostrun.base import HostRunError
from retriever.adapters import RetrieverError, fetch


def run(
    plan: dict,
    mode: str,
    fixtures: Path,
    checkpoint: str | None = None,
    check_window: bool = True,
    errors: list[dict] | None = None,
) -> list[dict]:
    """Fetch every planned query; one source failure must not hide the others.

    A rate-limited or unreachable source is recorded in ``errors`` and the loop
    continues, so a real retrieval can still return candidates from the
    remaining sources.  Callers decide whether partial evidence is acceptable.
    """
    start = (plan.get("window") or {}).get("start")
    end = (plan.get("window") or {}).get("end")
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for query in plan.get("queries", []):
        if checkpoint and query.get("checkpoint_id") != checkpoint:
            continue
        try:
            fetched = fetch(query, mode, fixtures)
        except RetrieverError as exc:
            if errors is not None:
                errors.append(
                    {
                        "query_id": query.get("query_id"),
                        "source": query.get("source"),
                        "error": str(exc),
                    }
                )
            continue
        for rank, paper in enumerate(fetched, start=1):
            key = (paper.get("source", ""), paper.get("source_id", ""))
            if key in seen:
                continue
            published = paper.get("published") or ""
            if check_window and start and end and published and not (start <= published <= end):
                continue
            seen.add(key)
            out.append(
                {
                    **paper,
                    "paper_id": paper_id(paper.get("source", "unknown"), paper.get("source_id", "")),
                    "checkpoint_id": query["checkpoint_id"],
                    "retrieval": {
                        "query_id": query["query_id"],
                        "checkpoint_id": query["checkpoint_id"],
                        "source": query["source"],
                        "rank_hint": rank,
                    },
                }
            )
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retriever", description=__doc__)
    parser.add_argument("--code", help="code directory (default: this checkout)")
    parser.add_argument("--archive", help="archive directory (default: <code>/../horizon-archive)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("--plan", required=True)
    p.add_argument("--out", required=True, help="candidates.jsonl path")
    p.add_argument("--source", choices=["fixture", "live"], default="fixture")
    p.add_argument("--fixtures", default="fixtures/papers")
    p.add_argument("--card", "--checkpoint", dest="checkpoint", help="filter by knowledge card id")
    p.add_argument("--no-window", action="store_true", help="do not filter by publication window")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(layout.cli_argv(argv))
        resolved = layout.resolve_for_cli(args)
    except HostRunError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    layout.export(resolved)
    plan = read_json(Path(args.plan))
    if not plan:
        print(json.dumps({"ok": False, "error": f"no plan at {args.plan}"}), file=sys.stderr)
        return 1
    errors: list[dict] = []
    try:
        rows = run(
            plan,
            args.source,
            Path(args.fixtures),
            args.checkpoint,
            check_window=not args.no_window,
            errors=errors,
        )
    except RetrieverError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3
    if not rows:
        print(
            json.dumps(
                {"ok": False, "reason": "no candidates", "source_errors": errors},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    write_jsonl(Path(args.out), rows)
    emit(
        {
            "ok": True,
            "out": args.out,
            "count": len(rows),
            "source_errors": errors,
            "by_checkpoint": {
                cp: sum(1 for r in rows if r["checkpoint_id"] == cp)
                for cp in sorted({r["checkpoint_id"] for r in rows})
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
