import pytest

from hostrun.agent_service import CommandError
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.librarian import Librarian


TOKEN = "desktop"
AGENT = "agent:qa"
GUI = "gui"


def create(librarian, kind, operation, *, testing=False, **fields):
    command = {"operation_id": operation, "schema_version": 1, **fields}
    if testing:
        command["test_data"] = True
    origin = AGENT if testing else GUI
    librarian.request_batch(operation, [("create", command)], token=TOKEN, origin=origin, entity=kind)
    return librarian.decide(operation, "confirm", token=TOKEN, origin=origin)["results"][0]


def test_test_records_are_tagged_and_cleanup_only_removes_test_progress_under_real_frontier(tmp_path):
    librarian = Librarian(KnowledgeCardService(tmp_path, token=TOKEN), token=TOKEN)
    real = create(librarian, "frontier", "real", question="真实前沿")
    real_progress = create(librarian, "progress", "real-progress",
                           frontier_id=real["frontier_id"], content="真实进展")
    term = create(librarian, "term", "term", testing=True, title="清理术语", content="测试释义", convergence="unresolved")
    question = create(librarian, "question", "question", testing=True,
                      prompt="清理问题 [[term:" + term["card_id"] + "]]", answer="测试解答", convergence="unresolved")
    source = create(librarian, "source", "source", testing=True, title="清理来源", label="出处", url="https://example.org/")
    frontier = create(librarian, "frontier", "frontier", testing=True, question="清理前沿")
    progress = create(librarian, "progress", "progress", testing=True, frontier_id=real["frontier_id"],
                      content="测试进展", source_ids=[source["source_id"]])
    assert all(record["tags"] == ["测试"] for record in (term, question, source, frontier, progress))
    assert "tags" not in real and "tags" not in real_progress
    assert {(row["kind"], row["id"]) for row in librarian.bulletin_links(token=TOKEN)} == {
        ("term", term["card_id"]), ("question", question["question_id"]),
        ("frontier", frontier["frontier_id"]), ("frontier", real["frontier_id"]),
    }

    plan = librarian.preview_test_cleanup(token=TOKEN)
    assert {row["kind"] for row in plan["entries"] if not row["blocked"]} == {"term", "question", "source", "frontier", "progress"}
    test_progress = next(row for row in plan["entries"] if row["kind"] == "progress")
    assert any(ref["id"] == real["frontier_id"] and ref["effect"] == "retained" for ref in test_progress["references"])

    card = librarian.request_test_cleanup("clean", token=TOKEN, origin=GUI)
    assert card["status"] == "pending"
    assert len(librarian.list_progress(real["frontier_id"], token=TOKEN)) == 2
    with pytest.raises(CommandError, match="等待确认"):
        librarian.request_frontier("update", {"schema_version": 1, "operation_id": "pause",
            "frontier_id": real["frontier_id"], "expected_version": real["version"], "status": "paused"},
            token=TOKEN, origin=GUI)
    with pytest.raises(CommandError, match="本会话"):
        librarian.decide("clean", "confirm", token=TOKEN, origin=AGENT)
    result = librarian.decide("clean", "confirm", token=TOKEN, origin=GUI)
    assert len(result["results"]) == 5
    remaining, = librarian.bulletin_links(token=TOKEN)
    assert (remaining["kind"], remaining["id"]) == ("frontier", real["frontier_id"])
    assert librarian.view_frontier(real["frontier_id"], token=TOKEN) == real
    assert librarian.list_progress(real["frontier_id"], token=TOKEN) == [real_progress]
    assert librarian.preview_test_cleanup(token=TOKEN)["entries"] == []
    assert librarian.decide("clean", "confirm", token=TOKEN, origin=GUI) == result


