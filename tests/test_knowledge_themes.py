"""Focused public-behavior tests for K3: Themes with two-sided evidence.

The seam under test is KnowledgeThemeService (public application service) and
the authenticated desktop bridge, persisted in the stable data home. A Theme
carries a judgement, applicability conditions, related Knowledge Card versions
and directional evidence. Tests cover a claim with no cards or evidence, a
single-sided claim that stays visible but insufficient, opposing evidence
coexisting without rewriting the judgement, verified snapshot citations that
open the actual page/paragraph, related-card version validation through the
Knowledge Card service, replay, persistence, archive and material removal.
No live source, model or GUI is contacted.
"""

import threading

import pytest

from hostrun.agent_service import CommandError
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_themes import KnowledgeThemeService
from hostrun.voyager_materials import MaterialsService


EXCERPT = "Calibrated optical sensors measure received radiation every day."


def captured_source(materials, monkeypatch, *, operation_id="public-source",
                    url="https://example.org/observatory", excerpt=EXCERPT):
    from hostrun import voyager_web

    body = (f"<html><head><title>Observatory report</title></head><body><article>"
            f"<h1>Observatory report</h1><p>{(excerpt + ' ') * 6}</p>"
            f"</article></body></html>").encode()
    monkeypatch.setattr(voyager_web, "fetch_public",
                        lambda target, headers: (200,
                                                 {"Content-Type": "text/html; charset=utf-8"},
                                                 body))
    material = materials.register_web({
        "schema_version": 1, "operation_id": operation_id, "url": url,
    }, token="s")
    return material, {"material_id": material["material_id"],
                      "locator": "Results", "excerpt": excerpt}


def theme_command(**overrides):
    command = {
        "schema_version": 1,
        "operation_id": "theme-create-1",
        "judgement": "在分布外扰动上，现有证据不足以支持直接实验决策。",
        "conditions": ["仅适用于未见基因", "表达谱需可比"],
        "related_cards": [],
        "evidence": [],
    }
    command.update(overrides)
    return command


def directional(stance, citation, reason="该结果直接检验了判断。", **overrides):
    entry = {"stance": stance, "reason": reason, "source": citation}
    entry.update(overrides)
    return entry


def test_minimal_theme_needs_no_cards_or_evidence_and_is_known(tmp_path):
    service = KnowledgeThemeService(tmp_path, token="s")
    created = service.create({"schema_version": 1, "operation_id": "theme-minimal",
                              "judgement": "一个只含判断的最小主题。"}, token="s")

    assert created["version"] == 1
    assert created["judgement"] == "一个只含判断的最小主题。"
    assert created["conditions"] == []
    assert created["related_cards"] == []
    assert created["evidence"] == []
    assert created["evidence_sufficiency"] == "unsupported"
    assert created["known"] is True
    assert created["origin"] == "manual"
    assert created["status"] == "active"

    reopened = KnowledgeThemeService(tmp_path, token="s")
    assert reopened.view(created["theme_id"], token="s") == created
    summary = reopened.list(token="s")[0]
    assert (summary["theme_id"], summary["version"], summary["status"]) == (
        created["theme_id"], 1, "active")
    assert summary["evidence_sufficiency"] == "unsupported"
    assert summary["evidence_count"] == 0
    assert summary["related_card_count"] == 0


