"""Cache and persist one original file for each Archive document."""

import hashlib
import os
import re
import tempfile
import uuid
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urljoin

from hostrun.agent_service import CommandError
from hostrun.cleanup import InstanceBusy, InstanceInvalid
from hostrun.layout import archive_for_runtime
from hostrun.library_tags import new_test_tags
from hostrun.managed_import import managed_paths, store, _blocks
from hostrun.voyager_subscriptions import fetch_public
from hostrun.voyager_web import public_address

MAX_SOURCE_BYTES = 10 * 1024 * 1024
EXTENSIONS = {
    "application/pdf": "pdf", "text/html": "html", "application/xhtml+xml": "xhtml",
    "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp",
    "image/bmp": "bmp", "image/tiff": "tiff", "image/avif": "avif", "image/svg+xml": "svg",
}
MEDIA_TYPES = {suffix: media_type for media_type, suffix in EXTENSIONS.items()}
DOCUMENT_ID = re.compile(r"d-[0-9a-f]{32}")
SHA256 = re.compile(r"[0-9a-f]{64}")


class ArchiveDocuments:
    def __init__(self, home, connect):
        self.home = Path(home)
        self.connect = connect

    @staticmethod
    def initialize(db):
        db.execute("CREATE TABLE IF NOT EXISTS library_source_cache (file_id TEXT PRIMARY KEY, original BLOB NOT NULL)")

    def cache_original(self, data, media_type):
        extension = EXTENSIONS.get(media_type) if isinstance(media_type, str) else None
        if not extension or not isinstance(data, bytes) or not 0 < len(data) <= MAX_SOURCE_BYTES:
            raise CommandError("invalid_document", "请选择不超过 10 MiB 的 PDF、图片或 HTML 源文件")
        digest = hashlib.sha256(data).hexdigest()
        file_id = f"{digest}.{extension}"
        with self.connect() as db:
            self.initialize(db)
            db.execute("INSERT OR IGNORE INTO library_source_cache VALUES(?,?)", (file_id, data))
        return {"file_id": file_id, "sha256": digest, "media_type": media_type, "byte_length": len(data)}

    def download(self, url):
        try:
            for _ in range(5):
                public_address(url)
                status, headers, data = fetch_public(
                    url, {"Accept": ", ".join(EXTENSIONS)}, byte_limit=MAX_SOURCE_BYTES,
                )
                headers = {key.lower(): value for key, value in headers.items()}
                if status in (301, 302, 303, 307, 308) and headers.get("location"):
                    url = urljoin(url, headers["location"])
                    continue
                if status == 200:
                    return self.cache_original(data, headers.get("content-type", "").split(";")[0].strip().lower())
                break
        except (OSError, ValueError, HTTPException):
            pass
        raise CommandError("source_unavailable", "无法下载公开的源文档")

    @staticmethod
    def _text(command, field, limit, *, optional=False):
        value = command.get(field)
        if value is None and optional:
            return None
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise CommandError("invalid_document", "需要有效的文档标题、简介或学术引用")
        return value.strip()

    def _originals(self):
        managed = managed_paths(self.home)
        archive = managed[1] if managed else archive_for_runtime(self.home)
        return (archive or self.home / "archive") / "originals" / "documents"

    def prepare(self, db, command, *, publish):
        if set(command) - {"operation_id", "title", "summary", "source_files", "citation", "test_data"}:
            raise CommandError("invalid_document", "文档登记包含未知字段")
        files = command.get("source_files")
        if not isinstance(files, list) or len(files) != 1:
            raise CommandError("invalid_document", "首次登记需要恰好一份源文件")
        digest, media_type, original = self._cached_original(db, files[0])
        identity = f"horizon:archive-document:{digest}:{command['operation_id']}"
        source_id = "d-" + uuid.uuid5(uuid.NAMESPACE_URL, identity).hex
        path = self._path(source_id, media_type)
        record = self._record(command, source_id, digest, media_type, str(path))
        if publish:
            self.publish(path, original, digest)
        return {**record, **new_test_tags(command)}

    def prepare_import(self, command):
        fields = command.get("document")
        if not isinstance(fields, dict) or set(command) - {"operation_id", "document", "test_data"}:
            raise CommandError("invalid_document", "导入需要已有的文档条目")
        source_id = fields.get("source_id")
        digest = fields.get("sha256")
        if (not isinstance(source_id, str) or not DOCUMENT_ID.fullmatch(source_id)
                or not isinstance(digest, str) or not SHA256.fullmatch(digest)):
            raise CommandError("invalid_document", "导入需要既有的文档 ID 和 SHA-256")
        media_type = fields.get("media_type")
        if media_type is not None and (not isinstance(media_type, str) or media_type not in EXTENSIONS):
            raise CommandError("invalid_document", "不支持的源文档格式")
        return {**self._record(fields, source_id, digest, media_type, None), **new_test_tags(command)}

    def restore(self, db, command, record, *, publish):
        if set(command) - {"operation_id", "source_id", "expected_version", "file_id"}:
            raise CommandError("invalid_document", "文档恢复包含未知字段")
        digest, media_type, original = self._cached_original(db, command.get("file_id"))
        if digest != record["sha256"]:
            raise CommandError("hash_mismatch", "所选文件的 SHA-256 与原文不一致")
        media_type = record["media_type"] or media_type
        path = self._path(record["source_id"], media_type)
        if publish:
            self.publish(path, original, digest)
        if str(path) == record["file_path"] and media_type == record["media_type"]:
            return record
        return {**record, "media_type": media_type, "file_path": str(path), "version": record["version"] + 1}

    def _cached_original(self, db, file_id):
        match = re.fullmatch(r"([0-9a-f]{64})\.([a-z]+)", file_id) if isinstance(file_id, str) else None
        if not match or match[2] not in MEDIA_TYPES:
            raise CommandError("invalid_document", "需要已缓存的源文档")
        digest, extension = match.groups()
        self.initialize(db)
        cached = db.execute("SELECT original FROM library_source_cache WHERE file_id=?", (file_id,)).fetchone()
        if cached is None or hashlib.sha256(cached[0]).hexdigest() != digest:
            raise CommandError("source_unavailable", "缓存中的源文档不可用或已更改")
        return digest, MEDIA_TYPES[extension], cached[0]

    def _record(self, fields, source_id, digest, media_type, file_path):
        summary = self._text(fields, "summary", 2000)
        return {
            "source_id": source_id, "version": 1, "status": "active", "title": self._text(fields, "title", 500),
            "summary": summary, "label": summary, "citation": self._text(fields, "citation", 2000, optional=True),
            "media_type": media_type, "sha256": digest, "file_path": file_path, "material_id": None, "url": None,
        }

    def _path(self, source_id, media_type):
        return self._originals() / f"{source_id}.{EXTENSIONS[media_type]}"

    def publish(self, path, data, digest):
        if managed_paths(self.home) is None:
            self._publish(path, data, digest)
            return
        try:
            with store(self.home) as material:
                name = path.relative_to(material.root).as_posix()
                current = material.existing(name)
                if current is None:
                    material.publish(name, [data], hashlib.md5(data).hexdigest())
                else:
                    self._read_managed(current, digest)
        except (InstanceInvalid, InstanceBusy, OSError) as error:
            raise CommandError("source_unavailable", "无法保存源文档") from error

    def read(self, record):
        if record["file_path"] is None:
            raise CommandError("source_unavailable", "原文不可用")
        path = self._path(record["source_id"], record["media_type"])
        if managed_paths(self.home) is None:
            return self._read(path, record["sha256"])
        try:
            with store(self.home) as material:
                pin = material.existing(path.relative_to(material.root).as_posix())
                return self._read_managed(pin, record["sha256"])
        except (InstanceInvalid, InstanceBusy, OSError) as error:
            raise CommandError("source_unavailable", "原文不可用或已更改") from error

    @staticmethod
    def _read_managed(pin, digest):
        original = bytearray()
        if pin is not None:
            for block in _blocks(pin):
                original.extend(block)
                if len(original) > MAX_SOURCE_BYTES:
                    break
            if len(original) <= MAX_SOURCE_BYTES and hashlib.sha256(original).hexdigest() == digest:
                return bytes(original)
        raise CommandError("source_unavailable", "原文不可用或已更改")

    @staticmethod
    def _read(path, digest):
        try:
            if path.absolute() != path.resolve(strict=True):
                raise OSError("Linked document")
            with path.open("rb") as source:
                data = source.read(MAX_SOURCE_BYTES + 1)
        except OSError as error:
            raise CommandError("source_unavailable", "原文不可用或已更改") from error
        if len(data) > MAX_SOURCE_BYTES or hashlib.sha256(data).hexdigest() != digest:
            raise CommandError("source_unavailable", "原文不可用或已更改")
        return data

    @classmethod
    def _publish(cls, path, data, digest):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.parent.absolute() != path.parent.resolve(strict=True):
                raise OSError("Linked document directory")
            descriptor, filename = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
            temporary_path = Path(filename)
            try:
                with os.fdopen(descriptor, "wb") as temporary:
                    temporary.write(data)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                try:
                    os.link(temporary_path, path, follow_symlinks=False)
                except FileExistsError:
                    pass
            finally:
                temporary_path.unlink(missing_ok=True)
            cls._read(path, digest)
        except OSError as error:
            raise CommandError("source_unavailable", "无法保存源文档") from error
