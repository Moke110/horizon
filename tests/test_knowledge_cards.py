"""Focused public-behavior tests for K2: manual Knowledge Cards and known state.

The seam under test is KnowledgeCardService (public application service) and the
authenticated desktop bridge, persisted in the stable data home. Tests cover
manual entry without an external source, references to material snapshots,
edit/archive, version conflicts with a visible diff, replay,
persistence and the public list, open and query reads that carry committed
versions for Voyager/Editor. No live source, model or GUI is contacted.
"""

import threading

import pytest

from hostrun.agent_service import CommandError
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.voyager_materials import MaterialsService


def captured_source(materials, monkeypatch):
    from hostrun import voyager_web

    excerpt = "Calibrated optical sensors measure received radiation every day."
    body = (f"<html><head><title>Observatory report</title></head><body><article>"
            f"<h1>Observatory report</h1><p>{(excerpt + ' ') * 6}</p>"
            f"</article></body></html>").encode()
    monkeypatch.setattr(voyager_web, "fetch_public",
                        lambda url, headers: (200, {"Content-Type": "text/html; charset=utf-8"}, body))
    material = materials.register_web({
        "schema_version": 1, "operation_id": "public-source",
        "url": "https://example.org/observatory",
    }, token="s")
    return material, {"material_id": material["material_id"], "locator": "Results", "excerpt": excerpt}


def card_command(**overrides):
    command = {
        "schema_version": 1,
        "operation_id": "card-create-1",
        "title": "单细胞扰动预测",
        "content": "扰动预测模型用表达谱推断未见基因扰动后的细胞响应。",
        "aliases": ["扰动响应预测", "perturbation prediction"],
        "sources": [],
        "relations": [],
        "questionable": False,
    }
    command.update(overrides)
    return command


def test_minimal_card_defaults_optional_fields_and_derives_user_statement(tmp_path):
    service = KnowledgeCardService(tmp_path, token="s")
    created = service.create({"schema_version": 1, "operation_id": "card-minimal",
                              "title": "最小卡片", "content": "只有必填字段。"}, token="s")
    assert created["aliases"] == []
    assert created["sources"] == []
    assert created["relations"] == []
    assert created["questionable"] is False
    assert created["provenance"] == "user_statement"
    assert created["source_pending"] is True
    assert created["known"] is True
    assert created["status"] == "active"


def test_manual_card_without_source_is_user_statement_pending_and_known(tmp_path):
    service = KnowledgeCardService(tmp_path, token="s")
    created = service.create(card_command(), token="s")

    assert created["version"] == 1
    assert created["title"] == "单细胞扰动预测"
    assert created["content"] == "扰动预测模型用表达谱推断未见基因扰动后的细胞响应。"
    assert created["aliases"] == ["扰动响应预测", "perturbation prediction"]
    assert created["sources"] == []
    assert created["relations"] == []
    assert created["provenance"] == "user_statement"
    assert created["source_pending"] is True
    assert created["questionable"] is False
    assert created["known"] is True
    assert created["status"] == "active"

    # Public list/open reads carry the committed version for Voyager/Editor.
    summary = service.list(token="s")[0]
    assert summary["content"] == "扰动预测模型用表达谱推断未见基因扰动后的细胞响应。"
    assert (summary["card_id"], summary["version"], summary["status"]) == (
        created["card_id"], 1, "active")
    assert summary["provenance"] == "user_statement"
    assert summary["source_pending"] is True
    assert service.view(created["card_id"], token="s") == created

    reopened = KnowledgeCardService(tmp_path, token="s")
    assert reopened.view(created["card_id"], token="s") == created


