"""Public-behavior tests for K4 backend search across the workbench.

The seam under test is ``KnowledgeWorkbenchService.search`` and the
authenticated desktop bridge, persisted in the stable data home. Search covers
short Chinese terms, mixed Chinese/English input and Unicode normalization
without vectors or a network call. Scopes are active (default), inactive and
pending; relations are direct, typed and bidirectional; paging and every public
input are validated. No live source, model or GUI is contacted.
"""

import json

import pytest

from hostrun.agent_service import CommandError
from hostrun.frontier import FrontierService
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_themes import KnowledgeThemeService
from hostrun.knowledge_workbench import KnowledgeWorkbenchService
from hostrun.managed_database import database_connection


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
    workbench = KnowledgeWorkbenchService(
        tmp_path, token=token, cards=cards, themes=themes, frontiers=frontiers)
    return workbench, cards, themes, frontiers


def add_card(cards, operation_id, title, content="内容", **overrides):
    command = {
        "schema_version": 1, "operation_id": operation_id,
        "title": title, "content": content, "sources": [], "relations": [],
    }
    command.update(overrides)
    return cards.create(command, token="s")


def add_theme(themes, operation_id, judgement, **overrides):
    command = {
        "schema_version": 1, "operation_id": operation_id,
        "judgement": judgement, "conditions": [], "related_cards": [],
        "evidence": [],
    }
    command.update(overrides)
    return themes.create(command, token="s")


def add_frontier(frontiers, operation_id, question, *, status="following"):
    return frontiers.create({
        "schema_version": 1, "operation_id": operation_id,
        "question": question, "status": status,
        "scope": {"text": "长期稳定性", "alternatives": [], "sources": ["arxiv"]},
        "hard_constraints": {"must_include": [], "exclude": []},
        "relations": [],
    }, token="s")


def search(workbench, query="", **overrides):
    return workbench.search(query, token="s", **overrides)


def found(page):
    return {(row["kind"], row["id"]) for row in page["results"]}


def test_short_chinese_mixed_language_and_normalized_queries_are_offline(tmp_path):
    workbench, cards, themes, frontiers = owners(tmp_path)
    optical = add_card(
        cards, "optical", "光学传感器标定",
        "每天测量接收到的辐射。",
        aliases=["光学校准", "sensor calibration"])
    # Seed the same snapshot a release with the retired field would have saved.
    legacy = json.dumps(optical | {"recall_terms": ["obsolete keyword"]})
    with database_connection(tmp_path) as db:
        db.execute("UPDATE knowledge_cards SET result=? WHERE card_id=?",
                   (legacy, optical["card_id"]))
        db.commit()
    assert cards.view(optical["card_id"], token="s") == optical
    single = add_card(
        cards, "single-cell", "单细胞扰动预测",
        "扰动预测模型用表达谱推断未见基因扰动后的细胞响应。")
    folding = add_card(cards, "folding", "蛋白质折叠", "AlphaFold 预测结构。")
    uncommon = add_card(cards, "uncommon", "𠮷号分类", "Café 示例。")
    claim = add_theme(
        themes, "optical-theme", "光学传感器在低温下漂移。",
        conditions=["仅适用于真空腔"])
    frontier = add_frontier(
        frontiers, "optical-frontier", "光学传感器如何长期标定？")

    optical_hit = ("card", optical["card_id"])
    theme_hit = ("theme", claim["theme_id"])
    frontier_hit = ("frontier", frontier["frontier_id"])

    assert found(search(workbench, "光")) == {
        optical_hit, theme_hit, frontier_hit}
    assert found(search(workbench, "光学")) == {
        optical_hit, theme_hit, frontier_hit}
    assert found(search(workbench, "单细胞")) == {("card", single["card_id"])}
    assert found(search(workbench, "单细胞扰动")) == {
        ("card", single["card_id"])}
    assert found(search(workbench, "辐")) == {optical_hit}

    # Mixed Chinese/English, with or without a separating space, and
    # normalization (full-width Latin letters and case) all locate the same
    # card through title/alias fields.
    assert found(search(workbench, "光学 sensor")) == {optical_hit}
    assert found(search(workbench, "光学sensor")) == {optical_hit}
    assert found(search(workbench, "ＳＥＮＳＯＲ")) == {optical_hit}
    assert found(search(workbench, "obsolete keyword")) == set()
    assert found(search(workbench, "  光学  ")) == {
        optical_hit, theme_hit, frontier_hit}
    assert found(search(workbench, "不存在")) == set()
    assert found(search(workbench, "AlphaFold")) == {
        ("card", folding["card_id"])}
    assert found(search(workbench, "𠮷")) == {("card", uncommon["card_id"])}
    assert found(search(workbench, "𠮷号ＣＡＦÉ")) == {
        ("card", uncommon["card_id"])}

    # Punctuation only is not an empty query: it must not list everything.
    for punctuation in ("!!!", "。", "？!，", " - "):
        page = search(workbench, punctuation)
        assert page["results"] == []
        assert page["total"] == 0

    page = search(workbench, "光学")
    row = next(row for row in page["results"] if row["id"] == optical["card_id"])
    assert set(row) == {"kind", "id", "version", "title", "summary", "status",
                        "questionable", "source_pending"}
    assert row["title"] == "光学传感器标定"
    assert row["summary"] == "每天测量接收到的辐射。"
    assert row["questionable"] is False
    assert row["source_pending"] is True
    theme_row = next(row for row in page["results"]
                     if row["kind"] == "theme")
    assert set(theme_row) == {"kind", "id", "version", "title", "summary",
                              "status"}
    assert theme_row["title"] == "光学传感器在低温下漂移。"


