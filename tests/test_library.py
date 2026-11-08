import json
from pathlib import Path

import pytest

from conftest import cli_locations
from library import base as base_mod
from library.drive import FixtureDrive, md5_bytes
from library.service import (
    ARCHIVED,
    IN_LIBRARY,
    NO_SOURCE,
    PENDING,
    Library,
    LibraryError,
    source_fields,
)


class FakeBase:
    def __init__(self, *, fail_create: int = 0):
        self.tables: dict[str, list[dict]] = {}
        self.counter = 0
        self.fail_create = fail_create

    def _rows(self, table):
        return self.tables.setdefault(table, [])

    def list(self, table):
        return [dict(row, fields=dict(row["fields"])) for row in self._rows(table)]

    def create(self, table, rows):
        if self.fail_create:
            self.fail_create -= 1
            raise base_mod.LibraryBaseError("boom")
        ids = []
        for row in rows:
            self.counter += 1
            rid = f"rec{self.counter:04d}"
            self._rows(table).append({"record_id": rid, "fields": dict(row)})
            ids.append(rid)
        return ids

    def update(self, table, updates):
        by_id = {row["record_id"]: row for row in self._rows(table)}
        for rid, fields in updates.items():
            by_id[rid]["fields"].update(fields)

    def ensure_options(self, table, field, values):
        return None

    def field_names(self, table):
        return []

    def record(self, table, key, value):
        return next(
            row for row in self._rows(table) if row["fields"].get(key) == value
        )


def _settings(drive, *, limit_bytes=10 * 1024**2, threshold=80):
    source = drive.ensure_folder(drive.root, "原文")
    push = drive.ensure_folder(drive.root, "推送")
    return {
        "limit_gb": limit_bytes / 1024**3,
        "limit_bytes": limit_bytes,
        "threshold_pct": threshold,
        "source_folder": source,
        "push_folder": push,
        "tables": {
            "sources": "sources",
            "pushes": "pushes",
            "decisions": "decisions",
            "config": "config",
            "cards": "cards",
            "papers": "papers",
        },
    }


def _home(tmp_path):
    home = tmp_path / "code" / ".horizon"
    (home / "archive" / "raw").mkdir(parents=True)
    (home / "archive" / "pushes").mkdir(parents=True)
    (home / "knowledge").mkdir(parents=True)
    (home / "knowledge" / "base.json").write_text(
        json.dumps({"base_token": "fake", "tables": {"papers": "papers", "cards": "cards"}}),
        encoding="utf-8",
    )
    return home


def _pdf(home, paper_id="pdf_demo_01", data=b"%PDF-1.4 demo"):
    path = home / "src" / f"{paper_id}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    (home / "archive" / "raw" / f"{paper_id}.json").write_text(
        json.dumps(
            {
                "paper_id": paper_id,
                "title": "A demo paper",
                "authors": ["Ada", "Bob"],
                "journal": "Demo Journal",
                "year": 2026,
                "doi": "10.1/demo",
                "source": "pdf",
                "pdf_path": str(path),
                "pdf_pages": 12,
                "is_preprint": False,
                "url": "https://doi.org/10.1/demo",
            }
        ),
        encoding="utf-8",
    )
    return path, data


def _library(home, drive=None, base=None, **kwargs):
    drive = drive or FixtureDrive()
    base = base or FakeBase()
    return Library(home, drive=drive, base=base, settings=_settings(drive, **kwargs)), drive, base


def test_source_fields_maps_raw_record():
    fields = source_fields(
        {
            "title": "T", "authors": ["A", "B"], "journal": "J", "year": "2024",
            "source": "pdf", "is_preprint": True, "pdf_pages": 9,
            "doi": "10.1/x", "url": "https://doi.org/10.1/x",
        }
    )
    assert fields["作者"] == "A; B"
    assert fields["发表年份"] == 2024
    assert fields["是否预印本"] is True
    assert fields["来源"] == ["pdf"]