def test_source_requires_a_verified_snapshot_and_matching_excerpt(tmp_path, monkeypatch):
    service = KnowledgeCardService(tmp_path, token="s")
    material, citation = captured_source(service.materials, monkeypatch)
    sourced = service.create(card_command(
        operation_id="card-source-1", sources=[citation]), token="s")
    assert sourced["provenance"] == "sourced"
    assert sourced["source_pending"] is False
    source = sourced["sources"][0]
    assert source["content_hash"] == material["snapshot"]["content_hash"]
    assert source["snapshot_id"] == material["snapshot"]["snapshot_id"]
    assert source["material_version"] == material["version"]
    assert source["excerpt"] == citation["excerpt"]
    with pytest.raises(CommandError) as fabricated:
        service.create(card_command(operation_id="bad-quote",
            sources=[{**citation, "excerpt": "No such excerpt exists."}]), token="s")
    assert fabricated.value.code == "invalid_source"
    with pytest.raises(CommandError) as dangling:
        service.create(card_command(operation_id="bad-source",
            sources=[{**citation, "material_id": "missing"}]), token="s")
    assert dangling.value.code == "not_found"

    # A caller cannot claim external support without a located source, cannot
    # make a manual card not-known, and cannot create it already archived.
    claimed = service.create(card_command(
        operation_id="card-claim-1", sources=[],
        provenance="sourced", source_pending=False, known=False,
        status="archived",
    ), token="s")
    assert claimed["provenance"] == "user_statement"
    assert claimed["source_pending"] is True
    assert claimed["known"] is True
    assert claimed["status"] == "active"


def test_card_reference_is_reported_in_material_deletion_impact(tmp_path, monkeypatch):
    service = KnowledgeCardService(tmp_path, token="s")
    material, citation = captured_source(service.materials, monkeypatch)
    card = service.create(card_command(sources=[citation]), token="s")
    service.materials.archive_material(material["material_id"], token="s")
    impact = service.materials.deletion_impact(material["material_id"], token="s")
    assert len(impact["references"]) == 1
    assert impact["references"][0]["owner_id"].startswith(f"card:{card['card_id']}:")
    service.materials.delete_material(material["material_id"], impact["confirmation"], token="s")
    current = service.query(token="s", card_ids=[card["card_id"]])[0]
    assert current["version"] == card["version"]
    assert current["sources"] == [{**card["sources"][0], "status": "removed"}]
    assert service.list(token="s")[0]["source_removed"] is True


def test_relation_requires_an_existing_different_card(tmp_path):
    service = KnowledgeCardService(tmp_path, token="s")
    card = service.create(card_command(), token="s")
    for target, code in (("missing", "not_found"), (card["card_id"], "invalid_card")):
        with pytest.raises(CommandError) as exc:
            service.update({
                "schema_version": 1, "operation_id": f"invalid-relation-{target}",
                "card_id": card["card_id"], "expected_version": card["version"],
                "relations": [target],
            }, token="s")
        assert exc.value.code == code
    assert service.view(card["card_id"], token="s") == card


def test_edit_persists_fields_and_recomputes_evidence(tmp_path, monkeypatch):
    service = KnowledgeCardService(tmp_path, token="s")
    created = service.create(card_command(), token="s")
    related = service.create(card_command(operation_id="related"), token="s")
    _, citation = captured_source(service.materials, monkeypatch)
    edited = service.update({
        "schema_version": 1, "operation_id": "card-edit-1",
        "card_id": created["card_id"], "expected_version": created["version"],
        "title": "扰动响应预测",
        "content": "更新后的内容：扰动预测需要区分训练分布内与分布外基因。",
        "aliases": ["扰动预测", "perturbation response"],
        "recall_terms": ["分布外"],
        "sources": [citation],
        "relations": [related["card_id"]],
        "questionable": True,
    }, token="s")

    assert edited["version"] == 2
    assert edited["title"] == "扰动响应预测"
    assert edited["aliases"] == ["扰动预测", "perturbation response"]
    assert "recall_terms" not in edited
    assert edited["sources"][0]["material_id"] == citation["material_id"]
    assert edited["relations"] == [related["card_id"]]
    assert edited["questionable"] is True
    assert edited["provenance"] == "sourced"
    assert edited["source_pending"] is False
    assert edited["known"] is True

    reopened = KnowledgeCardService(tmp_path, token="s")
    assert reopened.view(created["card_id"], token="s") == edited
    summary = next(row for row in reopened.list(token="s") if row["card_id"] == created["card_id"])
    assert (summary["card_id"], summary["version"], summary["status"]) == (
        created["card_id"], 2, "active")
    assert summary["title"] == "扰动响应预测"
    assert summary["provenance"] == "sourced"
    assert summary["source_pending"] is False
    assert summary["questionable"] is True

    local = service.materials.register_text({
        "schema_version": 1, "operation_id": "material",
        "title": "笔记", "text": "组会讨论记录", "source_label": "手录资料",
    }, token="s")
    local_citation = {"material_id": local["material_id"], "excerpt": "组会讨论", "locator": "首段"}
    labelled = service.update({
        "schema_version": 1, "operation_id": "card-edit-label",
        "card_id": edited["card_id"], "expected_version": edited["version"],
        "sources": [local_citation], "questionable": False,
    }, token="s")
    assert labelled["version"] == 3
    assert labelled["sources"][0]["source_kind"] == "user_input"
    assert labelled["provenance"] == "user_statement"
    assert labelled["source_pending"] is True
    assert labelled["questionable"] is False
    assert labelled["known"] is True

    # Removing the last source returns the card to a user statement pending
    # support.
    unsourced = service.update({
        "schema_version": 1, "operation_id": "card-edit-2",
        "card_id": labelled["card_id"], "expected_version": labelled["version"],
        "sources": [],
    }, token="s")
    assert unsourced["version"] == 4
    assert unsourced["provenance"] == "user_statement"
    assert unsourced["source_pending"] is True
    assert unsourced["known"] is True


