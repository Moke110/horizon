"""Public-behavior tests for K5: knowledge history, preview and non-covering undo.

The seam under test is the authenticated desktop bridge
``knowledge_history_command`` over the three existing owners (Card, Theme and
Frontier). History is derived from each owner's immutable operation receipts
and version chain; undo commits a new owner version and never rewrites an old
snapshot, request or event. No live source, model or GUI is contacted.
"""

import pytest

from hostrun.agent_service import CommandError
from hostrun.frontier import FrontierService
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_themes import KnowledgeThemeService


class Clock:
    """Deterministic, strictly increasing timestamps for stable ordering."""

    def __init__(self, start=1000):
        self.now = start

    def __call__(self):
        self.now += 1
        return self.now


def owners(tmp_path, token="s"):
    clock = Clock()
    cards = KnowledgeCardService(tmp_path, token=token, clock=clock)
    themes = KnowledgeThemeService(tmp_path, token=token, cards=cards, clock=clock)
    frontiers = FrontierService(tmp_path, token=token, clock=clock)
    return {"card": cards, "theme": themes, "frontier": frontiers}


def bridge(services, action, kind, identifier, operation_id, *, token="s", **extra):
    from hostrun.desktop_bridge import knowledge_history_command

    command = {
        "action": action, "schema_version": 1, "kind": kind,
        "id": identifier, "operation_id": operation_id,
    }
    command.update(extra)
    return knowledge_history_command(services, command, token=token)


def create_card(cards, operation_id="card-create", **overrides):
    command = {
        "schema_version": 1, "operation_id": operation_id,
        "title": "第一版标题", "content": "第一版内容。", "sources": [],
        "relations": [],
    }
    command.update(overrides)
    return cards.create(command, token="s")


def update_card(cards, card, operation_id, **overrides):
    command = {
        "schema_version": 1, "operation_id": operation_id,
        "card_id": card["card_id"], "expected_version": card["version"],
    }
    command.update(overrides)
    return cards.update(command, token="s")


def create_theme(themes, operation_id="theme-create", **overrides):
    command = {
        "schema_version": 1, "operation_id": operation_id,
        "judgement": "第一版判断。", "conditions": [], "related_cards": [],
        "evidence": [],
    }
    command.update(overrides)
    return themes.create(command, token="s")


def create_frontier(frontiers, operation_id="frontier-create", **overrides):
    command = {
        "schema_version": 1, "operation_id": operation_id,
        "question": "如何长期标定？", "status": "following",
        "scope": {"text": "第一版范围", "alternatives": ["甲"], "sources": ["arxiv"]},
        "hard_constraints": {"must_include": ["精度"], "exclude": []},
        "relations": [],
    }
    command.update(overrides)
    return frontiers.create(command, token="s")


def _versions(page):
    return [entry["version"] for entry in page["entries"]]


def _entry(page, version):
    return next(entry for entry in page["entries"] if entry["version"] == version)


def _change(preview, field):
    return next(change for change in preview["changes"] if change["field"] == field)


def captured_source(materials, monkeypatch):
    from hostrun import voyager_web

    excerpt = "Calibrated optical sensors measure received radiation every day."
    body = (f"<html><head><title>Observatory report</title></head><body><article>"
            f"<h1>Observatory report</h1><p>{(excerpt + ' ') * 6}</p>"
            f"</article></body></html>").encode()
    monkeypatch.setattr(
        voyager_web, "fetch_public",
        lambda url, headers: (200, {"Content-Type": "text/html; charset=utf-8"}, body))
    material = materials.register_web({
        "schema_version": 1, "operation_id": "public-source",
        "url": "https://example.org/observatory",
    }, token="s")
    return material, {
        "material_id": material["material_id"],
        "locator": "Results",
        "excerpt": excerpt,
    }


