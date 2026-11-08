"""Ingest the PDF corpus into ``archive/raw`` with page-level anchors.

The manifest (``fixtures/pdf-manifest.jsonl``) is authoritative for identity
and bibliography: PDF ``/Title``, printed DOIs and ``/CreationDate`` are known
to be unreliable for this corpus.

Layout written by this module (through :class:`archive.store.Archive`):

- ``archive/raw/<paper_id>.json``       metadata (source ``pdf``)
- ``archive/raw/text/<paper_id>.txt``   full text, pages joined by ``PAGE_SEP``
- ``archive/raw/pages/<paper_id>.json`` page anchors into that text
- ``archive/reports/pdf-ingest-<ts>.json`` run report

Every step is idempotent: a complete paper is skipped on rerun, an incomplete
one is repaired in place (missing/empty text or pages sidecar).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from archive.store import Archive, ArchiveError
from common import emit, now, paper_id as make_source_paper_id, slug, write_json

PAGE_SEP = "\n\n"
DEFAULT_MANIFEST = "fixtures/pdf-manifest.jsonl"


# ------------------------------------------------------------------ identity
def paper_id_for(pdf_path: str | Path) -> str:
    """Stable paper id from the PDF file stem.

    Mirrors the convention already used by the approved theme inventory:
    ``2025_Anders_Bench-LinBaseline.pdf`` -> ``pdf_2025_anders_bench_linbaseline``.
    """
    stem = Path(str(pdf_path)).stem
    return make_source_paper_id("pdf", slug(stem).replace("-", "_"))


def _normalize_doi(doi: str) -> str:
    return (doi or "").strip().lower()


def _normalize_arxiv(arxiv: str) -> str:
    value = (arxiv or "").strip()
    for prefix in ("arXiv:", "arxiv:"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.strip().lower()


def source_id_for(row: dict) -> str:
    """Dedupe key inside source ``pdf``: DOI, then PMID, then arXiv, then md5."""
    doi = _normalize_doi(row.get("doi", ""))
    if doi:
        return doi
    pmid = str(row.get("pmid") or "").strip()
    if pmid:
        return f"pmid:{pmid}"
    arxiv = _normalize_arxiv(row.get("arxiv", ""))
    if arxiv:
        return f"arxiv:{arxiv}"
    md5 = str(row.get("md5") or "").strip().lower()
    if md5:
        return f"md5:{md5}"
    raise ArchiveError(f"manifest row has no identifier: {row.get('path')}")


# -------------------------------------------------------------------- pages
def build_text_and_anchors(pages: Iterable[str]) -> tuple[str, list[dict]]:
    """Join page texts and return ``[start, end)`` anchors into the join."""
    texts = list(pages)
    anchors: list[dict] = []
    cursor = 0
    for number, text in enumerate(texts, start=1):
        text = text or ""
        start, end = cursor, cursor + len(text)
        anchors.append(
            {
                "page": number,
                "start": start,
                "end": end,
                "chars": len(text),
                "text_empty": not text.strip(),
            }
        )
        cursor = end + len(PAGE_SEP)
    return PAGE_SEP.join(texts), anchors


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_pages(path: Path) -> list[str]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - environment guard
        raise ArchiveError("PyMuPDF is required for PDF ingestion") from exc
    # Corpus PDFs contain harmless broken color-space references; keep CLI
    # output machine-readable by suppressing MuPDF's stderr chatter.
    try:
        fitz.TOOLS.mupdf_display_errors(False)
    except Exception:  # pragma: no cover - version guard
        pass
    doc = fitz.open(path)
    try:
        return [page.get_text("text") for page in doc]
    finally:
        doc.close()


# -------------------------------------------------------------------- record
def _authors(raw: str) -> list[str]:
    return [name.strip() for name in (raw or "").split(",") if name.strip()]


def _record(row: dict, paper_id: str, source_id: str, digest: str) -> dict:
    doi = _normalize_doi(row.get("doi", ""))
    record = {
        "schema_version": 1,
        "paper_id": paper_id,
        "source": "pdf",
        "source_id": source_id,
        "title": row.get("title", ""),
        "authors": _authors(row.get("authors", "")),
        "journal": row.get("journal", ""),
        "year": row.get("year"),
        "doi": doi,
        "pmid": str(row.get("pmid") or ""),
        "pmcid": str(row.get("pmcid") or ""),
        "arxiv": row.get("arxiv", ""),
        "md5": digest,
        "category": row.get("category", ""),
        "pdf_path": row.get("path", ""),
        "pages": row.get("pages"),
        "pdf_pages": row.get("pages"),
        "is_preprint": bool(row.get("preprint")),
        "url": f"https://doi.org/{doi}" if doi else row.get("path", ""),
        "notes": row.get("notes", ""),
        "fetched_at": now(),
    }
    return record


def _complete(store: Archive, paper_id: str, md5: str) -> tuple[bool, bool, bool]:
    raw_ok = (store.raw / f"{paper_id}.json").exists()
    text_path = store.raw_text / f"{paper_id}.txt"
    text_ok = text_path.exists() and bool(
        text_path.read_text(encoding="utf-8").strip()
    )
    pages = store.read_pages(paper_id)
    pages_ok = bool(
        pages
        and pages.get("md5") == md5
        and pages.get("page_anchors")
        and len(pages["page_anchors"]) == pages.get("pdf_pages")
    )
    return raw_ok, text_ok, pages_ok


# ----------------------------------------------------------------------- run
def _read_manifest(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def run(
    home_dir: str | Path,
    manifest: str | Path = DEFAULT_MANIFEST,
    *,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    home_path = Path(home_dir).expanduser().resolve()
    manifest_path = Path(manifest).expanduser().resolve()
    store = Archive(home_path)
    rows = _read_manifest(manifest_path)
    if limit is not None:
        rows = rows[:limit]

    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "pdf-ingest",
        "started_at": now(),
        "home": str(home_path),
        "manifest": str(manifest_path),
        "dry_run": dry_run,
        "counts": {
            "total": len(rows),
            "ingested": 0,
            "repaired": 0,
            "skipped": 0,
            "failed": 0,
            "planned": 0,
        },
        "results": [],
        "failures": [],
    }

    for row in rows:
        raw_path = str(row.get("path", ""))
        pdf_path = Path(raw_path).expanduser()
        result: dict[str, Any] = {"path": raw_path}
        try:
            paper_id = paper_id_for(raw_path)
            source_id = source_id_for(row)
            result.update({"paper_id": paper_id, "source_id": source_id})

            if not pdf_path.exists():
                raise ArchiveError(f"PDF not found: {pdf_path}")

            if dry_run:
                result["status"] = "planned"
                report["counts"]["planned"] += 1
                report["results"].append(result)
                continue

            digest = _md5(pdf_path)
            expected = str(row.get("md5") or "").strip().lower()
            if expected and expected != digest:
                raise ArchiveError(
                    f"md5 mismatch: manifest {expected}, file {digest}"
                )

            existing = store.find_paper("pdf", source_id)
            if existing and existing["paper_id"] != paper_id:
                raise ArchiveError(
                    f"source_id {source_id} already stored as {existing['paper_id']}"
                )

            raw_ok, text_ok, pages_ok = _complete(store, paper_id, digest)
            if raw_ok and text_ok and pages_ok:
                result["status"] = "skipped"
                report["counts"]["skipped"] += 1
                report["results"].append(result)
                continue

            page_texts = _extract_pages(pdf_path)
            if not any(text.strip() for text in page_texts):
                raise ArchiveError("PDF has no extractable text")
            text, anchors = build_text_and_anchors(page_texts)

            record = _record(row, paper_id, source_id, digest)
            record["text"] = text
            record["pdf_pages"] = len(page_texts)
            store.add_paper(record)
            store.put_pages(
                paper_id,
                {
                    "schema_version": 1,
                    "kind": "page-anchors",
                    "paper_id": paper_id,
                    "text_path": f"raw/text/{paper_id}.txt",
                    "md5": digest,
                    "page_sep": PAGE_SEP,
                    "pdf_pages": len(page_texts),
                    "text_chars": len(text),
                    "extracted_at": now(),
                    "page_anchors": anchors,
                },
            )

            result.update(
                {
                    "status": "repaired" if raw_ok else "ingested",
                    "pdf_pages": len(page_texts),
                    "text_chars": len(text),
                    "empty_pages": sum(1 for a in anchors if a["text_empty"]),
                }
            )
            report["counts"][result["status"]] += 1
        except Exception as exc:  # noqa: BLE001 - one bad row must not abort the batch
            result["status"] = "failed"
            result["reason"] = str(exc)
            report["counts"]["failed"] += 1
            report["failures"].append(result)
        report["results"].append(result)

    report["finished_at"] = now()
    if not dry_run:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        report_path = store.root / "reports" / f"pdf-ingest-{stamp}.json"
        report["report_path"] = str(report_path)
        write_json(report_path, report)
    return report


def summary(report: dict) -> dict:
    return {
        "ok": report["counts"]["failed"] == 0,
        "counts": report["counts"],
        "report": report.get("report_path"),
        "failures": report["failures"],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys
    from pathlib import Path

    from hostrun import layout
    from hostrun.base import HostRunError

    parser = argparse.ArgumentParser(prog="pdf_ingest", description=__doc__)
    parser.add_argument("--code", help="code directory (default: this checkout)")
    parser.add_argument("--archive", help="archive directory (default: <code>/../horizon-archive)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="ingest a manifest of PDFs")
    p.add_argument("--manifest", default=DEFAULT_MANIFEST)
    p.add_argument("--limit", type=int)
    p.add_argument("--dry-run", action="store_true")
    try:
        args = parser.parse_args(layout.cli_argv(argv))
        resolved = layout.resolve_for_cli(args)
    except HostRunError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    layout.export(resolved)

    if args.cmd != "run":  # pragma: no cover - argparse enforces this
        parser.error(f"unknown command {args.cmd}")

    report = run(
        Path(resolved["runtime"]),
        args.manifest,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    emit(summary(report))
    return 0 if report["counts"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
