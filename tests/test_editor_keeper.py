import copy

import pytest

from hostrun.editor_reports import ReportService, report_evidence
from hostrun.keeper import KeeperPrincipal
from test_keeper_runtime import Owner, TOKEN


def test_production_keeper_only_accepts_a_committed_editor_evidence_request(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    task = owner.task["task_id"]
    reports = ReportService(tmp_path, token=TOKEN)
    evidence = report_evidence(owner.materials, material["material_id"], task, token=TOKEN)
    excerpt = evidence["locators"][1]["text"]
    report = reports.save("editor", task, evidence, {}, [
        {"kind": "source", "text": "探测器每日记录辐射。",
         "citations": [{"excerpt": excerpt, "locator": "p2"}]}], token=TOKEN,
        task_version=owner.task["version"], keeper_proposals=[
            {"kind": "card", "claim_index": 0, "reason": "可复用的观察",
             "fields": {"title": "辐射传感器", "content": "校准传感器记录辐射。"}},
            {"kind": "preference", "claim_index": 0, "reason": "不属于知识库", "fields": {}}])
    command, = ReportService(tmp_path, token=TOKEN).pending_keeper_requests(token=TOKEN)
    assert command["report_id"] == report["report_id"]
    assert command["report_version"] == 1
    assert len(command["groups"]) == 1
    with pytest.raises(PermissionError):
        owner.keeper.request(command, token=TOKEN, principal=KeeperPrincipal("editor", True))
    tampered = copy.deepcopy(command)
    tampered["groups"][0]["changes"][0]["fields"]["content"] = "A different payload"
    principal = KeeperPrincipal("editor", False)
    with pytest.raises(PermissionError):
        owner.keeper.request(tampered, token=TOKEN, principal=principal)
    queued = owner.keeper.request(command, token=TOKEN, principal=principal)
    assert owner.keeper.request(command, token=TOKEN, principal=principal) == queued
    assert owner.keeper.queued(token=TOKEN, production_only=True) == [queued["request_id"]]
    assert owner.cards.query(token=TOKEN) == []

    def review(context):
        assert context["report_id"] == report["report_id"]
        assert context["report_version"] == 1
        assert context["provenance"] == {"source": "editor"}
        return {"status": "reviewed"}

    reviewed = owner.keeper.review(queued["request_id"], token=TOKEN, dispatch=review)
    proposal = owner.keeper.view(reviewed["proposal_ids"][0], token=TOKEN)
    assert proposal["provenance"] == "editor"
    decision = {"schema_version": 1, "operation_id": "explorer-approve",
                "proposal_id": proposal["proposal_id"], "revision": proposal["revision"],
                "expected_versions": [None], "decision": "approve"}
    with pytest.raises(PermissionError):
        owner.keeper.decide(decision, token=TOKEN, principal=principal)
    approved = owner.keeper.decide(decision, token=TOKEN, principal=KeeperPrincipal("explorer", False))
    assert approved["status"] == "approved"
    assert len(owner.cards.query(token=TOKEN)) == 1


def test_frontier_proposal_keeps_current_scope_until_explorer_approval(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    task = owner.task["task_id"]
    frontier = owner.frontiers.create({"schema_version": 1, "operation_id": "frontier",
        "question": "How are sensors calibrated?", "hard_constraints": {},
        "scope": {"text": "calibration", "sources": ["arxiv"]}}, token=TOKEN)
    frontier_id = frontier["frontier_id"]
    reports = ReportService(tmp_path, token=TOKEN)
    evidence = report_evidence(owner.materials, material["material_id"], task, token=TOKEN)
    excerpt = evidence["locators"][1]["text"]
    claims = [{"kind": "source", "text": "校准传感器每日测量辐射。",
               "citations": [{"excerpt": excerpt, "locator": "p2"}]}]
    proposed = {"kind": "frontier", "id": frontier_id, "claim_index": 0,
                "reason": "补充来源明确支持的测量频率。",
                "fields": {"question": "How often do calibrated sensors measure radiation?"}}
    versions = {"knowledge": [{"kind": "frontier", "id": frontier_id, "version": 1}]}
    reports.save("unscoped", task, evidence, versions, claims, token=TOKEN,
                 task_version=owner.task["version"], keeper_proposals=[proposed])
    assert reports.pending_keeper_requests(token=TOKEN) == []

    reports.save("scoped", task, evidence, {**versions, "writing_frontier_id": frontier_id}, claims,
                 token=TOKEN, task_version=owner.task["version"], keeper_proposals=[proposed])
    request, = reports.pending_keeper_requests(token=TOKEN)
    keeper = owner.keeper
    queued = keeper.request(request, token=TOKEN, principal=KeeperPrincipal("editor", False))
    reviewed = keeper.review(queued["request_id"], token=TOKEN, dispatch=lambda _: {"status": "reviewed"})
    assert owner.frontiers.view(frontier_id, token=TOKEN) == frontier
    proposal = keeper.view(reviewed["proposal_ids"][0], token=TOKEN)
    decision = keeper.decide({"schema_version": 1, "operation_id": "approve-frontier", "decision": "approve",
        "proposal_id": proposal["proposal_id"], "revision": 1, "expected_versions": [frontier["version"]]},
        token=TOKEN, principal=KeeperPrincipal("explorer", False))
    assert decision["status"] == "approved"
    assert owner.frontiers.view(frontier_id, token=TOKEN)["question"] == proposed["fields"]["question"]