def test_history_derives_every_version_from_existing_owner_receipts(tmp_path):
    services = owners(tmp_path)
    cards = services["card"]
    created = create_card(cards, reason="建立卡片")
    second = update_card(cards, created, "card-update-2", title="第二版标题",
                         reason="修正标题")
    third = update_card(cards, second, "card-update-3", content="第三版内容。")

    kind, page = bridge(services, "knowledge_history", "card",
                        created["card_id"], "history-1")

    assert kind == "knowledge_history"
    assert page["operation_id"] == "history-1"
    assert (page["kind"], page["id"]) == ("card", created["card_id"])
    assert page["current_version"] == 3
    assert (page["total"], page["offset"], page["limit"]) == (3, 0, 10)
    assert _versions(page) == [3, 2, 1]

    newest, middle, oldest = page["entries"]
    assert set(newest) == {
        "version", "action", "actor", "time", "reason", "target_version",
        "before", "after", "can_preview"}
    assert newest["action"] == "update"
    assert newest["actor"] == "explorer"
    assert newest["time"] == third["updated_at"]
    assert newest["reason"] is None
    assert newest["target_version"] is None
    assert newest["before"] == second
    assert newest["after"] == third
    assert newest["can_preview"] is True

    assert middle["action"] == "update"
    assert middle["reason"] == "修正标题"
    assert middle["before"] == created
    assert middle["after"] == second

    assert oldest["action"] == "create"
    assert oldest["reason"] == "建立卡片"
    assert oldest["before"] is None
    assert oldest["after"] == created
    assert oldest["can_preview"] is False

    # The caller command and the launch token never appear in the projection.
    assert "command" not in page and "token" not in page
    assert all("command" not in entry for entry in page["entries"])

    # History is a derivation of the immutable receipt rows, not a copy: a
    # second read is identical and old snapshots keep their committed content.
    kind, again = bridge(services, "knowledge_history", "card",
                         created["card_id"], "history-2")
    assert kind == "knowledge_history"
    assert again["entries"] == page["entries"]
    assert again["entries"][-1]["after"] == created


def test_history_pages_and_archived_history_stays_readable(tmp_path):
    services = owners(tmp_path)
    cards = services["card"]
    created = create_card(cards)
    second = update_card(cards, created, "card-update-2", title="第二版标题")
    third = update_card(cards, second, "card-update-3", title="第三版标题")
    archived = cards.archive({
        "schema_version": 1, "operation_id": "card-archive",
        "card_id": created["card_id"], "expected_version": third["version"],
    }, token="s")

    kind, page = bridge(services, "knowledge_history", "card",
                        created["card_id"], "history-page", limit=2, offset=1)
    assert kind == "knowledge_history"
    assert page["current_version"] == archived["version"]
    assert (page["total"], page["offset"], page["limit"]) == (4, 1, 2)
    assert _versions(page) == [3, 2]

    kind, whole = bridge(services, "knowledge_history", "card",
                         created["card_id"], "history-whole")
    assert kind == "knowledge_history"
    assert _versions(whole) == [4, 3, 2, 1]
    assert whole["entries"][0]["action"] == "archive"
    assert whole["entries"][0]["can_preview"] is False
    assert all(entry["can_preview"] is False for entry in whole["entries"])

    for invalid in ({"limit": 21}, {"limit": 0}, {"limit": True},
                    {"offset": -1}, {"offset": "0"}):
        with pytest.raises(CommandError) as exc:
            bridge(services, "knowledge_history", "card", created["card_id"],
                   "history-invalid", **invalid)
        assert exc.value.code == "invalid_command"
    with pytest.raises(CommandError) as missing:
        bridge(services, "knowledge_history", "card", "missing", "history-missing")
    assert missing.value.code == "not_found"
    with pytest.raises(CommandError) as bad_kind:
        bridge(services, "knowledge_history", "note", created["card_id"],
               "history-kind")
    assert bad_kind.value.code == "invalid_command"


