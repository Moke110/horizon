"""Public K8 tests: revise a Keeper proposal after real owner drift.

The seam under test is ``KeeperService.decide``, ``KeeperService.revise``,
``KeeperService.queued_revisions`` and ``KeeperService.review_revision``.
An otherwise valid approval whose owner pins moved no longer writes knowledge:
it atomically records a durable revision job and leaves the displayed proposal
unchanged while Keeper rebuilds only that group against the latest snapshot.
Explicit Explorer choices are bound to the displayed revision at enqueue; a
revising job cannot be rewritten underneath an active worker.
"""

import json
import uuid

import pytest

from hostrun import keeper_revisions
from hostrun.agent_service import AgentService, CommandError
from hostrun.frontier import FrontierService
from hostrun.keeper import KeeperPrincipal, KeeperService
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_themes import KnowledgeThemeService
from hostrun.managed_database import database_connection
from tests.test_keeper import TOKEN, Owner

EXPLORER = KeeperPrincipal("explorer", False)


def pending_update(owner, *, title="Proposed title", operation="revision-request",
                   fields=None):
    material = owner.material(operation="revision-material")
    card = owner.create_card("Original title", material, operation="revision-card")
    change = {
        "kind": "card", "action": "update", "id": card["card_id"],
        "expected_version": card["version"],
        "fields": fields or {"title": title},
        "reason": "Use the later title.", "sources": [owner.source(material)],
    }
    receipt = owner.request([{"changes": [change]}], operation=operation)
    result = owner.reviewed(receipt)
    assert result["status"] == "pending"
    return card, material, result["proposal_ids"][0]


def linked_updates(owner, *, operation):
    """One proposal with two linked changes so omission can be partial."""
    material = owner.material(operation=operation + "-material")
    source = owner.source(material)
    card = owner.create_card("Related card", material,
                             operation=operation + "-card")
    theme = owner.themes.create({
        "schema_version": 1, "operation_id": operation + "-theme",
        "judgement": "Original judgement.",
        "related_cards": [{"card_id": card["card_id"],
                           "version": card["version"]}],
    }, token=TOKEN)
    theme_change = {
        "kind": "theme", "action": "update", "id": theme["theme_id"],
        "expected_version": theme["version"],
        "fields": {"judgement": "Proposed judgement.",
                   "related_cards": [{"card_id": card["card_id"],
                                      "version": card["version"]}]},
        "reason": "Refine.", "sources": [source],
    }
    card_change = {
        "kind": "card", "action": "update", "id": card["card_id"],
        "expected_version": card["version"],
        "fields": {"content": "Proposed content."},
        "reason": "Refine the card too.", "sources": [source],
    }
    receipt = owner.request([{"changes": [theme_change, card_change]}],
                            operation=operation + "-request")
    result = owner.reviewed(receipt)
    assert result["status"] == "pending"
    identifier = result["proposal_ids"][0]
    stored = owner.keeper.view(identifier, token=TOKEN)
    assert len(stored["changes"]) == 2
    return card, theme, identifier


def decision_payload(owner, identifier, *, operation, decision="approve",
                     revision=None, expected_versions=None):
    stored = owner.keeper.view(identifier, token=TOKEN)
    if expected_versions is None:
        expected_versions = [change["expected_version"]
                             for change in stored["changes"]]
    return {
        "schema_version": 1, "operation_id": operation,
        "proposal_id": identifier,
        "revision": stored["revision"] if revision is None else revision,
        "expected_versions": list(expected_versions),
        "decision": decision,
    }


def approve(owner, identifier, *, operation):
    return owner.keeper.decide(decision_payload(owner, identifier,
                                                operation=operation),
                               token=TOKEN, principal=EXPLORER)


def revise(owner, identifier, *, operation, decision="adjust",
           revision=None, feedback=None, omit_changes=None,
           keep_current_fields=None):
    stored = owner.keeper.view(identifier, token=TOKEN)
    payload = {
        "schema_version": 1, "operation_id": operation,
        "proposal_id": identifier,
        "revision": stored["revision"] if revision is None else revision,
        "decision": decision,
    }
    if feedback is not None:
        payload["feedback"] = feedback
    if omit_changes is not None:
        payload["omit_changes"] = omit_changes
    if keep_current_fields is not None:
        payload["keep_current_fields"] = keep_current_fields
    return owner.keeper.revise(payload, token=TOKEN, principal=EXPLORER)


