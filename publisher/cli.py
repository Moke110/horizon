"""publisher CLI: Feishu push, ratings, approvals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import emit, read_json
from hostrun import layout
from hostrun.base import HostRunError
from publisher import alerts as alert_service
from publisher.cards import build_rating_card
from publisher.feishu import FeishuError
from publisher.service import (
    deliver_next,
    listen,
    replay,
    resolve_send,
    send_push,
    send_rating,
)
from publisher.store import PublisherStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="publisher", description=__doc__)
    parser.add_argument("--code", help="code directory (default: this checkout)")
    parser.add_argument("--archive", help="archive directory (default: <code>/../horizon-archive)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("send", help="send one push: HTML file + rating card")
    p.add_argument("--push", required=True)
    p.add_argument("--html", help="self-contained HTML rendered by `writer render`")
    p.add_argument("--pdf", help="legacy PDF rendered by `writer render`")
    p.add_argument("--to", required=True, help="open_id (ou_...)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="re-send even if already pushed")
    p.add_argument("--as", dest="identity", default="bot", choices=["bot", "user"])

    p = sub.add_parser("rating", help="(re)send only the rating card for a push")
    p.add_argument("--push", required=True)
    p.add_argument("--to", required=True, help="open_id (ou_...)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--as", dest="identity", default="bot", choices=["bot", "user"])

    p = sub.add_parser("preview", help="print the rating card JSON, send nothing")
    p.add_argument("--push", required=True)

    p = sub.add_parser("preview-update", help="print one update-question card JSON")
    p.add_argument("--push", required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--total", type=int, required=True)

    p = sub.add_parser("ingest", help="replay card.action.trigger NDJSON offline")
    p.add_argument("--events", required=True)
    p.add_argument("--offline", action="store_true", help="do not call Feishu (write outbox)")
    p.add_argument("--as", dest="identity", default="bot", choices=["bot", "user"])

    p = sub.add_parser("listen", help="consume real card callbacks")
    p.add_argument("--max-events", type=int, default=1)
    p.add_argument("--timeout", default="10m")
    p.add_argument("--as", dest="identity", default="bot", choices=["bot", "user"])

    p = sub.add_parser("alerts", help="deliver pending library quota alerts as text")
    p.add_argument("--to", required=True, help="owner open_id (ou_...)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max", type=int, default=5)
    p.add_argument("--as", dest="identity", default="bot", choices=["bot", "user"])

    p = sub.add_parser(
        "resolve-send",
        help="reconcile a send whose outcome was unknown (check Feishu first)",
    )
    p.add_argument("--push", required=True)
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("--message", help="message_id seen in Feishu: resolves as sent")
    action.add_argument(
        "--not-sent",
        dest="not_sent",
        action="store_true",
        help="confirmed not sent: clears the attempt so a retry is allowed",
    )

    p = sub.add_parser("pending")
    p = sub.add_parser(
        "deliver",
        help="deliver the next ready question to the verified recipient",
    )
    p.add_argument("--to", required=True, help="open_id (ou_...)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--as", dest="identity", default="bot", choices=["bot", "user"])
    return parser


def _emit_result(result: dict) -> int:
    """Print the result and turn a refusal into a non-zero exit code.

    A silent exit 0 on `duplicate_paper` is what let a headless run keep
    retrying and then time out with nothing delivered.
    """
    emit(result)
    if result.get("ok") is False:
        reason = result.get("reason") or result.get("error") or "failed"
        print(json.dumps({"ok": False, "reason": reason}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(layout.cli_argv(argv))
        resolved = layout.resolve_for_cli(args)
    except HostRunError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    # The locations this command resolved must govern the recorded lark-cli path
    # and every store path; the export is also what spawned module commands read.
    layout.export(resolved)
    root = Path(resolved["runtime"])
    try:
        if args.cmd == "send":
            push = read_json(Path(args.push))
            if not push:
                raise ValueError(f"no push at {args.push}")
            if args.html or args.pdf:
                path = Path(args.html or args.pdf)
                return _emit_result(
                    send_push(
                        root,
                        push,
                        path,
                        args.to,
                        args.dry_run,
                        args.identity,
                        force=args.force,
                    )
                )
            if push.get("format") in ("pdf", "html"):
                raise ValueError("--html (or legacy --pdf) is required for a push file")
            return _emit_result(send_rating(root, push, args.to, args.dry_run, args.identity))
        elif args.cmd == "rating":
            push = read_json(Path(args.push))
            if not push:
                raise ValueError(f"no push at {args.push}")
            return _emit_result(send_rating(root, push, args.to, args.dry_run, args.identity))
        elif args.cmd == "deliver":
            delivered = deliver_next(root, args.to, args.dry_run, args.identity)
            emit(delivered or {"ok": True, "active": None})
        elif args.cmd == "preview":
            push = read_json(Path(args.push))
            if not push:
                raise ValueError(f"no push at {args.push}")
            emit(build_rating_card(push))
        elif args.cmd == "preview-update":
            from publisher.update_cards import build_update_card

            push = read_json(Path(args.push))
            item = read_json(Path(args.item))
            if not push or not item:
                raise ValueError("provide both --push and --item JSON files")
            emit(build_update_card(push, item, args.index, args.total))
        elif args.cmd == "ingest":
            for result in replay(root, Path(args.events), args.offline, args.identity):
                result.pop("approval_card", None)
                emit(result)
        elif args.cmd == "listen":
            return listen(root, args.identity, args.max_events, args.timeout)
        elif args.cmd == "alerts":
            return _emit_result(
                alert_service.deliver(
                    root, args.to,
                    dry_run=args.dry_run,
                    identity=args.identity,
                    max_alerts=args.max,
                )
            )
        elif args.cmd == "resolve-send":
            return _emit_result(
                resolve_send(
                    root,
                    args.push,
                    message_id=args.message,
                    not_sent=args.not_sent,
                )
            )
        elif args.cmd == "pending":
            emit(PublisherStore(root).pending_proposals())
        else:  # pragma: no cover
            raise ValueError(args.cmd)
    except (ValueError, KeyError, FeishuError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