def test_stale_expected_version_returns_current_view_and_diff_without_overwrite(tmp_path):
    service = KnowledgeCardService(tmp_path, token="s")
    created = service.create(card_command(), token="s")
    current = service.update({
        "schema_version": 1, "operation_id": "card-edit-current",
        "card_id": created["card_id"], "expected_version": 1,
        "title": "别人已提交的标题",
    }, token="s")
    assert current["version"] == 2

    with pytest.raises(CommandError) as exc:
        service.update({
            "schema_version": 1, "operation_id": "card-edit-stale",
            "card_id": created["card_id"], "expected_version": 1,
            "title": "我的草稿标题",
            "content": "我的草稿内容",
        }, token="s")

    error = exc.value
    assert error.code == "version_conflict"
    assert error.details["expected_version"] == 1
    assert error.details["current_version"] == 2
    assert error.details["current"] == current
    assert error.details["changed"] == ["title", "content"]
    assert error.details["submitted"] == {
        "title": "我的草稿标题", "content": "我的草稿内容"}

    # The committed content is never overwritten by the stale draft.
    assert service.view(created["card_id"], token="s") == current

    from hostrun.desktop_bridge import card_command as bridge
    with pytest.raises(CommandError) as bridged:
        bridge(service, {"action": "card_update",
                         "schema_version": 1, "operation_id": "card-edit-bridge-stale",
                         "card_id": created["card_id"], "expected_version": 1,
                         "title": "桌面草稿标题"}, token="s")
    assert bridged.value.code == "version_conflict"
    assert bridged.value.details["current"] == current
    assert bridged.value.details["changed"] == ["title"]


def test_archive_keeps_card_readable_but_excludes_it_from_known(tmp_path):
    service = KnowledgeCardService(tmp_path, token="s")
    created = service.create(card_command(), token="s")
    archived = service.archive({
        "schema_version": 1, "operation_id": "card-archive-1",
        "card_id": created["card_id"], "expected_version": 1,
    }, token="s")

    assert archived["status"] == "archived"
    assert archived["version"] == 2
    assert archived["known"] is False
    assert service.view(created["card_id"], token="s") == archived
    # Archived cards remain listed with their status; they are not deleted.
    listed = service.list(token="s")
    assert [(row["card_id"], row["status"], row["version"]) for row in listed] == [
        (created["card_id"], "archived", 2)]

    assert service.query(token="s") == []
    with pytest.raises(CommandError) as edit:
        service.update({"schema_version": 1, "operation_id": "edit-archived",
                        "card_id": created["card_id"], "expected_version": 2,
                        "title": "试图改写归档版本"}, token="s")
    assert edit.value.code == "invalid_transition"
    with pytest.raises(CommandError) as exc:
        service.archive({"schema_version": 1, "operation_id": "card-archive-again",
                         "card_id": created["card_id"],
                         "expected_version": archived["version"]}, token="s")
    assert exc.value.code == "invalid_transition"
    assert service.view(created["card_id"], token="s") == archived


