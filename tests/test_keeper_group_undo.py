"""Public-behavior tests for K9: whole-group undo of one approved Keeper proposal.

The seam under test is ``KeeperGroupUndo`` beside the existing owner services in
the stable data home. An approved proposal is the only source of truth: its
deterministic per-owner receipts, ordered approval snapshot and the owners'
true prior versions are re-derived under one ``BEGIN IMMEDIATE``. Undo commits
a new owner version per target and never mutates the approval.
"""

import pytest

from hostrun.agent_service import AgentService, CommandError
from hostrun.desktop_bridge import KeeperCommands, keeper_bridge_command
from hostrun.frontier import FrontierService
from hostrun.keeper import KeeperPrincipal, KeeperService
from hostrun.keeper_group_undo import KeeperGroupUndo
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_history import KnowledgeHistoryService
from hostrun.knowledge_themes import KnowledgeThemeService
from tests.test_keeper import TOKEN, Owner

EXPLORER = KeeperPrincipal("explorer", False)


def approved(owner, groups, *, request="group-request", decision="group-decision"):
    """Submit, review and approve one group; return its frozen approval input."""
    receipt = owner.request(groups, operation=request)
    reviewed = owner.reviewed(receipt)
    assert reviewed["status"] == "pending"
    assert len(reviewed["proposal_ids"]) == 1
    identifier = reviewed["proposal_ids"][0]
    stored = owner.keeper.view(identifier, token=TOKEN)
    payload = {
        "schema_version": 1,
        "operation_id": decision,
        "proposal_id": identifier,
        "revision": stored["revision"],
        "expected_versions": [change["expected_version"]
                              for change in stored["changes"]],
        "decision": "approve",
    }
    result = owner.keeper.decide(payload, token=TOKEN, principal=EXPLORER)
    assert result["status"] == "approved"
    return identifier, result, stored


def preview_command(identifier, revision, *, operation="undo-preview"):
    return {"schema_version": 1, "operation_id": operation,
            "proposal_id": identifier, "revision": revision}


def commit_command(identifier, revision, preview, *, operation="undo-commit"):
    return {
        "schema_version": 1, "operation_id": operation,
        "proposal_id": identifier, "revision": revision,
        "expected_versions": list(preview["expected_versions"]),
        "preview_token": preview["preview_token"],
    }


