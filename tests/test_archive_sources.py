import base64
import re
from io import BytesIO
from pathlib import Path

import pymupdf
import pytest

from hostrun.agent_service import CommandError
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.librarian import Librarian
from hostrun.voyager_materials import MaterialsService


def test_library_preserves_legacy_source_and_opens_confirmed_pdf_reference(tmp_path):
    token = "desktop"
    materials = MaterialsService(tmp_path, token=token)
    legacy = materials.register_text({
        "schema_version": 1, "operation_id": "legacy", "title": "历史报道",
        "text": "保留的原始报道", "source_label": "原始来源",
    }, token=token)
    librarian = Librarian(KnowledgeCardService(tmp_path, token=token, materials=materials), token=token)
    source = librarian.list_sources(token=token)[0]
    assert source["title"] == "历史报道"
    assert source["material_id"] == legacy["material_id"]
    assert librarian.open_source(source["source_id"], token=token)["original_text"] == "保留的原始报道"

    pdf = BytesIO()
    with pymupdf.open() as document:
        document.new_page().insert_text((50, 50), "Original source")
        document.save(pdf)
    material = materials.register_pdf({
        "schema_version": 1, "operation_id": "pdf-file", "title": "PDF file",
        "source_label": "File storage", "pdf_base64": base64.b64encode(pdf.getvalue()).decode(),
    }, token=token)
    request = {"operation_id": "source-write", "title": "原文记录", "label": "图书馆来源",
               "material_id": material["material_id"], "url": "https://example.com/original"}
    card = librarian.request_source("create", request, token=token, origin="gui")
    assert card["status"] == "pending"
    assert len(librarian.list_sources(token=token)) == 1
    with pytest.raises(CommandError, match="本会话"):
        librarian.decide("source-write", "confirm", token=token, origin="agent")
    librarian = Librarian(KnowledgeCardService(tmp_path, token=token, materials=materials), token=token)
    result = librarian.decide("source-write", "confirm", token=token, origin="gui")
    source_id = result["results"][0]["source_id"]
    assert librarian.view_source(source_id, token=token)["url"] == "https://example.com/original"
    assert librarian.open_source(source_id, token=token)["pdf"]["anchors"][0]["page"] == 1
    assert librarian.source_pdf(source_id, token=token) == pdf.getvalue()
    assert librarian.decide("source-write", "confirm", token=token, origin="gui") == result
    librarian.request_source("delete", {
        "operation_id": "delete-link", "source_id": source_id, "expected_version": 1,
    }, token=token, origin="gui")
    librarian.decide("delete-link", "confirm", token=token, origin="gui")
    assert materials.pdf_original(material["material_id"], token=token) == pdf.getvalue()


def test_source_update_and_deletion_wait_for_confirmation(tmp_path):
    token = "desktop"
    librarian = Librarian(KnowledgeCardService(tmp_path, token=token), token=token)
    librarian.request_source("create", {
        "operation_id": "create", "title": "来源", "label": "出处", "url": "https://example.com/",
    }, token=token, origin="gui")
    source = librarian.decide("create", "confirm", token=token, origin="gui")["results"][0]
    edit = {"operation_id": "edit", "source_id": source["source_id"], "expected_version": 1,
            "title": "新标题"}
    librarian.request_source("update", edit, token=token, origin="gui")
    with pytest.raises(CommandError, match="确认"):
        librarian.request_source("update", {**edit, "operation_id": "duplicate"},
                                 token=token, origin="gui")
    assert librarian.view_source(source["source_id"], token=token)["title"] == "来源"
    librarian.decide("edit", "reject", token=token, origin="gui")
    assert librarian.list_sources(token=token) == [source]
    librarian.request_source("update", {**edit, "operation_id": "edit-again"}, token=token, origin="gui")
    updated = librarian.decide("edit-again", "confirm", token=token, origin="gui")["results"][0]
    assert updated["title"] == "新标题"
    assert updated["version"] == 2
    librarian.request_source("delete", {
        "operation_id": "delete", "source_id": source["source_id"], "expected_version": 2,
    }, token=token, origin="gui")
    assert librarian.list_sources(token=token) == [updated]
    librarian.decide("delete", "confirm", token=token, origin="gui")
    assert librarian.list_sources(token=token) == []