def drifted(owner, *, title="Proposed title", operation="drift-request"):
    card, material, identifier = pending_update(
        owner, title=title, operation=operation)
    owner.cards.update({
        "schema_version": 1, "operation_id": operation + "-manual",
        "card_id": card["card_id"], "expected_version": card["version"],
        "title": "Changed by hand",
    }, token=TOKEN)
    result = approve(owner, identifier, operation=operation + "-decision")
    assert result["status"] == "revising"
    return card, material, identifier, result


def test_owner_version_drift_starts_one_revision_job_without_owner_writes(tmp_path):
    owner = Owner(tmp_path)
    card, _material, identifier, result = drifted(owner)

    assert result["decision"] == "approve"
    assert result["revision"] == 1
    assert result["applied"] == []
    assert result["status"] == "revising"
    assert result["review_id"]
    assert owner.keeper.queued_revisions(token=TOKEN) == [result["review_id"]]

    stored = owner.keeper.view(identifier, token=TOKEN)
    assert stored["status"] == "revising"
    assert stored["revision"] == 1
    assert stored["decision"] == result
    assert stored["changes"][0]["before"]["title"] == "Original title"
    notes = stored["revision_notes"]
    assert notes["previous_revision"] == 1
    assert notes["previous_changes"][0]["before"]["title"] == "Original title"
    assert notes["reason"] == "owner_drift"
    assert notes["review_id"] == result["review_id"]
    assert notes["child_id"] == "keeper-child"
    assert notes["conflicts"] == []
    assert "feedback" not in notes
    current = owner.cards.view(card["card_id"], token=TOKEN)
    assert current["title"] == "Changed by hand"
    assert current["version"] == card["version"] + 1
    assert owner.cards.view(card["card_id"], token=TOKEN) == current

    # The exact operation replays the original revising receipt. A stale
    # approval is a safe revising receipt, never an owner write.
    payload = decision_payload(owner, identifier, operation="drift-request-decision")
    assert owner.keeper.decide(payload, token=TOKEN,
                               principal=EXPLORER) == result
    with pytest.raises(CommandError) as conflict:
        owner.keeper.decide({**payload, "decision": "reject"}, token=TOKEN,
                            principal=EXPLORER)
    assert conflict.value.code == "operation_conflict"
    stale = owner.keeper.decide({**payload, "operation_id": "second-decision",
                                 "revision": 2}, token=TOKEN,
                                principal=EXPLORER)
    assert stale["status"] == "revising"
    assert stale["reason"] == "version_conflict"
    assert stale["review_id"] == result["review_id"]
    with pytest.raises(CommandError) as pins:
        owner.keeper.decide({**payload, "operation_id": "third-decision",
                             "expected_versions": [99]}, token=TOKEN,
                            principal=EXPLORER)
    assert pins.value.code == "version_conflict"

    events = owner.keeper.audit(identifier, token=TOKEN)
    assert [event["type"] for event in events] == [
        "keeper.revision_requested", "keeper.decision_stale"]
    assert events[0]["review_id"] == result["review_id"]
    assert events[0]["actor"] == "explorer"
    assert events[1]["reason"] == "version_conflict"


