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
        file_id = files[0]
        match = re.fullmatch(r"([0-9a-f]{64})\.([a-z]+)", file_id) if isinstance(file_id, str) else None
        if not match or match[2] not in MEDIA_TYPES:
            raise CommandError("invalid_document", "需要已缓存的源文档")
        digest, extension = match.groups()
        identity = f"horizon:archive-document:{digest}:{command['operation_id']}"
        source_id = "d-" + uuid.uuid5(uuid.NAMESPACE_URL, identity).hex
        path = self._originals() / f"{source_id}.{extension}"
        summary = self._text(command, "summary", 2000)
        record = {
            "source_id": source_id, "version": 1, "status": "active", "title": self._text(command, "title", 500),
            "summary": summary, "label": summary, "citation": self._text(command, "citation", 2000, optional=True),
            "media_type": MEDIA_TYPES[extension], "sha256": digest, "file_path": str(path),
            "material_id": None, "url": None, **new_test_tags(command),
        }
        self.initialize(db)
        cached = db.execute("SELECT original FROM library_source_cache WHERE file_id=?", (file_id,)).fetchone()
        if cached is None or hashlib.sha256(cached[0]).hexdigest() != digest:
            raise CommandError("source_unavailable", "缓存中的源文档不可用或已更改")
        if publish:
            self.publish(path, cached[0], digest)
        return record

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
        extension = EXTENSIONS[record["media_type"]]
        path = self._originals() / f"{record['source_id']}.{extension}"
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