def test_reading_and_listing_do_not_change_known_cards(tmp_path):
    service = KnowledgeCardService(tmp_path, token="s")
    created = service.create(card_command(), token="s")
    for _ in range(3):
        assert service.view(created["card_id"], token="s") == created
        assert service.list(token="s")[0]["version"] == 1
    assert service.view(created["card_id"], token="s")["known"] is True
    assert service.view(created["card_id"], token="s")["version"] == 1


def test_replay_returns_original_result_and_rejects_operation_conflict(tmp_path):
    service = KnowledgeCardService(tmp_path, token="s")
    created = service.create(card_command(), token="s")
    assert service.create(card_command(), token="s") == created
    with pytest.raises(CommandError) as exc:
        service.create(card_command(content="不同内容"), token="s")
    assert exc.value.code == "operation_conflict"

    edit = {"schema_version": 1, "operation_id": "card-edit-replay",
            "card_id": created["card_id"], "expected_version": 1,
            "title": "第一次编辑"}
    first = service.update(edit, token="s")
    later = service.update({"schema_version": 1, "operation_id": "card-edit-later",
                            "card_id": created["card_id"],
                            "expected_version": first["version"],
                            "title": "第二次编辑"}, token="s")
    assert later["version"] == 3
    # Replay is decided before the stale expected_version check.
    assert service.update(edit, token="s") == first
    assert service.view(created["card_id"], token="s") == later


def test_concurrent_create_with_same_operation_id_is_idempotent(tmp_path):
    service = KnowledgeCardService(tmp_path, token="s")
    results = []

    def run():
        results.append(service.create(card_command(), token="s")["card_id"])

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(results) == 4
    assert len(set(results)) == 1
    assert len(service.list(token="s")) == 1


def test_validation_rejects_unbounded_or_false_evidence_input(tmp_path):
    service = KnowledgeCardService(tmp_path, token="s")
    cases = [
        (card_command(title=""), "invalid_card"),
        (card_command(title="   "), "invalid_card"),
        (card_command(title="标" * 180), "invalid_card"),
        (card_command(content=""), "invalid_card"),
        (card_command(aliases=[123]), "invalid_card"),
        (card_command(aliases=["词" * 86]), "invalid_card"),
        (card_command(aliases=["词"] * 33), "invalid_card"),
        (card_command(sources=[""]), "invalid_card"),
        (card_command(sources=["s" * 2049]), "invalid_card"),
        (card_command(sources=["https://example.org"] * 33), "invalid_card"),
        (card_command(sources=[123]), "invalid_card"),
        (card_command(sources={"label": "not a list"}), "invalid_card"),
        (card_command(sources=[{"label": "标" * 167}]), "invalid_card"),
        (card_command(sources=[{"label": "x", "locator": "l" * 2001}]), "invalid_card"),
        (card_command(sources=[{"label": "x", "excerpt": "e" * 4001}]), "invalid_card"),
        (card_command(sources=[{"label": "  ", "locator": ""}]), "invalid_card"),
        (card_command(sources=[{"label": "x", "unexpected": "y"}]), "invalid_card"),
        (card_command(relations=["r" * 513]), "invalid_card"),
        (card_command(relations=["r"] * 33), "invalid_card"),
        (card_command(questionable="yes"), "invalid_card"),
    ]
    for index, (command, code) in enumerate(cases):
        command = {**command, "operation_id": f"validate-{index}"}
        with pytest.raises(CommandError) as exc:
            service.create(command, token="s")
        assert exc.value.code == code

    created = service.create(card_command(operation_id="validate-base"), token="s")
    with pytest.raises(CommandError) as exc:
        service.update({"schema_version": 1, "operation_id": "validate-status",
                        "card_id": created["card_id"], "expected_version": 1,
                        "status": "deleted"}, token="s")
    assert exc.value.code == "invalid_status"

    with pytest.raises(CommandError) as exc:
        service.view("missing", token="s")
    assert exc.value.code == "not_found"
    with pytest.raises(PermissionError):
        service.list(token="wrong")