def test_adjust_requires_bounded_feedback_and_cancel_is_terminal(tmp_path):
    owner = Owner(tmp_path)
    card, _material, identifier = pending_update(owner, operation="cancel-request")

    with pytest.raises(CommandError) as empty:
        revise(owner, identifier, operation="adjust-empty", feedback="  ")
    assert empty.value.code == "invalid_command"
    with pytest.raises(CommandError) as huge:
        revise(owner, identifier, operation="adjust-huge", feedback="x" * 501)
    assert huge.value.code == "invalid_command"
    assert owner.keeper.view(identifier, token=TOKEN)["status"] == "pending"

    # Adjust is an explicit pre-dispatch action and creates the one job that
    # owns this revision; it must not be rewritten while that job is revising.
    adjusted = revise(owner, identifier, operation="adjust-1",
                      feedback="Keep the manual title and adjust the proposal.")
    assert adjusted["status"] == "revising"
    assert adjusted["review_id"]
    assert adjusted["applied"] == []
    review_id = adjusted["review_id"]
    assert owner.keeper.queued_revisions(token=TOKEN) == [review_id]
    stored = owner.keeper.view(identifier, token=TOKEN)
    assert stored["status"] == "revising"
    assert stored["revision_notes"]["feedback"] == \
        "Keep the manual title and adjust the proposal."
    assert stored["revision_notes"]["reason"] == "explorer_adjustment"
    with pytest.raises(CommandError) as busy:
        revise(owner, identifier, operation="adjust-2",
               feedback="Rewrite the queued job.")
    assert busy.value.code == "revision_in_progress"

    cancelled = revise(owner, identifier, operation="cancel-1",
                       decision="cancel", feedback="Stop this proposal.")
    assert cancelled["status"] == "cancelled"
    assert cancelled["applied"] == []
    assert cancelled["review_id"] == review_id
    stored = owner.keeper.view(identifier, token=TOKEN)
    assert stored["status"] == "cancelled"
    assert stored["decision"] == cancelled
    assert owner.cards.view(card["card_id"], token=TOKEN)["title"] == "Original title"
    assert owner.keeper.queued_revisions(token=TOKEN) == []
    assert revise(owner, identifier, operation="cancel-1",
                  decision="cancel", feedback="Stop this proposal.") == cancelled

    # A cancelled job can never be resurrected by a late completion.
    assert owner.keeper.review_revision(
        review_id, token=TOKEN,
        dispatch=lambda context: pytest.fail("cancelled work must not dispatch")
    ) == cancelled
    with pytest.raises(CommandError) as terminal:
        revise(owner, identifier, operation="cancel-2",
               decision="cancel", feedback="Again.")
    assert terminal.value.code == "already_decided"
    with pytest.raises(CommandError) as old:
        revise(owner, identifier, operation="old-adjust", revision=2,
               feedback="Old revision.")
    assert old.value.code == "version_conflict"
    with pytest.raises(PermissionError):
        owner.keeper.revise({
            "schema_version": 1, "operation_id": "unauthorized",
            "proposal_id": identifier, "revision": 1, "decision": "cancel",
        }, token=TOKEN, principal=KeeperPrincipal("explorer", True))
    assert owner.keeper.view(identifier, token=TOKEN)["status"] == "cancelled"