def test_put_source_uploads_then_registers_and_is_idempotent(tmp_path):
    home = _home(tmp_path)
    path, data = _pdf(home)
    lib, drive, base = _library(home)

    first = lib.put_source("pdf_demo_01")
    assert first["status"] == "uploaded"
    assert first["md5"] == md5_bytes(data)

    files = drive.list(lib.folder("source"))
    assert [f["name"] for f in files] == ["pdf_demo_01.pdf"]
    row = base.record("sources", "原文 ID", "pdf_demo_01")
    assert row["fields"]["状态"] == [IN_LIBRARY]
    assert row["fields"]["文件大小"] == len(data)
    assert row["fields"]["标题"] == "A demo paper"
    assert row["record_id"] in row["fields"]["云盘链接"] or row["fields"]["云盘链接"]

    second = lib.put_source("pdf_demo_01")
    assert second["status"] == "skipped"
    assert len(drive.list(lib.folder("source"))) == 1


def test_put_source_leaves_orphan_file_when_base_write_fails(tmp_path):
    home = _home(tmp_path)
    path, data = _pdf(home)
    lib, drive, base = _library(home, base=FakeBase(fail_create=1))

    first = lib.put_source("pdf_demo_01")
    assert first["status"] == "orphan"
    assert len(drive.list(lib.folder("source"))) == 1
    assert base.list("sources") == []

    second = lib.put_source("pdf_demo_01")
    assert second["status"] == "uploaded"
    assert len(drive.list(lib.folder("source"))) == 1
    assert base.record("sources", "原文 ID", "pdf_demo_01")


def test_same_paper_different_md5_is_a_conflict(tmp_path):
    home = _home(tmp_path)
    path, data = _pdf(home)
    lib, drive, base = _library(home)
    lib.put_source("pdf_demo_01")

    path.write_bytes(b"%PDF-1.4 different version")
    result = lib.put_source("pdf_demo_01")
    assert result["status"] == "conflict"
    row = base.record("sources", "原文 ID", "pdf_demo_01")
    assert row["fields"]["md5"] == md5_bytes(data)


def test_quota_blocks_new_source_but_not_push(tmp_path):
    home = _home(tmp_path)
    path, data = _pdf(home, data=b"x" * 50)
    lib, drive, base = _library(home, limit_bytes=10)

    blocked = lib.put_source("pdf_demo_01")
    assert blocked["status"] == "quota_blocked"
    assert drive.list(lib.folder("source")) == []
    assert (home / "library" / "alerts").exists()

    push_id = "2026-10-26_demo_01"
    html = home / "outbox" / f"{push_id}.html"
    html.parent.mkdir(parents=True)
    html.write_text("<html>push</html>", encoding="utf-8")
    (home / "archive" / "pushes" / f"{push_id}.json").write_text(
        json.dumps(
            {
                "push_id": push_id,
                "paper": {"paper_id": "pdf_demo_01"},
                "title": "Demo push",
                "date": "2026-10-26",
                "sections": {key: f"text {key}" for key in (
                    "takeaway", "background", "methods", "results", "why", "limits", "sources"
                )},
            }
        ),
        encoding="utf-8",
    )
    pushed = lib.put_push(push_id, html)
    assert pushed["status"] == "uploaded"
    assert len(drive.list(lib.folder("push"))) == 1


