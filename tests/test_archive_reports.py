import hashlib
from pathlib import Path
import re

import pytest

from hostrun.agent_service import CommandError
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.librarian import Librarian


TOKEN = "test"
ORIGIN = "agent:report-task"
HTML = "<!doctype html><h1>Finding</h1><p>Confirmed research.</p>"


def librarian(home):
    return Librarian(KnowledgeCardService(home, token=TOKEN), token=TOKEN)


def document(service):
    cached = service.cache_source(b"<p>Source evidence.</p>", "text/html", token=TOKEN)
    service.request_source("register", {
        "operation_id": "source", "title": "Source", "summary": "Evidence",
        "source_files": [cached["file_id"]],
    }, token=TOKEN, origin=ORIGIN)
    result = service.decide("source", "confirm", token=TOKEN, origin=ORIGIN)
    return result["results"][0]["source_id"]


def test_confirming_completed_report_publishes_one_reopenable_original(tmp_path):
    service = librarian(tmp_path)
    covered = document(service)
    draft = service.stage_report(HTML, token=TOKEN, origin=ORIGIN)
    request = {
        "operation_id": "publish-report", "draft_id": draft["draft_id"],
        "title": "Finding", "summary": "Research overview",
        "covered_docs": [covered],
    }
    card = service.request_report("create", request, token=TOKEN, origin=ORIGIN)
    assert card["status"] == "pending"
    assert librarian(tmp_path).list_reports(token=TOKEN) == []
    assert list(tmp_path.glob("archive/originals/reports/*.html")) == []

    decision = librarian(tmp_path).decide("publish-report", "confirm", token=TOKEN, origin=ORIGIN)
    report, = decision["results"]
    assert decision["status"] == "confirmed"
    assert re.fullmatch(r"r-[0-9a-f]{32}", report["report_id"])
    assert report["status"] == "completed"
    assert report["covered_docs"] == [covered]
    assert report["sha256"] == hashlib.sha256(HTML.encode("utf-8")).hexdigest()
    assert Path(report["file_path"]).read_text(encoding="utf-8") == HTML
    assert librarian(tmp_path).open_report(report["report_id"], token=TOKEN) == {"report": report, "html": HTML}
    assert librarian(tmp_path).decide("publish-report", "confirm", token=TOKEN, origin=ORIGIN) == decision


def test_drafts_and_rejected_reports_do_not_escape_their_task_or_cache(tmp_path):
    service = librarian(tmp_path)
    covered = document(service)
    draft = service.stage_report(HTML, token=TOKEN, origin=ORIGIN)
    request = {
        "operation_id": "interrupted", "draft_id": draft["draft_id"],
        "title": "Finding", "summary": "Research overview", "covered_docs": [covered],
    }
    for ids in ([], ["d-" + "0" * 32]):
        with pytest.raises(CommandError):
            service.request_report("create", {**request, "covered_docs": ids}, token=TOKEN, origin=ORIGIN)
    with pytest.raises(CommandError) as error:
        service.request_report("create", request, token=TOKEN, origin="agent:other")
    assert error.value.code == "report_unavailable"
    service.request_report("create", request, token=TOKEN, origin=ORIGIN)
    with pytest.raises(CommandError, match="本会话没有该确认请求"):
        service.decide("interrupted", "confirm", token=TOKEN, origin="agent:other")
    assert service.decide("interrupted", "reject", token=TOKEN, origin=ORIGIN)["status"] == "rejected"
    assert librarian(tmp_path).list_reports(token=TOKEN) == []
    assert list(tmp_path.glob("archive/originals/reports/*.html")) == []


def test_registered_report_rejects_in_place_changes(tmp_path):
    service = librarian(tmp_path)
    covered = document(service)
    draft = service.stage_report(HTML, token=TOKEN, origin=ORIGIN)
    request = {
        "operation_id": "publish-report", "draft_id": draft["draft_id"],
        "title": "Finding", "summary": "Research overview", "covered_docs": [covered],
    }
    service.request_report("create", request, token=TOKEN, origin=ORIGIN)
    result = service.decide("publish-report", "confirm", token=TOKEN, origin=ORIGIN)
    report, = result["results"]
    with pytest.raises(CommandError) as error:
        service.request_report("update", {
            **request, "report_id": report["report_id"], "title": "Rewritten",
        }, token=TOKEN, origin=ORIGIN)
    assert error.value.code == "immutable_report"
    assert service.open_report(report["report_id"], token=TOKEN) == {"report": report, "html": HTML}


def test_import_report_without_html_preserves_id_hash_and_coverage_positions(tmp_path):
    service = librarian(tmp_path)
    missing_first, present_offline, missing_last = ["d-" + value * 32 for value in "abc"]
    report_id = "r-" + "d" * 32
    digest = hashlib.sha256(HTML.encode("utf-8")).hexdigest()

    def import_offline_document(identity, operation_id):
        service.request_source("import", {"operation_id": operation_id, "document": {
            "source_id": identity, "sha256": "1" * 64, "title": "Offline evidence", "summary": "No local copy",
        }}, token=TOKEN, origin=ORIGIN)
        return service.decide(operation_id, "confirm", token=TOKEN, origin=ORIGIN)["results"][0]

    assert import_offline_document(present_offline, "offline")["file_path"] is None
    request = {"operation_id": "report-import", "report": {
        "report_id": report_id, "sha256": digest, "title": "Imported report",
        "summary": "Original HTML is unavailable",
        "covered_docs": [missing_first, present_offline, missing_last],
    }}
    service.request_report("import", request, token=TOKEN, origin=ORIGIN)
    assert service.list_reports(token=TOKEN) == []
    decision = service.decide("report-import", "confirm", token=TOKEN, origin=ORIGIN)
    assert decision["status"] == "confirmed"
    imported, = decision["results"]
    assert imported["report_id"] == report_id
    assert imported["sha256"] == digest
    assert imported["file_path"] is None
    assert imported["status"] == "completed"
    assert imported["covered_docs"] == ["d-unknown", present_offline, "d-unknown"]
    assert [row["source_id"] for row in service.list_sources(token=TOKEN)] == [present_offline]

    import_offline_document(missing_first, "found-first")
    import_offline_document(missing_last, "found-last")
    assert librarian(tmp_path).open_report(report_id, token=TOKEN) == {"report": imported, "html": None}


def test_import_report_attaches_only_matching_cached_html(tmp_path):
    service = librarian(tmp_path)
    draft = service.stage_report(HTML, token=TOKEN, origin=ORIGIN)
    report_id = "r-" + "b" * 32
    request = {"operation_id": "import-with-html", "draft_id": draft["draft_id"], "report": {
        "report_id": report_id, "sha256": "4" * 64, "title": "Original HTML available",
        "summary": "Transferred report", "covered_docs": ["d-unknown"],
    }}
    with pytest.raises(CommandError) as error:
        service.request_report("import", request, token=TOKEN, origin=ORIGIN)
    assert error.value.code == "hash_mismatch"
    request["report"]["sha256"] = draft["sha256"]
    service.request_report("import", request, token=TOKEN, origin=ORIGIN)
    result = service.decide("import-with-html", "confirm", token=TOKEN, origin=ORIGIN)
    entry, = result["results"]
    assert librarian(tmp_path).open_report(report_id, token=TOKEN) == {"report": entry, "html": HTML}
    with pytest.raises(CommandError) as error:
        service.request_report("import", {**request, "operation_id": "unrelated-import"}, token=TOKEN, origin=ORIGIN)
    assert error.value.code == "immutable_report"
