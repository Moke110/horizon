from hostrun.editor_reports import report_evidence
from hostrun.voyager_materials import MaterialsService


def test_report_evidence_identifies_the_captured_body(tmp_path):
    materials = MaterialsService(tmp_path, token="editor-test")
    body = "The original source recorded twelve observations."
    saved = materials.register_text(
        {"schema_version": 1, "operation_id": "input", "title": "Observations",
         "source_label": "Field notes", "text": body},
        token="editor-test",
    )

    evidence = report_evidence(materials, saved["material_id"], "task", token="editor-test")

    assert evidence["text"] == body
    assert evidence["material_version"] == 1
    assert evidence["snapshot"] == saved["snapshot"]
    assert evidence["limitation"] is None


def test_report_commit_preserves_identity_and_opens_the_pinned_excerpt(tmp_path):
    from hostrun.editor_reports import ReportService

    token = "editor-test"
    materials = MaterialsService(tmp_path, token=token)
    body = "The original source recorded twelve observations."
    saved = materials.register_text(
        {"schema_version": 1, "operation_id": "input", "title": "Observations",
         "source_label": "Field notes", "text": body}, token=token)
    evidence = report_evidence(materials, saved["material_id"], "task", token=token)
    claim = {"kind": "source", "text": "原始记录包含十二条观察。",
             "citations": [{"excerpt": body, "locator": "p1"}]}
    report = ReportService(tmp_path, token=token).save(
        "operation", "task", evidence, {"knowledge": [], "preferences": []},
        [claim], token=token)
    reopened = ReportService(tmp_path, token=token)

    assert reopened.get(report["report_id"], token=token) == report
    assert reopened.save("operation", "task", evidence, {"knowledge": [], "preferences": []},
                         [claim], token=token) == report
    assert reopened.citation(report["report_id"], 0, 0, token=token)["segment"] == body


def test_report_refuses_excerpts_missing_from_the_selected_snapshot(tmp_path):
    import pytest
    from hostrun.agent_service import CommandError
    from hostrun.editor_reports import ReportService

    token = "editor-test"
    materials = MaterialsService(tmp_path, token=token)
    saved = materials.register_text(
        {"schema_version": 1, "operation_id": "input", "title": "Observations",
         "source_label": "Field notes", "text": "The original source recorded twelve observations."},
        token=token)
    evidence = report_evidence(materials, saved["material_id"], "task", token=token)
    service = ReportService(tmp_path, token=token)
    claim = {"kind": "source", "text": "记录包含二十条观察。",
             "citations": [{"excerpt": "twenty observations", "locator": "p1"}]}

    with pytest.raises(CommandError) as error:
        service.save("operation", "task", evidence, {}, [claim], token=token)

    assert error.value.code == "invalid_source"
    claim["citations"][0]["excerpt"] = "twelve observations"
    claim["text"] = "记录包含十二条观察。"
    assert service.save("operation", "task", evidence, {}, [claim], token=token)["version"] == 1
