from hostrun.editor_preferences import PreferenceService
from hostrun.frontier import FrontierService


def test_writing_preferences_compare_scopes_and_keep_one_time_local(tmp_path):
    token = "launch"
    service = PreferenceService(tmp_path, token=token)
    frontier = FrontierService(tmp_path, token=token).create(
        {"schema_version": 1, "operation_id": "frontier", "question": "Research?",
         "hard_constraints": {}, "scope": {"text": "topic", "sources": ["arxiv"]}}, token=token)
    frontier_id = frontier["frontier_id"]
    assert service.effective(token=token)["length"]["value"] == "standard"
    service.change({"operation_id": "global", "action": "save", "key": "length",
                    "value": "concise", "expected_version": 0}, token=token)
    service.change({"operation_id": "scoped", "action": "save", "key": "length",
                    "value": "detailed", "frontier_id": frontier_id, "expected_version": 0}, token=token)
    reopened = PreferenceService(tmp_path, token=token)
    assert reopened.effective(token=token)["length"]["value"] == "concise"
    assert reopened.effective(frontier_id=frontier_id, token=token)["length"]["value"] == "detailed"
    assert reopened.effective(frontier_id=frontier_id, once={"length": "standard"},
                              token=token)["length"]["scope"] == "once"
    assert reopened.effective(frontier_id=frontier_id, token=token)["length"]["value"] == "detailed"


def test_preference_lifecycle_keeps_immutable_report_provenance(tmp_path):
    import pytest
    from hostrun.agent_service import CommandError
    from hostrun.editor_reports import ReportService, report_evidence

    token = "launch"
    prefs = PreferenceService(tmp_path, token=token)
    reports = ReportService(tmp_path, token=token)
    text = "A report source recorded six measurements."
    registered = reports.materials.register_text(
        {"schema_version": 1, "operation_id": "material", "title": "Measurement",
         "text": text, "source_label": "Notes"}, token=token)
    evidence = report_evidence(reports.materials, registered["material_id"], "task", token=token)
    claim = {"kind": "source", "text": "资料记录了六次测量。",
             "citations": [{"locator": "p1", "excerpt": text}]}
    initial = reports.save("first", "task", evidence,
        {"preferences": prefs.effective(token=token)}, [claim], token=token)
    feedback = {"score": 2, "reason": "篇幅太长"}
    saved = reports.set_feedback(initial["report_id"], "task", 1, "rating", 0, token=token, **feedback)
    assert reports.set_feedback(initial["report_id"], "task", 1, "rating", 0, token=token, **feedback) == saved
    assert prefs.effective(token=token)["length"]["scope"] == "default"
    assert prefs.list(task_id="other", token=token)["candidates"] == []
    candidate, = prefs.list(task_id="task", token=token)["candidates"]
    command = {"action": "adopt", "operation_id": "adopt", "candidate_id": candidate["id"], "expected_version": 0}
    with pytest.raises(CommandError, match="候选已不可用"):
        prefs.change(command, task_id="other", token=token)
    adopted = prefs.change(command, task_id="task", token=token)
    assert prefs.change(command, task_id="task", token=token) == adopted
    assert adopted["source"]["report_id"] == initial["report_id"]
    assert prefs.effective(token=token)["length"]["value"] == "concise"
    assert prefs.list(task_id="task", token=token)["candidates"] == []

    with pytest.raises(CommandError, match="已更新"):
        prefs.change({"action": "disable", "key": "length", "operation_id": "stale",
                      "expected_version": 0}, token=token)
    disabled = prefs.change({"action": "disable", "key": "length", "operation_id": "disable",
                             "expected_version": adopted["version"]}, token=token)
    assert prefs.effective(token=token)["length"]["scope"] == "default"
    edited = prefs.change({"action": "save", "key": "length", "operation_id": "edit",
                           "value": "detailed", "expected_version": disabled["version"]}, token=token)
    assert prefs.effective(token=token)["length"]["value"] == "detailed"
    prefs.change({"action": "delete", "key": "length", "operation_id": "delete",
                  "expected_version": edited["version"]}, token=token)
    structure = prefs.change({"action": "save", "key": "structure", "operation_id": "structure",
                  "value": "conclusion_first", "expected_version": 0}, token=token)
    prefs.change({"action": "reset", "operation_id": "reset",
                  "expected_revisions": {"structure": structure["version"]}}, token=token)
    assert prefs.list(token=token)["preferences"] == []
    actual = reports.get(initial["report_id"], "task", token=token)
    assert actual["context_versions"] == initial["context_versions"]
    assert actual["claims"] == initial["claims"]
    with pytest.raises(CommandError, match="支持的写作样式"):
        prefs.effective(token=token, once={"citations": "disabled"})


