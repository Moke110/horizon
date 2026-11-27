import hashlib
import os
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



@pytest.mark.parametrize("substituted", [False, True])
def test_correcting_report_publishes_new_identity_and_retires_old_original(tmp_path, monkeypatch, substituted):
    service = librarian(tmp_path)
    covered = document(service)
    draft = service.stage_report(HTML, token=TOKEN, origin=ORIGIN)
    request = {
        "operation_id": "original", "draft_id": draft["draft_id"],
        "title": "Finding", "summary": "Research overview", "covered_docs": [covered],
    }
    service.request_report("create", request, token=TOKEN, origin=ORIGIN)
    old, = service.decide("original", "confirm", token=TOKEN, origin=ORIGIN)["results"]
    old_id = old["report_id"]
    with pytest.raises(CommandError, match="另起任务"):
        service.request_report("replace", {
            **request, "operation_id": "same-task", "replaces_report_id": old_id,
        }, token=TOKEN, origin=ORIGIN)

    origin = "agent:correction"
    corrected_html = "<h1>Corrected finding</h1>"
    draft = service.stage_report(corrected_html, token=TOKEN, origin=origin)
    correction = {
        **request, "operation_id": "bad-correction", "draft_id": draft["draft_id"],
        "title": "Corrected finding", "replaces_report_id": old_id,
    }
    with pytest.raises(CommandError):
        service.request_report("replace", {**correction, "covered_docs": []}, token=TOKEN, origin=origin)
    assert service.open_report(old_id, token=TOKEN)["html"] == HTML

    service.request_report("replace", correction, token=TOKEN, origin=origin)

    def file_unavailable(*args, **kwargs):
        raise PermissionError("Archive file unavailable")

    with monkeypatch.context() as write_failure:
        write_failure.setattr(os, "link", file_unavailable)
        returned = service.decide("bad-correction", "confirm", token=TOKEN, origin=origin)
    assert returned["status"] == "returned"
    assert service.open_report(old_id, token=TOKEN)["html"] == HTML
    assert service.list_reports(token=TOKEN) == [old]

    correction["operation_id"] = "correction"
    service.request_report("replace", correction, token=TOKEN, origin=origin)
    assert service.open_report(old_id, token=TOKEN)["html"] == HTML
    old_path = Path(old["file_path"])
    if substituted:
        old_path.write_text("Unrelated file", encoding="utf-8")
    decision = service.decide("correction", "confirm", token=TOKEN, origin=origin)
    assert decision["status"] == "confirmed"
    new, = decision["results"]
    assert new["report_id"] != old_id and new["replaces_report_id"] == old_id
    if substituted:
        assert old_path.read_text(encoding="utf-8") == "Unrelated file"
    else:
        assert not old_path.exists()
    reopened = librarian(tmp_path)
    assert reopened.open_report(new["report_id"], token=TOKEN) == {"report": new, "html": corrected_html}
    assert reopened.list_reports(token=TOKEN) == [new]
    with pytest.raises(CommandError, match="报道已删除") as error:
        reopened.open_report(old_id, token=TOKEN)
    assert error.value.code == "report_deleted"
    with pytest.raises(CommandError, match="报道已删除"):
        reopened.request_report("import", {"operation_id": "import-old", "report": old}, token=TOKEN, origin=origin)
    assert reopened.decide("correction", "confirm", token=TOKEN, origin=origin) == decision


def test_interrupted_file_publication_is_recovered_before_retrying_a_correction(tmp_path, monkeypatch):
    service = librarian(tmp_path)
    covered = document(service)
    draft = service.stage_report(HTML, token=TOKEN, origin=ORIGIN)
    request = {"operation_id": "original", "draft_id": draft["draft_id"], "title": "Finding",
               "summary": "Evidence", "covered_docs": [covered]}
    service.request_report("create", request, token=TOKEN, origin=ORIGIN)
    old, = service.decide("original", "confirm", token=TOKEN, origin=ORIGIN)["results"]
    correction_origin = "agent:separate-correction"
    corrected_html = "<h1>Corrected original</h1>"
    draft = service.stage_report(corrected_html, token=TOKEN, origin=correction_origin)
    service.request_report("replace", {**request, "operation_id": "correction", "draft_id": draft["draft_id"],
        "replaces_report_id": old["report_id"]}, token=TOKEN, origin=correction_origin)

    link = os.link

    def interrupted_after_write(*args, **kwargs):
        link(*args, **kwargs)
        raise RuntimeError("Stopped before registration committed")

    with monkeypatch.context() as failure:
        failure.setattr(os, "link", interrupted_after_write)
        with pytest.raises(RuntimeError, match="Stopped before registration committed"):
            service.decide("correction", "confirm", token=TOKEN, origin=correction_origin)

    reopened = librarian(tmp_path)
    assert reopened.list_reports(token=TOKEN) == [old]
    old_path = Path(old["file_path"])
    assert list(old_path.parent.glob("*.html")) == [old_path]
    assert reopened.open_report(old["report_id"], token=TOKEN)["html"] == HTML
    corrected, = reopened.decide("correction", "confirm", token=TOKEN, origin=correction_origin)["results"]
    assert reopened.open_report(corrected["report_id"], token=TOKEN)["html"] == corrected_html


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


