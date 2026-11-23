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
    prefs.change({"action": "save", "key": "structure", "operation_id": "structure",
                  "value": "conclusion_first", "expected_version": 0}, token=token)
    prefs.change({"action": "reset", "operation_id": "reset"}, token=token)
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