def test_single_sided_evidence_saves_but_marks_insufficient_and_keeps_claim(tmp_path, monkeypatch):
    service = KnowledgeThemeService(tmp_path, token="s")
    material, citation = captured_source(service.materials, monkeypatch)
    support = directional("supports", citation)
    created = service.create(theme_command(evidence=[support]), token="s")

    assert created["evidence_sufficiency"] == "single_sided"
    assert created["judgement"] == theme_command()["judgement"]
    evidence = created["evidence"][0]
    assert evidence["evidence_id"]
    assert evidence["stance"] == "supports"
    assert evidence["reason"] == support["reason"]
    assert evidence["source"]["material_id"] == material["material_id"]
    assert evidence["source"]["excerpt"] == citation["excerpt"]
    assert evidence["source"]["locator"] == citation["locator"]
    assert evidence["source"]["material_version"] == material["version"]
    assert evidence["source"]["content_hash"] == material["snapshot"]["content_hash"]
    assert evidence["source"]["snapshot_id"] == material["snapshot"]["snapshot_id"]

    # Adding opposing evidence keeps both directions and never rewrites the
    # Explorer's judgement as a side effect.
    _, other = captured_source(
        service.materials, monkeypatch, operation_id="second-source",
        url="https://example.org/reanalysis",
        excerpt="A later reanalysis found the sensor response was not stable.")
    opposing = directional("opposes", other, reason="重复分析未复现原结论。")
    balanced = service.update({
        "schema_version": 1, "operation_id": "theme-add-opposition",
        "theme_id": created["theme_id"], "expected_version": created["version"],
        "evidence": [evidence, opposing],
    }, token="s")

    assert balanced["version"] == 2
    assert balanced["judgement"] == created["judgement"]
    assert balanced["evidence_sufficiency"] == "balanced"
    assert [(row["stance"], row["reason"]) for row in balanced["evidence"]] == [
        ("supports", support["reason"]), ("opposes", opposing["reason"])]
    # The earlier citation stays pinned to the material version it cited.
    assert balanced["evidence"][0]["source"] == evidence["source"]
    assert balanced["evidence"][0]["evidence_id"] == evidence["evidence_id"]


def test_evidence_requires_stance_reason_and_verified_snapshot_excerpt(tmp_path, monkeypatch):
    service = KnowledgeThemeService(tmp_path, token="s")
    _, citation = captured_source(service.materials, monkeypatch)

    cases = [
        (theme_command(operation_id="bad-stance", evidence=[
            directional("maybe", citation)]), "invalid_theme"),
        (theme_command(operation_id="bad-reason", evidence=[
            directional("supports", citation, reason="   ")]), "invalid_theme"),
        (theme_command(operation_id="bad-source-shape", evidence=[
            directional("supports", citation, source={"report": "A1"})]),
         "invalid_theme"),
        (theme_command(operation_id="bad-excerpt", evidence=[
            directional("supports", {**citation, "excerpt": "No such excerpt exists."})]),
         "invalid_source"),
        (theme_command(operation_id="bad-material", evidence=[
            directional("supports", {**citation, "material_id": "missing"})]),
         "not_found"),
        (theme_command(operation_id="stale-material", evidence=[
            directional("supports", {**citation, "material_version": 2})]),
         "version_conflict"),
        (theme_command(operation_id="assigned-id", evidence=[
            directional("supports", citation, evidence_id="caller-chosen")]),
         "invalid_theme"),
    ]
    for command, code in cases:
        with pytest.raises(CommandError) as exc:
            service.create(command, token="s")
        assert exc.value.code == code

    # A failed evidence check leaves no Theme and no citation binding behind.
    assert service.list(token="s") == []
    service.materials.archive_material(citation["material_id"], token="s")
    assert service.materials.deletion_impact(citation["material_id"], token="s")[
        "references"] == []


