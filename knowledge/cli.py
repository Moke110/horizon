"""knowledge CLI: mirror the local knowledge layer into Bitable and compare."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import emit
from hostrun import layout
from hostrun.base import HostRunError
from knowledge import bitable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knowledge", description=__doc__)
    parser.add_argument("--code", help="code directory (default: this checkout)")
    parser.add_argument("--archive", help="archive directory (default: <code>/../horizon-archive)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sync", help="upsert cards/propositions/evidence/logs into Bitable")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("compare", help="report drift between local archive and Bitable")

    p = sub.add_parser("pull", help="apply text-only manual edits from Bitable")

    p = sub.add_parser("ledger", help="write the current local text baseline")

    sub.add_parser("config", help="print the Bitable config")
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
        if args.cmd == "sync":
            emit(bitable.sync(root, dry_run=args.dry_run))
        elif args.cmd == "compare":
            result = bitable.compare(root)
            emit(result)
            return 0 if result["ok"] else 1
        elif args.cmd == "pull":
            emit(bitable.pull(root))
        elif args.cmd == "ledger":
            emit(bitable.write_ledger(root))
        elif args.cmd == "config":
            emit(bitable.load_config(root))
    except bitable.BitableError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
