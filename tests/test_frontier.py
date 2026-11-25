"""Focused public-behavior tests for K1: local Frontier drives real retrieval.

The seam under test is FrontierService (public application service) ->
KnowledgeScopeProvider -> VoyagerService, persisted in the stable data home.
Tests exercise positive, failure, replay/concurrency and persistence behavior;
they do not reach any live source or cloud Base.
"""

import threading

import pytest

from hostrun.agent_service import CommandError
from hostrun.frontier import FrontierService
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.librarian import Librarian
from hostrun.voyager_service import VoyagerService


def frontier_command(**overrides):
    command = {
        "schema_version": 1,
        "operation_id": "create-1",
        "question": "可解释人工智能如何用于单细胞扰动预测？",
        "status": "following",
        "scope": {
            "text": "可解释人工智能",
            "alternatives": ["explainable artificial intelligence"],
            "sources": ["arxiv"],
        },
        "hard_constraints": {"must_include": ["解释"], "exclude": ["广告"]},
        "relations": [],
    }
    command.update(overrides)
    return command


def window(**overrides):
    result = {"mode": "initial", "basis": "discovered",
              "from_at": 100, "until_at": 200, "overlap_seconds": 10}
    result.update(overrides)
    return result


def voyager_command(frontier_id, operation="voy-1", **scope_overrides):
    scope = {"frontier_id": frontier_id}
    scope.update(scope_overrides)
    return {"schema_version": 1, "operation_id": operation,
            "scope": scope, "window": window()}


def test_create_frontier_persists_question_scope_status_and_empty_relations(tmp_path):
    service = FrontierService(tmp_path, token="s")
    created = service.create(frontier_command(), token="s")
    assert created["version"] == 1
    assert created["question"] == "可解释人工智能如何用于单细胞扰动预测？"
    assert created["status"] == "following"
    assert created["relations"] == []
    assert created["scope"]["text"] == "可解释人工智能"
    assert created["scope"]["sources"] == ["arxiv"]
    assert created["hard_constraints"] == {"must_include": ["解释"], "exclude": ["广告"]}
    assert service.view(created["frontier_id"], token="s") == created
    # Persistence across a fresh service on the same home.
    reopened = FrontierService(tmp_path, token="s")
    assert reopened.view(created["frontier_id"], token="s") == created


def test_real_knowledge_provider_generates_voyager_queries_from_frontier(tmp_path):
    frontier = FrontierService(tmp_path, token="s").create(frontier_command(), token="s")
    voyager = VoyagerService(tmp_path, token="s")
    planned = voyager.create(voyager_command(frontier["frontier_id"]), token="s")
    assert planned["scope"]["kind"] == "frontier"
    assert planned["scope"]["frontier_id"] == frontier["frontier_id"]
    assert planned["scope"]["frontier_version"] == 1
    assert planned["scope"]["text"] == "可解释人工智能"
    assert planned["scope"]["must_include"] == ["解释"]
    assert planned["scope"]["exclude"] == ["广告"]
    assert {q["term"] for q in planned["queries"]} == {"可解释人工智能", "explainable artificial intelligence"}
    assert all(q["source"] == "arxiv" for q in planned["queries"])


def test_editing_scope_changes_new_queries_records_version_old_unchanged(tmp_path):
    service = FrontierService(tmp_path, token="s")
    frontier = service.create(frontier_command(), token="s")
    voyager = VoyagerService(tmp_path, token="s")
    old = voyager.create(voyager_command(frontier["frontier_id"], "old"), token="s")

    edited = service.update({
        "schema_version": 1, "operation_id": "upd-1",
        "frontier_id": frontier["frontier_id"], "expected_version": frontier["version"],
        "scope": {"text": "单细胞扰动预测", "alternatives": [], "sources": ["arxiv"]},
    }, token="s")
    assert edited["version"] == 2

    new = voyager.create(voyager_command(frontier["frontier_id"], "new"), token="s")
    assert new["scope"]["text"] == "单细胞扰动预测"
    assert new["scope"]["frontier_version"] == 2
    assert {q["term"] for q in new["queries"]} == {"单细胞扰动预测"}

    # The old request keeps its own scope snapshot and version; it is not rewritten.
    old_after = voyager.get(old["request_id"], token="s")
    assert old_after["scope"]["text"] == "可解释人工智能"
    assert old_after["scope"]["frontier_version"] == 1