def test_undo_ordinary_update_keeps_later_unrelated_edit(tmp_path):
    services = owners(tmp_path)
    cards = services["card"]
    created = create_card(cards, title="甲", content="保留的后续内容。")
    second = update_card(cards, created, "card-update-2", title="乙",
                         reason="改标题")
    third = update_card(cards, second, "card-update-3", content="后来改的内容。")

    kind, preview = bridge(services, "knowledge_undo_preview", "card",
                           created["card_id"], "preview-1", target_version=2)
    assert kind == "knowledge_undo_preview"
    assert (preview["kind"], preview["id"]) == ("card", created["card_id"])
    assert (preview["target_version"], preview["current_version"]) == (2, 3)
    assert preview["can_undo"] is True
    assert preview["code"] is None
    assert isinstance(preview["preview_token"], str) and preview["preview_token"]
    kind, repeat_preview = bridge(services, "knowledge_undo_preview", "card",
                                  created["card_id"], "preview-1b",
                                  target_version=2)
    assert kind == "knowledge_undo_preview"
    assert repeat_preview["preview_token"] == preview["preview_token"]
    assert preview["changes"] == [{
        "field": "title", "before": "甲", "after": "乙", "current": "乙",
        "proposed": "甲", "conflict": False}]

    kind, undone = bridge(services, "knowledge_undo", "card",
                          created["card_id"], "undo-1", target_version=2,
                          expected_version=3,
                          preview_token=preview["preview_token"],
                          reason="撤销标题修改")
    assert kind == "knowledge_undo"
    assert (undone["operation_id"], undone["kind"], undone["id"]) == (
        "undo-1", "card", created["card_id"])
    record = undone["record"]
    assert record["version"] == 4
    assert record["title"] == "甲"
    assert record["content"] == "后来改的内容。"
    assert record["operation_id"] == "undo-1"
    assert cards.view(created["card_id"], token="s") == record

    kind, page = bridge(services, "knowledge_history", "card",
                        created["card_id"], "history-after-undo")
    assert kind == "knowledge_history"
    assert _versions(page) == [4, 3, 2, 1]
    undo_entry = page["entries"][0]
    assert undo_entry["action"] == "undo"
    assert undo_entry["target_version"] == 2
    assert undo_entry["reason"] == "撤销标题修改"
    assert undo_entry["before"] == third
    assert undo_entry["after"] == record


def test_stale_expected_version_and_wrong_token_leave_no_rows(tmp_path):
    services = owners(tmp_path)
    cards = services["card"]
    created = create_card(cards, title="甲")
    second = update_card(cards, created, "card-update-2", title="乙")
    third = update_card(cards, second, "card-update-3", content="后续内容。")

    kind, preview = bridge(services, "knowledge_undo_preview", "card",
                           created["card_id"], "preview-old", target_version=2)
    assert kind == "knowledge_undo_preview" and preview["can_undo"] is True

    # A newer version makes the reviewed expected version stale.
    with pytest.raises(CommandError) as stale:
        bridge(services, "knowledge_undo", "card", created["card_id"],
               "undo-stale", target_version=2, expected_version=2,
               preview_token=preview["preview_token"])
    assert stale.value.code == "version_conflict"
    assert cards.view(created["card_id"], token="s") == third

    # The reviewed plan is bound to its token; a forged token is rejected.
    kind, current_preview = bridge(services, "knowledge_undo_preview", "card",
                                   created["card_id"], "preview-current",
                                   target_version=2)
    assert kind == "knowledge_undo_preview" and current_preview["can_undo"] is True
    with pytest.raises(CommandError) as forged:
        bridge(services, "knowledge_undo", "card", created["card_id"],
               "undo-forged", target_version=2, expected_version=3,
               preview_token="0" * 64)
    assert forged.value.code == "preview_conflict"
    assert cards.view(created["card_id"], token="s") == third

    kind, page = bridge(services, "knowledge_history", "card",
                        created["card_id"], "history-unchanged")
    assert kind == "knowledge_history"
    assert _versions(page) == [3, 2, 1]

    # The correct reviewed token still commits a new version.
    kind, undone = bridge(services, "knowledge_undo", "card",
                          created["card_id"], "undo-ok", target_version=2,
                          expected_version=3,
                          preview_token=current_preview["preview_token"])
    assert kind == "knowledge_undo" and undone["record"]["version"] == 4
    assert undone["record"]["title"] == "甲"
    assert undone["record"]["content"] == "后续内容。"


