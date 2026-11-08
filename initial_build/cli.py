"""CLI: ``python -m initial_build.cli apply --draft drafts/initial-build/<theme>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import emit
from hostrun import layout
from hostrun.base import HostRunError
from initial_build.build import apply, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="initial_build", description=__doc__)
    parser.add_argument("--code", help="code directory (default: this checkout)")
    parser.add_argument("--archive", help="archive directory (default: <code>/../horizon-archive)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("apply", help="validate then write a draft into archive")
    p.add_argument("--draft", required=True, help="directory with cards/propositions/sources.jsonl")
    p.add_argument("--dry-run", action="store_true")
    try:
        args = parser.parse_args(layout.cli_argv(argv))
        resolved = layout.resolve_for_cli(args)
    except HostRunError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    layout.export(resolved)

    report = apply(Path(resolved["runtime"]), args.draft, dry_run=args.dry_run)
    emit(summary(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
