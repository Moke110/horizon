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


def test_synthesis_resolves_each_citation_to_its_own_source(tmp_path):
    from hostrun.editor_reports import ReportService

    token = "editor-test"
    reports = ReportService(tmp_path, token=token)
    observed = ["The trial found no effect.", "The follow-up found a small effect."]
    evidence = []
    for i, text in enumerate(observed):
        material = reports.materials.register_text(
            {"schema_version": 1, "operation_id": str(i), "title": f"Study {i+1}",
             "source_label": f"Study {i+1}", "text": text}, token=token)
        evidence.append(report_evidence(reports.materials, material["material_id"], "task", token=token))
    result = reports.save_synthesis("op", "task", "Do the studies agree?", evidence, {}, [
        {"kind": "disagreement", "text": "两次观察结论不同。",
         "citations": [{"material_id": row["material_id"], "locator": "p7", "excerpt": excerpt}
                       for row, excerpt in zip(evidence, observed, strict=True)]}
    ], token=token)
    assert result["title"] == "Do the studies agree?"
    assert len(result["sources"]) == 2
    assert [item["locator"] for item in result["claims"][0]["citations"]] == ["p1", "p1"]
    reopened = ReportService(tmp_path, token=token)
    assert [reopened.citation(result["report_id"], "task", 0, i, token=token)["segment"]
            for i in range(2)] == observed


def test_synthesis_does_not_count_duplicate_or_reposted_sources_as_corroboration(tmp_path):
    import pytest
    from hostrun.agent_service import CommandError
    from hostrun.editor_reports import ReportService

    token = "editor-test"
    reports = ReportService(tmp_path, token=token)
    evidence = []
    for i, text in enumerate(["The trial found no effect.", "The trial found no effect.", "It found an effect."]):
        material = reports.materials.register_text(
            {"schema_version": 1, "operation_id": str(i), "title": f"Copy {i}",
             "source_label": f"Copy {i}", "text": text}, token=token)
        evidence.append(report_evidence(reports.materials, material["material_id"], "task", token=token))
    cites = [{"material_id": row["material_id"], "locator": "p1", "excerpt": row["text"]}
             for row in evidence]
    repost = evidence[2]["material_id"]
    with pytest.raises(CommandError, match="至少两份独立来源"):
        reports.save_synthesis("op", "task", "Discussion", evidence, {}, [
            {"kind": "disagreement", "text": "结论不一。", "citations": cites}
        ], reposts=[repost], token=token)
    saved = reports.save_synthesis("op", "task", "Discussion", evidence, {}, [
        {"kind": "source", "text": "研究未观察到效果。", "citations": cites[:2]}
    ], reposts=[repost], token=token)
    assert saved["claims"][0]["independent_source_count"] == 1
    assert saved["sources"][2]["repost"]


def test_synthesis_excludes_unsourced_and_questionable_background_and_records_partial_coverage(tmp_path, monkeypatch):
    from hostrun.editor_reports import ReportService
    from hostrun.editor_synthesis import commit_synthesis, prepare_synthesis
    from hostrun.knowledge_cards import KnowledgeCardService

    from test_knowledge_cards import captured_source

    token = "s"
    reports = ReportService(tmp_path, token=token)
    saved, citation = captured_source(reports.materials, monkeypatch)
    excerpt = citation["excerpt"]
    cards = KnowledgeCardService(tmp_path, token=token)
    backed = [citation]
    confirmed = cards.create({"schema_version": 1, "operation_id": "confirmed",
        "title": "Known", "content": excerpt, "sources": backed}, token=token)
    cards.create({"schema_version": 1, "operation_id": "pending",
        "title": "Pending", "content": "Unverified"}, token=token)
    cards.create({"schema_version": 1, "operation_id": "question",
        "title": "Questionable", "content": "In dispute", "questionable": True,
        "sources": backed}, token=token)
    command = {"question": "What changed?", "material_ids": ["missing", saved["material_id"]]}
    context, evidence, versions, reposts = prepare_synthesis(reports, tmp_path, "task", command, token=token)

    assert [item["id"] for item in context["confirmed_knowledge"]] == [confirmed["card_id"]]
    assert context["gaps"] == [{"material_id": "missing", "reason": "unavailable"}]
    result = commit_synthesis(reports, "no-new", "task", command, context, evidence, versions, reposts,
                              {"outcome": "no_new"}, token=token)
    assert result["outcome"] == "no_new"
    assert reports.get_by_operation("no-new", "task", command["material_ids"], token=token) is None

    output = {"claims": [{"kind": "known", "text": "会议已有两次记录在案的投票。",
                          "knowledge_id": confirmed["card_id"]}]}
    receipt = commit_synthesis(reports, "expand", "task", command, context, evidence, versions, reposts,
                               output, token=token)
    report = reports.get(receipt["report_id"], "task", token=token)
    assert report["gaps"] == context["gaps"]
    assert report["claims"][0]["knowledge"]["version"] == 1
    assert reports.get_by_operation("expand", "task", command["material_ids"], token=token) == report