def test_overlapping_later_edit_refuses_the_whole_undo(tmp_path):
    services = owners(tmp_path)
    cards = services["card"]
    created = create_card(cards, title="甲", content="原内容。")
    second = update_card(cards, created, "card-update-2", title="乙")
    third = update_card(cards, second, "card-update-3", title="丙")

    kind, preview = bridge(services, "knowledge_undo_preview", "card",
                           created["card_id"], "preview-conflict",
                           target_version=2)
    assert kind == "knowledge_undo_preview"
    assert preview["can_undo"] is False
    assert preview["code"] == "undo_conflict"
    assert preview["preview_token"] is None
    conflict = _change(preview, "title")
    assert conflict == {
        "field": "title", "before": "甲", "after": "乙", "current": "丙",
        "proposed": "甲", "conflict": True}
    # The untouched field is not part of the inverse patch.
    assert [change["field"] for change in preview["changes"]] == ["title"]

    with pytest.raises(CommandError) as refused:
        bridge(services, "knowledge_undo", "card", created["card_id"],
               "undo-conflict", target_version=2, expected_version=3,
               preview_token="anything")
    assert refused.value.code == "undo_conflict"
    assert cards.view(created["card_id"], token="s") == third
    kind, page = bridge(services, "knowledge_history", "card",
                        created["card_id"], "history-conflict")
    assert kind == "knowledge_history"
    assert _versions(page) == [3, 2, 1]


def test_replay_returns_original_receipt_before_state_checks(tmp_path):
    services = owners(tmp_path)
    cards = services["card"]
    created = create_card(cards, title="甲")
    second = update_card(cards, created, "card-update-2", title="乙")

    kind, preview = bridge(services, "knowledge_undo_preview", "card",
                           created["card_id"], "preview-replay",
                           target_version=2)
    assert kind == "knowledge_undo_preview" and preview["can_undo"] is True
    undo_command = {
        "target_version": 2, "expected_version": 2,
        "preview_token": preview["preview_token"],
    }
    kind, first = bridge(services, "knowledge_undo", "card",
                         created["card_id"], "undo-replay", **undo_command)
    assert kind == "knowledge_undo" and first["record"]["version"] == 3

    # Archive commits version 4, making the original expected version stale and
    # the record nonterminal. An identical operation still replays its receipt.
    archived = cards.archive({
        "schema_version": 1, "operation_id": "card-archive-replay",
        "card_id": created["card_id"], "expected_version": 3,
    }, token="s")
    assert archived["status"] == "archived"
    kind, replay = bridge(services, "knowledge_undo", "card",
                          created["card_id"], "undo-replay", **undo_command)
    assert kind == "knowledge_undo" and replay["record"] == first["record"]

    kind, page = bridge(services, "knowledge_history", "card",
                        created["card_id"], "history-replay")
    assert kind == "knowledge_history"
    assert _versions(page) == [4, 3, 2, 1]

    with pytest.raises(CommandError) as conflict:
        bridge(services, "knowledge_undo", "card", created["card_id"],
               "undo-replay", target_version=2, expected_version=4,
               preview_token=preview["preview_token"], reason="changed")
    assert conflict.value.code == "operation_conflict"


def test_later_reaffirmed_value_cannot_be_overwritten_by_early_undo(tmp_path):
    services = owners(tmp_path)
    cards = services["card"]
    first = create_card(cards, title="甲")
    second = update_card(cards, first, "second", title="乙")
    third = update_card(cards, second, "third", title="丙")
    fourth = update_card(cards, third, "fourth", title="乙")
    _, preview = bridge(services, "knowledge_undo_preview", "card",
                        first["card_id"], "preview", target_version=2)
    assert _change(preview, "title")["conflict"] is True
    assert preview["code"] == "undo_conflict" and not preview["can_undo"]
    assert cards.view(first["card_id"], token="s") == fourth