def test_source_citation_opens_the_actual_snapshot_paragraph(tmp_path, monkeypatch):
    service = KnowledgeThemeService(tmp_path, token="s")
    material, citation = captured_source(service.materials, monkeypatch)
    created = service.create(theme_command(
        evidence=[directional("supports", citation)]), token="s")
    evidence = created["evidence"][0]

    opened = service.open_evidence(created["theme_id"], evidence["evidence_id"], token="s")
    assert opened["status"] == "available"
    assert opened["material_id"] == material["material_id"]
    assert opened["content_hash"] == material["snapshot"]["content_hash"]
    assert opened["source"]["kind"] == "public_web"
    assert opened["source"]["label"] == "https://example.org/observatory"
    assert opened["locator"] == citation["locator"]
    assert opened["excerpt"] == citation["excerpt"]
    # The citation opens the real extracted paragraph, not a Report field.
    assert opened["anchor"]["id"].startswith("p")
    assert opened["segment"] and citation["excerpt"] in opened["segment"]
    assert "Report" not in evidence

    with pytest.raises(CommandError) as missing:
        service.open_evidence(created["theme_id"], "missing", token="s")
    assert missing.value.code == "not_found"
    with pytest.raises(PermissionError):
        service.open_evidence(created["theme_id"], evidence["evidence_id"], token="wrong")


def test_pdf_snapshot_opens_the_explicit_page_when_an_excerpt_repeats(tmp_path):
    import base64
    import pymupdf

    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "The common observation. First page.")
        document.new_page().insert_text((72, 72), "The common observation. Second page.")
        pdf = base64.b64encode(document.tobytes()).decode("ascii")
    service = KnowledgeThemeService(tmp_path, token="s")
    material = service.materials.register_pdf({
        "schema_version": 1, "operation_id": "two-pages", "title": "Two pages",
        "source_label": "User PDF", "pdf_base64": pdf,
    }, token="s")
    quote = {"material_id": material["material_id"], "material_version": material["version"],
             "locator": "结果", "excerpt": "The common observation."}
    with pytest.raises(CommandError) as ambiguous:
        service.create(theme_command(evidence=[directional("supports", quote)]), token="s")
    assert ambiguous.value.code == "ambiguous_source"
    with pytest.raises(CommandError) as wrong_page:
        service.create(theme_command(evidence=[directional("supports", {**quote, "locator": "第 3 页"})]), token="s")
    assert wrong_page.value.code == "invalid_source"

    created = service.create(theme_command(
        evidence=[directional("supports", {**quote, "locator": "第 2 页"})]), token="s")
    evidence = created["evidence"][0]
    from hostrun.desktop_bridge import theme_command as bridge

    kind, opened = bridge(service, {
        "action": "theme_source", "operation_id": "open-page",
        "theme_id": created["theme_id"], "evidence_id": evidence["evidence_id"],
        "expected_version": created["version"],
    }, token="s")
    assert kind == "theme_source"
    assert opened["operation_id"] == "open-page"
    assert opened["anchor"]["page"] == 2
    assert "Second page." in opened["segment"] and "First page." not in opened["segment"]

    updated = service.update({
        "schema_version": 1, "operation_id": "edit-theme", "theme_id": created["theme_id"],
        "expected_version": 1, "judgement": "修订后的判断",
    }, token="s")
    assert updated["version"] == 2
    with pytest.raises(CommandError) as stale:
        service.open_evidence(created["theme_id"], evidence["evidence_id"], token="s", expected_version=1)
    assert stale.value.code == "version_conflict"