def test_put_push_writes_seven_sections_and_links(tmp_path):
    home = _home(tmp_path)
    path, data = _pdf(home)
    lib, drive, base = _library(home)
    lib.put_source("pdf_demo_01")
    base.create("cards", [{"卡片 ID": "card_demo"}])
    card = base.record("cards", "卡片 ID", "card_demo")

    push_id = "2026-10-26_demo_01"
    html = home / "outbox" / f"{push_id}.html"
    html.parent.mkdir(parents=True)
    html.write_text("<html>push</html>", encoding="utf-8")
    sections = {
        "takeaway": "结论", "background": "背景", "methods": "方法",
        "results": "结果", "why": "关系", "limits": "局限", "sources": "来源",
    }
    (home / "archive" / "pushes" / f"{push_id}.json").write_text(
        json.dumps(
            {
                "push_id": push_id,
                "card_id": "card_demo",
                "paper": {"paper_id": "pdf_demo_01"},
                "title": "Demo push",
                "date": "2026-10-26",
                "sections": sections,
            }
        ),
        encoding="utf-8",
    )

    result = lib.put_push(push_id, html)
    assert result["status"] == "uploaded"
    row = base.record("pushes", "推送 ID", push_id)
    for label in ("一句话结论", "背景", "方法", "结果", "与知识的关系", "局限", "来源"):
        assert row["fields"][label]
    assert row["fields"]["知识卡"] == [{"id": card["record_id"]}]
    assert row["fields"]["原文"]


def test_resolve_downloads_then_repairs_a_truncated_cache(tmp_path):
    home = _home(tmp_path)
    path, data = _pdf(home)
    lib, drive, base = _library(home)
    lib.put_source("pdf_demo_01")

    first = lib.resolve("pdf_demo_01")
    assert first["cached"] is False
    cache = Path(first["path"])
    assert cache.read_bytes() == data

    cache.write_bytes(b"trunc")
    second = lib.resolve("pdf_demo_01")
    assert second["cached"] is False
    assert Path(second["path"]).read_bytes() == data


def test_resolve_unknown_and_no_source(tmp_path):
    home = _home(tmp_path)
    lib, drive, base = _library(home)
    with pytest.raises(LibraryError):
        lib.resolve("nope")

    (home / "archive" / "raw" / "pdf_paywalled.json").write_text(
        json.dumps({"paper_id": "pdf_paywalled", "title": "Paywalled", "source": "pdf"}),
        encoding="utf-8",
    )
    result = lib.register_source("pdf_paywalled", status=NO_SOURCE)
    assert result["status"] == NO_SOURCE
    assert lib.resolve("pdf_paywalled")["status"] == "no_source"


def test_reconcile_registers_orphans_and_reclassifies_broken_rows(tmp_path):
    home = _home(tmp_path)
    path, data = _pdf(home)
    lib, drive, base = _library(home)

    # orphan: file present, no Base row
    drive.put_bytes(lib.folder("source"), "pdf_demo_01.pdf", data)
    # broken: Base row in 在库 but no file
    base.create(
        "sources",
        [
            {
                "原文 ID": "pdf_gone",
                "状态": [IN_LIBRARY],
                "云盘链接": "https://example.feishu.cn/file/missing",
                "文件大小": 10,
            }
        ],
    )

    report = lib.reconcile()
    assert report["counts"]["orphan"] == 1
    assert report["counts"]["broken"] == 1
    row = base.record("sources", "原文 ID", "pdf_demo_01")
    assert row["fields"]["状态"] == [IN_LIBRARY]
    assert row["fields"]["md5"] == md5_bytes(data)
    gone = base.record("sources", "原文 ID", "pdf_gone")
    assert gone["fields"]["状态"] == [PENDING]


def test_reconcile_marks_missing_file_archived_when_backup_exists(tmp_path):
    home = _home(tmp_path)
    lib, drive, base = _library(home)
    backup = tmp_path / "backup" / "pdf_gone.pdf"
    backup.parent.mkdir()
    backup.write_bytes(b"%PDF-1.4 backup")
    base.create(
        "sources",
        [
            {
                "原文 ID": "pdf_gone",
                "状态": [IN_LIBRARY],
                "云盘链接": "https://example.feishu.cn/file/missing",
                "文件大小": 15,
                "md5": md5_bytes(b"%PDF-1.4 backup"),
                "备份位置": str(backup),
            }
        ],
    )
    lib.reconcile()
    row = base.record("sources", "原文 ID", "pdf_gone")
    assert row["fields"]["状态"] == [ARCHIVED]


