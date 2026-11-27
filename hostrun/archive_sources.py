"""Archive source records and references to the existing material store."""

import json
import uuid
from urllib.parse import urlsplit

from hostrun.agent_service import CommandError
from hostrun.archive_documents import ArchiveDocuments, DOCUMENT_ID
from hostrun.library_tags import new_test_tags

_FIELDS = {"title", "label", "url", "material_id"}


class ArchiveSources:
    def __init__(self, connect, materials):
        self._connect = connect
        self._materials = materials
        self.documents = ArchiveDocuments(materials.home, connect)

    @staticmethod
    def _initialized(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE name='library_archive_sources'").fetchone()

    def initialize(self, db):
        if self._initialized(db):
            return
        db.execute("""CREATE TABLE library_archive_sources(
            source_id TEXT PRIMARY KEY, record TEXT NOT NULL)""")
        rows = db.execute("SELECT result FROM voyager_materials ORDER BY rowid").fetchall()
        for (payload,) in rows:
            material = json.loads(payload)
            identity = material["material_id"]
            url = ((material.get("web") or {}).get("url")
                   or (material.get("academic") or {}).get("url"))
            self._save(db, {"source_id": identity, "version": 1, "title": material["title"],
                            "label": material["source"]["label"], "material_id": identity,
                            "url": url, "status": "active"})
            self._materials._bind_reference(db, material, "archive:" + identity)

    def _ensure_initialized(self, db):
        if not self._initialized(db):
            db.execute("BEGIN IMMEDIATE")
            self.initialize(db)

    def list(self):
        with self._connect() as db:
            self._ensure_initialized(db)
            rows = db.execute("""SELECT record FROM library_archive_sources
                WHERE json_extract(record, '$.status')='active' ORDER BY rowid DESC""").fetchall()
        return [json.loads(row[0]) for row in rows]

    def view(self, source_id):
        with self._connect() as db:
            self._ensure_initialized(db)
            return self.read_in_transaction(db, source_id)

    def _original(self, source_id, *, token, task_id, include_pdf):
        reference = self.view(source_id)
        if DOCUMENT_ID.fullmatch(source_id):
            return {"document": reference}, self.documents.read(reference) if include_pdf else None
        material_id = reference["material_id"]
        if material_id:
            try:
                package = self._materials.get_package(material_id, token=token, task_id=task_id)
                pdf = self._materials.pdf_original(material_id, token=token) if include_pdf and package.get("pdf") else None
                return package, pdf
            except CommandError as error:
                if error.code != "not_found" or not reference["url"]:
                    raise
        return {"source_url": reference["url"]}, None

    def open(self, source_id, *, token, task_id=None):
        package, _ = self._original(source_id, token=token, task_id=task_id, include_pdf=False)
        return package

    def original(self, source_id, *, token, task_id=None):
        return self._original(source_id, token=token, task_id=task_id, include_pdf=True)

    def pdf(self, source_id, *, token):
        reference = self.view(source_id)
        if DOCUMENT_ID.fullmatch(source_id):
            if reference["media_type"] != "application/pdf":
                raise CommandError("invalid_document", "源文档不是 PDF")
            return self.documents.read(reference)
        return self._materials.pdf_original(reference["material_id"], token=token)

    @staticmethod
    def read_in_transaction(db, source_id):
        if not isinstance(source_id, str):
            raise CommandError("invalid_command", "需要资料库来源标识")
        row = db.execute("SELECT record FROM library_archive_sources WHERE source_id=?",
                         (source_id,)).fetchone()
        if row:
            record = json.loads(row[0])
            if record["status"] == "active":
                return record
        raise CommandError("not_found", "未找到资料库来源")

    @staticmethod
    def _save(db, record):
        db.execute("""INSERT INTO library_archive_sources VALUES(?,?)
            ON CONFLICT(source_id) DO UPDATE SET record=excluded.record""",
                   (record["source_id"], json.dumps(record, ensure_ascii=False)))

    def apply(self, db, action, command, *, publish=False):
        if action == "register":
            record = self.documents.prepare(db, command, publish=publish)
            db.execute("INSERT INTO library_archive_sources VALUES(?,?)",
                       (record["source_id"], json.dumps(record, ensure_ascii=False)))
            return record
        allowed = {"operation_id", "schema_version"}
        if action == "create":
            allowed.add("test_data")
        if action != "create":
            allowed |= {"source_id", "expected_version"}
        if action != "delete":
            allowed |= _FIELDS
        if set(command) - allowed:
            raise CommandError("invalid_command", "资料库来源包含未知字段")
        if "schema_version" in command and (
            type(command["schema_version"]) is not int or command["schema_version"] != 1
        ):
            raise CommandError("invalid_command", "不支持的资料库来源版本")
        if action == "create":
            record = {"source_id": str(uuid.uuid4()), "version": 0, "status": "active", **new_test_tags(command)}
        else:
            record = self.read_in_transaction(db, command.get("source_id"))
            if action == "update" and (DOCUMENT_ID.fullmatch(record["source_id"]) or any(
                field in command and command[field] != record.get(field) for field in ("material_id", "url")
            )):
                raise CommandError("immutable_document", "已登记的文档不能更换原文")
            self.check_write_lock(db, record["source_id"])
            expected = command.get("expected_version")
            if type(expected) is not int or record["version"] != expected:
                raise CommandError("version_conflict", "资料库来源版本已变更")

        owner = "archive:" + record["source_id"] + ":"
        if action == "delete":
            record["status"] = "deleted"
            self._drop_references(db, owner)
        else:
            record = self._validated(db, {**record, **{key: command[key] for key in _FIELDS & command.keys()}})
            material_id = record["material_id"]
            self._drop_references(db, owner)
            if material_id:
                (payload,) = db.execute("SELECT result FROM voyager_materials WHERE material_id=?",
                                        (material_id,)).fetchone()
                self._materials._bind_reference(db, json.loads(payload), owner[:-1])
        record["version"] += 1
        self._save(db, record)
        return record

    @staticmethod
    def check_write_lock(db, source_id):
        if db.execute("SELECT 1 FROM librarian_term_locks WHERE term_id=?",
                      ("archive:" + source_id,)).fetchone():
            raise CommandError("source_locked", "资料库来源正在等待确认")

    @staticmethod
    def _drop_references(db, owner):
        db.execute("DELETE FROM voyager_material_references WHERE owner_id GLOB ?", (owner + "*",))

    @staticmethod
    def _validated(db, record):
        for name, limit in (("title", 500), ("label", 2000)):
            value = record.get(name)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise CommandError("invalid_command", "需要有效的标题和来源说明")
            record[name] = value.strip()
        material_id = record.get("material_id")
        if material_id is not None and (
            not isinstance(material_id, str)
            or not db.execute("SELECT 1 FROM voyager_materials WHERE material_id=?", (material_id,)).fetchone()
        ):
            raise CommandError("not_found", "未找到所引用的原文文件")
        url = record.get("url")
        if url is not None:
            try:
                if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() for c in url):
                    raise ValueError("Invalid URL")
                parts = urlsplit(url)
                if parts.scheme not in ("https", "http") or not parts.hostname or parts.username is not None:
                    raise ValueError("Invalid URL")
            except ValueError as error:
                raise CommandError("invalid_command", "原始来源链接须为 HTTP 或 HTTPS 地址") from error
        if not material_id and not url:
            raise CommandError("invalid_command", "需要原始来源链接或已有原文文件")
        record.update(material_id=material_id, url=url)
        return record
