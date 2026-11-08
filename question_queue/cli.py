"""queue CLI: inspect and drive the unified question queue."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import emit, read_json
from hostrun import layout
from hostrun.base import HostRunError
from question_queue.store import QueueError, QueueStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="queue", description=__doc__)
    parser.add_argument("--code", help="code directory (default: this checkout)")
    parser.add_argument("--archive", help="archive directory (default: <code>/../horizon-archive)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="print queue state and pending request")

    p = sub.add_parser("enqueue", help="enqueue a push's questions")
    p.add_argument("--push", required=True, help="push JSON path")

    p = sub.add_parser("next", help="return/activate the next question")

    p = sub.add_parser("answer", help="answer an active question")
    p.add_argument("--item", required=True)
    p.add_argument("--verdict", choices=["irrelevant", "ok", "great"])
    p.add_argument("--decision", choices=["agree_all", "reject_all", "adopt", "keep_previous"])
    p.add_argument("--comment", default="")
    p.add_argument("--operator", default="cli")

    p = sub.add_parser("review-ready", help="attach a generated review and activate its question")
    p.add_argument("--item", required=True)
    p.add_argument("--review", required=True)
    p.add_argument("--html", required=True)
    p.add_argument("--base-url", default=None)

    p = sub.add_parser("retry")
    p.add_argument("--item", required=True)

    p = sub.add_parser("abandon")
    p.add_argument("--item", required=True)

    p = sub.add_parser("request-push")
    p.add_argument("--source", choices=["manual", "scheduled"], required=True)
    p.add_argument("--card")

    sub.add_parser("try-start")

    p = sub.add_parser("finish-start")
    p.add_argument("--success", action="store_true")
    p.add_argument("--error", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(layout.cli_argv(argv))
        resolved = layout.resolve_for_cli(args)
    except HostRunError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    layout.export(resolved)
    store = QueueStore(Path(resolved["runtime"]))
    store.init()
    try:
        if args.cmd == "status":
            emit(store.status())
        elif args.cmd == "enqueue":
            push = read_json(Path(args.push))
            if not push:
                raise QueueError(f"no push at {args.push}")
            emit(store.enqueue_push(push))
        elif args.cmd == "next":
            emit(store.next_active() or {"ok": True, "active": None})
        elif args.cmd == "answer":
            if args.verdict:
                answer = {
                    "verdict": args.verdict,
                    "comment": args.comment,
                    "operator_id": args.operator,
                }
            elif args.decision:
                answer = {"decision": args.decision, "operator_id": args.operator}
            else:
                raise QueueError("provide --verdict or --decision")
            emit(store.answer(args.item, answer))
        elif args.cmd == "review-ready":
            review = read_json(Path(args.review))
            if not review or not review.get("proposal"):
                raise QueueError(f"no review proposal at {args.review}")
            emit(
                store.mark_review_ready(
                    args.item,
                    [review["proposal"]],
                    stats=review.get("stats"),
                    base_url=args.base_url,
                    html_path=args.html,
                )
            )
        elif args.cmd == "retry":
            emit(store.retry(args.item))
        elif args.cmd == "abandon":
            emit(store.abandon(args.item))
        elif args.cmd == "request-push":
            emit(store.request_push(args.source, args.card))
        elif args.cmd == "try-start":
            emit(store.try_start() or {"ok": True, "start": False})
        elif args.cmd == "finish-start":
            emit(store.finish_start(args.success, args.error))
        else:  # pragma: no cover
            raise QueueError(f"unknown command {args.cmd}")
    except QueueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