def test_review_revision_rebuilds_pending_revision_and_preserves_unrelated_edits(tmp_path):
    owner = Owner(tmp_path)
    card, _material, identifier = pending_update(
        owner, operation="rebuild-request")
    owner.cards.update({
        "schema_version": 1, "operation_id": "manual-content",
        "card_id": card["card_id"], "expected_version": card["version"],
        "content": "A separately maintained content body.",
    }, token=TOKEN)
    current = owner.cards.view(card["card_id"], token=TOKEN)

    adjusted = revise(owner, identifier, operation="rebuild-adjust",
                      feedback="Rebuild against the latest card, keep unrelated edits.")
    assert adjusted["status"] == "revising"
    review_id = adjusted["review_id"]
    contexts = []

    def dispatch(context):
        contexts.append(context)
        return {"status": "reviewed", "child_id": "revision-child"}

    completed = owner.keeper.review_revision(review_id, token=TOKEN,
                                             dispatch=dispatch)
    assert completed["status"] == "completed"
    assert completed["proposal_status"] == "pending"
    assert completed["proposal_id"] == identifier
    assert completed["revision"] == 1
    assert completed["proposal_revision"] == 2
    assert completed["child_id"] == "revision-child"
    assert owner.keeper.queued_revisions(token=TOKEN) == []

    context = contexts[0]
    assert context["review_id"] == review_id
    assert context["proposal_id"] == identifier
    assert context["revision"] == 2
    assert context["feedback"] == "Rebuild against the latest card, keep unrelated edits."
    assert context["task_id"] == owner.task["task_id"]
    assert context["report_id"] == owner.report["turn_id"]
    assert isinstance(context["provenance"], dict)
    assert context["provenance"]["proposal_id"] == identifier
    assert context["provenance"]["review_id"] == review_id
    assert context["request_id"] == keeper_revisions.request_id(
        review_id, context["fingerprint"])
    uuid.UUID(context["request_id"])

    revised = owner.keeper.view(identifier, token=TOKEN)
    assert revised["status"] == "pending"
    assert revised["revision"] == 2
    assert revised["review_id"] == review_id
    assert revised["feedback"] == context["feedback"]
    notes = revised["revision_notes"]
    assert notes["previous_revision"] == 1
    assert notes["previous_changes"][0]["before"]["title"] == "Original title"
    assert notes["reason"] == "explorer_adjustment"
    assert notes["review_id"] == review_id
    assert notes["child_id"] == "revision-child"
    assert notes["conflicts"] == []
    change = revised["changes"][0]
    assert change["before"]["title"] == "Original title"
    assert change["before"]["content"] == "A separately maintained content body."
    assert change["after"]["title"] == "Proposed title"
    assert change["after"]["content"] == "A separately maintained content body."

    # Neither the review nor the rebuild wrote knowledge.
    assert owner.cards.view(card["card_id"], token=TOKEN) == current
    events = owner.keeper.audit(identifier, token=TOKEN)
    assert [event["type"] for event in events] == [
        "keeper.revision_feedback", "keeper.revision_completed"]

    # Repeating the review returns the identical durable receipt without
    # dispatching the Keeper child again.
    assert owner.keeper.review_revision(
        review_id, token=TOKEN,
        dispatch=lambda context: pytest.fail("completed work re-dispatched")
    ) == completed

    # The freshly revised revision remains explicitly confirmable.
    decided = approve(owner, identifier, operation="rebuild-confirm")
    assert decided["status"] == "approved"
    final = owner.cards.view(card["card_id"], token=TOKEN)
    assert final["title"] == "Proposed title"
    assert final["content"] == "A separately maintained content body."


def test_manual_same_field_feedback_can_keep_current_and_continue_other_fields(tmp_path):
    owner = Owner(tmp_path)
    card, _material, identifier = pending_update(
        owner, operation="same-field-request",
        fields={"title": "Proposed title", "content": "Proposed content."})

    adjusted = revise(
        owner, identifier, operation="same-field-adjust",
        feedback="The owner already has the requested title; keep it and apply the body.",
        keep_current_fields=[{"change_index": 0, "fields": ["title"]}])
    assert adjusted["status"] == "revising"

    # The manual same-field edit lands after the choices were bound to the
    # displayed revision but before the worker rebuilds.
    owner.cards.update({
        "schema_version": 1, "operation_id": "manual-title",
        "card_id": card["card_id"], "expected_version": card["version"],
        "title": "Proposed title",
    }, token=TOKEN)
    current = owner.cards.view(card["card_id"], token=TOKEN)

    completed = owner.keeper.review_revision(
        adjusted["review_id"], token=TOKEN,
        dispatch=lambda context: {"status": "reviewed", "child_id": "same-child"})
    assert completed["status"] == "completed", (completed, completed.get("reason"))

    revised = owner.keeper.view(identifier, token=TOKEN)
    assert revised["status"] == "pending"
    change = revised["changes"][0]
    assert change["before"]["title"] == "Proposed title"
    assert change["after"]["title"] == "Proposed title"
    assert change["after"]["content"] == "Proposed content."
    conflict = {
        "kind": "card", "id": card["card_id"], "field": "title",
        "latest": "Proposed title", "proposed": "Proposed title",
        "already_matches": True,
    }
    assert revised["conflicts"] == [conflict]
    assert revised["revision_notes"]["conflicts"] == [conflict]
    assert revised["revision_notes"]["feedback"] == \
        "The owner already has the requested title; keep it and apply the body."
    assert owner.cards.view(card["card_id"], token=TOKEN) == current