def test_pause_resolve_delete_stop_future_retrieval_and_preserve_frontier(tmp_path):
    service = FrontierService(tmp_path, token="s")
    frontier = service.create(frontier_command(), token="s")
    voyager = VoyagerService(tmp_path, token="s")

    paused = service.update({"schema_version": 1, "operation_id": "pause-1",
                             "frontier_id": frontier["frontier_id"],
                             "expected_version": frontier["version"], "status": "paused"}, token="s")
    assert paused["status"] == "paused"
    with pytest.raises(CommandError) as exc:
        voyager.create(voyager_command(frontier["frontier_id"], "after-pause"), token="s")
    assert exc.value.code == "frontier_inactive"

    resolved = service.update({"schema_version": 1, "operation_id": "resolve-1",
                               "frontier_id": frontier["frontier_id"],
                               "expected_version": paused["version"], "status": "resolved"}, token="s")
    with pytest.raises(CommandError):
        voyager.create(voyager_command(frontier["frontier_id"], "after-resolve"), token="s")

    deleted = service.delete({"schema_version": 1, "operation_id": "delete-1",
                              "frontier_id": frontier["frontier_id"],
                              "expected_version": resolved["version"]}, token="s")
    assert deleted["status"] == "deleted"
    with pytest.raises(CommandError):
        voyager.create(voyager_command(frontier["frontier_id"], "after-delete"), token="s")

    # History is preserved: the Frontier row remains inspectable and its status sticks.
    assert service.view(frontier["frontier_id"], token="s")["status"] == "deleted"
    assert [row["status"] for row in service.list(token="s")] == ["deleted"]


def test_version_conflict_and_terminal_guard_on_update(tmp_path):
    service = FrontierService(tmp_path, token="s")
    frontier = service.create(frontier_command(), token="s")
    with pytest.raises(CommandError) as exc:
        service.update({"schema_version": 1, "operation_id": "bad-1",
                        "frontier_id": frontier["frontier_id"],
                        "expected_version": 99, "status": "resolved"}, token="s")
    assert exc.value.code == "version_conflict"

    service.delete({"schema_version": 1, "operation_id": "delete-2",
                    "frontier_id": frontier["frontier_id"],
                    "expected_version": frontier["version"]}, token="s")
    with pytest.raises(CommandError) as exc:
        service.update({"schema_version": 1, "operation_id": "edit-deleted",
                        "frontier_id": frontier["frontier_id"],
                        "expected_version": 2, "status": "following"}, token="s")
    assert exc.value.code == "invalid_transition"


def test_frontier_expected_version_pin_blocks_stale_retrieval(tmp_path):
    frontier = FrontierService(tmp_path, token="s").create(frontier_command(), token="s")
    voyager = VoyagerService(tmp_path, token="s")
    with pytest.raises(CommandError) as exc:
        voyager.create(voyager_command(frontier["frontier_id"], "stale", expected_version=99), token="s")
    assert exc.value.code == "version_conflict"


def test_replay_same_operation_returns_same_frontier_and_rejects_conflict(tmp_path):
    service = FrontierService(tmp_path, token="s")
    created = service.create(frontier_command(), token="s")
    assert service.create(frontier_command(), token="s") == created
    with pytest.raises(CommandError) as exc:
        service.create(frontier_command(question="另一个问题"), token="s")
    assert exc.value.code == "operation_conflict"


def test_concurrent_create_with_same_operation_id_is_idempotent(tmp_path):
    service = FrontierService(tmp_path, token="s")
    results = []

    def run():
        results.append(service.create(frontier_command(), token="s")["frontier_id"])

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(results) == 4
    assert len(set(results)) == 1
    assert len(service.list(token="s")) == 1


def test_invalid_scope_and_status_are_rejected(tmp_path):
    service = FrontierService(tmp_path, token="s")
    with pytest.raises(CommandError) as exc:
        service.create(frontier_command(scope={"text": "主题", "alternatives": [], "sources": []}), token="s")
    assert exc.value.code == "invalid_scope"
    with pytest.raises(CommandError) as exc:
        service.create(frontier_command(status="weird"), token="s")
    assert exc.value.code == "invalid_status"


def test_desktop_frontier_commands_route_through_public_seam(tmp_path):
    from hostrun.desktop_bridge import frontier_command as bridge

    librarian = Librarian(KnowledgeCardService(tmp_path, token="s"), token="s")
    kind, pending = bridge(librarian, {"action": "frontier_create", **frontier_command()}, token="s")
    assert kind == "frontier_confirmation" and pending["status"] == "pending"
    assert librarian.list_frontiers(token="s") == []
    _, result = bridge(librarian, {"action": "frontier_confirm", "operation_id": pending["operation_id"],
                                  "choice": "confirm"}, token="s")
    created = result["results"][0]
    assert created["status"] == "following"
    kind, view = bridge(librarian, {"action": "frontier_open",
                                  "frontier_id": created["frontier_id"]}, token="s")
    assert kind == "frontier_view" and view == created
    kind, view = bridge(librarian, {"action": "frontier_open",
                                  "frontier_id": created["frontier_id"],
                                  "operation_id": "open-frontier"}, token="s")
    assert kind == "frontier_view"
    assert view == {**created, "read_operation_id": "open-frontier"}
    kind, listed = bridge(librarian, {"action": "frontier_list"}, token="s")
    assert kind == "frontier_list" and listed[0]["frontier_id"] == created["frontier_id"]
    with pytest.raises(CommandError):
        bridge(librarian, {"action": "frontier_unknown"}, token="s")


