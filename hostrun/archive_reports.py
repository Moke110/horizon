"""Draft cache and immutable Archive entries for Newsroom reports."""

import hashlib
import json
import re
import uuid

from hostrun.agent_service import CommandError
from hostrun.archive_documents import DOCUMENT_ID, MAX_SOURCE_BYTES, SHA256


REPORT_ID = re.compile(r"r-[0-9a-f]{32}")
UNKNOWN_DOCUMENT = "d-unknown"
ASSESSMENT_BASES = ("methodology", "integrity", "interpretation")


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
        db.execute("""CREATE TABLE IF NOT EXISTS library_report_assessments (
            operation_id TEXT PRIMARY KEY, report_id TEXT NOT NULL, decision TEXT NOT NULL,
            basis TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL)""")
        db.execute("""CREATE INDEX IF NOT EXISTS library_report_assessments_by_report
            ON library_report_assessments(report_id)""")

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

    def apply(self, db, action, command, *, origin, publish):
        if action == "assess":
            return self.assess(db, command, publish=publish)
        if action == "import":
            return self.import_existing(db, command, origin=origin, publish=publish)
        return self.create(db, command, origin=origin, publish=publish)

    def assess(self, db, command, *, publish):
        if set(command) != {"operation_id", "report_id", "decision", "basis", "reason", "evidence"}:
            raise CommandError("invalid_assessment", "重评需要报道 ID、判断、依据类别、理由与证据")
        report = self.read_entry(db, command["report_id"])
        decision, basis = command["decision"], command["basis"]
        if not ((decision in ("questionable", "invalid") and basis in ASSESSMENT_BASES)
                or (decision == "completed" and basis == "correction")):
            raise CommandError("invalid_assessment", "重评依据必须为研究方法错位、诚信缺陷、解读错误或恢复时的误判更正")
        reason = self._text(command["reason"], 2000, "重评理由")
        evidence = self._text(command["evidence"], 4000, "重评证据")
        if publish:
            db.execute("INSERT INTO library_report_assessments VALUES (?,?,?,?,?,?)",
                       (command["operation_id"], report["report_id"], decision, basis, reason, evidence))
        return {**report, "assessment": dict(decision=decision, basis=basis, reason=reason, evidence=evidence)}

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
        original = self.read_draft(db, origin, draft_id)
        identity = uuid.uuid5(uuid.NAMESPACE_URL, f"horizon:archive-report:{command['operation_id']}:{draft_id}")
        report_id = "r-" + identity.hex
        record = self.entry(report_id, draft_id, command, covered, self.path(report_id))
        return self.register(db, record, original, publish=publish)

    def import_existing(self, db, command, *, origin, publish):
        fields = command.get("report")
        if not isinstance(fields, dict) or set(command) - {"operation_id", "report", "draft_id"}:
            raise CommandError("invalid_report", "导入需要已有的报道条目")
        report_id, digest = fields.get("report_id"), fields.get("sha256")
        if (not isinstance(report_id, str) or not REPORT_ID.fullmatch(report_id)
                or not isinstance(digest, str) or not SHA256.fullmatch(digest)):
            raise CommandError("invalid_report", "导入需要原报道 ID 和 SHA-256")
        imported_docs = fields.get("covered_docs")
        if (not isinstance(imported_docs, list) or not imported_docs
                or any(not isinstance(item, str)
                       or (item != UNKNOWN_DOCUMENT and not DOCUMENT_ID.fullmatch(item)) for item in imported_docs)):
            raise CommandError("invalid_report", "导入需要有序的被报道文档 ID")
        covered = [self.imported_document_id(db, identity) for identity in imported_docs]
        draft_id = command.get("draft_id")
        if draft_id is not None and draft_id != digest:
            raise CommandError("hash_mismatch", "导入的 HTML 与原报道 SHA-256 不一致")
        original = self.read_draft(db, origin, draft_id) if draft_id is not None else None
        record = self.entry(report_id, digest, fields, covered, self.path(report_id) if original is not None else None)
        return self.register(db, record, original, publish=publish)

    def imported_document_id(self, db, identity):
        if identity == UNKNOWN_DOCUMENT:
            return identity
        try:
            self.sources.read_in_transaction(db, identity)
        except CommandError as error:
            if error.code != "not_found":
                raise
            return UNKNOWN_DOCUMENT
        return identity

    @staticmethod
    def read_draft(db, origin, draft_id):
        if not isinstance(draft_id, str) or not SHA256.fullmatch(draft_id):
            raise CommandError("invalid_report", "需要当前会话缓存的报道稿件")
        row = db.execute("SELECT html FROM library_report_drafts WHERE origin=? AND draft_id=?",
                         (origin, draft_id)).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != draft_id:
            raise CommandError("report_unavailable", "当前会话缓存的报道稿件不可用")
        return row[0]

    def entry(self, report_id, digest, fields, covered, path):
        return {
            "report_id": report_id, "status": "completed",
            "title": self._text(fields.get("title"), 500, "标题"),
            "summary": self._text(fields.get("summary"), 2000, "简介"),
            "sha256": digest, "file_path": str(path) if path is not None else None,
            "covered_docs": covered,
        }

    def register(self, db, record, original, *, publish):
        report_id = record["report_id"]
        if db.execute("SELECT 1 FROM library_archive_reports WHERE report_id=?", (report_id,)).fetchone():
            raise CommandError("immutable_report", "已登记报道不能修改")
        if publish:
            if original is not None:
                self.files.publish(self.path(report_id), original, record["sha256"])
            db.execute("INSERT INTO library_archive_reports VALUES (?,?)",
                       (report_id, json.dumps(record, ensure_ascii=False)))
        return record

    def list(self):
        with self.connect() as db:
            self.initialize(db)
            return [self.project_assessment(db, json.loads(record)) for (record,) in db.execute(
                "SELECT record FROM library_archive_reports ORDER BY rowid DESC")]

    @staticmethod
    def read_entry(db, report_id):
        if not isinstance(report_id, str) or not REPORT_ID.fullmatch(report_id):
            raise CommandError("invalid_report", "需要有效的报道 ID")
        row = db.execute("SELECT record FROM library_archive_reports WHERE report_id=?",
                         (report_id,)).fetchone()
        if not row:
            raise CommandError("not_found", "未找到报道")
        return json.loads(row[0])

    @staticmethod
    def project_assessment(db, report):
        row = db.execute("""SELECT decision, basis, reason, evidence FROM library_report_assessments
            WHERE report_id=? ORDER BY rowid DESC LIMIT 1""", (report["report_id"],)).fetchone()
        if row is None:
            return report
        decision, basis, reason, evidence = row
        return {**report, "assessment": dict(decision=decision, basis=basis, reason=reason, evidence=evidence)}

    def view(self, report_id):
        with self.connect() as db:
            self.initialize(db)
            return self.project_assessment(db, self.read_entry(db, report_id))

    def open(self, report_id):
        report = self.view(report_id)
        if report["file_path"] is None:
            return {"report": report, "html": None}
        try:
            html = self.files.read_file(self.path(report_id), report["sha256"]).decode("utf-8")
        except (CommandError, UnicodeError):
            html = None
        return {"report": report, "html": html}