def test_omit_changes_is_bound_to_the_displayed_revision(tmp_path):
    owner = Owner(tmp_path)
    card, theme, identifier = linked_updates(owner, operation="omit")

    with pytest.raises(CommandError) as all_changes:
        revise(owner, identifier, operation="omit-all",
               feedback="Drop everything.", omit_changes=[0, 1])
    assert all_changes.value.code == "invalid_revision"
    with pytest.raises(CommandError) as outside:
        revise(owner, identifier, operation="omit-outside",
               feedback="Drop the missing one.", omit_changes=[4])
    assert outside.value.code == "invalid_revision"
    with pytest.raises(CommandError) as unordered:
        revise(owner, identifier, operation="omit-unordered",
               feedback="Drop it.", omit_changes=[1, 0])
    assert unordered.value.code == "invalid_command"

    adjusted = revise(owner, identifier, operation="omit-theme",
                      feedback="Only apply the card body; leave the theme as it is.",
                      omit_changes=[0])
    completed = owner.keeper.review_revision(
        adjusted["review_id"], token=TOKEN,
        dispatch=lambda context: {"status": "reviewed", "child_id": "omit-child"})
    assert completed["status"] == "completed", (completed, completed.get("reason"))

    revised = owner.keeper.view(identifier, token=TOKEN)
    assert revised["status"] == "pending"
    assert len(revised["changes"]) == 1
    assert revised["changes"][0]["kind"] == "card"
    assert revised["changes"][0]["after"]["content"] == "Proposed content."
    assert owner.themes.view(theme["theme_id"], token=TOKEN)["judgement"] == \
        "Original judgement."
    assert owner.cards.view(card["card_id"], token=TOKEN)["content"] == \
        "A manually recorded concept."


def test_keep_current_rejects_fields_the_update_never_proposed(tmp_path):
    owner = Owner(tmp_path)
    _card, _material, identifier = pending_update(
        owner, operation="keep-invalid")

    with pytest.raises(CommandError) as unrelated:
        revise(owner, identifier, operation="keep-unrelated",
               feedback="Keep an untouched field.",
               keep_current_fields=[{"change_index": 0, "fields": ["aliases"]}])
    assert unrelated.value.code == "invalid_revision"
    with pytest.raises(CommandError) as shape:
        revise(owner, identifier, operation="keep-shape",
               feedback="Bad shape.",
               keep_current_fields=[{"change_index": 0}])
    assert shape.value.code == "invalid_command"
    with pytest.raises(CommandError) as too_many:
        revise(owner, identifier, operation="keep-many",
               feedback="Too many.",
               keep_current_fields=[{"change_index": index, "fields": ["title"]}
                                    for index in range(17)])
    assert too_many.value.code == "invalid_command"
    with pytest.raises(CommandError) as bounded:
        revise(owner, identifier, operation="keep-bound",
               feedback="Too many indexes.",
               omit_changes=list(range(17)))
    assert bounded.value.code == "invalid_command"
    assert owner.keeper.view(identifier, token=TOKEN)["status"] == "pending"


def test_keeping_every_effect_current_returns_no_effect_then_retries(tmp_path):
    owner = Owner(tmp_path)
    card, _material, identifier = pending_update(
        owner, operation="no-effect")

    adjusted = revise(
        owner, identifier, operation="no-effect-adjust",
        feedback="The owner title is already current; do not write it.",
        keep_current_fields=[{"change_index": 0, "fields": ["title"]}])
    returned = owner.keeper.review_revision(
        adjusted["review_id"], token=TOKEN,
        dispatch=lambda context: {"status": "reviewed", "child_id": "no-effect-child"})
    assert returned["status"] == "returned"
    assert returned["reason"] == "no_effect"
    assert returned["proposal_status"] == "returned"
    stored = owner.keeper.view(identifier, token=TOKEN)
    assert stored["status"] == "returned"
    assert stored["revision"] == 1
    assert stored["reason"] == "no_effect"
    assert stored["review_id"] == adjusted["review_id"]
    assert stored["feedback"] == "The owner title is already current; do not write it."
    assert stored["revision_notes"]["reason"] == "no_effect"
    assert owner.cards.view(card["card_id"], token=TOKEN)["title"] == "Original title"

    # Adjusting a returned proposal is a new job; the previous job stays
    # returned for audit/readback.
    retried = revise(owner, identifier, operation="no-effect-retry",
                     feedback="Re-review the original proposal.")
    assert retried["status"] == "revising"
    assert retried["review_id"] != adjusted["review_id"]
    assert owner.keeper.queued_revisions(token=TOKEN) == [retried["review_id"]]
    completed = owner.keeper.review_revision(
        retried["review_id"], token=TOKEN,
        dispatch=lambda context: {"status": "reviewed", "child_id": "retry-child"})
    assert completed["status"] == "completed"
    revised = owner.keeper.view(identifier, token=TOKEN)
    assert revised["status"] == "pending"
    assert revised["revision"] == 2
    assert revised["changes"][0]["after"]["title"] == "Proposed title"


