"""Focused K7 tests for the pure Keeper rejection/duplicate policy helper."""

import json
import sqlite3

import pytest

from hostrun.keeper_evidence_policy import (
    ALREADY_APPLIED, DUPLICATE_CHANGE, INVALID_SUPERSEDES, NO_EFFECT,
    change_intent, intent, review_policy,
)


QUOTE_A = "Calibrated optical sensors measure received radiation every day."
QUOTE_B = "The archive records a second independent observation campaign."


@pytest.fixture
def db():
    """A small true SQLite database carrying the K6 proposal table."""
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE keeper_proposals("
        "proposal_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, "
        "result TEXT NOT NULL)")
    yield connection
    connection.close()


def store(db, proposal_id, changes, *, status="rejected"):
    proposal = {
        "proposal_id": proposal_id, "revision": 1, "status": status,
        "request_id": "request-1", "changes": changes,
    }
    db.execute("INSERT INTO keeper_proposals VALUES(?,?,?)",
               (proposal_id, "request-1", json.dumps(proposal)))
    return proposal


def source(excerpt=QUOTE_A, *, material_id="mat-1", material_version=1,
           content_hash="hash-1", anchor="p2", locator="p2",
           title="Observatory report", source_label="Fixture"):
    return {
        "material_id": material_id, "material_version": material_version,
        "content_hash": content_hash, "anchor": anchor, "locator": locator,
        "excerpt": excerpt, "title": title, "source_label": source_label,
    }


def change(kind="card", action="update", identity="card-1",
           expected_version=3, before=None, after=None, sources=()):
    return {
        "kind": kind, "action": action, "id": identity,
        "expected_version": expected_version, "before": before,
        "after": after, "reason": "Reported by the test report.",
        "sources": [dict(item) for item in sources],
    }


def entry(value):
    return {
        "kind": value["kind"], "id": value["id"],
        "before": value["before"], "after": value["after"],
        "proposal": value,
    }


def card_before(title="Old title", content="A manually recorded concept.",
                aliases=(), relations=()):
    return {
        "schema_version": 1, "card_id": "card-1", "version": 3,
        "operation_id": "old-operation", "title": title, "content": content,
        "aliases": list(aliases), "recall_terms": [], "relations": list(relations),
        "sources": [], "questionable": False, "known": True,
        "origin": "manual", "status": "active",
        "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
    }


def card_update(*, title="New title", before_title="Old title",
                content=None, identity="card-1", expected_version=3,
                sources=()):
    before = card_before(before_title)
    after = dict(before)
    after.update(version=expected_version + 1,
                 operation_id="new-operation",
                 updated_at="2026-02-02T00:00:00", title=title)
    if content is not None:
        after["content"] = content
    return entry(change(identity=identity, expected_version=expected_version,
                        before=before, after=after, sources=sources))


def evidence(evidence_id, excerpt=QUOTE_A, *, stance="supports",
             reason="The report states it.", locator="p2", material_id="mat-1",
             content_hash="hash-1", anchor="p2"):
    return {
        "evidence_id": evidence_id, "stance": stance, "reason": reason,
        "source": source(excerpt, material_id=material_id, content_hash=content_hash,
                         anchor=anchor, locator=locator),
    }


def theme_before(judgement="Old judgement", evidence_items=()):
    return {
        "schema_version": 1, "theme_id": "theme-1", "version": 2,
        "operation_id": "old-operation", "judgement": judgement,
        "conditions": [], "related_cards": [], "evidence": list(evidence_items),
        "evidence_sufficiency": "unsupported", "known": True,
        "origin": "manual", "status": "active",
        "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
    }


def theme_update(*, judgement="Old judgement", evidence_items=(),
                 before_evidence=(), identity="theme-1", sources=()):
    before = theme_before(judgement, before_evidence)
    after = dict(before)
    after.update(version=3, operation_id="new-operation",
                 updated_at="2026-02-02T00:00:00",
                 judgement=judgement, evidence=list(evidence_items))
    return entry(change(kind="theme", identity=identity, before=before,
                        after=after, sources=sources))


def supersedes(proposal_id, reason):
    return {"supersedes_proposal_id": proposal_id,
            "new_evidence_reason": reason}


def test_intent_ignores_versions_operation_ids_timestamps_labels_and_order():
    first = card_update(
        title="Calibrated  OPTICS", sources=[source()])
    before = card_before("Old title", aliases=["b", "a"])
    after = dict(before)
    after.update(version=99, operation_id="other-operation",
                 updated_at="2030-01-01T00:00:00", title="calibrated optics",
                 aliases=["A", "B"])
    second = entry(change(before=before, after=after, expected_version=77,
                          sources=[source(material_id="mat-9",
                                          source_label="Different label")]))

    assert intent(first) == intent(second)
    assert change_intent(first["proposal"]) == intent(second)