def test_evict_refuses_on_md5_mismatch_then_archives(tmp_path):
    home = _home(tmp_path)
    path, data = _pdf(home)
    lib, drive, base = _library(home)
    lib.put_source("pdf_demo_01")
    token = drive.find(lib.folder("source"), "pdf_demo_01.pdf")["token"]
    drive.corrupt(token, b"tampered")

    failed = lib.evict(execute=True, backup_root=tmp_path / "offline")
    assert failed["ok"] is False
    assert failed["failed"][0]["id"] == "pdf_demo_01"
    assert drive.find(lib.folder("source"), "pdf_demo_01.pdf")
    row = base.record("sources", "原文 ID", "pdf_demo_01")
    assert row["fields"]["状态"] == [IN_LIBRARY]

    drive.put_bytes(lib.folder("source"), "pdf_demo_01.pdf", data)
    ok = lib.evict(execute=True, backup_root=tmp_path / "offline")
    assert ok["ok"] is True
    assert ok["freed_bytes"] == len(data)
    assert drive.find(lib.folder("source"), "pdf_demo_01.pdf") is None
    row = base.record("sources", "原文 ID", "pdf_demo_01")
    assert row["fields"]["状态"] == [ARCHIVED]
    backup = Path(row["fields"]["备份位置"])
    assert backup.read_bytes() == data
    assert lib.resolve("pdf_demo_01")["path"]


def test_restore_reuploads_an_archived_source(tmp_path):
    home = _home(tmp_path)
    path, data = _pdf(home)
    lib, drive, base = _library(home)
    lib.put_source("pdf_demo_01")
    lib.evict(execute=True, backup_root=tmp_path / "offline")

    result = lib.restore("pdf_demo_01")
    assert result["ok"] is True
    assert drive.find(lib.folder("source"), "pdf_demo_01.pdf")
    row = base.record("sources", "原文 ID", "pdf_demo_01")
    assert row["fields"]["状态"] == [IN_LIBRARY]
    assert row["fields"]["备份位置"] == ""


def test_alert_cools_down_per_level(tmp_path):
    home = _home(tmp_path)
    lib, drive, base = _library(home, limit_bytes=100, threshold=80)
    base.create(
        "sources",
        [{"原文 ID": "pdf_big", "状态": [IN_LIBRARY], "文件大小": 90}],
    )
    first = lib.maybe_alert()
    assert first and first["level"] == "warn"
    assert lib.maybe_alert() is None

    base.update("sources", {base.list("sources")[0]["record_id"]: {"文件大小": 120}})
    full = lib.maybe_alert()
    assert full and full["level"] == "full"

    # 100% is not cooled down: every attempt reminds
    assert lib.maybe_alert() is not None
    forced = lib.maybe_alert(force=True)
    assert forced is not None
    assert len(list((home / "library" / "alerts").glob("*.json"))) == 4


def test_eviction_candidates_prefer_unreferenced_then_oldest(tmp_path):
    home = _home(tmp_path)
    lib, drive, base = _library(home)
    base.create(
        "sources",
        [
            {"原文 ID": "pdf_ref_old", "状态": [IN_LIBRARY], "文件大小": 1,
             "入库日期": "2026-01-01"},
            {"原文 ID": "pdf_free_new", "状态": [IN_LIBRARY], "文件大小": 1,
             "入库日期": "2026-06-01"},
            {"原文 ID": "pdf_free_old", "状态": [IN_LIBRARY], "文件大小": 1,
             "入库日期": "2026-02-01"},
        ],
    )
    base.create("papers", [{"paper_id": "pdf_ref_old", "page": 1, "quote": "x" * 10}])
    ids = [c["id"] for c in lib.eviction_candidates()]
    assert ids == ["pdf_free_old", "pdf_free_new", "pdf_ref_old"]