def test_scopes_kinds_and_known_flags(tmp_path):
    workbench, cards, themes, frontiers = owners(tmp_path)
    active = add_card(cards, "active", "当前知识卡片", "当前有效内容。")
    doubting = add_card(cards, "doubting", "存疑但仍有效", "需要复核。",
                        questionable=True)
    archived = add_card(cards, "archived", "归档知识卡片", "归档内容。")
    cards.archive({"schema_version": 1, "operation_id": "archive-card",
                   "card_id": archived["card_id"],
                   "expected_version": archived["version"]}, token="s")
    theme = add_theme(themes, "theme", "当前有效命题")
    old_theme = add_theme(themes, "old-theme", "归档命题")
    themes.archive({"schema_version": 1, "operation_id": "archive-theme",
                    "theme_id": old_theme["theme_id"],
                    "expected_version": old_theme["version"]}, token="s")
    following = add_frontier(frontiers, "following", "关注中的问题")
    paused = add_frontier(frontiers, "paused", "暂停的问题", status="paused")
    resolved = add_frontier(frontiers, "resolved", "已解决的问题", status="resolved")
    deleted = add_frontier(frontiers, "deleted", "删除的问题")
    frontiers.delete({"schema_version": 1, "operation_id": "delete-frontier",
                      "frontier_id": deleted["frontier_id"],
                      "expected_version": deleted["version"]}, token="s")

    page = search(workbench)
    assert found(page) == {
        ("card", active["card_id"]), ("card", doubting["card_id"]),
        ("theme", theme["theme_id"]), ("frontier", following["frontier_id"])}
    assert page["total"] == 4
    assert page["offset"] == 0
    assert page["limit"] == 40
    by_id = {row["id"]: row for row in page["results"]}
    assert by_id[doubting["card_id"]]["questionable"] is True
    assert by_id[doubting["card_id"]]["status"] == "active"
    assert by_id[doubting["card_id"]]["source_pending"] is True

    assert found(search(workbench, kind="card")) == {
        ("card", active["card_id"]), ("card", doubting["card_id"])}
    assert found(search(workbench, kind="theme")) == {
        ("theme", theme["theme_id"])}
    assert found(search(workbench, kind="frontier")) == {
        ("frontier", following["frontier_id"])}

    page = search(workbench, scope="inactive")
    assert found(page) == {
        ("card", archived["card_id"]), ("theme", old_theme["theme_id"]),
        ("frontier", paused["frontier_id"]),
        ("frontier", resolved["frontier_id"]),
        ("frontier", deleted["frontier_id"])}
    by_id = {row["id"]: row for row in page["results"]}
    assert by_id[archived["card_id"]]["status"] == "archived"
    assert by_id[paused["frontier_id"]]["status"] == "paused"
    assert by_id[deleted["frontier_id"]]["status"] == "deleted"
    assert found(search(workbench, scope="inactive", kind="frontier")) == {
        ("frontier", paused["frontier_id"]),
        ("frontier", resolved["frontier_id"]),
        ("frontier", deleted["frontier_id"])}

    # No Keeper exists yet, so an explicit pending scope is empty.
    for overrides in ({}, {"kind": "card"}, {"query": "卡片"}):
        pending = search(workbench, scope="pending", **overrides)
        assert pending["results"] == []
        assert pending["total"] == 0

    # The owner read refactor still lists archived records with their status.
    assert {row["card_id"] for row in cards.list(token="s")} == {
        active["card_id"], doubting["card_id"], archived["card_id"]}