def test_intent_tracks_entity_action_and_actual_before_after_changes():
    baseline = card_update(title="New title")
    assert intent(baseline) != intent(card_update(title="Other title"))
    assert intent(baseline) == intent(card_update(before_title="Other old"))
    assert intent(baseline) != intent(card_update(identity="card-2"))
    assert intent(baseline) != intent(card_update(content="Different body"))

    rejected_evidence = theme_update(
        evidence_items=[evidence("evidence-1")],
        sources=[source()])
    recaptured = theme_update(
        evidence_items=[evidence("evidence-9", material_id="mat-9")],
        before_evidence=[],
        sources=[source(material_id="mat-9")])
    different_reason = theme_update(
        evidence_items=[evidence("evidence-1", reason="Another reason")],
        sources=[source()])
    different_stance = theme_update(
        evidence_items=[evidence("evidence-1", stance="opposes")],
        sources=[source()])
    assert intent(rejected_evidence) == intent(recaptured)
    assert intent(rejected_evidence) != intent(different_reason)
    assert intent(rejected_evidence) != intent(different_stance)


def test_intent_ignores_evidence_order_and_related_card_versions():
    first = theme_update(before_evidence=[evidence("a", locator="p1"),
                                          evidence("b", locator="p2")])
    second = theme_update(before_evidence=[evidence("b", locator="p2"),
                                           evidence("a", locator="p1")])
    assert intent(first) == intent(second)

    before = theme_before()
    after = dict(before)
    after["related_cards"] = [{"card_id": "card-1", "version": 7}]
    newer = dict(after, related_cards=[{"card_id": "card-1", "version": 8}])
    assert change_intent(change(kind="theme", before=before, after=after)) == \
        change_intent(change(kind="theme", before=before, after=newer))


def test_review_policy_is_none_without_matching_history(db):
    assert review_policy(db, [card_update()]) is None
    store(db, "rejected-1", [card_update(title="A different title")["proposal"]])
    assert review_policy(db, [card_update()]) is None


def test_rejected_same_edit_is_a_duplicate_despite_recapture_and_regrouping(db):
    rejected = card_update(title="New title", sources=[source()])
    store(db, "rejected-1", [rejected["proposal"]])

    recaptured = card_update(
        title="  new   TITLE ", expected_version=4,
        sources=[source("  CALIBRATED optical sensors measure received "
                        "radiation every day.  ",
                        material_id="mat-9", material_version=4)])

    result = review_policy(db, [recaptured])

    assert result["reason"] == DUPLICATE_CHANGE
    assert result["proposal_id"] == "rejected-1"
    assert result["kind"] == "card"
    assert result["id"] == "card-1"


def test_a_larger_group_that_hides_a_rejected_piece_is_still_duplicate(db):
    rejected = theme_update(evidence_items=[evidence("evidence-1")],
                            sources=[source()])
    store(db, "rejected-theme", [rejected["proposal"]])

    hidden = theme_update(
        judgement="A new judgement",
        before_evidence=[evidence("evidence-0", locator="p1")],
        evidence_items=[evidence("evidence-0", locator="p1"),
                        evidence("evidence-1"),
                        evidence("evidence-2", locator="p3")],
        sources=[source()])
    assert review_policy(db, [hidden])["reason"] == DUPLICATE_CHANGE

    replacement = theme_update(
        judgement="A new judgement",
        before_evidence=[],
        evidence_items=[evidence("evidence-3", excerpt=QUOTE_B)],
        sources=[source(QUOTE_B, content_hash="hash-3", anchor="p3")])
    assert review_policy(db, [replacement]) is None


def test_new_evidence_needs_the_matching_rejected_proposal_and_a_reason(db):
    rejected = theme_update(evidence_items=[evidence("evidence-1")],
                            sources=[source()])
    store(db, "rejected-theme", [rejected["proposal"]])
    store(db, "rejected-other", [theme_update(
        identity="theme-2", evidence_items=[evidence("old")],
        sources=[source(QUOTE_B)])["proposal"]])

    incoming = theme_update(
        evidence_items=[evidence("evidence-1"),
                        evidence("evidence-2", excerpt=QUOTE_B,
                                 locator="p4", content_hash="hash-4",
                                 anchor="p4")],
        sources=[source(), source(QUOTE_B, locator="p4",
                                  content_hash="hash-4", anchor="p4")])

    assert review_policy(db, [incoming], supersedes(
        "rejected-theme", "Two independent observation campaigns now agree.")) is None
    assert review_policy(db, [incoming], supersedes("rejected-theme", ""))["reason"] == \
        INVALID_SUPERSEDES
    assert review_policy(db, [incoming], supersedes("missing", "Reason"))["reason"] == \
        INVALID_SUPERSEDES
    assert review_policy(db, [incoming], supersedes("rejected-other", "Reason"))[
        "reason"] == INVALID_SUPERSEDES