def test_public_query_returns_committed_cards_with_pinnable_versions(tmp_path):
    from hostrun.desktop_bridge import card_command as bridge

    service = KnowledgeCardService(tmp_path, token="s")
    first = service.create(card_command(operation_id="query-card-1"), token="s")
    second = service.create(card_command(
        operation_id="query-card-2", title="第二张卡片"), token="s")
    archived = service.archive({
        "schema_version": 1, "operation_id": "query-archive",
        "card_id": first["card_id"], "expected_version": first["version"]},
        token="s")

    # Default query is the active committed set; archived cards stay available
    # only on an explicit request.
    assert [(card["card_id"], card["version"]) for card in service.query(token="s")] == [
        (second["card_id"], 1)]
    assert {(card["card_id"], card["version"], card["status"])
            for card in service.query(token="s", include_archived=True)} == {
        (second["card_id"], 1, "active"),
        (archived["card_id"], 2, "archived")}

    pinned = service.query(token="s", card_ids=[second["card_id"]])
    assert pinned == [service.view(second["card_id"], token="s")]
    assert pinned[0]["provenance"] == "user_statement"
    assert pinned[0]["source_pending"] is True
    assert pinned[0]["known"] is True
    # Reading through the public query never mutates the pinned card.
    assert service.view(second["card_id"], token="s") == second

    with pytest.raises(CommandError) as missing:
        service.query(token="s", card_ids=["missing"])
    assert missing.value.code == "not_found"
    with pytest.raises(CommandError) as empty:
        service.query(token="s", card_ids=[])
    assert empty.value.code == "invalid_query"
    with pytest.raises(CommandError):
        service.query(token="s", include_archived="yes")
    with pytest.raises(PermissionError):
        service.query(token="wrong")

    kind, data = bridge(service, {"action": "card_query",
                                  "card_ids": [second["card_id"]]}, token="s")
    assert kind == "card_query" and data == pinned
    kind, every = bridge(service, {"action": "card_query",
                                   "include_archived": True}, token="s")
    assert kind == "card_query" and len(every) == 2


def test_desktop_bridge_routes_card_commands_and_authenticates(tmp_path):
    from hostrun.desktop_bridge import card_command as bridge

    service = KnowledgeCardService(tmp_path, token="s")
    kind, created = bridge(service, {"action": "card_create",
                                     **card_command()}, token="s")
    assert kind == "card_view"
    assert created["provenance"] == "user_statement"

    kind, opened = bridge(service, {"action": "card_open",
                                    "card_id": created["card_id"]}, token="s")
    assert kind == "card_view" and opened == created

    kind, listed = bridge(service, {"action": "card_list"}, token="s")
    assert kind == "card_list"
    assert listed[0]["version"] == created["version"]

    kind, updated = bridge(service, {
        "action": "card_update", "schema_version": 1,
        "operation_id": "card-update-bridge", "card_id": created["card_id"],
        "expected_version": created["version"], "questionable": True,
    }, token="s")
    assert kind == "card_view" and updated["version"] == 2
    assert updated["questionable"] is True

    kind, archived = bridge(service, {
        "action": "card_archive", "schema_version": 1,
        "operation_id": "card-archive-bridge", "card_id": created["card_id"],
        "expected_version": updated["version"],
    }, token="s")
    assert kind == "card_view" and archived["status"] == "archived"

    kind, listed = bridge(service, {"action": "card_list"}, token="s")
    assert kind == "card_list"
    assert [(row["card_id"], row["status"]) for row in listed] == [
        (created["card_id"], "archived")]

    with pytest.raises(CommandError):
        bridge(service, {"action": "card_unknown"}, token="s")
    with pytest.raises(PermissionError):
        bridge(service, {"action": "card_list"}, token="wrong")