def test_failed_worker_persists_returned_proposal_and_cancel_reads_back(tmp_path):
    owner = Owner(tmp_path)
    card, _material, identifier, result = drifted(
        owner, operation="failure-request")

    def broken(context):
        raise RuntimeError("the review process lost its worker")

    failed = owner.keeper.review_revision(result["review_id"], token=TOKEN,
                                          dispatch=broken)
    assert failed["status"] == "failed"
    assert failed["proposal_status"] == "returned"
    stored = owner.keeper.view(identifier, token=TOKEN)
    assert stored["status"] == "returned"
    assert stored["reason"] == "keeper_execution_failed"
    assert stored["review_id"] == result["review_id"]
    assert stored["revision_notes"]["reason"] == "keeper_execution_failed"
    assert owner.keeper.queued_revisions(token=TOKEN) == []
    assert owner.cards.view(card["card_id"], token=TOKEN)["title"] == "Changed by hand"

    cancelled = revise(owner, identifier, operation="cancel-returned",
                       decision="cancel")
    assert cancelled["status"] == "cancelled"
    assert cancelled["review_id"] == result["review_id"]
    stored = owner.keeper.view(identifier, token=TOKEN)
    assert stored["status"] == "cancelled"
    assert stored["decision"] == cancelled
    assert owner.keeper.review_revision(
        result["review_id"], token=TOKEN,
        dispatch=lambda context: pytest.fail("cancelled work must not dispatch")
    ) == cancelled


def test_snapshot_change_during_worker_never_issues_a_stale_revision(tmp_path):
    owner = Owner(tmp_path)
    card, _material, identifier = pending_update(
        owner, operation="toctou-request")
    adjusted = revise(owner, identifier, operation="toctou-adjust",
                      feedback="Rebuild after the worker finishes.")
    review_id = adjusted["review_id"]
    contexts = []

    def racing_dispatch(context):
        contexts.append(context)
        owner.cards.update({
            "schema_version": 1, "operation_id": "during-worker",
            "card_id": card["card_id"],
            "expected_version": owner.cards.view(card["card_id"], token=TOKEN)["version"],
            "content": "Written while the Keeper child was running.",
        }, token=TOKEN)
        return {"status": "reviewed", "child_id": "racing-child"}

    first = owner.keeper.review_revision(review_id, token=TOKEN,
                                         dispatch=racing_dispatch)
    assert first["status"] == "queued"
    assert first["reason"] == "snapshot_changed"
    assert owner.keeper.view(identifier, token=TOKEN)["revision"] == 1
    assert owner.keeper.queued_revisions(token=TOKEN) == [review_id]
    assert owner.cards.view(card["card_id"], token=TOKEN)["content"] == \
        "Written while the Keeper child was running."

    second = owner.keeper.review_revision(
        review_id, token=TOKEN,
        dispatch=lambda context: contexts.append(context) or
        {"status": "reviewed", "child_id": "fresh-child"})
    assert second["status"] == "completed"
    revised = owner.keeper.view(identifier, token=TOKEN)
    assert revised["revision"] == 2 and revised["status"] == "pending"
    change = revised["changes"][0]
    assert change["before"]["title"] == "Original title"
    assert change["after"]["title"] == "Proposed title"
    assert change["before"]["content"] == "Written while the Keeper child was running."
    assert change["after"]["content"] == "Written while the Keeper child was running."
    assert len(contexts) == 2
    # A changed snapshot gets a new child identity; the job identity remains.
    assert contexts[0]["review_id"] == contexts[1]["review_id"] == review_id
    assert contexts[0]["request_id"] != contexts[1]["request_id"]
    assert contexts[1]["request_id"] == keeper_revisions.request_id(
        review_id, contexts[1]["fingerprint"])