def test_related_cards_are_validated_and_versioned_through_the_card_service(tmp_path):
    cards = KnowledgeCardService(tmp_path, token="s")
    themes = KnowledgeThemeService(tmp_path, token="s", cards=cards)
    card = cards.create({"schema_version": 1, "operation_id": "related-card",
                         "title": "扰动响应", "content": "相关卡片内容。"}, token="s")
    created = themes.create(theme_command(
        related_cards=[card["card_id"]]), token="s")
    assert created["related_cards"] == [
        {"card_id": card["card_id"], "version": card["version"]}]

    updated = cards.update({
        "schema_version": 1, "operation_id": "related-card-edit",
        "card_id": card["card_id"], "expected_version": card["version"],
        "title": "扰动响应（更新）"}, token="s")
    with pytest.raises(CommandError) as stale:
        themes.update({
            "schema_version": 1, "operation_id": "theme-stale-relation",
            "theme_id": created["theme_id"], "expected_version": created["version"],
            "related_cards": [{"card_id": card["card_id"], "version": 1}],
        }, token="s")
    assert stale.value.code == "version_conflict"
    assert stale.value.details["kind"] == "related_card"
    assert stale.value.details["card_id"] == card["card_id"]
    assert stale.value.details["current_version"] == updated["version"]
    assert themes.view(created["theme_id"], token="s") == created

    adopted = themes.update({
        "schema_version": 1, "operation_id": "theme-adopt-relation",
        "theme_id": created["theme_id"], "expected_version": created["version"],
        "related_cards": [{"card_id": card["card_id"],
                           "version": updated["version"]}],
    }, token="s")
    assert adopted["related_cards"] == [
        {"card_id": card["card_id"], "version": updated["version"]}]

    for target, code in (("missing", "not_found"),):
        with pytest.raises(CommandError) as exc:
            themes.update({
                "schema_version": 1, "operation_id": f"theme-bad-relation-{target}",
                "theme_id": adopted["theme_id"],
                "expected_version": adopted["version"],
                "related_cards": [target],
            }, token="s")
        assert exc.value.code == code

    archived = cards.archive({
        "schema_version": 1, "operation_id": "related-card-archive",
        "card_id": card["card_id"], "expected_version": updated["version"],
    }, token="s")
    with pytest.raises(CommandError) as exc:
        themes.update({
            "schema_version": 1, "operation_id": "theme-archived-relation",
            "theme_id": adopted["theme_id"], "expected_version": adopted["version"],
            "related_cards": [{"card_id": card["card_id"],
                               "version": archived["version"]}],
        }, token="s")
    assert exc.value.code == "invalid_relation"
    assert themes.view(adopted["theme_id"], token="s") == adopted


def test_failed_relation_or_evidence_leaves_no_half_theme(tmp_path, monkeypatch):
    cards = KnowledgeCardService(tmp_path, token="s")
    themes = KnowledgeThemeService(tmp_path, token="s", cards=cards)
    card = cards.create({"schema_version": 1, "operation_id": "half-card",
                         "title": "已存在卡片", "content": "内容。"}, token="s")
    _, citation = captured_source(themes.materials, monkeypatch)

    with pytest.raises(CommandError) as exc:
        themes.create(theme_command(
            operation_id="half-theme",
            related_cards=[card["card_id"], "missing"],
            evidence=[directional("supports", citation)],
        ), token="s")
    assert exc.value.code == "not_found"
    assert themes.list(token="s") == []
    themes.materials.archive_material(citation["material_id"], token="s")
    assert themes.materials.deletion_impact(citation["material_id"], token="s")[
        "references"] == []


def test_stale_theme_version_shows_diff_and_does_not_overwrite(tmp_path):
    service = KnowledgeThemeService(tmp_path, token="s")
    created = service.create(theme_command(), token="s")
    current = service.update({
        "schema_version": 1, "operation_id": "theme-edit-current",
        "theme_id": created["theme_id"], "expected_version": 1,
        "judgement": "别人已提交的判断。",
    }, token="s")
    assert current["version"] == 2

    with pytest.raises(CommandError) as exc:
        service.update({
            "schema_version": 1, "operation_id": "theme-edit-stale",
            "theme_id": created["theme_id"], "expected_version": 1,
            "judgement": "我的草稿判断。",
            "conditions": ["草稿条件"],
        }, token="s")
    error = exc.value
    assert error.code == "version_conflict"
    assert error.details["kind"] == "theme"
    assert error.details["expected_version"] == 1
    assert error.details["current_version"] == 2
    assert error.details["current"] == current
    assert error.details["changed"] == ["judgement", "conditions"]
    assert error.details["submitted"] == {
        "judgement": "我的草稿判断。", "conditions": ["草稿条件"]}
    assert service.view(created["theme_id"], token="s") == current

    from hostrun.desktop_bridge import theme_command as bridge
    with pytest.raises(CommandError) as bridged:
        bridge(service, {"action": "theme_update",
                         "schema_version": 1, "operation_id": "theme-bridge-stale",
                         "theme_id": created["theme_id"], "expected_version": 1,
                         "judgement": "桌面草稿判断。"}, token="s")
    assert bridged.value.code == "version_conflict"
    assert bridged.value.details["current"] == current
    assert bridged.value.details["changed"] == ["judgement"]


