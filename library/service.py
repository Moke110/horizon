"""Library service: source PDFs and push artifacts in Drive, registered in Base.

Invariants (contracts/lark-storage.md):
  * File first, Base row second. An orphan file is harmless; a dangling row is not.
  * Idempotency key is (library, business id, md5); same id + different md5 is a
    conflict, never an overwrite.
  * Only rows with 状态=在库 consume quota. 100% blocks new sources, never pushes.
  * Nothing is deleted before it is backed up and md5-verified.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from archive.store import Archive
from common import now, read_json, today, write_json
from library import base as base_mod
from library import config as config_mod
from library.drive import (
    DriveError,
    LarkDrive,
    copy_with_md5,
    md5_file,
    token_from_url,
)

PENDING = "待上传"
IN_LIBRARY = "在库"
NO_SOURCE = "无原件"
ARCHIVED = "已离线归档"

SECTIONS = (
    ("takeaway", "一句话结论"),
    ("background", "背景"),
    ("methods", "方法"),
    ("results", "结果"),
    ("why", "与知识的关系"),
    ("limits", "局限"),
    ("sources", "来源"),
)
SECTION_KEYS = [key for key, _ in SECTIONS]

DEFAULT_BACKUP_ROOT = "~/horizon-offline"
ALERT_COOLDOWN_HOURS = 24

# Lark's drive API rejects files above the tenant's per-file limit (20 MB on
# this tenant, code 1061043). Larger PDFs are stored as ordered parts and
# reassembled on read; everything else about the contract stays the same.
PART_SIZE = 19 * 1024 * 1024
PART_MARK = ".pdf.part"


class LibraryError(RuntimeError):
    pass


# --------------------------------------------------------------------- utils
def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(v) for v in value if v not in (None, ""))
    return str(value)


def _status(row: dict | None) -> str:
    fields = (row or {}).get("fields") or {}
    value = fields.get("状态")
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _links(value) -> list[str]:
    return [
        row.get("id")
        for row in value or []
        if isinstance(row, dict) and row.get("id")
    ]


def _same(left, right) -> bool:
    if isinstance(left, list) or isinstance(right, list):
        if isinstance(left, list) and all(isinstance(v, dict) for v in left):
            return set(_links(left)) == set(_links(right or []))
        if isinstance(right, list) and all(isinstance(v, dict) for v in right):
            return set(_links(right)) == set(_links(left or []))
        return [_text(v) for v in (left or [])] == [_text(v) for v in (right or [])]
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    left_text, right_text = _text(left), _text(right)
    if left_text and right_text and left_text[:19] == right_text[:19] and "T" in left_text:
        return True
    return left_text == right_text


def parse_parts(fields: dict) -> list[dict]:
    raw = fields.get("分片")
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict) and row.get("token")]


def part_names(paper_id: str, fields: dict) -> list[str]:
    parts = parse_parts(fields)
    return [str(p.get("name")) for p in parts] if parts else [f"{paper_id}.pdf"]


def _paper_id_from_name(name: str) -> tuple[str, bool]:
    """Return (business id, is_part) for a file in the source folder."""
    if PART_MARK in name:
        return name.split(PART_MARK)[0], True
    if name.endswith(".pdf"):
        return name[:-4], False
    return name, False


def _emit_timestamp() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S%f")


def _since(hours: int) -> str:
    """Local ISO timestamp ``hours`` ago, same clock as ``common.now()``."""
    return (datetime.now().astimezone() - timedelta(hours=hours)).replace(
        microsecond=0
    ).isoformat()


# ---------------------------------------------------------------- bibliography
def _authors(value) -> str:
    if isinstance(value, list):
        return "; ".join(str(v) for v in value if v)
    return str(value or "")


def source_fields(record: dict) -> dict:
    """Map an archive raw record (or manifest-like dict) onto 原文 columns."""
    year = record.get("year") or record.get("published")
    fields = {
        "标题": record.get("title", ""),
        "作者": _authors(record.get("authors")),
        "期刊": record.get("journal", ""),
        "发表年份": _int(year) if year else None,
        "DOI": record.get("doi", ""),
        "PMID": str(record.get("pmid") or ""),
        "PMCID": str(record.get("pmcid") or ""),
        "arXiv": record.get("arxiv", ""),
        "摘要": record.get("abstract", ""),
        "分类": record.get("category", ""),
        "是否预印本": bool(record.get("is_preprint") or record.get("preprint")),
        "PDF 页数": _int(record.get("pdf_pages") or record.get("pages")) or None,
        "来源": [record.get("source")] if record.get("source") else [],
        "原文来源链接": record.get("url") or record.get("pdf_path") or "",
    }
    return {key: value for key, value in fields.items() if value not in (None, "", [])}


class Library:
    def __init__(
        self,
        home: Path,
        *,
        drive=None,
        base=None,
        settings: dict | None = None,
    ):
        self.home = Path(home)
        self._drive = drive
        self._base = base
        self._settings = settings
        self.batch_usage: dict | None = None

    # ------------------------------------------------------------ plumbing
    @property
    def base(self):
        if self._base is None:
            cfg = base_mod.load_base_config(self.home)
            self._base = base_mod.LarkBaseClient(cfg)
        return self._base

    @property
    def drive(self):
        if self._drive is None:
            self._drive = LarkDrive()
        return self._drive

    @property
    def settings(self) -> dict:
        if self._settings is None:
            self._settings = config_mod.load(self.home, client=self.base)
        return self._settings

    @property
    def tables(self) -> dict:
        return self.settings["tables"]

    def folder(self, kind: str) -> str:
        key = "source_folder" if kind == "source" else "push_folder"
        token = self.settings.get(key) or ""
        if not token:
            raise LibraryError(
                f"Base 配置缺少{'原文' if kind == 'source' else '推送'}目录 token；"
                "先运行 `python -m library.cli setup`"
            )
        return token

    @property
    def cache_root(self) -> Path:
        return self.home / "cache"

    def rows(self, key: str) -> list[dict]:
        return self.base.list(self.tables[key])

    def find(self, key: str, field: str, value) -> dict | None:
        return next(
            (
                row
                for row in self.rows(key)
                if str(row["fields"].get(field) or "") == str(value)
            ),
            None,
        )

    def upsert(self, key: str, id_field: str, id_value: str, fields: dict) -> str:
        table = self.tables[key]
        current = self.find(key, id_field, id_value)
        if not current:
            return self.base.create(table, [{id_field: id_value, **fields}])[0]
        diff = {
            name: value
            for name, value in fields.items()
            if not _same(current["fields"].get(name), value)
        }
        if diff:
            self.base.update(table, {current["record_id"]: diff})
        return current["record_id"]

    # --------------------------------------------------------------- quota
    def usage(self) -> dict:
        sources = [r for r in self.rows(base_mod.SOURCE) if _status(r) == IN_LIBRARY]
        pushes = [r for r in self.rows(base_mod.PUSH) if _status(r) == IN_LIBRARY]
        source_bytes = sum(_int(r["fields"].get("文件大小")) for r in sources)
        push_bytes = sum(_int(r["fields"].get("文件大小")) for r in pushes)
        used = source_bytes + push_bytes
        limit = int(self.settings.get("limit_bytes") or 0)
        percent = round(used / limit * 100, 1) if limit else 0.0
        return {
            "used_bytes": used,
            "source_bytes": source_bytes,
            "push_bytes": push_bytes,
            "limit_bytes": limit,
            "percent": percent,
            "sources": len(sources),
            "pushes": len(pushes),
            "level": self._level(percent),
        }

    def _level(self, percent: float) -> str:
        if percent >= 100:
            return "full"
        if percent >= float(self.settings.get("threshold_pct") or 80):
            return "warn"
        return "ok"

    # ---------------------------------------------------------- alert file
    def referenced(self) -> tuple[set[str], set[str]]:
        """Paper ids and record ids referenced by 报道 rows."""
        table = self.tables.get("papers")
        if not table:
            return set(), set()
        try:
            rows = self.base.list(table)
        except base_mod.LibraryBaseError:
            return set(), set()
        paper_ids = {
            str(row["fields"].get("paper_id"))
            for row in rows
            if row["fields"].get("paper_id")
        }
        record_ids = {
            rid
            for row in rows
            for rid in _links(row["fields"].get("原文"))
        }
        return paper_ids, record_ids

    def eviction_candidates(self, *, limit: int | None = None) -> list[dict]:
        paper_ids, record_ids = self.referenced()
        sources = [r for r in self.rows(base_mod.SOURCE) if _status(r) == IN_LIBRARY]
        candidates = []
        for row in sources:
            fields = row["fields"]
            pid = str(fields.get("原文 ID") or "")
            referenced = pid in paper_ids or row["record_id"] in record_ids
            candidates.append(
                {
                    "kind": "source",
                    "id": pid,
                    "record_id": row["record_id"],
                    "size": _int(fields.get("文件大小")),
                    "referenced": referenced,
                    "added": str(fields.get("入库日期") or ""),
                    "url": str(fields.get("云盘链接") or ""),
                }
            )
        candidates.sort(key=lambda c: (c["referenced"], c["added"]))
        return candidates[:limit] if limit else candidates

    def _instruction(self, usage: dict, candidates: list[dict] | None = None) -> str:
        candidates = self.eviction_candidates() if candidates is None else candidates
        free = [c for c in candidates if not c["referenced"]]
        chosen = free or candidates
        count = len(chosen)
        size_mb = round(sum(c["size"] for c in chosen) / 1024 / 1024, 1)
        backup = f"{DEFAULT_BACKUP_ROOT}/{today()}"
        if count:
            return (
                f"清理 horizon 原文库：把 Base「原文」表中 状态=在库、"
                f"{'未被「报道」引用的 ' if free else ''}{count} 篇（约 {size_mb} MB）"
                f"下载到 `{backup}/`，逐篇校验 md5 一致后，把状态改为 `已离线归档` "
                f"并填写备份位置，再从飞书云盘删除文件；"
                f"完成后报告释放空间与剩余用量（当前 {usage['percent']}%，"
                f"上限 {usage['limit_bytes'] // 1024**3} GB）。"
            )
        return (
            "清理 horizon 原文库：当前没有可清理的在库原文；"
            "先在 Base「配置」表提高容量上限，或手动归档后收缩原文库。"
        )

    def maybe_alert(self, *, reason: str = "", force: bool = False) -> dict | None:
        usage = self.usage()
        if usage["level"] == "ok" and not force:
            return None
        state_path = self.home / "library" / "state.json"
        state = read_json(state_path) or {"schema_version": 1}
        last = state.get("last_alert") or {}
        if (
            not force
            and usage["level"] != "full"
            and last.get("level") == usage["level"]
            and str(last.get("at") or "") > _since(ALERT_COOLDOWN_HOURS)
        ):
            return None
        candidates = self.eviction_candidates()
        alert = {
            "schema_version": 1,
            "created_at": now(),
            "level": usage["level"],
            "reason": reason or usage["level"],
            "used_bytes": usage["used_bytes"],
            "limit_bytes": usage["limit_bytes"],
            "percent": usage["percent"],
            "candidates": {
                "count": len(candidates),
                "bytes": sum(c["size"] for c in candidates),
            },
            "instruction": self._instruction(usage, candidates),
        }
        path = self.home / "library" / "alerts" / f"{_emit_timestamp()}.json"
        write_json(path, alert)
        alert["path"] = str(path)
        state["last_alert"] = {"level": usage["level"], "at": alert["created_at"]}
        write_json(state_path, state)
        return alert

    # ------------------------------------------------------------- sources
    def _existing_source(self, paper_id: str) -> dict | None:
        return self.find(base_mod.SOURCE, "原文 ID", paper_id)

    def _raw_record(self, paper_id: str) -> dict:
        return read_json(Archive(self.home).raw / f"{paper_id}.json") or {}

    def _upload_pdf(
        self, folder: str, paper_id: str, path: Path, expected: str | None = None
    ) -> tuple[str, list[dict], int]:
        """Upload a PDF as one file or as ordered parts; return (url, parts, size)."""
        path = Path(path)
        size = path.stat().st_size
        if size <= PART_SIZE:
            name = f"{paper_id}.pdf"
            found = self.drive.find(folder, name)
            if found and found["type"] != "file":
                raise LibraryError(f"原文目录里 {name} 是 {found['type']}，不是文件")
            if found:
                tmp = self.cache_root / "tmp" / f"{paper_id}.adopt"
                tmp.parent.mkdir(parents=True, exist_ok=True)
                try:
                    self.drive.download(found["token"], tmp)
                    digest = md5_file(tmp)
                finally:
                    tmp.unlink(missing_ok=True)
                if expected and digest != expected:
                    raise LibraryError(
                        f"云盘已有同名文件 {name} 但内容不同；不覆盖"
                    )
                return found["url"], [], size
            uploaded = self.drive.upload(folder, name, path)
            return uploaded["url"], [], size

        parts: list[dict] = []
        index = 0
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(PART_SIZE)
                if not chunk:
                    break
                index += 1
                name = f"{paper_id}{PART_MARK}{index:02d}"
                with tempfile.NamedTemporaryFile(delete=False, suffix=".part") as tf:
                    tf.write(chunk)
                    tmp = Path(tf.name)
                try:
                    found = self.drive.find(folder, name)
                    if found and found["type"] != "file":
                        raise LibraryError(f"原文目录里 {name} 是 {found['type']}，不是文件")
                    if found:
                        probe = self.cache_root / "tmp" / f"{paper_id}.adopt.part"
                        probe.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            self.drive.download(found["token"], probe)
                            same = probe.read_bytes() == chunk
                        finally:
                            probe.unlink(missing_ok=True)
                        if not same:
                            raise LibraryError(
                                f"云盘已有分片 {name} 但内容不同；不覆盖"
                            )
                        uploaded = {"token": found["token"], "url": found["url"]}
                    else:
                        uploaded = self.drive.upload(folder, name, tmp)
                    parts.append(
                        {
                            "i": index,
                            "name": name,
                            "token": uploaded["token"],
                            "url": uploaded["url"],
                            "size": len(chunk),
                        }
                    )
                finally:
                    tmp.unlink(missing_ok=True)
        if not parts:
            raise LibraryError(f"empty PDF: {path}")
        return parts[0]["url"], parts, size

    def _materialize(self, row: dict, target: Path) -> Path:
        """Download a source (single file or ordered parts) into ``target``."""
        fields = row["fields"]
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        parts = parse_parts(fields)
        if not parts:
            token = token_from_url(fields.get("云盘链接"))
            if not token:
                raise LibraryError("云盘链接为空")
            self.drive.download(token, target)
            return target
        ordered = sorted(parts, key=lambda p: int(p.get("i") or 0))
        tmpdir = target.parent / f".{target.name}.parts"
        shutil.rmtree(tmpdir, ignore_errors=True)
        tmpdir.mkdir(parents=True)
        try:
            for part in ordered:
                index = int(part.get("i") or 0)
                self.drive.download(part["token"], tmpdir / f"{index:03d}")
            with target.open("wb") as out:
                for part in ordered:
                    index = int(part.get("i") or 0)
                    out.write((tmpdir / f"{index:03d}").read_bytes())
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return target

    def register_source(self, paper_id: str, *, status: str = NO_SOURCE, record: dict | None = None) -> dict:
        record = record if record is not None else self._raw_record(paper_id)
        existing = self._existing_source(paper_id)
        if existing and _status(existing) not in ("", PENDING):
            return {"ok": True, "paper_id": paper_id, "status": _status(existing), "skipped": True}
        fields = {
            **source_fields(record),
            "状态": [status],
        }
        self.upsert(base_mod.SOURCE, "原文 ID", paper_id, fields)
        return {"ok": True, "paper_id": paper_id, "status": status}

    def put_source(
        self,
        paper_id: str,
        pdf_path: str | Path | None = None,
        *,
        record: dict | None = None,
        dry_run: bool = False,
    ) -> dict:
        record = record if record is not None else self._raw_record(paper_id)
        pdf_path = Path(pdf_path or record.get("pdf_path") or "").expanduser()
        existing = self._existing_source(paper_id)
        status = _status(existing)
        if status == ARCHIVED:
            return {
                "ok": False,
                "paper_id": paper_id,
                "status": "archived",
                "reason": "已离线归档；先 restore 再重新入库",
            }
        if not str(pdf_path) or not pdf_path.is_file():
            if existing:
                return {"ok": True, "paper_id": paper_id, "status": status, "skipped": True}
            return self.register_source(paper_id, status=NO_SOURCE, record=record)

        digest = md5_file(pdf_path)
        size = pdf_path.stat().st_size
        if existing and status == IN_LIBRARY:
            if str(existing["fields"].get("md5") or "") == digest:
                return {"ok": True, "paper_id": paper_id, "status": "skipped", "md5": digest}
            return {
                "ok": False,
                "paper_id": paper_id,
                "status": "conflict",
                "reason": "同一 paper_id 的 md5 变了；不覆盖，等人工确认",
                "md5": digest,
                "recorded_md5": existing["fields"].get("md5"),
            }

        if status != IN_LIBRARY:
            usage = self.batch_usage or self.usage()
            projected = usage["used_bytes"] + size
            if usage["limit_bytes"] and projected > usage["limit_bytes"]:
                alert = self.maybe_alert(reason="quota_blocked", force=True)
                return {
                    "ok": False,
                    "paper_id": paper_id,
                    "status": "quota_blocked",
                    "usage": usage,
                    "alert": (alert or {}).get("path"),
                }

        if dry_run:
            return {
                "ok": True,
                "paper_id": paper_id,
                "status": "planned",
                "bytes": size,
                "md5": digest,
            }

        folder = self.folder("source")
        url, parts, size = self._upload_pdf(folder, paper_id, pdf_path, digest)

        fields = {
            **source_fields(record),
            "状态": [IN_LIBRARY],
            "云盘链接": url,
            "文件大小": size,
            "md5": digest,
            "分片": json.dumps(parts, ensure_ascii=False) if parts else "",
            "入库日期": str((existing or {}).get("fields", {}).get("入库日期") or now()),
        }
        try:
            self.upsert(base_mod.SOURCE, "原文 ID", paper_id, fields)
        except base_mod.LibraryBaseError as exc:
            return {
                "ok": False,
                "paper_id": paper_id,
                "status": "orphan",
                "reason": f"文件已上传但 Base 写入失败：{exc}",
            }

        alert = None
        if self.batch_usage is not None:
            self.batch_usage["used_bytes"] += size
            self.batch_usage["source_bytes"] += size
        else:
            alert = self.maybe_alert(reason="put_source")
        return {
            "ok": True,
            "paper_id": paper_id,
            "status": "uploaded",
            "bytes": size,
            "md5": digest,
            "url": url,
            "parts": len(parts),
            "alert": (alert or {}).get("path"),
        }

    def ensure_source(self, paper_id: str, *, dry_run: bool = False) -> dict:
        """Register a paper on first sight: upload its PDF, else mark 无原件."""
        existing = self._existing_source(paper_id)
        status = _status(existing)
        if status in (IN_LIBRARY, NO_SOURCE, ARCHIVED):
            return {"ok": True, "paper_id": paper_id, "status": status, "skipped": True}
        record = self._raw_record(paper_id)
        pdf = Path(record.get("pdf_path") or "").expanduser()
        if str(pdf) and pdf.is_file():
            return self.put_source(paper_id, pdf, record=record, dry_run=dry_run)
        return self.register_source(paper_id, status=NO_SOURCE, record=record)

    # --------------------------------------------------------------- pushes
    def _push_record(self, push_id: str) -> dict:
        return read_json(Archive(self.home).pushes / f"{push_id}.json") or {}

    def _push_fields(
        self, push: dict, existing: dict | None, url: str, size: int, digest: str
    ) -> dict:
        paper_id = str((push.get("paper") or {}).get("paper_id") or "")
        source = self._existing_source(paper_id) if paper_id else None
        card_id = str(push.get("card_id") or "")
        card_row = self.find("cards", "卡片 ID", card_id) if card_id else None
        sections = push.get("sections") or {}
        fields = {
            "日期": str(push.get("date") or push.get("created_at") or now()),
            "标题": push.get("title", ""),
            "知识卡": [{"id": card_row["record_id"]}] if card_row else [],
            "原文": [{"id": source["record_id"]}] if source else [],
            "云盘链接": url,
            "文件大小": size,
            "md5": digest,
            "入库日期": str((existing or {}).get("fields", {}).get("入库日期") or now()),
            "状态": [IN_LIBRARY],
        }
        for key, label in SECTIONS:
            fields[label] = str(sections.get(key) or "")
        return fields

    def put_push(
        self,
        push_id: str,
        html_path: str | Path,
        *,
        push: dict | None = None,
        dry_run: bool = False,
    ) -> dict:
        push = push or self._push_record(push_id)
        if not push:
            raise LibraryError(f"no local push JSON for {push_id}")
        html_path = Path(html_path)
        if not html_path.is_file():
            raise LibraryError(f"html not found: {html_path}")
        existing = self.find(base_mod.PUSH, "推送 ID", push_id)
        size = html_path.stat().st_size
        digest = md5_file(html_path)
        if existing and _status(existing) == IN_LIBRARY:
            if (
                _int(existing["fields"].get("文件大小")) == size
                and str(existing["fields"].get("md5") or "") == digest
            ):
                return {"ok": True, "push_id": push_id, "status": "skipped"}
            return {
                "ok": False,
                "push_id": push_id,
                "status": "conflict",
                "reason": "同一 push_id 的推送件内容变了；保留旧文件",
            }

        if dry_run:
            return {"ok": True, "push_id": push_id, "status": "planned", "bytes": size}

        folder = self.folder("push")
        name = f"{push_id}.html"
        found = self.drive.find(folder, name)
        if found:
            tmp = self.cache_root / "tmp" / name
            tmp.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.drive.download(found["token"], tmp)
                if md5_file(tmp) != digest:
                    return {
                        "ok": False,
                        "push_id": push_id,
                        "status": "conflict",
                        "reason": "云盘已有同名推送件但内容不同",
                    }
            finally:
                tmp.unlink(missing_ok=True)
            uploaded = {"token": found["token"], "url": found["url"], "size": size}
        else:
            uploaded = self.drive.upload(folder, name, html_path)

        fields = self._push_fields(push, existing, uploaded["url"], size, digest)
        try:
            self.upsert(base_mod.PUSH, "推送 ID", push_id, fields)
        except base_mod.LibraryBaseError as exc:
            return {
                "ok": False,
                "push_id": push_id,
                "status": "orphan",
                "reason": f"文件已上传但 Base 写入失败：{exc}",
                "file_token": uploaded["token"],
            }
        alert = self.maybe_alert(reason="put_push")
        return {
            "ok": True,
            "push_id": push_id,
            "status": "uploaded",
            "bytes": size,
            "url": uploaded["url"],
            "alert": (alert or {}).get("path"),
        }

    # ---------------------------------------------------------------- read
    def resolve(self, paper_id: str, *, dry_run: bool = False) -> dict:
        row = self._existing_source(paper_id)
        if not row:
            raise LibraryError(f"原文库没有 {paper_id}")
        fields = row["fields"]
        status = _status(row)
        expected = str(fields.get("md5") or "")
        cache = self.cache_root / "raw" / f"{paper_id}.pdf"

        if cache.is_file():
            if not expected or md5_file(cache) == expected:
                return {"ok": True, "paper_id": paper_id, "status": status,
                        "path": str(cache), "cached": True, "md5": expected}

        if dry_run:
            return {"ok": True, "paper_id": paper_id, "status": status,
                    "path": str(cache), "cached": False, "dry_run": True,
                    "md5": expected}

        if status == IN_LIBRARY:
            part = cache.with_suffix(".download")
            part.unlink(missing_ok=True)
            try:
                self._materialize(row, part)
                digest = md5_file(part)
                if expected and digest != expected:
                    part.unlink(missing_ok=True)
                    # one retry: a previous download may have been truncated
                    self._materialize(row, part)
                    digest = md5_file(part)
                    if digest != expected:
                        part.unlink(missing_ok=True)
                        raise LibraryError(
                            f"{paper_id} 下载后 md5 不符（{digest} != {expected}）"
                        )
                part.replace(cache)
            finally:
                part.unlink(missing_ok=True)
            return {"ok": True, "paper_id": paper_id, "status": status,
                    "path": str(cache), "cached": False, "md5": expected}

        if status == ARCHIVED:
            backup = Path(str(fields.get("备份位置") or "")).expanduser()
            if not str(backup) or not backup.is_file():
                raise LibraryError(f"{paper_id} 已离线归档但备份不在：{backup}")
            copy_with_md5(backup, cache, expected or None)
            return {"ok": True, "paper_id": paper_id, "status": status,
                    "path": str(cache), "cached": False,
                    "md5": expected or md5_file(cache)}

        return {
            "ok": False,
            "paper_id": paper_id,
            "status": "no_source" if status == NO_SOURCE else "pending_upload",
            "reason": f"没有可读原件（状态={status or '空'}）",
        }

    # -------------------------------------------------------------- evict
    def evict(
        self,
        *,
        execute: bool = False,
        limit: int | None = None,
        backup_root: str | Path = DEFAULT_BACKUP_ROOT,
    ) -> dict:
        candidates = self.eviction_candidates(limit=limit)
        if not execute:
            return {
                "ok": True,
                "execute": False,
                "candidates": candidates,
                "total_bytes": sum(c["size"] for c in candidates),
            }
        backup_dir = Path(backup_root).expanduser() / today()
        backup_dir.mkdir(parents=True, exist_ok=True)
        evicted, failed = [], []
        for item in candidates:
            row = next(
                (r for r in self.rows(base_mod.SOURCE) if r["record_id"] == item["record_id"]),
                None,
            )
            if not row:
                continue
            fields = row["fields"]
            expected = str(fields.get("md5") or "")
            target = backup_dir / f"{item['id']}.pdf"
            tmp = backup_dir / f".{item['id']}.part"
            tokens = [p["token"] for p in parse_parts(fields)] or [
                token_from_url(fields.get("云盘链接"))
            ]
            try:
                if not any(tokens):
                    raise LibraryError("云盘链接为空")
                self._materialize(row, tmp)
                digest = md5_file(tmp)
                if expected and digest != expected:
                    raise LibraryError(f"md5 不符（{digest} != {expected}）")
                tmp.replace(target)
                self.base.update(
                    self.tables[base_mod.SOURCE],
                    {
                        row["record_id"]: {
                            "状态": [ARCHIVED],
                            "备份位置": str(target),
                        }
                    },
                )
                for token in tokens:
                    if token:
                        self.drive.delete(token)
                evicted.append(
                    {"id": item["id"], "bytes": item["size"], "backup": str(target)}
                )
            except (DriveError, LibraryError, base_mod.LibraryBaseError) as exc:
                failed.append({"id": item["id"], "reason": str(exc)})
            finally:
                tmp.unlink(missing_ok=True)
        return {
            "ok": not failed,
            "execute": True,
            "backup_dir": str(backup_dir),
            "evicted": evicted,
            "failed": failed,
            "freed_bytes": sum(e["bytes"] for e in evicted),
        }

    def restore(self, paper_id: str, *, dry_run: bool = False) -> dict:
        row = self._existing_source(paper_id)
        if not row:
            raise LibraryError(f"原文库没有 {paper_id}")
        fields = row["fields"]
        if _status(row) != ARCHIVED:
            return {"ok": False, "paper_id": paper_id, "reason": "不在已离线归档状态"}
        backup = Path(str(fields.get("备份位置") or "")).expanduser()
        if not str(backup) or not backup.is_file():
            raise LibraryError(f"备份文件不存在：{backup}")
        digest = md5_file(backup)
        expected = str(fields.get("md5") or "")
        if expected and digest != expected:
            raise LibraryError(f"备份 md5 不符（{digest} != {expected}）")
        if dry_run:
            return {"ok": True, "paper_id": paper_id, "status": "planned",
                    "backup": str(backup), "bytes": backup.stat().st_size}
        folder = self.folder("source")
        url, parts, size = self._upload_pdf(folder, paper_id, backup, digest)
        self.base.update(
            self.tables[base_mod.SOURCE],
            {
                row["record_id"]: {
                    "状态": [IN_LIBRARY],
                    "云盘链接": url,
                    "文件大小": size,
                    "分片": json.dumps(parts, ensure_ascii=False) if parts else "",
                    "备份位置": "",
                }
            },
        )
        return {"ok": True, "paper_id": paper_id, "status": "restored",
                "url": url, "bytes": size}

    # -------------------------------------------------------------- reconcile
    def reconcile(self, *, dry_run: bool = False, verify: bool = False) -> dict:
        report = {
            "schema_version": 1,
            "orphan": [],
            "broken": [],
            "duplicate": [],
            "mismatch": [],
            "actions": [],
            "dry_run": dry_run,
        }
        for kind, key, suffix, id_field in (
            ("source", base_mod.SOURCE, ".pdf", "原文 ID"),
            ("push", base_mod.PUSH, ".html", "推送 ID"),
        ):
            folder = self.folder(kind)
            files = {
                row["name"]: row
                for row in self.drive.list(folder)
                if row["type"] == "file"
            }
            rows = self.rows(key)
            by_id = {
                str(row["fields"].get(id_field) or ""): row
                for row in rows
                if row["fields"].get(id_field)
            }

            groups: dict[str, list[tuple[str, dict]]] = {}
            for name, entry in files.items():
                if kind == "source":
                    business_id, _ = _paper_id_from_name(name)
                elif name.endswith(suffix):
                    business_id = name[: -len(suffix)]
                else:
                    continue
                groups.setdefault(business_id, []).append((name, entry))

            for business_id, group in groups.items():
                if business_id in by_id:
                    continue
                names = [name for name, _ in group]
                report["orphan"].append(
                    {"kind": kind, "id": business_id, "files": names}
                )
                single = next(
                    (entry for name, entry in group if name == f"{business_id}{suffix}"),
                    None,
                )
                if single and not dry_run:
                    suffix_name = ".pdf" if kind == "source" else ".html"
                    tmp = self.cache_root / "tmp" / f"{business_id}{suffix_name}"
                    tmp.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        self.drive.download(single["token"], tmp)
                        digest = md5_file(tmp)
                        if kind == "source":
                            record = self._raw_record(business_id)
                            fields = {
                                **source_fields(record),
                                "云盘链接": single.get("url") or "",
                                "文件大小": tmp.stat().st_size,
                                "md5": digest,
                                "分片": "",
                            }
                            key_name = "原文 ID"
                        else:
                            push = self._push_record(business_id)
                            if not push:
                                fields = {}
                            else:
                                fields = self._push_fields(
                                    push, None, single.get("url") or "",
                                    tmp.stat().st_size, digest,
                                )
                            key_name = "推送 ID"
                        if fields:
                            fields["状态"] = [IN_LIBRARY]
                            fields["入库日期"] = now()
                            self.upsert(key, key_name, business_id, fields)
                            report["actions"].append(
                                {"kind": "register_orphan", "id": business_id}
                            )
                    finally:
                        tmp.unlink(missing_ok=True)

            for business_id, row in by_id.items():
                status = _status(row)
                expected = (
                    part_names(business_id, row["fields"])
                    if kind == "source"
                    else [f"{business_id}{suffix}"]
                )
                missing = [name for name in expected if name not in files]
                if status == IN_LIBRARY and missing:
                    backup = Path(
                        str(row["fields"].get("备份位置") or "")
                    ).expanduser()
                    target_status = "回填" if kind == "push" else PENDING
                    if kind == "source" and str(backup) and backup.is_file():
                        # A backup exists, so this row was already archived;
                        # never send it back to 待上传 (that would re-upload).
                        target_status = ARCHIVED
                        digest = md5_file(backup)
                        expected_md5 = str(row["fields"].get("md5") or "")
                        if expected_md5 and digest != expected_md5:
                            report["mismatch"].append(
                                {"kind": "source-backup", "id": business_id,
                                 "expected": expected_md5, "actual": digest}
                            )
                    report["broken"].append(
                        {"kind": kind, "id": business_id, "missing": missing,
                         "next": target_status}
                    )
                    if not dry_run:
                        self.base.update(
                            self.tables[key],
                            {row["record_id"]: {"状态": [target_status]}},
                        )
                        report["actions"].append(
                            {"kind": "reclassify", "id": business_id, "status": target_status}
                        )
                if status == ARCHIVED and not missing:
                    report["mismatch"].append(
                        {"kind": f"{kind}-state", "id": business_id,
                         "reason": "状态=已离线归档，但云盘文件仍在"}
                    )
                if status == IN_LIBRARY and not missing and verify:
                    tmp = self.cache_root / "tmp" / f"{business_id}{suffix}"
                    tmp.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        self._materialize(row, tmp)
                        digest = md5_file(tmp)
                        expected_md5 = str(row["fields"].get("md5") or "")
                        if expected_md5 and digest != expected_md5:
                            report["mismatch"].append(
                                {"kind": kind, "id": business_id,
                                 "expected": expected_md5, "actual": digest}
                            )
                    finally:
                        tmp.unlink(missing_ok=True)

        # duplicate business ids in Base are always a bug
        for key, id_field in (
            (base_mod.SOURCE, "原文 ID"),
            (base_mod.PUSH, "推送 ID"),
        ):
            seen: set[str] = set()
            for row in self.rows(key):
                business_id = str(row["fields"].get(id_field) or "")
                if not business_id:
                    continue
                if business_id in seen:
                    report["duplicate"].append(
                        {"kind": key, "id": business_id,
                         "record_id": row["record_id"]}
                    )
                seen.add(business_id)

        report["counts"] = {
            name: len(report[name])
            for name in ("orphan", "broken", "duplicate", "mismatch")
        }
        if not dry_run:
            config_id = self.settings.get("config_record_id")
            if config_id:
                try:
                    self.base.update(
                        self.tables[base_mod.CONFIG],
                        {config_id: {"最近对账": now()}},
                    )
                except base_mod.LibraryBaseError:
                    pass
        report["ok"] = not any(report["counts"].values())
        if not dry_run:
            path = (
                self.home / "library" / "reports"
                / f"reconcile-{_emit_timestamp()}.json"
            )
            write_json(path, report)
            report["report_path"] = str(path)
        return report

    # ---------------------------------------------------------------- cache
    def prune_cache(self, max_bytes: int | None = None, *, dry_run: bool = False) -> dict:
        files = [p for p in self.cache_root.rglob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        removed = []
        if max_bytes is not None:
            for path in sorted(files, key=lambda p: p.stat().st_mtime):
                if total <= max_bytes:
                    break
                total -= path.stat().st_size
                removed.append({"path": str(path), "bytes": path.stat().st_size})
                if not dry_run:
                    path.unlink(missing_ok=True)
            if dry_run:
                total = sum(p.stat().st_size for p in files) - sum(
                    row["bytes"] for row in removed
                )
        return {
            "ok": True,
            "before_bytes": sum(p.stat().st_size for p in files),
            "after_bytes": total,
            "removed": removed,
        }