def test_repeat_undo_with_new_operation_cannot_create_empty_version(tmp_path):
    services = owners(tmp_path)
    cards = services["card"]
    first = create_card(cards, title="甲")
    second = update_card(cards, first, "second", title="乙")
    _, preview = bridge(services, "knowledge_undo_preview", "card",
                        first["card_id"], "preview", target_version=2)
    _, result = bridge(services, "knowledge_undo", "card", first["card_id"], "undo",
                       target_version=2, expected_version=second["version"],
                       preview_token=preview["preview_token"])
    _, empty = bridge(services, "knowledge_undo_preview", "card",
                      first["card_id"], "empty", target_version=2)
    assert empty["code"] == "nothing_to_undo" and not empty["can_undo"]
    _, redo = bridge(services, "knowledge_undo_preview", "card",
                     first["card_id"], "redo", target_version=3)
    assert redo["can_undo"] and _change(redo, "title")["proposed"] == "乙"
    assert cards.view(first["card_id"], token="s") == result["record"]


def test_authentication_is_required_and_failed_checks_leave_no_rows(tmp_path):
    services = owners(tmp_path)
    cards = services["card"]
    created = create_card(cards, title="甲")
    second = update_card(cards, created, "card-update-2", title="乙")

    kind, preview = bridge(services, "knowledge_undo_preview", "card",
                           created["card_id"], "preview-auth",
                           target_version=2)
    assert kind == "knowledge_undo_preview"

    for action, extra in (
            ("knowledge_history", {}),
            ("knowledge_undo_preview", {"target_version": 2}),
            ("knowledge_undo", {
                "target_version": 2, "expected_version": 2,
                "preview_token": preview["preview_token"]})):
        with pytest.raises(PermissionError):
            bridge(services, action, "card", created["card_id"],
                   f"auth-{action}", token="wrong", **extra)

    assert cards.view(created["card_id"], token="s") == second
    kind, page = bridge(services, "knowledge_history", "card",
                        created["card_id"], "history-auth")
    assert kind == "knowledge_history"
    assert _versions(page) == [2, 1]


def test_reopened_services_keep_history_and_undo(tmp_path):
    services = owners(tmp_path)
    cards = services["card"]
    created = create_card(cards, title="甲")
    second = update_card(cards, created, "card-update-2", title="乙")
    kind, preview = bridge(services, "knowledge_undo_preview", "card",
                           created["card_id"], "preview-reopened",
                           target_version=2)
    assert kind == "knowledge_undo_preview"
    kind, undone = bridge(services, "knowledge_undo", "card",
                          created["card_id"], "undo-reopened",
                          target_version=2, expected_version=2,
                          preview_token=preview["preview_token"])
    assert kind == "knowledge_undo"
    record = undone["record"]

    reopened = owners(tmp_path)
    assert reopened["card"].view(created["card_id"], token="s") == record
    kind, page = bridge(reopened, "knowledge_history", "card",
                        created["card_id"], "history-reopened")
    assert kind == "knowledge_history"
    assert _versions(page) == [3, 2, 1]
    assert page["entries"][0]["action"] == "undo"
    assert page["entries"][0]["after"] == record
    assert page["entries"][0]["before"] == second