def test_replacement_rating_retracts_unadopted_style_suggestion(tmp_path):
    from hostrun.editor_reports import ReportService, report_evidence

    token = "launch"
    service = PreferenceService(tmp_path, token=token)
    reports = ReportService(tmp_path, token=token)
    material = reports.materials.register_text(
        {"schema_version": 1, "operation_id": "material", "title": "Measurement",
         "text": "Six measurements.", "source_label": "Notes"}, token=token)
    evidence = report_evidence(reports.materials, material["material_id"], "task", token=token)
    report = reports.save("write", "task", evidence, {}, [
        {"kind": "source", "text": "六次测量。", "citations": [{"excerpt": "Six measurements.", "locator": "p1"}]}],
        token=token)
    reports.set_feedback(report["report_id"], "task", 1, "style", 0,
                         score=2, reason="太简略", token=token)
    assert len(service.list(task_id="task", token=token)["candidates"]) == 1
    reports.set_feedback(report["report_id"], "task", 1, "check-source", 1,
                         score=2, reason="引用的资料不正确", token=token)
    assert service.list(task_id="task", token=token)["candidates"] == []


def test_stale_scope_reset_does_not_discard_a_concurrent_preference(tmp_path):
    import pytest
    from hostrun.agent_service import CommandError

    token = "launch"
    first = PreferenceService(tmp_path, token=token)
    other = PreferenceService(tmp_path, token=token)
    saved = first.change({"action": "save", "key": "length", "operation_id": "length",
                          "value": "concise", "expected_version": 0}, token=token)
    stale = {"action": "reset", "operation_id": "reset", "expected_revisions": {"length": saved["version"]}}
    added = other.change({"action": "save", "key": "structure", "operation_id": "structure",
                          "value": "sections", "expected_version": 0}, token=token)
    with pytest.raises(CommandError, match="已更新"):
        first.change(stale, token=token)
    assert other.effective(token=token)["structure"]["value"] == "sections"
    current = {**stale, "expected_revisions": {**stale["expected_revisions"], "structure": added["version"]}}
    assert first.change(current, token=token) == first.change(current, token=token)
    assert other.list(token=token)["preferences"] == []


def test_cross_domain_feedback_binds_report_claim_and_frontier_without_writing(tmp_path):
    import pytest
    from hostrun.agent_service import CommandError
    from hostrun.editor_reports import ReportService, report_evidence

    token = "launch"
    reports = ReportService(tmp_path, token=token)
    frontier_service = FrontierService(tmp_path, token=token)
    frontier = frontier_service.create({"schema_version": 1, "operation_id": "frontier",
        "question": "Research?", "hard_constraints": {}, "scope": {"text": "topic", "sources": ["arxiv"]}},
        token=token)
    material = reports.materials.register_text(
        {"schema_version": 1, "operation_id": "material", "title": "Measurement",
         "text": "Six measurements.", "source_label": "Notes"}, token=token)
    evidence = report_evidence(reports.materials, material["material_id"], "task", token=token)
    frontier_id = frontier["frontier_id"]
    report = reports.save("write", "task", evidence, {"writing_frontier_id": frontier_id}, [
        {"kind": "source", "text": "六次测量。", "citations": [{"excerpt": "Six measurements.", "locator": "p1"}]}],
        token=token)
    report_id = report["report_id"]
    note = "请把复现问题纳入范围。"
    draft = reports.prepare_feedback_handoff(report_id, "task", 1, owner="frontier", note=note,
                                             claim_index=0, token=token)
    assert draft == {"owner": "frontier", "task_id": "task", "report_id": report_id,
                     "report_version": 1, "claim_index": 0, "claim_text": "六次测量。",
                     "note": note, "report_frontier_id": frontier_id,
                     "frontier_scope": {"frontier_id": frontier_id, "version": 1, "question": "Research?"}}
    for owner in ("voyager", "knowledge"):
        other_draft = reports.prepare_feedback_handoff(report_id, "task", 1, owner=owner, note=note, token=token)
        assert (other_draft["owner"], other_draft["note"], other_draft["report_id"]) == (owner, note, report_id)
    with pytest.raises(CommandError, match="找不到报道"):
        reports.prepare_feedback_handoff(report_id, "other", 1, owner="frontier", note=note, token=token)
    assert frontier_service.view(frontier_id, token=token) == frontier
    assert reports.get(report_id, "task", token=token) == report