class FakeAdmin(FakeBase):
    def __init__(self):
        super().__init__()
        self.tables_meta = [{"id": "tblpapers", "name": "报道"}]
        self.fields = {"tblpapers": ["paper_id", "page"]}

    def list_tables(self):
        return list(self.tables_meta)

    def field_names(self, table):
        return list(self.fields.get(table, []))

    def create_table(self, name, fields):
        table_id = f"tbl{len(self.tables_meta) + 1}"
        self.tables_meta.append({"id": table_id, "name": name})
        self.fields[table_id] = [f["name"] for f in fields]
        return table_id

    def create_fields(self, table, fields):
        self.fields.setdefault(table, []).extend(f["name"] for f in fields)
        return fields


def test_setup_dry_run_plans_without_writing(tmp_path):
    from library.setup import setup

    home = _home(tmp_path)
    admin = FakeAdmin()
    drive = FixtureDrive()
    result = setup(home, dry_run=True, client=admin, drive=drive)
    assert any("create table 原文" in a for a in result["actions"])
    assert drive.list(drive.root) == []
    config = json.loads((home / "knowledge" / "base.json").read_text())
    assert "sources" not in config["tables"]


def test_setup_creates_tables_folders_and_config_row(tmp_path):
    from library.setup import setup

    home = _home(tmp_path)
    admin = FakeAdmin()
    drive = FixtureDrive()
    result = setup(home, client=admin, drive=drive)
    names = {t["name"] for t in admin.list_tables()}
    assert {"原文", "推送日志", "决定", "配置"} <= names
    folders = {e["name"] for e in drive.list(drive.root)}
    assert {"horizon"} <= folders
    horizon = next(e["token"] for e in drive.list(drive.root) if e["name"] == "horizon")
    assert {"原文", "推送"} <= {e["name"] for e in drive.list(horizon)}
    config = json.loads((home / "knowledge" / "base.json").read_text())
    assert config["tables"]["sources"].startswith("tbl")
    assert config["tables"]["pushes"].startswith("tbl")
    row = admin.record(config["tables"]["config"], "键", "config")
    assert row["fields"]["容量上限（GB）"] == 5
    assert row["fields"]["原文目录 token"]


def test_backfill_dedupes_the_same_md5(tmp_path):
    from common import write_jsonl
    from library.cli import _backfill

    home = _home(tmp_path)
    one = home / "src" / "a.pdf"
    two = home / "src" / "b.pdf"
    one.parent.mkdir(parents=True, exist_ok=True)
    one.write_bytes(b"%PDF-1.4 same")
    two.write_bytes(b"%PDF-1.4 same")
    manifest = tmp_path / "manifest.jsonl"
    digest = md5_bytes(b"%PDF-1.4 same")
    write_jsonl(
        manifest,
        [
            {"path": str(one), "md5": digest, "title": "A", "source": "pdf"},
            {"path": str(two), "md5": digest, "title": "B", "source": "pdf"},
        ],
    )
    lib, drive, base = _library(home)
    report = _backfill(lib, manifest, limit=None, dry_run=True)
    assert report["counts"]["planned"] == 1
    assert len(report["duplicates"]) == 1
    assert report["duplicates"][0]["same_as"] == "pdf_a"