def test_theme_undo_restores_atomic_evidence_and_reuses_pinned_source(
        tmp_path, monkeypatch):
    services = owners(tmp_path)
    themes = services["theme"]
    material, citation = captured_source(themes.materials, monkeypatch)
    first_evidence = {
        "stance": "supports", "reason": "第一版理由。", "source": citation}
    created = create_theme(themes, evidence=[first_evidence])
    evidence_id = created["evidence"][0]["evidence_id"]
    pinned = created["evidence"][0]["source"]

    second = themes.update({
        "schema_version": 1, "operation_id": "theme-update-2",
        "theme_id": created["theme_id"], "expected_version": 1,
        "judgement": "第二版判断。",
        "evidence": [{"evidence_id": evidence_id, "stance": "supports",
                      "reason": "第二版理由。", "source": citation}],
    }, token="s")
    assert second["evidence"][0]["source"] == pinned
    third = themes.update({
        "schema_version": 1, "operation_id": "theme-update-3",
        "theme_id": created["theme_id"], "expected_version": 2,
        "conditions": ["后来加上的条件"],
    }, token="s")

    kind, preview = bridge(services, "knowledge_undo_preview", "theme",
                           created["theme_id"], "theme-preview",
                           target_version=2)
    assert kind == "knowledge_undo_preview"
    assert preview["can_undo"] is True
    assert {change["field"] for change in preview["changes"]} == {
        "judgement", "evidence"}

    kind, undone = bridge(services, "knowledge_undo", "theme",
                          created["theme_id"], "theme-undo",
                          target_version=2, expected_version=3,
                          preview_token=preview["preview_token"])
    assert kind == "knowledge_undo"
    record = undone["record"]
    assert record["version"] == 4
    assert record["judgement"] == "第一版判断。"
    assert record["conditions"] == ["后来加上的条件"]
    assert record["evidence"][0]["evidence_id"] == evidence_id
    assert record["evidence"][0]["reason"] == "第一版理由。"
    # The historical pinned snapshot reference is reused; no new material
    # version or revalidation is involved.
    assert record["evidence"][0]["source"] == pinned
    assert record["evidence_sufficiency"] == "single_sided"
    assert themes.view(created["theme_id"], token="s") == record

    kind, page = bridge(services, "knowledge_history", "theme",
                        created["theme_id"], "theme-history")
    assert kind == "knowledge_history"
    assert _versions(page) == [4, 3, 2, 1]
    assert page["entries"][0]["action"] == "undo"
    assert page["entries"][0]["target_version"] == 2
    assert page["entries"][-1]["before"] is None


def test_frontier_undo_restores_disjoint_scope_leaves_and_keeps_following(tmp_path):
    services = owners(tmp_path)
    frontiers = services["frontier"]
    created = create_frontier(frontiers)
    second = frontiers.update({
        "schema_version": 1, "operation_id": "frontier-update-2",
        "frontier_id": created["frontier_id"],
        "expected_version": 1,
        "scope": {"text": "第二版范围", "alternatives": ["乙"], "sources": ["arxiv"]},
    }, token="s")
    third = frontiers.update({
        "schema_version": 1, "operation_id": "frontier-update-3",
        "frontier_id": created["frontier_id"],
        "expected_version": 2,
        "hard_constraints": {"must_include": ["后来约束"], "exclude": []},
    }, token="s")

    kind, preview = bridge(services, "knowledge_undo_preview", "frontier",
                           created["frontier_id"], "frontier-preview",
                           target_version=2)
    assert kind == "knowledge_undo_preview"
    assert preview["can_undo"] is True
    assert [change["field"] for change in preview["changes"]] == [
        "scope.text", "scope.alternatives"]
    assert _change(preview, "scope.text")["proposed"] == "第一版范围"

    kind, undone = bridge(services, "knowledge_undo", "frontier",
                          created["frontier_id"], "frontier-undo",
                          target_version=2, expected_version=3,
                          preview_token=preview["preview_token"])
    assert kind == "knowledge_undo"
    record = undone["record"]
    assert record["version"] == 4
    assert record["status"] == "following"
    assert record["scope"]["text"] == "第一版范围"
    assert record["scope"]["alternatives"] == ["甲"]
    assert record["scope"]["sources"] == ["arxiv"]
    assert record["hard_constraints"]["must_include"] == ["后来约束"]
    assert record["question"] == third["question"]
    assert frontiers.view(created["frontier_id"], token="s") == record

    kind, page = bridge(services, "knowledge_history", "frontier",
                        created["frontier_id"], "frontier-history")
    assert kind == "knowledge_history"
    assert _versions(page) == [4, 3, 2, 1]
    assert page["entries"][0]["action"] == "undo"

    # A deleted Frontier keeps readable history but cannot be undone.
    deleted = frontiers.delete({
        "schema_version": 1, "operation_id": "frontier-delete",
        "frontier_id": created["frontier_id"], "expected_version": 4,
    }, token="s")
    kind, preview = bridge(services, "knowledge_undo_preview", "frontier",
                           created["frontier_id"], "frontier-preview-deleted",
                           target_version=2)
    assert kind == "knowledge_undo_preview"
    assert preview["can_undo"] is False
    assert preview["code"] == "invalid_transition"
    kind, page = bridge(services, "knowledge_history", "frontier",
                        created["frontier_id"], "frontier-history-deleted")
    assert kind == "knowledge_history"
    assert page["current_version"] == deleted["version"]
    assert _versions(page) == [5, 4, 3, 2, 1]
    assert all(entry["can_preview"] is False for entry in page["entries"])


