"""Owner transaction seams: writes join the caller's transaction.

The seam under test is ``apply_in_transaction`` on the three knowledge owners.
It must write only through the supplied connection, return whether a new
commit-worthy change occurred, and leave notification to the caller after its
own commit. These tests roll back a caller transaction and verify that no
owner row, receipt or notification survived.
"""

import json

import pytest

from hostrun.frontier import FrontierService
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_themes import KnowledgeThemeService
from hostrun.managed_database import database_connection

TOKEN = "s"


def frontier_command(operation_id="tx-frontier", **overrides):
    command = {
        "schema_version": 1,
        "operation_id": operation_id,
        "question": "事务回滚是否保留关注问题？",
        "status": "following",
        "scope": {"text": "事务边界", "alternatives": [], "sources": ["arxiv"]},
        "hard_constraints": {"must_include": ["回滚"], "exclude": []},
        "relations": [],
    }
    command.update(overrides)
    return command


def card_command(operation_id="tx-card", **overrides):
    command = {
        "schema_version": 1,
        "operation_id": operation_id,
        "title": "事务边界",
        "content": "同一调用方事务内的写入要么一起提交，要么一起回滚。",
        "sources": [],
        "relations": [],
    }
    command.update(overrides)
    return command


def theme_command(operation_id="tx-theme", **overrides):
    command = {
        "schema_version": 1,
        "operation_id": operation_id,
        "judgement": "知识写入应复用调用方事务。",
        "conditions": [],
        "related_cards": [],
        "evidence": [],
    }
    command.update(overrides)
    return command


def test_owner_apply_in_transaction_rolls_back_with_the_callers_transaction(tmp_path):
    events = []
    owners = [
        (FrontierService(tmp_path, token=TOKEN, on_change=events.append),
         frontier_command(), "frontier_id", "frontiers", "frontier_operations"),
        (KnowledgeCardService(tmp_path, token=TOKEN, on_change=events.append),
         card_command(), "card_id", "knowledge_cards", "knowledge_card_operations"),
        (KnowledgeThemeService(tmp_path, token=TOKEN, on_change=events.append),
         theme_command(), "theme_id", "knowledge_themes", "knowledge_theme_operations"),
    ]

    for service, command, id_field, table, operations in owners:
        before = len(events)
        with database_connection(tmp_path) as db:
            db.execute("BEGIN IMMEDIATE")
            record, changed = service.apply_in_transaction(
                db, "create", command, token=TOKEN)
            assert changed is True
            assert record["version"] == 1
            db.rollback()

        with database_connection(tmp_path) as db:
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            assert db.execute(f"SELECT COUNT(*) FROM {operations}").fetchone()[0] == 0
        # apply_in_transaction never notifies; the caller owns commit and notify.
        assert len(events) == before

        committed = service.create(command, token=TOKEN)
        assert committed["version"] == 1
        assert events[-1][id_field] == committed[id_field]


def test_apply_in_transaction_notifies_only_after_commit_and_only_when_changed(tmp_path):
    events = []
    cards = KnowledgeCardService(tmp_path, token=TOKEN, on_change=events.append)
    command = card_command("notify-card")

    with database_connection(tmp_path) as db:
        db.execute("BEGIN IMMEDIATE")
        card, changed = cards.apply_in_transaction(db, "create", command, token=TOKEN)
        assert changed is True
        assert events == []
        db.commit()

    cards.notify_committed(card)
    assert events == [{"schema_version": 1, "card_id": card["card_id"],
                       "version": 1, "status": "active"}]

    # A replay returns the receipt as unchanged data and must not notify again.
    before = list(events)
    assert cards.create(command, token=TOKEN) == card
    assert events == before