def test_decisions_import_is_per_proposal_and_idempotent(tmp_path):
    from common import write_jsonl
    from library.cli import _decision_rows

    home = _home(tmp_path)
    lib, drive, base = _library(home)
    push_id = "2026-10-26_demo_01"
    (home / "archive" / "pushes" / f"{push_id}.json").write_text(
        json.dumps(
            {
                "push_id": push_id,
                "proposals": [
                    {"proposal_id": "p1", "target_id": "prop_a"},
                    {"proposal_id": "p2", "target_id": "card_b"},
                ],
            }
        ),
        encoding="utf-8",
    )
    base.create("pushes", [{"推送 ID": push_id}])
    path = tmp_path / "decisions.jsonl"
    write_jsonl(
        path,
        [
            {"question_id": "q1", "push_id": push_id, "proposal_id": "p1",
             "action": "拒绝", "comment": "样本太小", "at": "2026-10-26T10:00:00+08:00"},
            {"question_id": "q1", "push_id": push_id, "proposal_id": "p2",
             "action": "采纳", "at": "2026-10-26T10:00:00+08:00"},
        ],
    )
    rows = _decision_rows(lib, path)
    assert [r["操作"] for r in rows] == [["拒绝"], ["采纳"]]
    assert rows[0]["目标"] == "prop_a"
    assert rows[0]["评语"] == "样本太小"

    for row in rows:
        lib.upsert("decisions", "决定 ID", row["决定 ID"], row)
    for row in rows:
        lib.upsert("decisions", "决定 ID", row["决定 ID"], row)
    assert len(base.list("decisions")) == 2


def test_large_pdfs_are_split_into_parts_and_reassembled(tmp_path, monkeypatch):
    import library.service as service

    monkeypatch.setattr(service, "PART_SIZE", 32)
    home = _home(tmp_path)
    path, data = _pdf(home, data=b"%PDF-1.4 " + b"x" * 100)
    lib, drive, base = _library(home)

    result = lib.put_source("pdf_demo_01")
    assert result["status"] == "uploaded"
    assert result["parts"] == 4
    names = sorted(f["name"] for f in drive.list(lib.folder("source")))
    assert names == [f"pdf_demo_01.pdf.part{i:02d}" for i in range(1, 5)]

    row = base.record("sources", "原文 ID", "pdf_demo_01")
    assert len(json.loads(row["fields"]["分片"])) == 4

    resolved = lib.resolve("pdf_demo_01")
    assert Path(resolved["path"]).read_bytes() == data

    evicted = lib.evict(execute=True, backup_root=tmp_path / "offline")
    assert evicted["ok"] is True
    assert drive.list(lib.folder("source")) == []
    backup = Path(base.record("sources", "原文 ID", "pdf_demo_01")["fields"]["备份位置"])
    assert backup.read_bytes() == data

    restored = lib.restore("pdf_demo_01")
    assert restored["ok"] is True
    assert len(drive.list(lib.folder("source"))) == 4


def test_offline_cli_roundtrip(tmp_path):
    from library.cli import main

    home = _home(tmp_path)
    assert main([*cli_locations(home), "--offline", "setup"]) == 0
    path, data = _pdf(home)
    assert main([*cli_locations(home), "--offline", "put-source",
                 "--paper", "pdf_demo_01"]) == 0
    assert main([*cli_locations(home), "--offline", "resolve",
                 "--paper", "pdf_demo_01"]) == 0
    assert (home / "cache" / "raw" / "pdf_demo_01.pdf").read_bytes() == data
    assert main([*cli_locations(home), "--offline", "quota"]) == 0
    assert main([*cli_locations(home), "--offline", "reconcile"]) == 0
    config = json.loads((home / "knowledge" / "base.json").read_text())
    assert config["tables"]["sources"].startswith("tbloff")


def test_push_idempotency_checks_md5_not_just_size(tmp_path):
    home = _home(tmp_path)
    lib, drive, base = _library(home)
    push_id = "2026-10-26_demo_01"
    push = {
        "push_id": push_id,
        "paper": {"paper_id": "pdf_x"},
        "title": "T",
        "date": "2026-10-26",
        "sections": {key: "x" for key in (
            "takeaway", "background", "methods", "results", "why", "limits", "sources"
        )},
    }
    (home / "archive" / "pushes" / f"{push_id}.json").write_text(
        json.dumps(push), encoding="utf-8"
    )
    html = home / "outbox" / f"{push_id}.html"
    html.parent.mkdir(parents=True)
    html.write_text("<html>aaaa</html>", encoding="utf-8")
    assert lib.put_push(push_id, html)["status"] == "uploaded"

    html.write_text("<html>bbbb</html>", encoding="utf-8")  # same length, new md5
    result = lib.put_push(push_id, html)
    assert result["status"] == "conflict"
    assert base.record("pushes", "推送 ID", push_id)["fields"]["md5"]