def test_cleanup_reports_references_from_real_content_and_leaves_that_test_source(tmp_path):
    librarian = Librarian(KnowledgeCardService(tmp_path, token=TOKEN), token=TOKEN)
    source = create(librarian, "source", "source", testing=True, title="测试来源", label="出处", url="https://example.org/")
    real = create(librarian, "frontier", "real", question="真实前沿")
    progress = create(librarian, "progress", "progress", frontier_id=real["frontier_id"],
                      content="需保留的引用", source_ids=[source["source_id"]])
    entry, = librarian.preview_test_cleanup(token=TOKEN)["entries"]
    assert entry["blocked"]
    assert any(ref["id"] == progress["progress_id"] and ref["effect"] == "blocked" for ref in entry["references"])
    with pytest.raises(CommandError, match="没有可清理"):
        librarian.request_test_cleanup("clean", token=TOKEN, origin=GUI)
    assert librarian.view_source(source["source_id"], token=TOKEN) == source


def test_test_selection_never_accepts_an_untagged_record_or_tags_an_existing_record(tmp_path):
    librarian = Librarian(KnowledgeCardService(tmp_path, token=TOKEN), token=TOKEN)
    real = create(librarian, "frontier", "real", question="真实前沿")
    with pytest.raises(CommandError, match="包含非测试"):
        librarian.request_test_cleanup("unsafe", token=TOKEN, origin=GUI,
                                       selection=[["frontier", real["frontier_id"]]])
    with pytest.raises(CommandError, match="只有 Agent 新建"):
        librarian.request_frontier("update", {"schema_version": 1, "operation_id": "retag",
            "frontier_id": real["frontier_id"], "expected_version": real["version"], "test_data": True},
            token=TOKEN, origin=AGENT)
    assert librarian.view_frontier(real["frontier_id"], token=TOKEN) == real


def test_confirmation_revalidation_returns_durable_failure_and_releases_term_lock(tmp_path):
    librarian = Librarian(KnowledgeCardService(tmp_path, token=TOKEN), token=TOKEN)
    target = create(librarian, "term", "test-term", testing=True, title="未清理术语", content="说明")
    card = librarian.request_test_cleanup("clean", token=TOKEN, origin=GUI)
    assert card["status"] == "pending"
    create(librarian, "term", "real-term", title="真实术语", content="定义", relations=[target["card_id"]])
    failure = librarian.decide("clean", "confirm", token=TOKEN, origin=GUI)
    assert failure["status"] == "returned"
    assert failure["error"]["code"] == "test_cleanup_changed"
    assert failure["results"] == []
    assert librarian.decide("clean", "confirm", token=TOKEN, origin=GUI) == failure
    assert librarian.view(target["card_id"], token=TOKEN) == target
    update = {"schema_version": 1, "operation_id": "edit-still-present", "card_id": target["card_id"],
              "expected_version": 1, "content": "可继续编辑"}
    assert librarian.request("update", update, token=TOKEN, origin=GUI)["status"] == "pending"


def test_failed_confirmation_rolls_back_an_earlier_change_in_the_same_batch(tmp_path):
    cards = KnowledgeCardService(tmp_path, token=TOKEN)
    librarian = Librarian(cards, token=TOKEN)
    material = cards.materials.register_text({"schema_version": 1, "operation_id": "material-for-review",
        "title": "原文", "text": "可溯源原文", "source_label": "原始来源"}, token=TOKEN)
    material_id = material["material_id"]
    changes = [
        ("create", {"schema_version": 1, "operation_id": "first", "title": "第一条", "content": "释义"}),
        ("create", {"schema_version": 1, "operation_id": "second", "title": "第二条", "content": "释义",
                    "sources": [{"material_id": material_id, "locator": "p1", "excerpt": "可溯源原文"}]}),
    ]
    assert librarian.request_batch("batch", changes, token=TOKEN, origin=GUI)["status"] == "pending"
    cards.materials.archive_material(material_id, token=TOKEN)
    impact = cards.materials.deletion_impact(material_id, token=TOKEN)
    cards.materials.delete_material(material_id, impact["confirmation"], token=TOKEN)
    result = librarian.decide("batch", "confirm", token=TOKEN, origin=GUI)
    assert result["status"] == "returned"
    assert result["results"] == []
    assert librarian.list(token=TOKEN) == []
    assert librarian.decide("batch", "confirm", token=TOKEN, origin=GUI) == result
