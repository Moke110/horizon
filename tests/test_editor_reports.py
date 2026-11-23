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

    assert reopened.get(report["report_id"], "task", token=token) == report
    assert reopened.save("operation", "task", evidence, {"knowledge": [], "preferences": []},
                         [claim], token=token) == report
    assert reopened.citation(report["report_id"], "task", 0, 0, token=token)["segment"] == body


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


def test_large_source_limits_report_coverage_but_preserves_original_reading(tmp_path):
    import json
    import pytest
    from hostrun.agent_service import CommandError
    from hostrun.editor_reports import ReportService, build_editor_context

    token = "editor-test"
    materials = MaterialsService(tmp_path, token=token)
    body = "This is the initial observation. " + ("观测" * 32_000) + "\nA later observation."
    material = materials.register_text(
        {"schema_version": 1, "operation_id": "source", "title": "Observations",
         "source_label": "Field notes", "text": body}, token=token)
    evidence = report_evidence(materials, material["material_id"], "task", token=token)
    context = build_editor_context(evidence, [])
    assert len(json.dumps(context, ensure_ascii=False).encode()) < 65_536
    assert context["source"]["limitation"]

    service = ReportService(tmp_path, token=token)
    claim = {"kind": "source", "text": "有一条观察。",
             "citations": [{"locator": "p2", "excerpt": "A later observation."}]}
    with pytest.raises(CommandError, match="不在本次提供的快照片段"):
        service.save("operation", "task", evidence, {}, [claim], token=token)
    claim["citations"] = [{"locator": "p1", "excerpt": "This is the initial observation."}]
    report = service.save("operation", "task", evidence, {}, [claim], token=token)
    assert report["limitation"] == context["source"]["limitation"]
    with pytest.raises(CommandError, match="找不到报道"):
        service.get(report["report_id"], "different-task", token=token)
    original = service.citation(report["report_id"], "task", 0, 0, snapshot_offset=0, token=token)
    assert original["segment"] == body[:12_000]
    assert original["next_offset"] == 12_000

def test_report_feedback_is_revisioned_withdrawn_and_scoped_to_version(tmp_path):
    import pytest
    from hostrun.agent_service import CommandError
    from hostrun.editor_reports import ReportService

    token = "editor-test"
    service = ReportService(tmp_path, token=token)
    material = service.materials.register_text(
        {"schema_version": 1, "operation_id": "source", "title": "Observations",
         "source_label": "Field notes", "text": "Twelve observations."}, token=token)
    evidence = report_evidence(service.materials, material["material_id"], "task", token=token)
    report = service.save("report", "task", evidence, {}, [{"kind": "unknown", "text": "样本未知"}], token=token)
    identity = report["report_id"]
    first = service.set_feedback(identity, "task", 1, "first", 0, score=3,
                                 reason="需要解释", claim_index=0, token=token)
    assert first["revision"] == 1
    assert first["score"] == 3
    restarted = ReportService(tmp_path, token=token)
    assert restarted.get(identity, "task", token=token, version=1)["feedback"] == first
    with pytest.raises(CommandError, match="反馈已更新"):
        restarted.set_feedback(identity, "task", 1, "stale", 0, score=4, token=token)
    withdrawn = restarted.set_feedback(identity, "task", 1, "withdraw", 1, score=None, token=token)
    assert withdrawn["score"] is None
    assert restarted.set_feedback(identity, "task", 1, "first", 0, score=3,
                                  reason="需要解释", claim_index=0, token=token) == first
    assert restarted.get(identity, "task", token=token, version=1)["feedback"] == withdrawn

def test_rewrite_reuses_report_identity_and_preserves_old_version_and_feedback(tmp_path):
    import pytest
    from hostrun.agent_service import CommandError
    from hostrun.editor_reports import ReportService

    token = "editor-test"
    service = ReportService(tmp_path, token=token)
    material = service.materials.register_text(
        {"schema_version": 1, "operation_id": "source", "title": "Observations",
         "source_label": "Field notes", "text": "Twelve observations."}, token=token)
    evidence = report_evidence(service.materials, material["material_id"], "task", token=token)
    source = {"kind": "source", "text": "原始记录有十二条观察。",
              "citations": [{"excerpt": "Twelve observations.", "locator": "p1"}]}
    v1 = service.save("report", "task", evidence, {}, [source], token=token)
    identity = v1["report_id"]
    service.set_feedback(identity, "task", 1, "rating", 0, score=4, token=token)
    changed = {**source, "text": "来源描述了十二条观察。"}
    v2 = service.save("rewrite", "task", evidence, {}, [changed], report_id=identity, base_version=1, token=token)
    assert (v2["report_id"], v2["version"]) == (identity, 2)
    assert service.get_by_operation("rewrite", "task", material["material_id"], token=token) == v2
    assert service.get(identity, "task", version=2, token=token) == v2
    assert service.get(identity, "task", version=1, token=token)["feedback"]["score"] == 4
    assert service.get(identity, "task", version=1, token=token)["claims"] == v1["claims"]
    assert service.citation(identity, "task", 0, 0, version=2, token=token)["segment"] == "Twelve observations."
    assert service.save("rewrite", "task", evidence, {}, [changed], report_id=identity, base_version=1, token=token) == v2
    with pytest.raises(CommandError, match="已有新版本"):
        service.save("old", "task", evidence, {}, [changed], report_id=identity, base_version=1, token=token)