def test_removed_archive_original_falls_back_to_source_url(tmp_path):
    token = "desktop"
    materials = MaterialsService(tmp_path, token=token)
    material = materials.register_text({"schema_version": 1, "operation_id": "original",
        "title": "原文件", "text": "原文", "source_label": "来源"}, token=token)
    librarian = Librarian(KnowledgeCardService(tmp_path, token=token, materials=materials), token=token)
    material_id = material["material_id"]
    librarian.request_source("create", {"operation_id": "write", "title": "来源", "label": "网络来源",
        "material_id": material_id, "url": "https://example.com/original"}, token=token, origin="gui")
    source = librarian.decide("write", "confirm", token=token, origin="gui")["results"][0]
    assert librarian.open_source(source["source_id"], token=token)["original_text"] == "原文"
    materials.archive_material(material_id, token=token)
    impact = materials.deletion_impact(material_id, token=token)
    materials.delete_material(material_id, impact["confirmation"], token=token)
    fallback = {"source_url": source["url"]}
    assert librarian.open_source(source["source_id"], token=token) == fallback
    assert librarian.source_original(source["source_id"], token=token) == (fallback, None)
    assert librarian.view_source(source["source_id"], token=token) == source


def test_library_registers_one_original_only_after_confirmation(tmp_path):
    token = "desktop"
    librarian = Librarian(KnowledgeCardService(tmp_path, token=token), token=token)
    original = b"<!doctype html><p>Original document</p>"
    candidate = librarian.cache_source(original, "text/html", token=token)
    request = {"operation_id": "source-register", "title": "所选文档", "summary": "原件简介",
               "source_files": [candidate["file_id"]], "citation": "Author (2026)"}
    for files in ([], [candidate["file_id"], candidate["file_id"]]):
        with pytest.raises(CommandError, match="恰好一份"):
            librarian.request_source("register", {**request, "source_files": files},
                                     token=token, origin="agent:task")
    review = librarian.request_source("register", request, token=token, origin="agent:task")
    assert review["status"] == "pending"
    assert librarian.list_sources(token=token) == []
    receipt = librarian.decide("source-register", "confirm", token=token, origin="agent:task")
    source_id = receipt["results"][0]["source_id"]
    assert re.fullmatch(r"d-[0-9a-f]{32}", source_id)
    reopened = Librarian(KnowledgeCardService(tmp_path, token=token), token=token)
    record = reopened.view_source(source_id, token=token)
    assert record["title"] == "所选文档"
    assert record["summary"] == "原件简介"
    assert record["citation"] == "Author (2026)"
    assert record["sha256"] == "dfabed9b097986025c235f3c1b5ae23dfdf0605f15eb8751652bd69853c1a948"
    assert Path(record["file_path"]).read_bytes() == original
    assert reopened.source_original(source_id, token=token)[1] == original
    assert reopened.decide("source-register", "confirm", token=token, origin="agent:task") == receipt


def test_document_selection_can_register_equal_files_and_cannot_replace_an_original(tmp_path):
    token = "desktop"
    librarian = Librarian(KnowledgeCardService(tmp_path, token=token), token=token)
    candidate = librarian.cache_source(b"<p>Same original</p>", "text/html", token=token)
    source_ids = []
    for title in ("First selection", "Second selection"):
        librarian.request_source("register", {"operation_id": title, "title": title, "summary": "Selected",
            "source_files": [candidate["file_id"]]}, token=token, origin="gui")
        receipt = librarian.decide(title, "confirm", token=token, origin="gui")
        source_ids.append(receipt["results"][0]["source_id"])
    assert len(set(source_ids)) == 2
    first = librarian.view_source(source_ids[0], token=token)
    with pytest.raises(CommandError, match="不能更换原文"):
        librarian.request_source("update", {"operation_id": "replace-source", "source_id": source_ids[0],
            "expected_version": first["version"], "url": "https://example.com/replacement"}, token=token, origin="gui")
    assert librarian.source_original(source_ids[0], token=token)[1] == b"<p>Same original</p>"
    Path(first["file_path"]).write_bytes(b"<p>Unexpected contents</p>")
    with pytest.raises(CommandError, match="原文不可用或已更改"):
        librarian.source_original(source_ids[0], token=token)
    assert librarian.source_original(source_ids[1], token=token)[1] == b"<p>Same original</p>"