def test_delete_maps_to_archive_and_archive_is_the_same_receipt(tmp_path):
    cards = KnowledgeCardService(tmp_path, token=TOKEN)
    themes = KnowledgeThemeService(tmp_path, token=TOKEN)
    frontier = FrontierService(tmp_path, token=TOKEN)

    card = cards.create(card_command("archive-source"), token=TOKEN)
    theme = themes.create(theme_command("archive-source"), token=TOKEN)
    target = frontier.create(frontier_command("delete-source"), token=TOKEN)

    card_archive = {"schema_version": 1, "operation_id": "archive-card",
                    "card_id": card["card_id"], "expected_version": card["version"]}
    theme_archive = {"schema_version": 1, "operation_id": "archive-theme",
                     "theme_id": theme["theme_id"], "expected_version": theme["version"]}
    frontier_delete = {"schema_version": 1, "operation_id": "delete-frontier",
                       "frontier_id": target["frontier_id"],
                       "expected_version": target["version"]}

    with database_connection(tmp_path) as db:
        db.execute("BEGIN IMMEDIATE")
        archived_card, card_changed = cards.apply_in_transaction(
            db, "delete", card_archive, token=TOKEN)
        archived_theme, theme_changed = themes.apply_in_transaction(
            db, "delete", theme_archive, token=TOKEN)
        deleted_frontier, frontier_changed = frontier.apply_in_transaction(
            db, "delete", frontier_delete, token=TOKEN)
        db.commit()

    assert card_changed is theme_changed is frontier_changed is True
    assert archived_card["status"] == "archived"
    assert archived_theme["status"] == "archived"
    assert deleted_frontier["status"] == "deleted"

    with database_connection(tmp_path) as db:
        db.execute("BEGIN IMMEDIATE")
        replayed_card, card_changed = cards.apply_in_transaction(
            db, "archive", card_archive, token=TOKEN)
        replayed_theme, theme_changed = themes.apply_in_transaction(
            db, "archive", theme_archive, token=TOKEN)
        db.commit()

    # The internal "archive" spelling rewrites the same receipt as "delete".
    assert (replayed_card, card_changed) == (archived_card, False)
    assert (replayed_theme, theme_changed) == (archived_theme, False)


def test_keeper_origin_is_a_trusted_keyword_and_receipts_stay_serialized(tmp_path):
    cards = KnowledgeCardService(tmp_path, token=TOKEN)
    themes = KnowledgeThemeService(tmp_path, token=TOKEN)

    keeper_command = card_command("keeper-card")
    with database_connection(tmp_path) as db:
        db.execute("BEGIN IMMEDIATE")
        keeper_card, _ = cards.apply_in_transaction(
            db, "create", keeper_command, token=TOKEN, origin="keeper")
        db.commit()
    assert keeper_card["origin"] == "keeper"

    with database_connection(tmp_path) as db:
        request = db.execute(
            "SELECT request FROM knowledge_card_operations WHERE operation_id=?",
            ("keeper-card",)).fetchone()[0]
    # Trusted attribution sits beside the command; replay keeps the same shape.
    assert json.loads(request) == {"action": "create", "command": keeper_command,
                                   "origin": "keeper"}
    with database_connection(tmp_path) as db:
        db.execute("BEGIN IMMEDIATE")
        replayed, changed = cards.apply_in_transaction(
            db, "create", keeper_command, token=TOKEN, origin="keeper")
        db.commit()
    assert (replayed, changed) == (keeper_card, False)

    # A client-supplied command field cannot turn a manual write into Keeper's.
    client_claimed = card_command("client-origin", origin="keeper")
    with database_connection(tmp_path) as db:
        db.execute("BEGIN IMMEDIATE")
        manual_card, _ = cards.apply_in_transaction(
            db, "create", client_claimed, token=TOKEN)
        db.commit()
    assert manual_card["origin"] == "manual"
    with database_connection(tmp_path) as db:
        manual_request = db.execute(
            "SELECT request FROM knowledge_card_operations WHERE operation_id=?",
            ("client-origin",)).fetchone()[0]
    # The ordinary receipt keeps its old serialization; the command field is
    # inert data and never becomes trusted attribution.
    assert json.loads(manual_request) == {
        "action": "create", "command": client_claimed}

    keeper_theme_command = theme_command("keeper-theme")
    with database_connection(tmp_path) as db:
        db.execute("BEGIN IMMEDIATE")
        keeper_theme, _ = themes.apply_in_transaction(
            db, "create", keeper_theme_command, token=TOKEN, origin="keeper")
        db.commit()
    assert keeper_theme["origin"] == "keeper"


def test_apply_in_transaction_requires_the_owner_token(tmp_path):
    cards = KnowledgeCardService(tmp_path, token=TOKEN)

    with database_connection(tmp_path) as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(PermissionError):
            cards.apply_in_transaction(
                db, "create", card_command("unauthorized"), token="wrong")
        db.rollback()

    assert cards.list(token=TOKEN) == []