def test_undo_rechecks_related_card_on_confirm(tmp_path):
    services = owners(tmp_path)
    cards, themes = services["card"], services["theme"]
    card = create_card(cards)
    theme = create_theme(themes, related_cards=[{"card_id": card["card_id"], "version": 1}])
    second = themes.update({
        "schema_version": 1, "operation_id": "remove-link", "theme_id": theme["theme_id"],
        "expected_version": 1, "related_cards": [],
    }, token="s")
    _, preview = bridge(services, "knowledge_undo_preview", "theme",
                        theme["theme_id"], "preview", target_version=2)
    assert preview["can_undo"]
    cards.archive({"schema_version": 1, "operation_id": "archive-card",
                   "card_id": card["card_id"], "expected_version": 1}, token="s")
    with pytest.raises(CommandError) as rejected:
        bridge(services, "knowledge_undo", "theme", theme["theme_id"], "undo",
               expected_version=2, target_version=2, preview_token=preview["preview_token"])
    assert rejected.value.code == "version_conflict"
    assert themes.view(theme["theme_id"], token="s") == second


def test_frontier_undo_revalidates_the_combined_constraint_budget(tmp_path):
    services = owners(tmp_path)
    frontiers = services["frontier"]
    required = [f"{n:02}" + "r" * 254 for n in range(16)]
    first = create_frontier(frontiers, hard_constraints={"must_include": required, "exclude": []})
    frontiers.update({
        "schema_version": 1, "operation_id": "drop-required",
        "frontier_id": first["frontier_id"], "expected_version": 1,
        "hard_constraints": {"must_include": [], "exclude": []},
    }, token="s")
    third = frontiers.update({
        "schema_version": 1, "operation_id": "add-excluded",
        "frontier_id": first["frontier_id"], "expected_version": 2,
        "hard_constraints": {"must_include": [], "exclude": ["排除"]},
    }, token="s")
    _, preview = bridge(services, "knowledge_undo_preview", "frontier",
                        first["frontier_id"], "preview", target_version=2)
    assert preview["code"] == "invalid_scope" and not preview["can_undo"]
    assert frontiers.view(first["frontier_id"], token="s") == third


def test_removed_source_invalidates_available_citation_preview(tmp_path, monkeypatch):
    services = owners(tmp_path)
    cards = services["card"]
    material, citation = captured_source(cards.materials, monkeypatch)
    first = create_card(cards, sources=[citation])
    second = update_card(cards, first, "remove-citation", sources=[])
    _, preview = bridge(services, "knowledge_undo_preview", "card",
                        first["card_id"], "preview", target_version=2)
    assert preview["can_undo"]
    cards.materials.archive_material(material["material_id"], token="s")
    impact = cards.materials.deletion_impact(material["material_id"], token="s")
    cards.materials.delete_material(material["material_id"], impact["confirmation"], token="s")
    with pytest.raises(CommandError) as rejected:
        bridge(services, "knowledge_undo", "card", first["card_id"], "undo",
               expected_version=2, target_version=2, preview_token=preview["preview_token"])
    assert rejected.value.code == "preview_conflict"
    assert cards.view(first["card_id"], token="s") == second
    _, rechecked = bridge(services, "knowledge_undo_preview", "card",
                          first["card_id"], "fresh-preview", target_version=2)
    assert _change(rechecked, "sources")["proposed"][0]["status"] == "removed"