def test_cosmetic_or_same_anchor_copy_is_not_new_evidence(db):
    rejected = theme_update(evidence_items=[evidence("evidence-1")],
                            sources=[source()])
    store(db, "rejected-theme", [rejected["proposal"]])

    cosmetic = theme_update(
        evidence_items=[evidence("evidence-1")],
        sources=[source("  calibrated OPTICAL sensors measure received "
                        "radiation every day. ", material_id="mat-new")])
    same_anchor = theme_update(
        evidence_items=[evidence("evidence-1")],
        sources=[source(QUOTE_B)])
    new_anchor = theme_update(
        evidence_items=[evidence("evidence-1")],
        sources=[source(QUOTE_B, content_hash="hash-2", anchor="p3",
                        locator="p3")])

    assert review_policy(db, [cosmetic], supersedes(
        "rejected-theme", "Recaptured metadata"))["reason"] == DUPLICATE_CHANGE
    assert review_policy(db, [same_anchor], supersedes(
        "rejected-theme", "Another quote on the same page"))["reason"] == \
        DUPLICATE_CHANGE
    assert review_policy(db, [new_anchor], supersedes(
        "rejected-theme", "A second independent observation page")) is None


def test_all_affected_rejected_entries_are_compared(db):
    store(db, "rejected-title",
          [card_update(title="New title")["proposal"]])
    store(db, "rejected-content",
          [card_update(content="New body")["proposal"]])

    incoming = card_update(title="New title", content="New body")
    result = review_policy(db, [incoming], supersedes("rejected-title", "New page"))

    assert result["reason"] == INVALID_SUPERSEDES
    assert result["detail"] == "other_rejected_proposals_affected"
    assert result["proposal_ids"] == ["rejected-content", "rejected-title"]
    assert review_policy(db, [incoming])["reason"] == DUPLICATE_CHANGE


def test_approved_same_scope_is_already_applied_even_under_a_new_operation(db):
    approved = card_update(title="New title")
    store(db, "approved-1", [approved["proposal"]], status="approved")

    same = card_update(title="new   TITLE")
    assert review_policy(db, [same])["reason"] == ALREADY_APPLIED
    assert review_policy(db, [same])["proposal_id"] == "approved-1"

    assert review_policy(db, [card_update(title="New title",
                                          expected_version=4)]) is None


def test_no_effect_update_reports_no_effect_or_already_applied(db):
    no_op = card_update(title="Old title")
    assert review_policy(db, [no_op])["reason"] == NO_EFFECT

    store(db, "approved-no-op", [no_op["proposal"]], status="approved")
    assert review_policy(db, [no_op])["reason"] == ALREADY_APPLIED
    assert review_policy(db, [card_update(title="Old title",
                                          expected_version=5)])["reason"] == NO_EFFECT


def test_approved_creation_with_the_same_content_is_already_applied(db):
    created = {
        "schema_version": 1, "card_id": None, "version": 1,
        "title": "Calibrated optics", "content": "Measured daily.",
        "aliases": [], "recall_terms": [], "relations": [],
        "sources": [source()], "known": True, "origin": "keeper",
        "status": "active",
    }
    approved = entry(change(action="create", identity=None, expected_version=None,
                            before=None, after=created, sources=[source()]))
    store(db, "approved-create", [approved["proposal"]], status="approved")

    same = entry(change(action="create", identity=None, expected_version=None,
                        before=None,
                        after=dict(created, title="calibrated   OPTICS",
                                   sources=[]),
                        sources=[source(material_id="mat-9")]))
    assert review_policy(db, [same])["reason"] == ALREADY_APPLIED

    different = entry(change(action="create", identity=None, expected_version=None,
                             before=None, after=dict(created, content="Other."),
                             sources=[source()]))
    assert review_policy(db, [different]) is None


def test_rejected_creation_is_deduplicated_by_normalized_content(db):
    created = {
        "schema_version": 1, "card_id": None, "version": 1,
        "title": "Calibrated optics", "content": "Measured daily.",
        "aliases": [], "recall_terms": [], "relations": [],
        "sources": [source()], "known": True, "origin": "keeper",
        "status": "active",
    }
    store(db, "rejected-create", [entry(change(
        action="create", identity=None, expected_version=None,
        before=None, after=created, sources=[source()]))["proposal"]])

    same = entry(change(action="create", identity=None, expected_version=None,
                        before=None,
                        after=dict(created, title="CALIBRATED  OPTICS"),
                        sources=[source(material_id="mat-9")]))
    assert review_policy(db, [same])["reason"] == DUPLICATE_CHANGE

    different = entry(change(action="create", identity=None, expected_version=None,
                             before=None, after=dict(created, content="Other."),
                             sources=[source()]))
    assert review_policy(db, [different]) is None


def test_policy_only_reads_the_stored_proposals(db):
    rejected = card_update(title="New title")["proposal"]
    store(db, "rejected-1", [rejected])
    before = db.execute("SELECT proposal_id,result FROM keeper_proposals").fetchall()

    review_policy(db, [card_update(title="New title")])

    after = db.execute("SELECT proposal_id,result FROM keeper_proposals").fetchall()
    assert after == before


def test_missing_proposal_table_means_no_history():
    connection = sqlite3.connect(":memory:")
    try:
        assert review_policy(connection, [card_update()]) is None
    finally:
        connection.close()
