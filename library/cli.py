"""library CLI: Drive blobs, Base registration, quota, reconcile, cleanup.

Exit codes: 0 ok, 1 failed/conflict/drift, 3 quota_blocked.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import emit, now, read_json, read_jsonl, slug, write_json
from hostrun import layout
from hostrun.base import HostRunError
from library import base as base_mod
from library import config as config_mod
from library import setup as setup_mod
from library.drive import DriveError
from library.service import NO_SOURCE, Library, LibraryError


def _size(value: str) -> int:
    text = str(value).strip().upper().replace("B", "")
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    if text and text[-1] in units:
        return int(float(text[:-1]) * units[text[-1]])
    return int(float(text))


def _offline_library(root: Path) -> tuple[Library, object, object]:
    from library.offline import BASE_REL, DRIVE_REL, JsonBase, JsonDrive

    config = base_mod.load_base_config(root)
    tables = config["tables"]
    base = JsonBase(root / BASE_REL)
    drive = JsonDrive(root / DRIVE_REL)
    config_id = tables.get(base_mod.CONFIG)
    row = base.record(config_id, "键", "config") if config_id else None
    settings = config_mod.settings_from_row(row, tables)
    return Library(root, drive=drive, base=base, settings=settings), base, drive


def _offline_setup(root: Path, *, dry_run: bool = False) -> dict:
    from library.offline import BASE_REL, DRIVE_REL, JsonBase, JsonDrive
    from library.setup import setup

    config_path = root / "knowledge" / "base.json"
    if not read_json(config_path):
        write_json(
            config_path,
            {
                "schema_version": 1,
                "base_token": "offline",
                "base_url": "",
                "tables": {"papers": "tblpapers", "cards": "tblcards"},
            },
        )
    base = JsonBase(root / BASE_REL)
    drive = JsonDrive(root / DRIVE_REL)
    return setup(root, dry_run=dry_run, client=base, drive=drive)


def _report_path(lib: Library, name: str) -> Path:
    path = lib.home / "library" / "reports" / f"{name}-{now().replace(':', '')}.json"
    return path


def _manifest_paper_id(path: str) -> str:
    return f"pdf_{slug(Path(path).stem).replace('-', '_')}"


def _backfill(lib: Library, manifest: Path, *, limit: int | None, dry_run: bool) -> dict:
    rows = read_jsonl(manifest)
    if limit:
        rows = rows[:limit]
    seen: dict[str, str] = {}
    results, duplicates = [], []
    lib.batch_usage = lib.usage()
    for row in rows:
        path = str(row.get("path") or "")
        paper_id = _manifest_paper_id(path)
        digest = str(row.get("md5") or "").lower()
        if digest and digest in seen:
            duplicates.append({"paper_id": paper_id, "same_as": seen[digest]})
            continue
        if digest:
            seen[digest] = paper_id
        record = {**row, "source": "pdf"}
        if not record.get("url") and record.get("doi"):
            record["url"] = f"https://doi.org/{record['doi']}"
        pdf = Path(path).expanduser()
        try:
            if not pdf.is_file():
                results.append(
                    {
                        "paper_id": paper_id,
                        "status": "missing_file",
                        "path": path,
                        "registered": lib.register_source(
                            paper_id, status=NO_SOURCE, record=record
                        ),
                    }
                )
                continue
            results.append(lib.put_source(paper_id, pdf, record=record, dry_run=dry_run))
        except Exception as exc:  # noqa: BLE001 - one bad row must not abort the batch
            results.append(
                {"paper_id": paper_id, "status": "failed", "path": path,
                 "reason": str(exc)}
            )
    lib.batch_usage = None
    if not dry_run:
        lib.maybe_alert(reason="backfill")
    counts: dict[str, int] = {}
    for row in results:
        key = row.get("status") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    report = {
        "schema_version": 1,
        "kind": "library-backfill",
        "at": now(),
        "dry_run": dry_run,
        "manifest": str(manifest),
        "counts": counts,
        "duplicates": duplicates,
        "results": results,
    }
    if not dry_run:
        path = _report_path(lib, "backfill")
        write_json(path, report)
        report["report_path"] = str(path)
    return report


def _decision_rows(lib: Library, path: Path) -> list[dict]:
    rows = read_jsonl(path)
    out = []
    for index, row in enumerate(rows, start=1):
        question = str(row.get("question_id") or row.get("item_id") or "")
        push_id = str(row.get("push_id") or "")
        proposal_id = str(row.get("proposal_id") or "")
        if not question or not proposal_id:
            continue
        push = lib._push_record(push_id) if push_id else {}
        proposal = next(
            (p for p in push.get("proposals") or []
             if p.get("proposal_id") == proposal_id),
            {},
        )
        out.append(
            {
                "决定 ID": str(row.get("decision_id") or f"dec_{question}_{index:02d}"),
                "问题 ID": question,
                "时间": str(row.get("at") or row.get("created_at") or now()),
                "提议 ID": proposal_id,
                "操作": [str(row.get("action") or "拒绝")],
                "目标": str(row.get("target_id") or proposal.get("target_id") or ""),
                "评语": str(row.get("comment") or ""),
                "推送": [{"id": (lib.find("pushes", "推送 ID", push_id) or {}).get("record_id")}]
                if lib.find("pushes", "推送 ID", push_id)
                else [],
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="library", description=__doc__)
    parser.add_argument("--code", help="code directory (default: this checkout)")
    parser.add_argument("--archive", help="archive directory (default: <code>/../horizon-archive)")
    parser.add_argument(
        "--offline", action="store_true",
        help="use the persisted JSON Drive/Base fixture (no lark-cli calls)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("setup", help="create tables and Drive folders (idempotent)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--base-token")

    p = sub.add_parser("put-source", help="upload one source PDF and register it")
    p.add_argument("--paper", required=True)
    p.add_argument("--file")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("ensure-source", help="register on first sight (upload or 无原件)")
    p.add_argument("--paper", required=True)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("put-push", help="upload one delivered push HTML")
    p.add_argument("--push", required=True)
    p.add_argument("--html", required=True)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("resolve", help="materialize a source PDF into the local cache")
    p.add_argument("--paper", required=True)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("quota", help="print usage; --check writes an alert when over")
    p.add_argument("--check", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="accepted for symmetry")
    p.add_argument("--force", action="store_true", help="ignore the 24h cooldown")

    p = sub.add_parser("reconcile", help="compare Drive files with Base rows")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verify", action="store_true", help="download and check md5")

    p = sub.add_parser("evict", help="list or execute offline cleanup (never automatic)")
    p.add_argument("--candidates", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--backup-root", default="~/horizon-offline")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("restore", help="upload an archived source back into the library")
    p.add_argument("--paper", required=True)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("cache", help="local cache maintenance")
    p.add_argument("action", choices=["prune"])
    p.add_argument("--max-size", default="2G")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("backfill", help="batch-import the existing PDF corpus")
    p.add_argument("--manifest", default="fixtures/pdf-manifest.jsonl")
    p.add_argument("--limit", type=int)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("decisions", help="import per-proposal decisions into Base")
    p.add_argument("action", choices=["sync"])
    p.add_argument("--file", default="publisher/decision_rows.jsonl")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("config", help="print effective settings")

    try:
        args = parser.parse_args(layout.cli_argv(argv))
        resolved = layout.resolve_for_cli(args)
    except HostRunError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    layout.export(resolved)
    root = Path(resolved["runtime"])
    try:
        if args.cmd == "setup":
            if args.offline:
                emit(_offline_setup(root, dry_run=args.dry_run))
            else:
                emit(setup_mod.setup(root, dry_run=args.dry_run, base_token=args.base_token))
            return 0

        if args.offline:
            lib, _, _ = _offline_library(root)
        else:
            lib = Library(root)

        if args.cmd == "config":
            emit(config_mod.load(root, force=True) if not args.offline else lib.settings)
            return 0

        if args.cmd == "put-source":
            result = lib.put_source(args.paper, args.file, dry_run=args.dry_run)
            emit(result)
            return 0 if result.get("ok") else (3 if result.get("status") == "quota_blocked" else 1)

        if args.cmd == "ensure-source":
            result = lib.ensure_source(args.paper, dry_run=args.dry_run)
            emit(result)
            return 0 if result.get("ok") else 1

        if args.cmd == "put-push":
            result = lib.put_push(args.push, args.html, dry_run=args.dry_run)
            emit(result)
            return 0 if result.get("ok") else 1

        if args.cmd == "resolve":
            result = lib.resolve(args.paper, dry_run=args.dry_run)
            emit(result)
            return 0 if result.get("ok") else 1
            return 0

        if args.cmd == "quota":
            usage = lib.usage()
            alert = lib.maybe_alert(reason="quota_check", force=args.force) if args.check else None
            emit({"ok": True, **usage, "alert": (alert or {}).get("path")})
            return 0

        if args.cmd == "reconcile":
            report = lib.reconcile(dry_run=args.dry_run, verify=args.verify)
            emit(report)
            return 0 if report["ok"] else 1

        if args.cmd == "evict":
            if args.execute and not args.dry_run:
                result = lib.evict(
                    execute=True,
                    limit=args.limit,
                    backup_root=args.backup_root,
                )
                emit(result)
                return 0 if result.get("ok") else 1
            candidates = lib.eviction_candidates(limit=args.limit)
            emit(
                {
                    "ok": True,
                    "candidates": candidates,
                    "total_bytes": sum(c["size"] for c in candidates),
                }
            )
            return 0

        if args.cmd == "restore":
            result = lib.restore(args.paper, dry_run=args.dry_run)
            emit(result)
            return 0 if result.get("ok") else (3 if result.get("status") == "quota_blocked" else 1)

        if args.cmd == "cache":
            emit(lib.prune_cache(_size(args.max_size), dry_run=args.dry_run))
            return 0

        if args.cmd == "backfill":
            report = _backfill(
                lib,
                Path(args.manifest),
                limit=args.limit,
                dry_run=args.dry_run,
            )
            emit(report)
            blocked = report["counts"].get("quota_blocked", 0)
            failed = report["counts"].get("conflict", 0)
            return 3 if blocked else (1 if failed else 0)

        if args.cmd == "decisions":
            path = Path(args.file)
            if not path.is_absolute():
                path = root / path
            rows = _decision_rows(lib, path)
            written = [] if args.dry_run else [
                {"决定 ID": row["决定 ID"],
                 "record_id": lib.upsert("decisions", "决定 ID", row["决定 ID"], row)}
                for row in rows
            ]
            emit({"ok": True, "count": len(written), "written": written})
            return 0
    except (LibraryError, config_mod.LibraryConfigError, DriveError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - surface lark-cli/config errors as JSON
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