def test_group_undo_archives_a_created_card_and_keeps_approval_immutable(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    identifier, decision, proposal = approved(
        owner, [{"changes": [owner.card_change([owner.source(material)])]}])
    card_id = decision["applied"][0]["id"]
    card = owner.cards.view(card_id, token=TOKEN)

    undo = KeeperGroupUndo(owner.keeper)
    preview = undo.preview(
        preview_command(identifier, proposal["revision"]),
        token=TOKEN, principal=EXPLORER)

    assert preview["schema_version"] == 1
    assert preview["operation_id"] == "undo-preview"
    assert preview["proposal_id"] == identifier
    assert preview["revision"] == proposal["revision"]
    assert preview["can_undo"] is True
    assert preview["expected_versions"] == [card["version"]]
    assert isinstance(preview["preview_token"], str)
    assert [(target["kind"], target["id"], target["action"],
             target["approval_action"])
            for target in preview["targets"]] == [
        ("card", card_id, "archive", "create")]
    assert preview["targets"][0]["projected"]["status"] == "archived"

    receipt = undo.commit(
        commit_command(identifier, proposal["revision"], preview),
        token=TOKEN, principal=EXPLORER)

    assert receipt["status"] == "undone"
    assert receipt["proposal_id"] == identifier
    assert [(item["kind"], item["id"], item["status"])
            for item in receipt["applied"]] == [("card", card_id, "archived")]
    archived = owner.cards.view(card_id, token=TOKEN)
    assert archived["status"] == "archived"
    assert archived["version"] == card["version"] + 1

    terminal = owner.keeper.view(identifier, token=TOKEN)
    assert terminal["status"] == "approved"
    assert terminal["decision"] == decision
    assert terminal["undo"] == receipt


def reopened(tmp_path, owner):
    """A fresh Keeper over the same stable home, as after a process restart."""
    return KeeperService(
        AgentService(tmp_path, token=TOKEN), owner.materials,
        KnowledgeCardService(tmp_path, token=TOKEN),
        KnowledgeThemeService(tmp_path, token=TOKEN),
        FrontierService(tmp_path, token=TOKEN), token=TOKEN)


def test_version_drift_blocks_and_shows_approved_current_inverse(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    card = owner.create_card("Original title", material)
    identifier, decision, proposal = approved(owner, [{"changes": [{
        "kind": "card", "action": "update", "id": card["card_id"],
        "expected_version": card["version"], "fields": {"title": "Keeper title"},
        "reason": "Refine.", "sources": [owner.source(material)]}]}])
    edited = owner.cards.update({
        "schema_version": 1, "operation_id": "manual-after-approval",
        "card_id": card["card_id"], "expected_version": 2,
        "title": "Edited later"}, token=TOKEN)

    undo = KeeperGroupUndo(owner.keeper)
    preview = undo.preview(
        preview_command(identifier, proposal["revision"]),
        token=TOKEN, principal=EXPLORER)

    assert preview["can_undo"] is False
    assert preview["code"] == "version_conflict"
    assert preview["preview_token"] is None
    target = preview["targets"][0]
    assert target["approved_version"] == decision["applied"][0]["version"] == 2
    assert target["current_version"] == edited["version"] == 3
    assert target["can_undo"] is False
    title = next(change for change in target["changes"]
                 if change["field"] == "title")
    assert title == {"field": "title", "approved": "Keeper title",
                     "current": "Edited later", "inverse": "Original title"}

    with pytest.raises(CommandError) as rejected:
        undo.commit(commit_command(identifier, proposal["revision"], {
            "expected_versions": [2], "preview_token": "0" * 64}),
            token=TOKEN, principal=EXPLORER)
    assert rejected.value.code == "version_conflict"
    assert owner.cards.view(card["card_id"], token=TOKEN)["version"] == 3


def test_commit_is_replayable_and_rejects_stale_or_colliding_plans(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    identifier, decision, proposal = approved(
        owner, [{"changes": [owner.card_change([owner.source(material)])]}])
    card_id = decision["applied"][0]["id"]
    undo = KeeperGroupUndo(owner.keeper)
    preview = undo.preview(
        preview_command(identifier, proposal["revision"]),
        token=TOKEN, principal=EXPLORER)
    command = commit_command(identifier, proposal["revision"], preview)

    with pytest.raises(CommandError) as stale:
        undo.commit({**command, "preview_token": "0" * 64},
                    token=TOKEN, principal=EXPLORER)
    assert stale.value.code == "preview_conflict"
    with pytest.raises(CommandError) as wrong_versions:
        undo.commit({**command, "expected_versions": [99]},
                    token=TOKEN, principal=EXPLORER)
    assert wrong_versions.value.code == "version_conflict"

    receipt = undo.commit(command, token=TOKEN, principal=EXPLORER)
    assert undo.commit(command, token=TOKEN, principal=EXPLORER) == receipt

    with pytest.raises(CommandError) as again:
        undo.commit(commit_command(identifier, proposal["revision"], preview,
                                   operation="undo-again"),
                    token=TOKEN, principal=EXPLORER)
    assert again.value.code == "already_undone"
    with pytest.raises(CommandError) as collision:
        undo.commit({**command, "expected_versions": [99]},
                    token=TOKEN, principal=EXPLORER)
    assert collision.value.code == "operation_conflict"
    assert owner.cards.view(card_id, token=TOKEN)["version"] == 2


def test_authentication_and_non_approved_groups_are_guarded(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    reviewed = owner.reviewed(owner.request(
        [{"changes": [owner.card_change([owner.source(material)])]}],
        operation="pending-request"))
    identifier = reviewed["proposal_ids"][0]
    command = preview_command(identifier, 1)
    undo = KeeperGroupUndo(owner.keeper)

    with pytest.raises(PermissionError):
        undo.preview(command, token="wrong", principal=EXPLORER)
    with pytest.raises(PermissionError):
        undo.commit(commit_command(identifier, 1, {
            "expected_versions": [1], "preview_token": "0" * 64}),
            token=TOKEN, principal=KeeperPrincipal("explorer", True))

    plan = undo.preview(command, token=TOKEN, principal=EXPLORER)
    assert plan["can_undo"] is False
    assert plan["code"] == "invalid_transition"
    with pytest.raises(CommandError) as pending:
        undo.commit(commit_command(identifier, 1, {
            "expected_versions": [1], "preview_token": "0" * 64}),
            token=TOKEN, principal=EXPLORER)
    assert pending.value.code == "invalid_transition"

    rejected = owner.reviewed(owner.request(
        [{"changes": [owner.card_change([owner.source(material)])]}],
        operation="reject-request"))
    reject_id = rejected["proposal_ids"][0]
    owner.keeper.decide({
        "schema_version": 1, "operation_id": "reject-decision",
        "proposal_id": reject_id, "revision": 1,
        "expected_versions": [None], "decision": "reject",
    }, token=TOKEN, principal=EXPLORER)
    rejected_plan = undo.preview(
        preview_command(reject_id, 1), token=TOKEN, principal=EXPLORER)
    assert rejected_plan["can_undo"] is False
    assert rejected_plan["code"] == "invalid_transition"


def test_bridge_routes_trusted_group_undo_without_caller_authority(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    identifier, decision, proposal = approved(
        owner, [{"changes": [owner.card_change([owner.source(material)])]}])
    commands = KeeperCommands(lambda: owner.keeper, object(), token=TOKEN,
                              emit=lambda kind, data: None)
    base = {"schema_version": 1, "proposal_id": identifier,
            "revision": proposal["revision"]}

    with pytest.raises(CommandError):
        keeper_bridge_command(
            commands, {"action": "keeper_undo_preview", "operation_id": "p",
                       **base, "principal": "caller"},
            test_enabled=False)
    with pytest.raises(CommandError):
        keeper_bridge_command(
            commands, {"action": "keeper_undo_preview", "operation_id": "p",
                       **base, "expected_versions": [1]},
            test_enabled=False)

    kind, preview = keeper_bridge_command(
        commands, {"action": "keeper_undo_preview", "operation_id": "p",
                   **base}, test_enabled=False)
    assert kind == "keeper_undo_preview"
    assert preview["can_undo"] is True
    kind, receipt = keeper_bridge_command(commands, {
        "action": "keeper_undo", "operation_id": "undo-bridge",
        **base, "expected_versions": preview["expected_versions"],
        "preview_token": preview["preview_token"],
        "reason": "恢复合并前的关系。",
    }, test_enabled=False)
    assert kind == "keeper_undo"
    assert receipt["status"] == "undone"
    assert receipt["reason"] == "恢复合并前的关系。"
    assert owner.keeper.audit(identifier, token=TOKEN)[-1]["reason"] == receipt["reason"]
    assert owner.cards.view(decision["applied"][0]["id"],
                            token=TOKEN)["status"] == "archived"


def test_restored_theme_pins_a_card_restored_in_the_same_group(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    card = owner.create_card("Linked card", material)
    theme = owner.themes.create({
        "schema_version": 1, "operation_id": "base-theme",
        "judgement": "The linked concept holds.",
        "related_cards": [{"card_id": card["card_id"],
                           "version": card["version"]}],
        "evidence": [],
    }, token=TOKEN)
    identifier, decision, proposal = approved(owner, [{"changes": [
        {"kind": "card", "action": "delete", "id": card["card_id"],
         "expected_version": card["version"], "fields": {},
         "reason": "Archive it.", "sources": [owner.source(material)]},
        {"kind": "theme", "action": "update", "id": theme["theme_id"],
         "expected_version": theme["version"],
         "fields": {"judgement": "The keeper judgement holds."},
         "reason": "Refine.", "sources": [owner.source(material)]},
    ]}])
    assert owner.cards.view(card["card_id"], token=TOKEN)["status"] == "archived"

    undo = KeeperGroupUndo(owner.keeper)
    preview = undo.preview(
        preview_command(identifier, proposal["revision"]),
        token=TOKEN, principal=EXPLORER)
    assert preview["can_undo"] is True
    theme_target = next(target for target in preview["targets"]
                        if target["kind"] == "theme")
    card_target = next(target for target in preview["targets"]
                       if target["kind"] == "card")
    projected_card = card_target["projected_version"]
    assert theme_target["projected"]["related_cards"] == [
        {"card_id": card["card_id"], "version": projected_card}]
    relation_diff = next(change for change in theme_target["changes"]
                         if change["field"] == "related_cards")
    assert relation_diff["inverse"] == theme_target["projected"]["related_cards"]

    receipt = undo.commit(
        commit_command(identifier, proposal["revision"], preview),
        token=TOKEN, principal=EXPLORER)
    assert receipt["status"] == "undone"
    restored_card = owner.cards.view(card["card_id"], token=TOKEN)
    restored_theme = owner.themes.view(theme["theme_id"], token=TOKEN)
    assert restored_card["status"] == "active"
    assert restored_theme["status"] == "active"
    assert restored_theme["related_cards"] == [
        {"card_id": card["card_id"], "version": restored_card["version"]}]


def test_external_active_incoming_link_blocks_archiving_a_created_card(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    identifier, decision, proposal = approved(
        owner, [{"changes": [owner.card_change([owner.source(material)])]}])
    card = owner.cards.view(decision["applied"][0]["id"], token=TOKEN)
    owner.themes.create({
        "schema_version": 1, "operation_id": "external-theme",
        "judgement": "An external Theme cites the new Card.",
        "related_cards": [{"card_id": card["card_id"],
                           "version": card["version"]}],
    }, token=TOKEN)

    undo = KeeperGroupUndo(owner.keeper)
    preview = undo.preview(
        preview_command(identifier, proposal["revision"]),
        token=TOKEN, principal=EXPLORER)
    assert preview["can_undo"] is False
    assert preview["code"] == "invalid_relation"
    with pytest.raises(CommandError) as rejected:
        undo.commit(commit_command(identifier, proposal["revision"], {
            "expected_versions": [card["version"]],
            "preview_token": "0" * 64}),
            token=TOKEN, principal=EXPLORER)
    assert rejected.value.code == "invalid_relation"
    assert owner.cards.view(card["card_id"], token=TOKEN)["status"] == "active"


def test_removed_material_invalidates_a_stale_group_preview(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    identifier, decision, proposal = approved(
        owner, [{"changes": [owner.card_change([owner.source(material)])]}])
    card_id = decision["applied"][0]["id"]
    undo = KeeperGroupUndo(owner.keeper)
    preview = undo.preview(
        preview_command(identifier, proposal["revision"]),
        token=TOKEN, principal=EXPLORER)
    assert preview["can_undo"] is True

    owner.materials.archive_material(material["material_id"], token=TOKEN)
    impact = owner.materials.deletion_impact(material["material_id"], token=TOKEN)
    owner.materials.delete_material(material["material_id"],
                                    impact["confirmation"], token=TOKEN)

    with pytest.raises(CommandError) as stale:
        undo.commit(
            commit_command(identifier, proposal["revision"], preview),
            token=TOKEN, principal=EXPLORER)
    assert stale.value.code == "preview_conflict"
    assert owner.cards.view(card_id, token=TOKEN)["status"] == "active"

    fresh = undo.preview(
        preview_command(identifier, proposal["revision"], operation="fresh"),
        token=TOKEN, principal=EXPLORER)
    source = fresh["targets"][0]["projected"]["sources"][0]
    assert source["status"] == "removed"
    assert source["material_id"] == material["material_id"]


def test_fault_injection_rolls_back_and_replays_after_reopen(tmp_path, monkeypatch):
    owner = Owner(tmp_path)
    material = owner.material()
    card = owner.create_card("Linked card", material)
    theme = owner.themes.create({
        "schema_version": 1, "operation_id": "fault-theme",
        "judgement": "The linked concept holds.",
        "related_cards": [{"card_id": card["card_id"],
                           "version": card["version"]}],
    }, token=TOKEN)
    identifier, decision, proposal = approved(owner, [{"changes": [
        {"kind": "card", "action": "update", "id": card["card_id"],
         "expected_version": card["version"], "fields": {"title": "Keeper title"},
         "reason": "Refine.", "sources": [owner.source(material)]},
        {"kind": "theme", "action": "update", "id": theme["theme_id"],
         "expected_version": theme["version"],
         "fields": {"judgement": "The keeper judgement holds."},
         "reason": "Refine.", "sources": [owner.source(material)]},
    ]}])
    undo = KeeperGroupUndo(owner.keeper)
    preview = undo.preview(
        preview_command(identifier, proposal["revision"]),
        token=TOKEN, principal=EXPLORER)
    assert len(preview["targets"]) == 2
    command = commit_command(identifier, proposal["revision"], preview)
    card_after_approval = owner.cards.view(card["card_id"], token=TOKEN)
    theme_after_approval = owner.themes.view(theme["theme_id"], token=TOKEN)
    assert card_after_approval["version"] == card["version"] + 1
    assert theme_after_approval["version"] == theme["version"] + 1

    original = KnowledgeHistoryService.write_version
    calls = []

    def flaky(db, meta, record, **kwargs):
        calls.append(record["version"])
        if len(calls) == 2:
            raise RuntimeError("injected write failure")
        return original(db, meta, record, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(KnowledgeHistoryService, "write_version",
                      staticmethod(flaky))
        with pytest.raises(RuntimeError):
            undo.commit(command, token=TOKEN, principal=EXPLORER)

    assert calls == [3, 3]
    assert owner.cards.view(card["card_id"], token=TOKEN) == card_after_approval
    assert owner.themes.view(theme["theme_id"], token=TOKEN) == theme_after_approval
    assert owner.keeper.view(identifier, token=TOKEN).get("undo") is None
    assert [event["type"] for event in owner.keeper.audit(identifier, token=TOKEN)] == [
        "keeper.decision"]

    fresh = KeeperGroupUndo(reopened(tmp_path, owner))
    fresh_preview = fresh.preview(
        preview_command(identifier, proposal["revision"], operation="undo-fresh"),
        token=TOKEN, principal=EXPLORER)
    fresh_command = commit_command(identifier, proposal["revision"],
                                   fresh_preview, operation="undo-fresh-commit")
    receipt = fresh.commit(fresh_command, token=TOKEN, principal=EXPLORER)
    assert receipt["status"] == "undone"
    assert owner.cards.view(card["card_id"], token=TOKEN)["version"] == 3
    assert owner.themes.view(theme["theme_id"], token=TOKEN)["version"] == 3

    again = KeeperGroupUndo(reopened(tmp_path, owner))
    assert again.commit(fresh_command, token=TOKEN,
                        principal=EXPLORER) == receipt


def test_individual_history_undo_cannot_invert_keeper_records(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    card = owner.create_card("Original title", material)
    identifier, decision, proposal = approved(owner, [{"changes": [{
        "kind": "card", "action": "update", "id": card["card_id"],
        "expected_version": card["version"], "fields": {"title": "Keeper title"},
        "reason": "Refine.", "sources": [owner.source(material)]}]}])
    KeeperGroupUndo(owner.keeper).commit(
        commit_command(identifier, proposal["revision"],
                       KeeperGroupUndo(owner.keeper).preview(
                           preview_command(identifier, proposal["revision"]),
                           token=TOKEN, principal=EXPLORER)),
        token=TOKEN, principal=EXPLORER)

    services = {"card": owner.cards, "theme": owner.themes,
                "frontier": owner.frontiers}
    from hostrun.desktop_bridge import knowledge_history_command
    kind, page = knowledge_history_command(services, {
        "action": "knowledge_history", "schema_version": 1, "kind": "card",
        "id": card["card_id"], "operation_id": "history",
    }, token=TOKEN)
    assert kind == "knowledge_history"
    assert [entry["version"] for entry in page["entries"]] == [3, 2, 1]
    assert [entry["action"] for entry in page["entries"]] == [
        "group_undo", "update", "create"]
    assert page["entries"][0]["can_preview"] is False
    assert page["entries"][1]["can_preview"] is False

    for target_version in (2, 3):
        kind, plan = knowledge_history_command(services, {
            "action": "knowledge_undo_preview", "schema_version": 1,
            "kind": "card", "id": card["card_id"],
            "operation_id": f"preview-{target_version}",
            "target_version": target_version,
        }, token=TOKEN)
        assert kind == "knowledge_undo_preview"
        assert plan["can_undo"] is False
        with pytest.raises(CommandError) as rejected:
            knowledge_history_command(services, {
                "action": "knowledge_undo", "schema_version": 1,
                "kind": "card", "id": card["card_id"],
                "operation_id": f"undo-{target_version}",
                "expected_version": 3, "target_version": target_version,
                "preview_token": "0" * 64,
            }, token=TOKEN)
        assert rejected.value.code == "undo_not_supported"


def test_frontier_group_undo_deletes_created_and_restores_deleted(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    fields = {
        "question": "How should the optics be calibrated?",
        "status": "following",
        "scope": {"text": "Calibration", "alternatives": [],
                  "sources": ["fixture"]},
        "hard_constraints": {"must_include": [], "exclude": []},
        "relations": [],
    }
    identifier, decision, proposal = approved(owner, [{"changes": [{
        "kind": "frontier", "action": "create", "fields": fields,
        "reason": "Record the open question.",
        "sources": [owner.source(material)]}]}],
        request="frontier-create-request", decision="frontier-create-decision")
    created = owner.frontiers.view(decision["applied"][0]["id"], token=TOKEN)

    undo = KeeperGroupUndo(owner.keeper)
    preview = undo.preview(
        preview_command(identifier, proposal["revision"]),
        token=TOKEN, principal=EXPLORER)
    assert preview["targets"][0]["action"] == "archive"
    assert preview["targets"][0]["projected"]["status"] == "deleted"
    from hostrun.librarian import Librarian
    librarian = Librarian(owner.cards, token=TOKEN)
    librarian.request_frontier("update", {"schema_version": 1, "operation_id": "locked-frontier",
        "frontier_id": created["frontier_id"], "expected_version": created["version"], "status": "paused"},
        token=TOKEN, origin="gui")
    with pytest.raises(CommandError, match="确认"):
        undo.commit(commit_command(identifier, proposal["revision"], preview), token=TOKEN, principal=EXPLORER)
    librarian.decide("locked-frontier", "reject", token=TOKEN, origin="gui")
    undo.commit(commit_command(identifier, proposal["revision"], preview),
                token=TOKEN, principal=EXPLORER)
    deleted = owner.frontiers.view(created["frontier_id"], token=TOKEN)
    assert deleted["status"] == "deleted"
    assert deleted["version"] == created["version"] + 1

    base = owner.frontiers.create({
        "schema_version": 1, "operation_id": "base-frontier",
        "question": "Which calibration survives a year?",
        "status": "following",
        "scope": {"text": "Annual drift", "alternatives": ["A"],
                  "sources": ["fixture"]},
        "hard_constraints": {"must_include": ["precision"], "exclude": []},
        "relations": [],
    }, token=TOKEN)
    identifier, decision, proposal = approved(owner, [{"changes": [{
        "kind": "frontier", "action": "delete", "id": base["frontier_id"],
        "expected_version": base["version"], "fields": {},
        "reason": "Resolved.", "sources": [owner.source(material)]}]}],
        request="frontier-delete-request", decision="frontier-delete-decision")
    assert owner.frontiers.view(base["frontier_id"],
                                token=TOKEN)["status"] == "deleted"
    preview = undo.preview(
        preview_command(identifier, proposal["revision"],
                        operation="frontier-undo-preview"),
        token=TOKEN, principal=EXPLORER)
    undo.commit(commit_command(identifier, proposal["revision"], preview,
                               operation="frontier-undo-commit"),
                token=TOKEN, principal=EXPLORER)
    restored = owner.frontiers.view(base["frontier_id"], token=TOKEN)
    assert restored["status"] == base["status"]
    assert restored["question"] == base["question"]
    assert restored["scope"] == base["scope"]
    assert restored["version"] == base["version"] + 2
