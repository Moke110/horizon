"""Draft cache and immutable Archive entries for Newsroom reports."""

import hashlib
import json
import re
import uuid

from hostrun.agent_service import CommandError
from hostrun.archive_documents import DOCUMENT_ID, MAX_SOURCE_BYTES


REPORT_ID = re.compile(r"r-[0-9a-f]{32}")


class ArchiveReports:
    def __init__(self, connect, sources):
        self.connect = connect
        self.sources = sources
        self.files = sources.documents

    @staticmethod
    def initialize(db):
        db.execute("""CREATE TABLE IF NOT EXISTS library_archive_reports (
            report_id TEXT PRIMARY KEY, record TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS library_report_drafts (
            origin TEXT NOT NULL, draft_id TEXT NOT NULL, html BLOB NOT NULL,
            PRIMARY KEY (origin, draft_id))""")

    def stage(self, html, origin):
        if not isinstance(html, str) or not html.strip():
            raise CommandError("invalid_report", "需要 Newsroom 生成的 HTML 原文")
        try:
            original = html.encode("utf-8")
        except UnicodeError as error:
            raise CommandError("invalid_report", "报道 HTML 必须使用有效的 Unicode") from error
        if len(original) > MAX_SOURCE_BYTES:
            raise CommandError("invalid_report", "报道 HTML 不能超过 10 MiB")
        digest = hashlib.sha256(original).hexdigest()
        with self.connect() as db:
            self.initialize(db)
            db.execute("INSERT OR IGNORE INTO library_report_drafts VALUES (?,?,?)",
                       (origin, digest, original))
        return {"draft_id": digest, "sha256": digest}

    @staticmethod
    def _text(value, limit, field):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise CommandError("invalid_report", f"需要有效的报道{field}")
        return value.strip()

    def path(self, report_id):
        return self.files.original_directory("reports") / f"{report_id}.html"

    def create(self, db, command, *, origin, publish):
        fields = {"operation_id", "draft_id", "title", "summary", "covered_docs"}
        if set(command) != fields:
            raise CommandError("invalid_report", "报道登记需要标题、简介、缓存稿件和被报道文档")
        covered = command["covered_docs"]
        if (not isinstance(covered, list) or not covered
                or any(not isinstance(item, str) or not DOCUMENT_ID.fullmatch(item) for item in covered)
                or len(set(covered)) != len(covered)):
            raise CommandError("invalid_report", "需要一项或多项不同的被报道文档 ID")
        for identity in covered:
            self.sources.read_in_transaction(db, identity)
        draft_id = command["draft_id"]
        if not isinstance(draft_id, str) or not re.fullmatch(r"[0-9a-f]{64}", draft_id):
            raise CommandError("invalid_report", "需要当前会话缓存的报道稿件")
        row = db.execute("SELECT html FROM library_report_drafts WHERE origin=? AND draft_id=?",
                         (origin, draft_id)).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != draft_id:
            raise CommandError("report_unavailable", "当前会话缓存的报道稿件不可用")
        identity = uuid.uuid5(uuid.NAMESPACE_URL, f"horizon:archive-report:{command['operation_id']}:{draft_id}")
        report_id = "r-" + identity.hex
        path = self.path(report_id)
        record = {
            "report_id": report_id, "status": "completed",
            "title": self._text(command["title"], 500, "标题"),
            "summary": self._text(command["summary"], 2000, "简介"),
            "sha256": draft_id, "file_path": str(path), "covered_docs": covered,
        }
        if db.execute("SELECT 1 FROM library_archive_reports WHERE report_id=?", (report_id,)).fetchone():
            raise CommandError("immutable_report", "已登记报道不能修改")
        if publish:
            self.files.publish(path, row[0], draft_id)
            db.execute("INSERT INTO library_archive_reports VALUES (?,?)",
                       (report_id, json.dumps(record, ensure_ascii=False)))
        return record

    def list(self):
        with self.connect() as db:
            self.initialize(db)
            return [json.loads(record) for (record,) in db.execute(
                "SELECT record FROM library_archive_reports ORDER BY rowid DESC")]

    def view(self, report_id):
        if not isinstance(report_id, str) or not REPORT_ID.fullmatch(report_id):
            raise CommandError("invalid_report", "需要有效的报道 ID")
        with self.connect() as db:
            self.initialize(db)
            row = db.execute("SELECT record FROM library_archive_reports WHERE report_id=?",
                             (report_id,)).fetchone()
        if not row:
            raise CommandError("not_found", "未找到报道")
        return json.loads(row[0])

    def open(self, report_id):
        report = self.view(report_id)
        try:
            html = self.files.read_file(self.path(report_id), report["sha256"]).decode("utf-8")
        except (CommandError, UnicodeError):
            html = None
        return {"report": report, "html": html}
