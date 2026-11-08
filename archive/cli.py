"""archive CLI — the only writer of the runtime area's archive store."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from archive import migrate as migration
from archive.store import Archive, ArchiveError
from common import emit, read_json
from hostrun import layout
from hostrun.base import HostRunError


def _payload(args) -> dict:
    if getattr(args, "json", None):
        if args.json == "-":
            return json.load(sys.stdin)
        if args.json.startswith("@"):
            return read_json(Path(args.json[1:]))
        return json.loads(args.json)
    if getattr(args, "file", None):
        if args.file == "-":
            return json.load(sys.stdin)
        return read_json(Path(args.file))
    raise SystemExit("provide --json <json|@file|-> or --file <path>")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="archive", description=__doc__)
    parser.add_argument("--code", help="code directory (default: this checkout)")
    parser.add_argument("--archive", help="archive directory (default: <code>/../horizon-archive)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create layer directories")
    sub.add_parser("reindex", help="rebuild indexes from files")

    p = sub.add_parser("add-paper", help="store a paper (dedupe by source:source_id)")
    p.add_argument("--json")
    p.add_argument("--file")

    p = sub.add_parser("get-paper")
    p.add_argument("paper_id")

    p = sub.add_parser("list-papers")
    p.add_argument("--card", help="filter by knowledge card id")

    p = sub.add_parser("add-push", help="store a generated push")
    p.add_argument("--json")
    p.add_argument("--file")

    p = sub.add_parser("get-push")
    p.add_argument("push_id")

    p = sub.add_parser("list-pushes")
    p.add_argument("--card", help="filter by knowledge card id")

    p = sub.add_parser("card-put", help="create or update a knowledge card")
    p.add_argument("--json")
    p.add_argument("--file")

    p = sub.add_parser("card-get")
    p.add_argument("card_id")

    sub.add_parser("card-list", help="list knowledge cards")

    p = sub.add_parser("prop-put", help="create or replace a proposition")
    p.add_argument("--json")
    p.add_argument("--file")

    p = sub.add_parser("prop-get")
    p.add_argument("prop_id")

    p = sub.add_parser("prop-list")
    p.add_argument("--card", help="filter by knowledge card id")

    p = sub.add_parser("context", help="aggregate card + propositions + history for writer")
    p.add_argument("--card", required=True)
    p.add_argument("--limit", type=int, default=5)

    p = sub.add_parser("apply-proposal")
    p.add_argument("--push-id", required=True)
    p.add_argument("--proposal-id", required=True)

    p = sub.add_parser("apply-group", help="apply one queue item's proposals atomically")
    p.add_argument("--file", required=True, help="queue item / JSON with proposals")
    p.add_argument("--group-id", help="idempotence key (default: item id or file stem)")

    p = sub.add_parser("apply-decisions", help="apply approved publisher decisions")
    p.add_argument("--pending-dir", required=True)
    p.add_argument("--decisions", required=True)

    p = sub.add_parser("migrate-v2", help="migrate a v1 data root (idempotent, backed up)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--rollback", metavar="BACKUP_DIR", help="restore a v1 backup")

    p = sub.add_parser("migrate-v3", help="convert v2 propositions to review + evidence + changelog")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--rollback", metavar="BACKUP_DIR", help="restore a pre-v3 backup")

    sub.add_parser("stats")
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
    store = Archive(root, archive=resolved["archive"])
    try:
        if args.cmd == "init":
            emit(store.init())
        elif args.cmd == "reindex":
            emit(store.reindex())
        elif args.cmd == "add-paper":
            emit(store.add_paper(_payload(args)))
        elif args.cmd == "get-paper":
            emit(store.get_paper(args.paper_id))
        elif args.cmd == "list-papers":
            emit(store.list_papers(args.card))
        elif args.cmd == "add-push":
            source_dir = Path(args.file).resolve().parent if args.file else None
            emit(store.add_push(_payload(args), source_dir=source_dir))
        elif args.cmd == "get-push":
            emit(store.get_push(args.push_id))
        elif args.cmd == "list-pushes":
            emit(store.list_pushes(args.card))
        elif args.cmd == "card-put":
            emit(store.put_card(_payload(args)))
        elif args.cmd == "card-get":
            emit(store.get_card(args.card_id))
        elif args.cmd == "card-list":
            emit(store.list_cards())
        elif args.cmd == "prop-put":
            emit(store.put_prop(_payload(args)))
        elif args.cmd == "prop-get":
            emit(store.get_prop(args.prop_id))
        elif args.cmd == "prop-list":
            emit(store.list_props(args.card))
        elif args.cmd == "context":
            emit(store.context(args.card, args.limit))
        elif args.cmd == "apply-proposal":
            emit(store.apply_proposal(args.push_id, args.proposal_id))
        elif args.cmd == "apply-group":
            payload = read_json(Path(args.file)) or {}
            proposals = (
                payload.get("proposals")
                or (payload.get("payload") or {}).get("proposals")
                or []
            )
            group_id = args.group_id or payload.get("id") or Path(args.file).stem
            emit(store.apply_group(proposals, group_id))
        elif args.cmd == "apply-decisions":
            emit(store.apply_decisions(Path(args.pending_dir), Path(args.decisions)))
        elif args.cmd == "migrate-v2":
            if args.rollback:
                emit(migration.rollback(root, args.rollback))
            else:
                emit(migration.migrate(root, dry_run=args.dry_run))
        elif args.cmd == "migrate-v3":
            if args.rollback:
                emit(migration.rollback_v3(root, args.rollback))
            else:
                emit(migration.migrate_v3(root, dry_run=args.dry_run))
        elif args.cmd == "stats":
            emit(store.stats())
        else:  # pragma: no cover
            raise ArchiveError(f"unknown command {args.cmd}")
    except ArchiveError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