def test_unconfirmed_import_does_not_claim_a_file_at_its_future_path(tmp_path):
    service = librarian(tmp_path)
    draft = service.stage_report(HTML, token=TOKEN, origin=ORIGIN)
    identity = "r-" + "b" * 32
    service.request_report("import", {"operation_id": "import", "draft_id": draft["draft_id"], "report": {
        "report_id": identity, "sha256": draft["sha256"], "title": "Imported report", "summary": "Evidence",
        "covered_docs": ["d-unknown"],
    }}, token=TOKEN, origin=ORIGIN)
    path = tmp_path / "archive" / "originals" / "reports" / f"{identity}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HTML, encoding="utf-8")

    reopened = librarian(tmp_path)
    assert reopened.list_reports(token=TOKEN) == []
    assert reopened.decide("import", "reject", token=TOKEN, origin=ORIGIN)["status"] == "rejected"
    assert path.read_text(encoding="utf-8") == HTML


def test_import_retains_correction_provenance_and_requires_a_new_task_to_correct(tmp_path):
    service = librarian(tmp_path)
    covered = document(service)
    previous = "r-" + "1" * 32
    successor = "r-" + "2" * 32
    original = {"sha256": "a" * 64, "title": "Original", "summary": "Evidence", "covered_docs": [covered]}
    for identity, extra in ((previous, {}), (successor, {"replaces_report_id": previous})):
        service.request_report("import", {"operation_id": identity, "report": {
            **original, "report_id": identity, **extra,
        }}, token=TOKEN, origin=ORIGIN)
        service.decide(identity, "confirm", token=TOKEN, origin=ORIGIN)
    assert service.open_report(successor, token=TOKEN)["report"]["replaces_report_id"] == previous
    assert service.open_report(previous, token=TOKEN)["report"]["report_id"] == previous
    staged = service.stage_report(HTML, token=TOKEN, origin=ORIGIN)
    with pytest.raises(CommandError, match="另起任务"):
        service.request_report("replace", {
            "operation_id": "same-import-task", "title": "Correction", "summary": "Evidence",
            "draft_id": staged["draft_id"], "covered_docs": [covered], "replaces_report_id": successor,
        }, token=TOKEN, origin=ORIGIN)


def test_confirmed_reassessment_changes_the_projection_without_rewriting_the_report(tmp_path):
    service = librarian(tmp_path)
    covered = document(service)
    draft = service.stage_report(HTML, token=TOKEN, origin=ORIGIN)
    service.request_report("create", {
        "operation_id": "publication", "draft_id": draft["draft_id"],
        "title": "Finding", "summary": "Research overview", "covered_docs": [covered],
    }, token=TOKEN, origin=ORIGIN)
    original, = service.decide("publication", "confirm", token=TOKEN, origin=ORIGIN)["results"]
    identity = original["report_id"]
    assessment = {"decision": "invalid", "basis": "interpretation", "reason": "The primary reading reverses the result.",
                  "evidence": "Table 2 gives a negative estimate, while the headline claims an increase."}
    card = service.request_report("assess", {"operation_id": "reassessment", "report_id": identity, **assessment},
                                  token=TOKEN, origin=ORIGIN)
    assert card["status"] == "pending"
    assert service.view_report(identity, token=TOKEN) == original
    assert service.decide("reassessment", "confirm", token=TOKEN, origin=ORIGIN)["status"] == "confirmed"

    reopened = librarian(tmp_path)
    assert reopened.open_report(identity, token=TOKEN) == {"report": {**original, "assessment": assessment}, "html": HTML}
    assert reopened.list_reports(token=TOKEN) == [{**original, "assessment": assessment}]
    assert Path(original["file_path"]).read_bytes() == HTML.encode("utf-8")

    disagreement = {**assessment, "decision": "questionable", "basis": "disagreement"}
    with pytest.raises(CommandError, match="重评依据"):
        reopened.request_report("assess", {"operation_id": "disagreement", "report_id": identity, **disagreement},
                                token=TOKEN, origin=ORIGIN)
    assert reopened.view_report(identity, token=TOKEN)["assessment"] == assessment

    later_decisions = [
        {"decision": "questionable", "basis": "methodology", "reason": "The effect still needs verification.",
         "evidence": "Appendix A reveals a cohort mismatch, but its size is not documented."},
        {"decision": "completed", "basis": "correction", "reason": "The cited effect and cohort were verified.",
         "evidence": "The corrected reading of Appendix A matches the analysis and report."},
    ]
    for index, newer in enumerate(later_decisions):
        request_id = f"reassessment-{index}"
        reopened.request_report("assess", {"operation_id": request_id, "report_id": identity, **newer},
                                token=TOKEN, origin=ORIGIN)
        reopened.decide(request_id, "confirm", token=TOKEN, origin=ORIGIN)
        assert librarian(tmp_path).open_report(identity, token=TOKEN) == {
            "report": {**original, "assessment": newer}, "html": HTML,
        }
    assert reopened.decide("reassessment", "confirm", token=TOKEN, origin=ORIGIN)["results"][0]["assessment"] == assessment
    assert len(reopened.list_reports(token=TOKEN)) == 1