def test_process_loss_restart_keeps_fingerprint_and_issues_a_new_claim(tmp_path):
    owner = Owner(tmp_path)
    _card, _material, identifier = pending_update(
        owner, operation="restart-request")
    adjusted = revise(owner, identifier, operation="restart-adjust",
                      feedback="Run against the same snapshot after restart.")
    review_id = adjusted["review_id"]
    seen = []

    def read_claim():
        with database_connection(owner.home) as db:
            row = db.execute(
                "SELECT request FROM keeper_revision_jobs WHERE review_id=?",
                (review_id,)).fetchone()
            return json.loads(row[0]).get("claim")

    def interrupted(context):
        seen.append(context)
        seen.append(("claim", read_claim()))
        raise SystemExit("worker process disappeared")

    with pytest.raises(SystemExit):
        owner.keeper.review_revision(review_id, token=TOKEN,
                                     dispatch=interrupted)
    first_context = seen[0]
    first_claim = seen[1][1]
    assert first_claim

    # Startup recovery preserves the request fingerprint for a true process
    # loss, so the same snapshot keeps the same stable child identity.
    assert owner.keeper.queued_revisions(token=TOKEN) == [review_id]

    def resumed(context):
        seen.append(context)
        seen.append(("claim", read_claim()))
        return {"status": "reviewed", "child_id": "resumed-child"}

    completed = owner.keeper.review_revision(review_id, token=TOKEN,
                                             dispatch=resumed)
    assert completed["status"] == "completed"
    second_context = seen[2]
    second_claim = seen[3][1]
    assert second_claim and second_claim != first_claim
    assert second_context["request_id"] == first_context["request_id"]
    assert second_context["fingerprint"] == first_context["fingerprint"]
    assert second_context["review_id"] == review_id
    revised = owner.keeper.view(identifier, token=TOKEN)
    assert revised["revision"] == 2 and revised["status"] == "pending"
    assert revised["revision_notes"]["previous_revision"] == 1
    assert revised["revision_notes"]["previous_revision"] is not None


def test_repeated_snapshot_races_never_publish_and_retry_from_returned(tmp_path):
    owner = Owner(tmp_path)
    card, _material, identifier = pending_update(
        owner, operation="repeat-request")
    adjusted = revise(owner, identifier, operation="repeat-adjust",
                      feedback="Keep retrying until the snapshot settles.")
    review_id = adjusted["review_id"]

    def race(context):
        current = owner.cards.view(card["card_id"], token=TOKEN)
        owner.cards.update({
            "schema_version": 1, "operation_id": f"race-{current['version']}",
            "card_id": card["card_id"], "expected_version": current["version"],
            "content": f"Manual content v{current['version']}.",
        }, token=TOKEN)
        return {"status": "reviewed", "child_id": f"race-child-{current['version']}"}

    first = owner.keeper.review_revision(review_id, token=TOKEN, dispatch=race)
    assert first["status"] == "queued"
    second = owner.keeper.review_revision(review_id, token=TOKEN, dispatch=race)
    assert second["status"] == "returned"
    assert second["reason"] == "snapshot_changed"
    assert second["proposal_status"] == "returned"
    stored = owner.keeper.view(identifier, token=TOKEN)
    assert stored["status"] == "returned"
    assert stored["revision"] == 1
    assert stored["reason"] == "snapshot_changed"

    retried = revise(owner, identifier, operation="repeat-retry",
                     feedback="Retry the rebuilt proposal.")
    assert retried["review_id"] != review_id
    completed = owner.keeper.review_revision(
        retried["review_id"], token=TOKEN,
        dispatch=lambda context: {"status": "reviewed", "child_id": "settled"})
    assert completed["status"] == "completed"
    revised = owner.keeper.view(identifier, token=TOKEN)
    assert revised["revision"] == 2 and revised["status"] == "pending"
    assert revised["changes"][0]["before"]["content"] == \
        owner.cards.view(card["card_id"], token=TOKEN)["content"]