def test_pause_after_plan_stops_provider_execution(tmp_path):
    service = FrontierService(tmp_path, token="s")
    frontier = service.create(frontier_command(), token="s")

    class Recording:
        def __init__(self):
            self.calls = []

        def search(self, query):
            self.calls.append(query)
            return {"candidates": [], "exhausted": True, "next_cursor": None,
                    "receipt": "should-not-run"}

    source = Recording()
    voyager = VoyagerService(tmp_path, token="s", providers={"arxiv": source},
                             clock=lambda: 150)
    planned = voyager.create(voyager_command(frontier["frontier_id"]), token="s")
    service.update({"schema_version": 1, "operation_id": "pause-3",
                    "frontier_id": frontier["frontier_id"],
                    "expected_version": frontier["version"], "status": "paused"}, token="s")

    with pytest.raises(CommandError) as exc:
        voyager.execute({"schema_version": 1, "operation_id": "run-after-pause",
                         "request_id": planned["request_id"],
                         "expected_version": planned["version"]}, token="s")
    assert exc.value.code == "frontier_inactive"
    assert source.calls == []
    assert voyager.get(planned["request_id"], token="s")["status"] == "planned"


def test_desktop_replayed_update_returns_its_original_committed_view(tmp_path):
    from hostrun.desktop_bridge import frontier_command as bridge

    librarian = Librarian(KnowledgeCardService(tmp_path, token="s"), token="s")

    def apply(command):
        kind, pending = bridge(librarian, command, token="s")
        assert kind == "frontier_confirmation"
        _, result = bridge(librarian, {"action": "frontier_confirm", "operation_id": pending["operation_id"],
                                      "choice": "confirm"}, token="s")
        return result["results"][0]

    created = apply({"action": "frontier_create", **frontier_command()})
    pause = {"action": "frontier_update", "schema_version": 1, "operation_id": "upd-pause",
             "frontier_id": created["frontier_id"], "expected_version": created["version"],
             "status": "paused"}
    paused = apply(pause)
    assert (paused["version"], paused["status"], paused["operation_id"]) == (2, "paused", "upd-pause")

    follow = {"action": "frontier_update", "schema_version": 1, "operation_id": "upd-follow",
              "frontier_id": created["frontier_id"], "expected_version": paused["version"],
              "status": "following"}
    followed = apply(follow)
    assert followed["version"] == 3 and followed["status"] == "following"

    assert apply(pause) == paused
    assert librarian.view_frontier(created["frontier_id"], token="s") == followed


def test_resolve_scope_is_the_public_knowledge_provider_entry(tmp_path):
    service = FrontierService(tmp_path, token="s")
    frontier = service.create(frontier_command(), token="s")
    scope = service.resolve_scope(frontier["frontier_id"], token="s")
    assert scope == {
        "kind": "frontier",
        "frontier_id": frontier["frontier_id"],
        "frontier_version": 1,
        "text": "可解释人工智能",
        "alternatives": ["explainable artificial intelligence"],
        "must_include": ["解释"],
        "exclude": ["广告"],
        "sources": ["arxiv"],
    }
    paused = service.update({"schema_version": 1, "operation_id": "pause-2",
                             "frontier_id": frontier["frontier_id"],
                             "expected_version": 1, "status": "paused"}, token="s")
    with pytest.raises(CommandError) as exc:
        service.resolve_scope(paused["frontier_id"], token="s")
    assert exc.value.code == "frontier_inactive"


def test_pause_during_first_receipt_stops_later_provider_calls(tmp_path):
    service = FrontierService(tmp_path, token="s")
    frontier = service.create(frontier_command(), token="s")
    calls = []

    class PausingSource:
        def search(self, query):
            if not calls:
                service.update({"schema_version": 1, "operation_id": "pause-during-run",
                                "frontier_id": frontier["frontier_id"], "expected_version": 1,
                                "status": "paused"}, token="s")
            calls.append(query)
            return {"candidates": [], "exhausted": True, "receipt": "first"}

    voyager = VoyagerService(tmp_path, token="s", providers={"arxiv": PausingSource()}, clock=lambda: 150)
    planned = voyager.create(voyager_command(frontier["frontier_id"]), token="s")
    command = {"schema_version": 1, "operation_id": "run", "request_id": planned["request_id"],
               "expected_version": planned["version"]}
    result = voyager.execute(command, token="s")
    assert len(calls) == 1
    assert (result["status"], result["stop_reason"]) == ("partial", "frontier_inactive")
    assert result["queries"][0]["attempts"][-1]["status"] == "received"
    assert voyager.execute(command, token="s") == result