def test_reconcile_archived_row_with_a_live_file_is_a_mismatch(tmp_path):
    home = _home(tmp_path)
    lib, drive, base = _library(home)
    base.create(
        "sources",
        [{"原文 ID": "pdf_zombie", "状态": [ARCHIVED], "文件大小": 5,
          "md5": "abc", "备份位置": "/tmp/whatever.pdf"}],
    )
    drive.put_bytes(lib.folder("source"), "pdf_zombie.pdf", b"%PDF")
    report = lib.reconcile()
    assert report["counts"]["mismatch"] == 1
    assert report["mismatch"][0]["reason"].startswith("状态=已离线归档")


def test_broken_row_with_a_backup_is_archived_even_if_md5_differs(tmp_path):
    home = _home(tmp_path)
    lib, drive, base = _library(home)
    backup = tmp_path / "backup" / "pdf_gone.pdf"
    backup.parent.mkdir()
    backup.write_bytes(b"%PDF-1.4 changed")
    base.create(
        "sources",
        [{"原文 ID": "pdf_gone", "状态": [IN_LIBRARY],
          "云盘链接": "https://example.feishu.cn/file/missing",
          "文件大小": 10, "md5": md5_bytes(b"%PDF-1.4 original"),
          "备份位置": str(backup)}],
    )
    report = lib.reconcile()
    row = base.record("sources", "原文 ID", "pdf_gone")
    assert row["fields"]["状态"] == [ARCHIVED]
    assert report["counts"]["mismatch"] == 1

def test_create_base_reads_the_nested_base_create_envelope(monkeypatch):
    """The shortcut nests base/table in ``data``; creation must still yield IDs."""
    from library import admin as admin_mod

    client = admin_mod.LarkAdminClient(bin="lark-cli")
    monkeypatch.setattr(
        client,
        "_call",
        lambda *args, **kwargs: {
            "base": {
                "base_token": "bas_created",
                "url": "https://example.feishu.cn/base/bas_created",
                "name": "horizon 知识库",
            },
            "created": True,
            "table": {"id": "tbl_created", "name": "知识卡片", "fields": []},
            "fields": [],
        },
    )

    created = client.create_base("horizon 知识库", table_name="知识卡片", fields=[])

    assert created["base_token"] == "bas_created"
    assert created["table_id"] == "tbl_created"
    assert created["url"] == "https://example.feishu.cn/base/bas_created"
    assert created["name"] == "horizon 知识库"

def test_library_and_bitable_clients_use_the_recorded_cli(monkeypatch):
    """ADR 0007: the recorded absolute CLI path, never a bare PATH guess."""
    import common
    from knowledge.bitable import LarkBitableClient
    from library.base import LarkBaseClient
    from library.drive import LarkDrive

    monkeypatch.setattr(common, "recorded_lark_cli", lambda: r"D:\lark\cli\lark-cli.exe")

    assert LarkDrive().bin == r"D:\lark\cli\lark-cli.exe"
    assert LarkBaseClient({"base_token": "bas_x"}).bin == r"D:\lark\cli\lark-cli.exe"
    assert LarkBitableClient({"base_token": "bas_x"}).bin == r"D:\lark\cli\lark-cli.exe"

def test_library_base_delete_confirms_the_high_risk_write(monkeypatch):
    from library.base import LarkBaseClient

    client = LarkBaseClient({"base_token": "bas_x"})
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(client, "_run", fake_run)

    client.delete("tbl_x", ["rec_1", "rec_2"])

    assert calls
    args, kwargs = calls[0]
    assert args[0] == "base" and args[1] == "+record-delete"
    assert kwargs.get("yes") is True