def test_archive_excludes_theme_from_query_but_keeps_history(tmp_path):
    service = KnowledgeThemeService(tmp_path, token="s")
    created = service.create(theme_command(), token="s")
    archived = service.archive({
        "schema_version": 1, "operation_id": "theme-archive-1",
        "theme_id": created["theme_id"], "expected_version": 1,
    }, token="s")
    assert archived["status"] == "archived"
    assert archived["known"] is False
    assert archived["version"] == 2
    assert service.view(created["theme_id"], token="s") == archived

    assert service.query(token="s") == []
    assert [(row["theme_id"], row["status"], row["version"])
            for row in service.list(token="s")] == [
        (created["theme_id"], "archived", 2)]
    with pytest.raises(CommandError) as edit:
        service.update({"schema_version": 1, "operation_id": "edit-archived-theme",
                        "theme_id": created["theme_id"], "expected_version": 2,
                        "judgement": "试图改写归档判断。"}, token="s")
    assert edit.value.code == "invalid_transition"
    with pytest.raises(CommandError) as again:
        service.archive({"schema_version": 1, "operation_id": "theme-archive-again",
                         "theme_id": created["theme_id"],
                         "expected_version": archived["version"]}, token="s")
    assert again.value.code == "invalid_transition"
    assert service.view(created["theme_id"], token="s") == archived


def test_replay_returns_original_result_and_rejects_conflicting_operation(tmp_path):
    service = KnowledgeThemeService(tmp_path, token="s")
    created = service.create(theme_command(), token="s")
    assert service.create(theme_command(), token="s") == created
    with pytest.raises(CommandError) as exc:
        service.create(theme_command(judgement="不同判断。"), token="s")
    assert exc.value.code == "operation_conflict"

    edit = {"schema_version": 1, "operation_id": "theme-edit-replay",
            "theme_id": created["theme_id"], "expected_version": 1,
            "judgement": "第一次编辑。"}
    first = service.update(edit, token="s")
    later = service.update({"schema_version": 1, "operation_id": "theme-edit-later",
                            "theme_id": created["theme_id"],
                            "expected_version": first["version"],
                            "judgement": "第二次编辑。"}, token="s")
    assert later["version"] == 3
    # Replay is decided before the stale expected_version check.
    assert service.update(edit, token="s") == first
    assert service.view(created["theme_id"], token="s") == later


def test_concurrent_create_with_same_operation_id_is_idempotent(tmp_path):
    service = KnowledgeThemeService(tmp_path, token="s")
    results = []

    def run():
        results.append(service.create(theme_command(), token="s")["theme_id"])

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(results) == 4
    assert len(set(results)) == 1
    assert len(service.list(token="s")) == 1