def test_relations_are_direct_typed_and_bidirectional(tmp_path):
    workbench, cards, themes, _ = owners(tmp_path)
    anchor = add_card(cards, "anchor", "基准卡片")
    middle = add_card(cards, "middle", "中间卡片",
                      relations=[anchor["card_id"]])
    outer = add_card(cards, "outer", "外层卡片", relations=[middle["card_id"]])
    standalone = add_card(cards, "standalone", "独立卡片")
    linked_theme = add_theme(
        themes, "linked-theme", "关联命题",
        related_cards=[{"card_id": anchor["card_id"], "version": 1}])
    standalone_theme = add_theme(themes, "standalone-theme", "独立命题")

    def related(target_kind, identifier, **overrides):
        return found(search(workbench, related_to={
            "kind": target_kind, "id": identifier}, **overrides))

    # Card/Card answers from both endpoints.
    assert related("card", anchor["card_id"]) == {
        ("card", middle["card_id"]), ("theme", linked_theme["theme_id"])}
    assert related("card", middle["card_id"]) == {
        ("card", anchor["card_id"]), ("card", outer["card_id"])}
    # No transitive closure: outer reaches middle only.
    assert related("card", outer["card_id"]) == {
        ("card", middle["card_id"])}
    # Theme/Card is bidirectional across kinds.
    assert related("theme", linked_theme["theme_id"]) == {
        ("card", anchor["card_id"])}

    # A result kind that differs from the target kind is still included.
    assert related("card", anchor["card_id"], kind="theme") == {
        ("theme", linked_theme["theme_id"])}
    assert related("theme", linked_theme["theme_id"], kind="card") == {
        ("card", anchor["card_id"])}

    # Valid but unrelated, absent and Frontier targets are empty, not errors.
    assert related("card", standalone["card_id"]) == set()
    assert related("card", "00000000-0000-0000-0000-000000000000") == set()
    assert related("theme", standalone_theme["theme_id"]) == set()
    assert related("frontier", "00000000-0000-0000-0000-000000000000") == set()


def test_updates_reopen_and_stable_descending_order(tmp_path):
    workbench, cards, themes, frontiers = owners(tmp_path)
    card = add_card(cards, "card", "最早卡片", "第一版内容。")
    theme = add_theme(themes, "theme", "随后命题")
    frontier = add_frontier(frontiers, "frontier", "最后问题")

    expected = [("frontier", frontier["frontier_id"]),
                ("theme", theme["theme_id"]),
                ("card", card["card_id"])]
    assert [(row["kind"], row["id"])
            for row in search(workbench)["results"]] == expected

    updated = cards.update({
        "schema_version": 1, "operation_id": "edit",
        "card_id": card["card_id"], "expected_version": card["version"],
        "title": "最新修订卡片", "content": "第二版内容。",
    }, token="s")
    page = search(workbench, "最新修订")
    assert [(row["kind"], row["id"]) for row in page["results"]] == [
        ("card", card["card_id"])]
    assert page["results"][0]["version"] == updated["version"]
    assert page["results"][0]["summary"] == "第二版内容。"
    assert [(row["kind"], row["id"])
            for row in search(workbench)["results"]] == [
        ("card", card["card_id"]), ("frontier", frontier["frontier_id"]),
        ("theme", theme["theme_id"])]

    reopened = KnowledgeWorkbenchService(tmp_path, token="s")
    first = search(reopened, "内容")
    again = search(reopened, "内容")
    assert first == again
    assert [(row["kind"], row["id"]) for row in first["results"]] == [
        ("card", card["card_id"])]


def test_equal_timestamps_order_by_kind_then_id(tmp_path):
    cards = KnowledgeCardService(tmp_path, token="s", clock=lambda: 1000)
    themes = KnowledgeThemeService(
        tmp_path, token="s", cards=cards, clock=lambda: 1000)
    frontiers = FrontierService(tmp_path, token="s", clock=lambda: 1000)
    workbench = KnowledgeWorkbenchService(
        tmp_path, token="s", cards=cards, themes=themes, frontiers=frontiers)
    second = add_card(cards, "b", "乙卡片")
    first = add_card(cards, "a", "甲卡片")
    theme = add_theme(themes, "t", "同刻命题")
    frontier = add_frontier(frontiers, "f", "同刻问题")

    order = [(row["kind"], row["id"]) for row in search(workbench)["results"]]
    assert order == [
        *(("card", identifier) for identifier in sorted([first["card_id"], second["card_id"]])),
        ("frontier", frontier["frontier_id"]), ("theme", theme["theme_id"])]


