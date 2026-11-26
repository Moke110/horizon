import pytest

from hostrun.agent_service import CommandError
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.librarian import Librarian
from hostrun.voyager_service import VoyagerService


def confirm(librarian, entity, action, operation, **fields):
    command = {"schema_version": 1, "operation_id": operation, **fields}
    librarian.request_batch(operation, [(action, command)], token="s", origin="gui", entity=entity)
    return librarian.decide(operation, "confirm", token="s", origin="gui")["results"][0]


def test_title_only_frontier_keeps_voyager_history_after_last_progress_is_deleted(tmp_path):
    librarian = Librarian(KnowledgeCardService(tmp_path, token="s"), token="s")
    librarian.request_frontier("create", {"schema_version": 1, "operation_id": "new",
                                        "question": "Photonic crystals"}, token="s", origin="gui")
    assert librarian.list_frontiers(token="s") == []
    frontier = librarian.decide("new", "confirm", token="s", origin="gui")["results"][0]
    frontier_id = frontier["frontier_id"]
    calls = []

    class Provider:
        def search(self, query):
            calls.append(query)
            return {"candidates": [], "exhausted": True, "receipt": "fixture"}

    voyager = VoyagerService(tmp_path, token="s", providers={"arxiv": Provider()}, clock=lambda: 150)
    plan = voyager.create({"schema_version": 1, "operation_id": "plan",
                          "scope": {"frontier_id": frontier_id},
                          "window": {"mode": "incremental", "basis": "discovered",
                                     "from_at": 100, "until_at": 200, "overlap_seconds": 0}}, token="s")
    result = voyager.execute({"schema_version": 1, "operation_id": "run",
                              "request_id": plan["request_id"], "expected_version": plan["version"]}, token="s")
    assert result["status"] == "complete"
    assert len(calls) == 1 and calls[0]["term"] == "Photonic crystals"
    assert voyager.recent(token="s", frontier_id=frontier_id)[0]["request_id"] == plan["request_id"]

    sources = [confirm(librarian, "source", "create", f"source-{n}", title=f"报道 {n}",
                       label="真实来源", url=f"https://example.com/{n}") for n in (1, 2)]
    progress = confirm(librarian, "progress", "create", "progress", frontier_id=frontier_id,
                       content="实验组公开了新的测量结果。", source_ids=[source["source_id"] for source in sources])
    rows = librarian.list_progress(frontier_id, token="s")
    assert rows == [progress]
    assert [librarian.view_source(source_id, token="s") for source_id in rows[0]["source_ids"]] == sources
    confirm(librarian, "progress", "delete", "remove", frontier_id=frontier_id,
            progress_id=progress["progress_id"], expected_version=1)
    assert librarian.list_progress(frontier_id, token="s") == []
    assert librarian.view_frontier(frontier_id, token="s") == frontier
    assert voyager.recent(token="s", frontier_id=frontier_id)[0]["request_id"] == plan["request_id"]


def test_pending_progress_keeps_parent_readable_and_reject_preserves_progress(tmp_path):
    librarian = Librarian(KnowledgeCardService(tmp_path, token="s"), token="s")
    frontier = confirm(librarian, "frontier", "create", "new", question="新前沿")
    frontier_id = frontier["frontier_id"]
    progress = confirm(librarian, "progress", "create", "progress", frontier_id=frontier_id, content="已有进展")
    data = {"schema_version": 1, "operation_id": "edit", "frontier_id": frontier_id,
            "progress_id": progress["progress_id"], "expected_version": 1, "content": "新进展"}
    card = librarian.request_progress("update", data, token="s", origin="gui")
    assert card["summaries"] == ["新进展"]
    assert librarian.list_progress(frontier_id, token="s") == [progress]
    with pytest.raises(CommandError, match="等待确认"):
        librarian.request_frontier("delete", {"schema_version": 1, "operation_id": "delete",
                                              "frontier_id": frontier_id, "expected_version": 1},
                                   token="s", origin="agent")
    librarian.decide("edit", "reject", token="s", origin="gui")
    assert librarian.list_progress(frontier_id, token="s") == [progress]
    assert librarian.view_frontier(frontier_id, token="s") == frontier

def test_frontier_tracking_preserves_resolution_across_tracking_changes(tmp_path):
    librarian = Librarian(KnowledgeCardService(tmp_path, token="s"), token="s")
    item = confirm(librarian, "frontier", "create", "new", question="光子")
    assert item["tracked"] is True
    for version, fields, tracked, resolved in (
        (1, {"tracked": False}, False, False),
        (2, {"status": "resolved"}, False, True),
        (3, {"tracked": True}, True, True),
    ):
        item = confirm(librarian, "frontier", "update", str(version), frontier_id=item["frontier_id"],
                       expected_version=version, **fields)
        assert item["tracked"] is tracked
        assert (item.get("resolved_at") is not None) is resolved
        assert librarian.list_frontiers(token="s")[0]["tracked"] is tracked

def test_legacy_frontier_progress_and_board_follow_same_identity(tmp_path):
    import json
    import sqlite3

    librarian = Librarian(KnowledgeCardService(tmp_path, token="s"), token="s")
    frontier = confirm(librarian, "frontier", "create", "new-frontier", question="Original title")
    original = frontier["frontier_id"]
    progress = confirm(librarian, "progress", "create", "new-progress", frontier_id=original,
                       content="Recorded update", source_ids=[])
    with sqlite3.connect(tmp_path / "horizon.sqlite3") as db:
        # Reconstruct the previous schema with a non-UUID owner key.
        db.execute("DELETE FROM hpedia_migrations WHERE name='typed_ids'")
        db.execute("DROP TABLE hpedia_reference_reviews")
        db.execute("UPDATE frontiers SET frontier_id='legacy', result=?",
                   (json.dumps({**frontier,"frontier_id":"legacy"}),))
        db.execute("UPDATE frontier_progress SET frontier_id='legacy', result=?",
                   (json.dumps({**progress,"frontier_id":"legacy"}),))
    librarian = Librarian(KnowledgeCardService(tmp_path, token="s"), token="s")
    migrated, = librarian.list_frontiers(token="s")
    identity = migrated["frontier_id"]
    assert identity.startswith("f-")
    row, = librarian.list_progress(identity, token="s")
    assert (row["frontier_id"],row["progress_id"]) == (identity,progress["progress_id"])
    confirm(librarian, "frontier", "update", "rename", frontier_id=identity,
            expected_version=migrated["version"],question="Current title")
    link, = librarian.bulletin_links(token="s")
    assert (link["id"],link["title"]) == (identity,"Current title")
