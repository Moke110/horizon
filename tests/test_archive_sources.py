import base64
from io import BytesIO

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
