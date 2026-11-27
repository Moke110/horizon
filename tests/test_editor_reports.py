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

def test_legacy_report_refuses_revision_and_preserves_original_and_feedback(tmp_path):
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
    with pytest.raises(CommandError, match="另起任务"):
        service.save("rewrite", "task", evidence, {}, [changed], report_id=identity, base_version=1, token=token)
    assert service.get_by_operation("rewrite", "task", material["material_id"], token=token) is None
    assert service.get(identity, "task", version=1, token=token)["feedback"]["score"] == 4
    assert service.get(identity, "task", version=1, token=token)["claims"] == v1["claims"]
    assert service.citation(identity, "task", 0, 0, version=1, token=token)["segment"] == "Twelve observations."


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
    import json

    from hostrun.editor_reports import ReportService
    from hostrun.editor_synthesis import commit_synthesis, prepare_synthesis
    from hostrun.knowledge_cards import KnowledgeCardService
    from hostrun.managed_database import database_connection

    from test_knowledge_cards import captured_source

    token = "s"
    reports = ReportService(tmp_path, token=token)
    saved, citation = captured_source(reports.materials, monkeypatch)
    excerpt = citation["excerpt"]
    cards = KnowledgeCardService(tmp_path, token=token)
    backed = [citation]
    confirmed = cards.create({"schema_version": 1, "operation_id": "confirmed",
        "title": "Known", "content": excerpt, "sources": backed}, token=token)
    followed = cards.create({"schema_version": 1, "operation_id": "followed",
        "title": "Legacy unresolved", "content": excerpt, "sources": backed}, token=token)
    # Seed the snapshot as written by the old client, before validity and tracking existed.
    legacy = {key: value for key, value in followed.items() if key not in ("validity", "tracked")}
    legacy.update(convergence="unresolved", questionable=True)
    with database_connection(tmp_path) as db:
        db.execute("UPDATE knowledge_cards SET result=? WHERE card_id=?",
                   (json.dumps(legacy), followed["card_id"]))
        db.commit()
    cards.create({"schema_version": 1, "operation_id": "pending",
        "title": "Pending", "content": "Unverified"}, token=token)
    cards.create({"schema_version": 1, "operation_id": "question",
        "title": "Questionable", "content": "In dispute", "questionable": True,
        "sources": backed}, token=token)
    archived = cards.create({"schema_version": 1, "operation_id": "formerly-known",
        "title": "Formerly known", "content": excerpt, "sources": backed}, token=token)
    cards.archive({"schema_version": 1, "operation_id": "archive", "card_id": archived["card_id"],
                   "expected_version": 1}, token=token)
    removed = reports.materials.register_text({"schema_version": 1, "operation_id": "withdrawn",
        "title": "Withdrawn", "source_label": "Field notes", "text": excerpt}, token=token)
    removed_id = removed["material_id"]
    cards.create({"schema_version": 1, "operation_id": "partially-backed",
        "title": "Partial sources", "content": excerpt,
        "sources": [citation, {**citation, "material_id": removed_id}]}, token=token)
    reports.materials.archive_material(removed_id, token=token)
    impact = reports.materials.deletion_impact(removed_id, token=token)
    reports.materials.delete_material(removed_id, impact["confirmation"], token=token)
    command = {"question": "What changed?", "material_ids": ["missing", saved["material_id"]]}
    context, evidence, versions, reposts = prepare_synthesis(reports, tmp_path, "task", command, token=token)

    assert {item["id"] for item in context["confirmed_knowledge"]} == {confirmed["card_id"], followed["card_id"]}
    assert context["gaps"] == [{"material_id": "missing", "reason": "unavailable"}]
    result = commit_synthesis(reports, "no-new", "task", command, context, evidence, versions, reposts,
                              {"outcome": "no_new"}, token=token)
    assert result["outcome"] == "no_new"
    assert reports.get_by_operation("no-new", "task", command["material_ids"], token=token) is None

    output = {"claims": [{"kind": "known", "text": "会议已有两次记录在案的投票。",
                          "knowledge_id": followed["card_id"]}]}
    receipt = commit_synthesis(reports, "expand", "task", command, context, evidence, versions, reposts,
                               output, token=token)
    report = reports.get(receipt["report_id"], "task", token=token)
    assert report["gaps"] == context["gaps"]
    assert report["claims"][0]["knowledge"]["version"] == 1
    assert reports.get_by_operation("expand", "task", command["material_ids"], token=token) == report

def test_legacy_synthesis_refuses_revision_and_retains_prior_citation(tmp_path):
    import pytest
    from hostrun.agent_service import CommandError
    from hostrun.editor_reports import ReportService
    from hostrun.editor_synthesis import commit_synthesis, prepare_synthesis

    token = "editor-test"
    reports = ReportService(tmp_path, token=token)
    ids = []
    for i, passage in enumerate(("First note estimates twelve observations.", "Second note estimates twenty observations.")):
        material = reports.materials.register_text({"schema_version":1, "operation_id":f"source-{i}",
            "title":f"Observations {i}", "source_label":"Field notes", "text":passage}, token=token)
        ids.append(material["material_id"])
    command = {"question":"为什么两个数目不同？", "material_ids":ids}
    context, evidence, versions, reposts = prepare_synthesis(reports, tmp_path, "task", command, token=token)
    claims = [{"kind":"disagreement","text":"两个估计不同。","citations":[
        {"material_id":ids[0],"locator":"p1","excerpt":"twelve observations"},
        {"material_id":ids[1],"locator":"p1","excerpt":"twenty observations"}]}]
    first = commit_synthesis(reports, "initial", "task", command, context, evidence, versions, reposts,
                             {"claims":claims}, token=token)
    rewrite = {**command, "report_id":first["report_id"], "base_version":1}
    claims[0]["text"] = "计数分别是十二和二十。"
    with pytest.raises(CommandError, match="另起任务"):
        commit_synthesis(reports, "rewrite", "task", rewrite, context, evidence, versions, reposts,
                         {"claims":claims}, token=token)
    value = reports.get(first["report_id"], "task", version=1, token=token)
    assert value["title"] == command["question"]
    assert value["selected_material_ids"] == ids
    assert value["claims"][0]["independent_source_count"] == 2
    assert "Second note" in reports.citation(first["report_id"], "task", 0, 1, version=1, token=token)["segment"]
    assert reports.get_by_operation("rewrite", "task", ids, token=token) is None
