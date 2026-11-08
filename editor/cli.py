"""editor CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import emit, read_json, today, write_json
from editor.model import fit, load_candidates, rank, write_model
from editor.tasks import build_task, validate as validate_selection
from hostrun import layout
from hostrun.base import HostRunError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="editor", description=__doc__)
    parser.add_argument("--code", help="code directory (default: this checkout)")
    parser.add_argument("--archive", help="archive directory (default: <code>/../horizon-archive)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fit", help="learn checkpoint/source preference from ratings")
    p.add_argument("--ratings")
    p.add_argument("--pushes")
    p.add_argument("--raw")
    p.add_argument("--out", help="default <运行时区>/state/editor.yaml")

    p = sub.add_parser("rank")
    p.add_argument("--candidates", required=True)
    p.add_argument("--date")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--per-checkpoint", type=int, default=2)
    p.add_argument("--model", help="default <运行时区>/state/editor.yaml")
    p.add_argument("--out", required=True)

    p = sub.add_parser("task", help="build an agent ranking packet (agent does the ranking)")
    p.add_argument("--candidates", required=True)
    p.add_argument("--date")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--per-checkpoint", type=int, default=2)
    p.add_argument("--out", required=True)

    p = sub.add_parser("check", help="validate an agent-written selection.json")
    p.add_argument("--task", required=True)
    p.add_argument("--selection", required=True)
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
        if args.cmd == "fit":
            model = fit(root, args.ratings, args.pushes, args.raw)
            out = Path(args.out or root / "state" / "editor.yaml")
            write_model(model, out)
            emit({"ok": True, "out": str(out), "model": model})
        elif args.cmd == "rank":
            candidates = load_candidates(Path(args.candidates))
            result = rank(
                root,
                candidates,
                day=args.date,
                limit=args.limit,
                per_checkpoint=args.per_checkpoint,
                model_path=Path(args.model) if args.model else None,
            )
            write_json(Path(args.out), result)
            emit(
                {
                    "ok": True,
                    "out": args.out,
                    "selected": len(result["selected"]),
                    "scores": [
                        {
                            "paper_id": row["paper"].get("paper_id"),
                            "checkpoint_id": row["checkpoint_id"],
                            "score": row["score"],
                        }
                        for row in result["selected"]
                    ],
                }
            )
        elif args.cmd == "task":
            candidates = load_candidates(Path(args.candidates))
            task = build_task(
                root,
                candidates,
                date=args.date or today(),
                limit=args.limit,
                per_checkpoint=args.per_checkpoint,
            )
            write_json(Path(args.out), task)
            emit(
                {
                    "ok": True,
                    "out": args.out,
                    "candidates": len(task["candidates"]),
                    "checkpoints": list(task["checkpoints"]),
                    "limits": task["limits"],
                }
            )
        elif args.cmd == "check":
            task = read_json(Path(args.task))
            selection = read_json(Path(args.selection))
            if not task or not selection:
                raise ValueError("missing task or selection file")
            result = validate_selection(task, selection)
            emit(result)
            return 0 if result["ok"] else 1
        else:  # pragma: no cover
            raise ValueError(args.cmd)
    except (ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