def test_validation_rejects_unbounded_theme_input(tmp_path):
    service = KnowledgeThemeService(tmp_path, token="s")
    cases = [
        (theme_command(judgement=""), "invalid_theme"),
        (theme_command(judgement="   "), "invalid_theme"),
        (theme_command(judgement="判" * 1400), "invalid_theme"),
        (theme_command(conditions="not a list"), "invalid_theme"),
        (theme_command(conditions=[123]), "invalid_theme"),
        (theme_command(conditions=["条" * 700]), "invalid_theme"),
        (theme_command(conditions=["条件"] * 17), "invalid_theme"),
        (theme_command(related_cards=["c"] * 33), "invalid_theme"),
        (theme_command(related_cards=[123]), "invalid_theme"),
        (theme_command(related_cards=[{"card_id": "c", "version": 1,
                                       "extra": True}]), "invalid_theme"),
        (theme_command(evidence=[{"stance": "supports"}]), "invalid_theme"),
        (theme_command(evidence=[directional("supports", {}, )]), "invalid_theme"),
        (theme_command(evidence=[directional("supports", {
            "material_id": "m", "locator": "l", "excerpt": "e",
            "unexpected": "x"})]), "invalid_theme"),
        (theme_command(evidence=[directional("supports", {
            "material_id": "m", "locator": "l" * 2001, "excerpt": "e"})]),
         "invalid_theme"),
        (theme_command(evidence=[directional("supports", {
            "material_id": "m", "locator": "l", "excerpt": "e" * 4001})]),
         "invalid_theme"),
        (theme_command(evidence=[directional("supports", {
            "material_id": "m", "locator": "l", "excerpt": "e"},
            reason="理" * 1400)]), "invalid_theme"),
        (theme_command(evidence=[directional("supports", {
            "material_id": "m", "locator": "l", "excerpt": "e"})] * 65),
         "invalid_theme"),
        (theme_command(judgement="有效判断", schema_version=2), "invalid_command"),
        (theme_command(judgement="有效判断", operation_id=""), "invalid_command"),
    ]
    for index, (command, code) in enumerate(cases):
        with pytest.raises(CommandError) as exc:
            service.create(command, token="s")
        assert exc.value.code == code, (index, command)

    created = service.create(theme_command(operation_id="validate-base"), token="s")
    with pytest.raises(CommandError) as exc:
        service.update({"schema_version": 1, "operation_id": "validate-status",
                        "theme_id": created["theme_id"], "expected_version": 1,
                        "status": "archived"}, token="s")
    assert exc.value.code == "invalid_status"
    with pytest.raises(CommandError) as exc:
        service.view("missing", token="s")
    assert exc.value.code == "not_found"
    with pytest.raises(PermissionError):
        service.list(token="wrong")


def test_public_query_pins_committed_theme_versions(tmp_path, monkeypatch):
    service = KnowledgeThemeService(tmp_path, token="s")
    first = service.create(theme_command(operation_id="query-theme-1"), token="s")
    second = service.create(theme_command(
        operation_id="query-theme-2", judgement="第二个判断。"), token="s")
    archived = service.archive({
        "schema_version": 1, "operation_id": "query-archive-theme",
        "theme_id": first["theme_id"], "expected_version": first["version"]},
        token="s")

    assert [(row["theme_id"], row["version"]) for row in service.query(token="s")] == [
        (second["theme_id"], 1)]
    assert {(row["theme_id"], row["version"], row["status"])
            for row in service.query(token="s", include_archived=True)} == {
        (second["theme_id"], 1, "active"),
        (archived["theme_id"], 2, "archived")}
    pinned = service.query(token="s", theme_ids=[second["theme_id"]])
    assert pinned == [service.view(second["theme_id"], token="s")]

    with pytest.raises(CommandError) as missing:
        service.query(token="s", theme_ids=["missing"])
    assert missing.value.code == "not_found"
    with pytest.raises(CommandError) as empty:
        service.query(token="s", theme_ids=[])
    assert empty.value.code == "invalid_query"
    with pytest.raises(CommandError):
        service.query(token="s", include_archived="yes")
    with pytest.raises(PermissionError):
        service.query(token="wrong")