def test_revision_revalidates_original_report_and_cited_relations(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material(operation="relation-material")
    source = owner.source(material)
    card = owner.create_card("Related card", material, operation="related-card")
    theme = owner.themes.create({
        "schema_version": 1, "operation_id": "relation-theme",
        "judgement": "Original judgement.",
    }, token=TOKEN)
    change = {
        "kind": "theme", "action": "update", "id": theme["theme_id"],
        "expected_version": theme["version"],
        "fields": {"judgement": "Proposed judgement.",
                   "related_cards": [{"card_id": card["card_id"],
                                      "version": card["version"]}]},
        "reason": "Refine.", "sources": [source],
    }
    receipt = owner.request([{"changes": [change]}], operation="relation-request")
    identifier = owner.reviewed(receipt)["proposal_ids"][0]
    owner.themes.update({
        "schema_version": 1, "operation_id": "relation-theme-drift",
        "theme_id": theme["theme_id"], "expected_version": theme["version"],
        "judgement": "A manual judgement.",
    }, token=TOKEN)
    owner.cards.update({
        "schema_version": 1, "operation_id": "relation-drift",
        "card_id": card["card_id"], "expected_version": card["version"],
        "content": "The related card moved independently.",
    }, token=TOKEN)
    result = approve(owner, identifier, operation="relation-decision")
    assert result["status"] == "revising"

    # A newer task turn is allowed: the original completed report stays pinned.
    owner.agent.begin_turn({
        "schema_version": 1, "operation_id": "newer-turn",
        "task_id": owner.task["task_id"], "expected_version": owner.task["version"],
        "text": "A later independent plan.",
    }, token=TOKEN)

    returned = owner.keeper.review_revision(
        result["review_id"], token=TOKEN,
        dispatch=lambda context: {"status": "reviewed", "child_id": "stale-relation"})
    assert returned["status"] == "returned"
    assert returned["reason"] == "version_conflict"
    stored = owner.keeper.view(identifier, token=TOKEN)
    assert stored["status"] == "returned"
    assert stored["reason"] == "version_conflict"
    assert stored["revision"] == 1
    assert stored["review_id"] == result["review_id"]
    assert stored["revision_notes"]["previous_revision"] == 1
    assert stored["revision_notes"]["previous_changes"][0]["kind"] == "theme"
    assert owner.themes.view(theme["theme_id"], token=TOKEN)["judgement"] == \
        "A manual judgement."

    assert stored["changes"][0]["expected_version"] == theme["version"]
    assert stored["changes"][0]["after"]["related_cards"] == [
        {"card_id": card["card_id"], "version": card["version"]},
    ]

    # An explicit decision to leave the relation alone reviews the remaining
    # judgement change with the same original report.
    retried = revise(owner, identifier, operation="relation-retry",
                     feedback="Leave the current relationships intact.",
                     keep_current_fields=[{"change_index": 0, "fields": ["related_cards"]}])
    assert retried["status"] == "revising"
    assert retried["review_id"] != result["review_id"]
    revised = owner.keeper.review_revision(
        retried["review_id"], token=TOKEN,
        dispatch=lambda context: {"status": "reviewed", "child_id": "judgement-review"})
    assert revised["status"] == "completed"
    assert owner.keeper.queued_revisions(token=TOKEN) == []
    stored = owner.keeper.view(identifier, token=TOKEN)
    assert stored["status"] == "pending" and stored["revision"] == 2
    assert stored["changes"][0]["after"]["related_cards"] == []
    assert stored["changes"][0]["after"]["judgement"] == "Proposed judgement."
