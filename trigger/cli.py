"""trigger CLI: listen for Lark keywords and run one manual push."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from common import emit
from hostrun import layout
from hostrun.base import HostRunError
from trigger.keywords import parse, resolve_checkpoint
from trigger.service import (
    Config,
    handle_event,
    listen,
    load_owner,
    run_pending,
    run_review,
    scheduled_pull,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trigger", description=__doc__)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--code", help="code directory (default: this checkout)")
    parser.add_argument("--archive", help="archive directory (default: <code>/../horizon-archive)")
    parser.add_argument("--owner", help="owner open_id (default: $HORIZON_OWNER_ID)")
    parser.add_argument("--timeout", type=int, default=1800, help="agent timeout seconds")
    parser.add_argument("--dry-run", action="store_true", help="ack locally, do not call Lark/agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("listen", help="consume im.message.receive_v1")
    p.add_argument("--max-events", type=int, default=0)
    p.add_argument("--event-timeout", default="0")
    p.add_argument("--as", dest="identity", default="bot", choices=["bot", "user"])

    p = sub.add_parser("once", help="handle one NDJSON event from stdin or a file")
    p.add_argument("--event", help="JSON file; default stdin")

    sub.add_parser("scheduled", help="08:00 timer entry: request or start one push")
    p = sub.add_parser("review-request", help="generate and send one queued review question")
    p.add_argument("--item", required=True)

    p = sub.add_parser("run-request", help="start the merged pending request if allowed")
    p.add_argument("--quiet", action="store_true", help="do not log no-op polls (retry timer)")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(layout.cli_argv(argv))
        resolved = layout.resolve_for_cli(args)
    except HostRunError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    layout.export(resolved)
    config = Config(
        repo=Path(args.repo).expanduser().resolve(),
        home=Path(resolved["runtime"]),
        owner=load_owner(args.owner),
        timeout=args.timeout,
        dry_run=args.dry_run or os.environ.get("HORIZON_DRY_RUN") == "1",
    )
    if args.cmd == "listen":
        if not config.owner and not config.dry_run:
            print(json.dumps({"ok": False, "error": "HORIZON_OWNER_ID is not set"}), file=sys.stderr)
            return 1
        return listen(config, args.max_events, args.event_timeout, args.identity)

    if args.cmd == "once":
        if args.event:
            event = json.loads(Path(args.event).read_text(encoding="utf-8"))
        else:
            event = json.load(sys.stdin)
        emit(handle_event(event, config))
        return 0
    if args.cmd == "scheduled":
        emit(scheduled_pull(config))
        return 0
    if args.cmd == "review-request":
        emit(run_review(config, args.item))
        return 0
    if args.cmd == "run-request":
        emit(run_pending(config, quiet=args.quiet))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