def test_material_deletion_marks_evidence_source_removed_and_keeps_citation(tmp_path, monkeypatch):
    service = KnowledgeThemeService(tmp_path, token="s")
    material, citation = captured_source(service.materials, monkeypatch)
    created = service.create(theme_command(
        evidence=[directional("supports", citation)]), token="s")
    evidence = created["evidence"][0]

    service.materials.archive_material(material["material_id"], token="s")
    impact = service.materials.deletion_impact(material["material_id"], token="s")
    assert len(impact["references"]) == 1
    assert impact["references"][0]["owner_id"].startswith(
        f"theme:{created['theme_id']}:")
    service.materials.delete_material(
        material["material_id"], impact["confirmation"], token="s")

    current = service.query(token="s", theme_ids=[created["theme_id"]])[0]
    assert current["version"] == created["version"]
    assert current["evidence"][0]["source"] == {
        **evidence["source"], "status": "removed"}
    assert current["evidence"][0]["reason"] == evidence["reason"]
    assert current["evidence"][0]["source"]["excerpt"] == citation["excerpt"]
    assert current["evidence_sufficiency"] == "single_sided"

    removed = service.open_evidence(
        created["theme_id"], evidence["evidence_id"], token="s")
    assert removed["status"] == "removed"
    assert removed["segment"] is None
    assert removed["locator"] == citation["locator"]

    amended = service.update({
        "schema_version": 1, "operation_id": "amend-reason", "theme_id": created["theme_id"],
        "expected_version": created["version"],
        "evidence": [{**current["evidence"][0], "reason": "保留证据，只补充原有判断的理由。"}],
    }, token="s")
    assert amended["evidence"][0]["evidence_id"] == evidence["evidence_id"]
    assert amended["evidence"][0]["source"] == evidence["source"]
    assert service.view(created["theme_id"], token="s")["evidence"][0]["source"]["status"] == "removed"


def test_desktop_bridge_routes_theme_commands_and_authenticates(tmp_path, monkeypatch):
    from hostrun.desktop_bridge import theme_command as bridge

    service = KnowledgeThemeService(tmp_path, token="s")
    kind, created = bridge(service, {"action": "theme_create",
                                     **theme_command()}, token="s")
    assert kind == "theme_view"
    assert created["evidence_sufficiency"] == "unsupported"

    kind, opened = bridge(service, {"action": "theme_open",
                                    "theme_id": created["theme_id"]}, token="s")
    assert kind == "theme_view" and opened == created

    kind, listed = bridge(service, {"action": "theme_list"}, token="s")
    assert kind == "theme_list"
    assert listed[0]["version"] == created["version"]

    kind, queried = bridge(service, {"action": "theme_query",
                                     "theme_ids": [created["theme_id"]]}, token="s")
    assert kind == "theme_query" and queried == [created]

    kind, updated = bridge(service, {
        "action": "theme_update", "schema_version": 1,
        "operation_id": "theme-update-bridge", "theme_id": created["theme_id"],
        "expected_version": created["version"], "conditions": ["更新后的条件"],
    }, token="s")
    assert kind == "theme_view" and updated["version"] == 2
    assert updated["conditions"] == ["更新后的条件"]

    _, citation = captured_source(service.materials, monkeypatch)
    kind, sourced = bridge(service, {
        "action": "theme_update", "schema_version": 1,
        "operation_id": "theme-source-bridge", "theme_id": created["theme_id"],
        "expected_version": updated["version"],
        "evidence": [directional("opposes", citation)],
    }, token="s")
    assert kind == "theme_view"
    evidence_id = sourced["evidence"][0]["evidence_id"]

    kind, target = bridge(service, {"action": "theme_source",
                                    "theme_id": created["theme_id"],
                                    "evidence_id": evidence_id}, token="s")
    assert kind == "theme_source"
    assert target["locator"] == citation["locator"]
    assert citation["excerpt"] in target["segment"]

    kind, archived = bridge(service, {
        "action": "theme_archive", "schema_version": 1,
        "operation_id": "theme-archive-bridge", "theme_id": created["theme_id"],
        "expected_version": sourced["version"],
    }, token="s")
    assert kind == "theme_view" and archived["status"] == "archived"

    with pytest.raises(CommandError):
        bridge(service, {"action": "theme_unknown"}, token="s")
    with pytest.raises(PermissionError):
        bridge(service, {"action": "theme_list"}, token="wrong")