def test_removed_source_is_projected_removed_in_history(tmp_path, monkeypatch):
    services = owners(tmp_path)
    cards = services["card"]
    material, citation = captured_source(cards.materials, monkeypatch)
    created = create_card(cards, sources=[citation])
    second = update_card(cards, created, "card-update-2", title="修改标题")
    cards.materials.archive_material(material["material_id"], token="s")
    impact = cards.materials.deletion_impact(material["material_id"], token="s")
    cards.materials.delete_material(material["material_id"], impact["confirmation"],
                                    token="s")

    kind, page = bridge(services, "knowledge_history", "card",
                        created["card_id"], "history-removed")
    assert kind == "knowledge_history"
    assert _versions(page) == [2, 1]
    for entry in page["entries"]:
        source = entry["after"]["sources"][0]
        assert source["status"] == "removed"
        assert source["excerpt"] == citation["excerpt"]
        assert source["material_id"] == material["material_id"]

    kind, preview = bridge(services, "knowledge_undo_preview", "card",
                           created["card_id"], "preview-removed",
                           target_version=1)
    assert kind == "knowledge_undo_preview"
    assert preview["can_undo"] is False
    assert preview["code"] == "undo_not_supported"


def test_undo_notifies_the_owner_after_commit_for_indexes_and_ui(tmp_path):
    events = []
    clock = Clock()
    cards = KnowledgeCardService(tmp_path, token="s", clock=clock,
                                 on_change=events.append)
    themes = KnowledgeThemeService(tmp_path, token="s", cards=cards, clock=clock)
    frontiers = FrontierService(tmp_path, token="s", clock=clock)
    services = {"card": cards, "theme": themes, "frontier": frontiers}
    created = create_card(cards, title="甲")
    second = update_card(cards, created, "card-update-2", title="乙")
    kind, preview = bridge(services, "knowledge_undo_preview", "card",
                           created["card_id"], "preview-notify", target_version=2)
    assert kind == "knowledge_undo_preview"
    events.clear()

    kind, undone = bridge(services, "knowledge_undo", "card",
                          created["card_id"], "undo-notify", target_version=2,
                          expected_version=second["version"],
                          preview_token=preview["preview_token"])
    assert kind == "knowledge_undo"
    assert events == [{"schema_version": 1, "card_id": created["card_id"],
                       "version": 3, "status": "active"}]
    assert undone["record"]["version"] == 3


def test_reason_is_validated_for_all_owners_without_legacy_invention(tmp_path):
    services = owners(tmp_path)
    cards = services["card"]
    themes = services["theme"]
    frontiers = services["frontier"]

    with pytest.raises(CommandError) as card_reason:
        create_card(cards, operation_id="bad-reason-card", reason="原" * 200)
    assert card_reason.value.code == "invalid_card"
    with pytest.raises(CommandError) as theme_reason:
        create_theme(themes, operation_id="bad-reason-theme", reason=17)
    assert theme_reason.value.code == "invalid_theme"
    with pytest.raises(CommandError) as frontier_reason:
        create_frontier(frontiers, operation_id="bad-reason-frontier",
                        reason="x" * 501)
    assert frontier_reason.value.code == "invalid_scope"

    card = create_card(cards, operation_id="reason-card")
    theme = create_theme(themes, operation_id="reason-theme")
    frontier = create_frontier(frontiers, operation_id="reason-frontier")
    for kind, service, identifier in (
            ("card", cards, card["card_id"]),
            ("theme", themes, theme["theme_id"]),
            ("frontier", frontiers, frontier["frontier_id"])):
        kind_name, page = bridge(services, "knowledge_history", kind, identifier,
                                 f"history-reason-{kind}")
        assert kind_name == "knowledge_history"
        assert page["entries"][0]["reason"] is None
