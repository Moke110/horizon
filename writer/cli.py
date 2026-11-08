"""writer CLI: agent task packets, validation, HTML/PDF rendering, and a fallback.

Primary flow (agent-driven):
  writer task -> writer figures -> agent writes push JSON -> writer check
  -> writer render -> archive add-push
Fallback (offline / tests):
  writer write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import emit, now, read_json, today, write_json
from hostrun import layout
from hostrun.base import HostRunError
from writer.figures import fetch_for_paper
from writer.model import write
from writer import review as review_task
from writer.render import RenderError, render_push
from writer.tasks import build_task, validate as validate_outbox


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="writer", description=__doc__)
    parser.add_argument("--code", help="code directory (default: this checkout)")
    parser.add_argument("--archive", help="archive directory (default: <code>/../horizon-archive)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("write", help="heuristic writer (fallback, no agent)")
    p.add_argument("--selection", required=True)
    p.add_argument("--out", required=True, help="outbox directory (not archive)")
    p.add_argument("--date")

    p = sub.add_parser("task", help="build an agent writing packet")
    p.add_argument("--selection", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--date")

    p = sub.add_parser("check", help="validate agent-written pushes")
    p.add_argument("--task", required=True)
    p.add_argument("--outbox", required=True)

    p = sub.add_parser("render", help="render a push JSON into self-contained HTML")
    p.add_argument("--push", required=True)
    p.add_argument("--out", help="output path (default: next to the push)")
    p.add_argument(
        "--format",
        choices=["html", "pdf"],
        default="html",
        help="html (default, self-contained) or legacy pdf",
    )

    p = sub.add_parser("review-task", help="build an agent review packet for a proposition")
    p.add_argument("--prop", required=True)
    p.add_argument("--item", help="review queue item id")
    p.add_argument("--out", required=True)

    p = sub.add_parser("review-check", help="validate an agent-written review")
    p.add_argument("--task", required=True)
    p.add_argument("--review", required=True)

    p = sub.add_parser("review-render", help="render a review JSON into self-contained HTML")
    p.add_argument("--review", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("figures", help="fetch paper figures (or generate a fallback)")
    p.add_argument("--selection", required=True)
    p.add_argument("--out", required=True, help="outbox directory")
    p.add_argument("--date")
    p.add_argument("--limit", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(layout.cli_argv(argv))
        resolved = layout.resolve_for_cli(args)
    except HostRunError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    layout.export(resolved)
    root = Path(resolved["runtime"])
    try:
        if args.cmd == "write":
            selection = read_json(Path(args.selection))
            if not selection:
                raise ValueError(f"no selection at {args.selection}")
            result = write(selection, root, Path(args.out), args.date)
            emit(
                {
                    "ok": True,
                    "count": result["count"],
                    "out_dir": result["out_dir"],
                    "pushes": [
                        {
                            "push_id": p["push_id"],
                            "title": p["title"],
                            "proposals": len(p.get("proposals", [])),
                            "path": str(Path(result["out_dir"]) / "pushes" / f"{p['push_id']}.json"),
                        }
                        for p in result["pushes"]
                    ],
                }
            )
        elif args.cmd == "task":
            selection = read_json(Path(args.selection))
            if not selection:
                raise ValueError(f"no selection at {args.selection}")
            task = build_task(root, selection, args.date or selection.get("date") or today())
            write_json(Path(args.out), task)
            emit(
                {
                    "ok": True,
                    "out": args.out,
                    "items": len(task["items"]),
                    "proposal_kinds": list(task["proposal_kinds"]),
                }
            )
        elif args.cmd == "check":
            task = read_json(Path(args.task))
            if not task:
                raise ValueError(f"no task at {args.task}")
            result = validate_outbox(task, Path(args.outbox))
            emit(result)
            return 0 if result["ok"] else 1
        elif args.cmd == "render":
            try:
                result = render_push(
                    Path(args.push),
                    Path(args.out) if args.out else None,
                    fmt=args.format,
                )
            except RenderError as exc:
                emit({"ok": False, "error": str(exc)})
                return 1
            emit(result)
        elif args.cmd == "review-task":
            task = review_task.build_task(root, args.prop, args.item)
            write_json(Path(args.out), task)
            emit(
                {
                    "ok": True,
                    "out": args.out,
                    "prop_id": task["prop_id"],
                    "evidence": len(task["evidence"]),
                    "output": task["output"],
                }
            )
        elif args.cmd == "review-check":
            task = read_json(Path(args.task))
            review = read_json(Path(args.review))
            if not task or not review:
                raise ValueError("review-check needs --task and --review files")
            errors = review_task.validate(task, review)
            emit({"ok": not errors, "errors": errors, "stats": review.get("stats")})
            return 0 if not errors else 1
        elif args.cmd == "review-render":
            review = read_json(Path(args.review))
            if not review:
                raise ValueError(f"no review at {args.review}")
            out = review_task.render_html(root, review, Path(args.out))
            emit({"ok": True, "html": str(out), "bytes": out.stat().st_size})
        elif args.cmd == "figures":
            selection = read_json(Path(args.selection))
            if not selection:
                raise ValueError(f"no selection at {args.selection}")
            date = args.date or selection.get("date") or today()
            out_dir = Path(args.out)
            figures: dict[str, dict] = {}
            for index, row in enumerate(selection.get("selected", []), start=1):
                paper = row.get("paper") or {}
                cp_id = row.get("checkpoint_id")
                push_id = f"{date}_{cp_id}_{index:02d}"
                assets = fetch_for_paper(paper, push_id, out_dir / "pushes", limit=args.limit)
                figures[push_id] = {
                    "paper_id": paper.get("paper_id"),
                    "checkpoint_id": cp_id,
                    "assets": assets,
                }
            write_json(
                out_dir / "figures.json",
                {"schema_version": 1, "generated_at": now(), "pushes": figures},
            )
            emit(
                {
                    "ok": True,
                    "out": str(out_dir / "figures.json"),
                    "pushes": {
                        pid: {
                            "paper_id": row["paper_id"],
                            "figures": [
                                {"id": a["id"], "source": a["source"], "path": a["path"]}
                                for a in row["assets"]
                            ],
                        }
                        for pid, row in figures.items()
                    },
                }
            )
        else:  # pragma: no cover
            raise ValueError(args.cmd)
    except (ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