def test_paging_and_invalid_inputs(tmp_path):
    workbench, cards, _, _ = owners(tmp_path)
    created = [add_card(cards, f"card-{index}", f"分页卡片 {index}")
               for index in range(5)]

    page = search(workbench, limit=2)
    assert page["total"] == 5
    assert page["limit"] == 2
    assert len(page["results"]) == 2
    first = [row["id"] for row in page["results"]]
    second = [row["id"] for row in search(workbench, limit=2, offset=2)["results"]]
    third = [row["id"] for row in search(workbench, limit=2, offset=4)["results"]]
    everything = sorted(first + second + third)
    assert everything == sorted(card["card_id"] for card in created)
    assert len(set(first + second + third)) == 5
    assert search(workbench, offset=99)["results"] == []
    assert search(workbench, offset=99)["total"] == 5

    for overrides in (
        {"query": None},
        {"query": "x" * 4097},
        {"kind": "cards"},
        {"kind": "Card"},
        {"kind": ""},
        {"kind": 1},
        {"scope": "archived"},
        {"scope": None},
        {"related_to": "id"},
        {"related_to": {"kind": "card"}},
        {"related_to": {"kind": "cards", "id": "x"}},
        {"related_to": {"kind": "card", "id": ""}},
        {"related_to": {"kind": "card", "id": 1}},
        {"related_to": {"kind": "card", "id": "x", "extra": 1}},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"limit": 1.5},
        {"limit": "10"},
        {"offset": -1},
        {"offset": True},
        {"offset": 1.5},
    ):
        with pytest.raises(CommandError) as invalid:
            search(workbench, **overrides)
        assert invalid.value.code == "invalid_query"

    with pytest.raises(CommandError) as not_text:
        workbench.search(None, token="s")
    assert not_text.value.code == "invalid_query"


def test_desktop_bridge_is_authenticated_and_echoes_operation_id(tmp_path):
    from hostrun.desktop_bridge import workbench_command

    workbench, cards, themes, _ = owners(tmp_path)
    card = add_card(cards, "persisted", "持久化中文卡片", "离线可搜的内容。")
    theme = add_theme(themes, "persisted-theme", "持久化命题")

    kind, page = workbench_command(workbench, {
        "action": "knowledge_search", "operation_id": "read-1",
        "query": "中文", "kind": "card", "scope": "active",
        "limit": 10, "offset": 0,
    }, token="s")
    assert kind == "knowledge_search"
    assert page["operation_id"] == "read-1"
    assert page["total"] == 1
    assert page["limit"] == 10
    assert page["offset"] == 0
    assert [row["id"] for row in page["results"]] == [card["card_id"]]

    default_kind, default_page = workbench_command(
        workbench, {"action": "knowledge_search", "operation_id": "read-2"},
        token="s")
    assert default_kind == "knowledge_search"
    assert default_page["operation_id"] == "read-2"
    assert default_page["limit"] == 40
    assert found(default_page) == {
        ("card", card["card_id"]), ("theme", theme["theme_id"])}

    # Repeating a read is side-effect free and byte-identical.
    again = workbench_command(workbench, {
        "action": "knowledge_search", "operation_id": "read-2"}, token="s")
    assert again == (default_kind, default_page)

    missing = workbench_command(
        workbench, {"action": "knowledge_search"}, token="s")[1]
    assert missing["operation_id"] is None

    with pytest.raises(CommandError) as unknown:
        workbench_command(workbench, {"action": "knowledge_scan"}, token="s")
    assert unknown.value.code == "invalid_command"
    for operation_id in (1, "", "   "):
        with pytest.raises(CommandError) as bad_id:
            workbench_command(workbench, {
                "action": "knowledge_search", "operation_id": operation_id},
                token="s")
        assert bad_id.value.code == "invalid_command"
    with pytest.raises(PermissionError):
        workbench_command(workbench, {"action": "knowledge_search"}, token="wrong")
    with pytest.raises(CommandError) as bad_limit:
        workbench_command(workbench, {
            "action": "knowledge_search", "limit": 500}, token="s")
    assert bad_limit.value.code == "invalid_query"

    # The bridge output is plain JSON, so the desktop can serialize it as is.
    assert json.loads(json.dumps(default_page)) == default_page
