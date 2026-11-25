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
    assert len(librarian.bulletin_links(token=TOKEN)) == 2

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
    assert librarian.bulletin_links(token=TOKEN) == []
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
