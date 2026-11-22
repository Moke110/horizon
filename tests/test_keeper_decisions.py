"""Public K7 tests: one atomic Explorer decision over a pending proposal.

The seam under test is ``KeeperService.decide`` and ``KeeperService.audit``.
A trusted Explorer approves or rejects a whole pending group. Approval joins
the owner writes, the decision receipt and the audit event in one transaction;
rejection records the decision without touching knowledge.
"""

import json
import threading

import pytest

from hostrun.agent_service import AgentService, CommandError
from hostrun.frontier import FrontierService
from hostrun.keeper import KeeperPrincipal, KeeperService
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_themes import KnowledgeThemeService
from hostrun.managed_database import database_connection
from tests.test_keeper import TOKEN, Owner

EXPLORER = KeeperPrincipal("explorer", False)


def proposal_id(owner, groups, *, operation):
    receipt = owner.request(groups, operation=operation)
    result = owner.reviewed(receipt)
    assert result["status"] == "pending"
    assert len(result["proposal_ids"]) == 1
    return result["proposal_ids"][0]


def command(owner, identifier, *, operation, decision="approve",
            expected_versions=None, revision=None, reason=None):
    stored = owner.keeper.view(identifier, token=TOKEN)
    if expected_versions is None:
        expected_versions = [change["expected_version"]
                             for change in stored["changes"]]
    payload = {
        "schema_version": 1, "operation_id": operation,
        "proposal_id": identifier,
        "revision": stored["revision"] if revision is None else revision,
        "expected_versions": list(expected_versions),
        "decision": decision,
    }
    if reason is not None:
        payload["reason"] = reason
    return payload


def decide(owner, payload):
    return owner.keeper.decide(payload, token=TOKEN, principal=EXPLORER)


def reopened(tmp_path, owner):
    return KeeperService(
        AgentService(tmp_path, token=TOKEN), owner.materials,
        KnowledgeCardService(tmp_path, token=TOKEN),
        KnowledgeThemeService(tmp_path, token=TOKEN),
        FrontierService(tmp_path, token=TOKEN), token=TOKEN)


def test_approve_commits_card_and_returns_replayable_audited_receipt(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    identifier = proposal_id(
        owner, [{"changes": [owner.card_change([owner.source(material)])]}],
        operation="create-request")
    payload = command(owner, identifier, operation="decision-1")

    with pytest.raises(PermissionError):
        owner.keeper.decide(payload, token=TOKEN)
    with pytest.raises(PermissionError):
        owner.keeper.decide(payload, token=TOKEN, principal=KeeperPrincipal("explorer", True))
    assert owner.cards.list(token=TOKEN) == []
    assert owner.keeper.audit(identifier, token=TOKEN) == []

    result = decide(owner, payload)

    assert set(result) == {"operation_id", "decision", "proposal_id", "revision",
                           "status", "applied", "decided_at"}
    assert result["operation_id"] == "decision-1"
    assert result["proposal_id"] == identifier
    assert result["revision"] == 1
    assert result["decision"] == "approve"
    assert result["status"] == "approved"
    assert type(result["decided_at"]) is int
    assert len(result["applied"]) == 1
    applied = result["applied"][0]
    assert applied["kind"] == "card"
    card = owner.cards.view(applied["id"], token=TOKEN)
    assert applied["version"] == card["version"] == 1
    assert card["origin"] == "keeper"
    assert card["status"] == "active"
    assert card["sources"][0]["material_version"] == material["version"]
    assert card["sources"][0]["content_hash"] == material["snapshot"]["content_hash"]

    terminal = owner.keeper.view(identifier, token=TOKEN)
    assert terminal["status"] == "approved"
    assert terminal["decision"] == result

    events = owner.keeper.audit(identifier, token=TOKEN)
    assert len(events) == 1
    assert events[0]["operation_id"] == "decision-1"
    assert events[0]["proposal_id"] == identifier
    assert events[0]["decision"] == "approve"
    assert events[0]["status"] == "approved"
    assert events[0]["actor"] == "explorer"
    assert events[0]["expected_versions"] == [None]
    assert events[0]["applied"] == result["applied"]

    # Exact replay across a fresh connection returns the receipt and writes nothing.
    other = reopened(tmp_path, owner)
    assert other.decide(payload, token=TOKEN, principal=EXPLORER) == result
    assert owner.cards.view(card["card_id"], token=TOKEN)["version"] == 1
    assert other.audit(identifier, token=TOKEN) == events

    with pytest.raises(CommandError) as conflict:
        other.decide({**payload, "decision": "reject"},
                     token=TOKEN, principal=EXPLORER)
    assert conflict.value.code == "operation_conflict"
    with pytest.raises(CommandError) as decided:
        other.decide({**payload, "operation_id": "decision-2"},
                     token=TOKEN, principal=EXPLORER)
    assert decided.value.code == "already_decided"
    assert [item["card_id"] for item in owner.cards.list(token=TOKEN)] == [card["card_id"]]


def test_approve_orders_themes_before_cards_but_reports_original_order(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    source = owner.source(material)
    card = owner.create_card("Original concept", material)
    theme = owner.themes.create({
        "schema_version": 1, "operation_id": "base-theme",
        "judgement": "The concept holds under calibration.",
        "related_cards": [{"card_id": card["card_id"],
                           "version": card["version"]}],
        "evidence": [],
    }, token=TOKEN)
    calls = []
    theme_apply = owner.themes.apply_in_transaction
    card_apply = owner.cards.apply_in_transaction
    owner.themes.apply_in_transaction = lambda *args, **kwargs: (
        calls.append("theme") or theme_apply(*args, **kwargs))
    owner.cards.apply_in_transaction = lambda *args, **kwargs: (
        calls.append("card") or card_apply(*args, **kwargs))
    identifier = proposal_id(owner, [{"changes": [
        {"kind": "card", "action": "update", "id": card["card_id"],
         "expected_version": card["version"],
         "fields": {"title": "Revised concept"}, "reason": "Clarify.",
         "sources": [source]},
        {"kind": "theme", "action": "update", "id": theme["theme_id"],
         "expected_version": theme["version"],
         "fields": {"judgement": "The calibrated concept holds."},
         "reason": "Refine.", "sources": [source]},
    ]}], operation="linked-request")

    result = decide(owner, command(owner, identifier, operation="linked-decision"))

    assert calls == ["theme", "card"]
    assert [item["kind"] for item in result["applied"]] == ["card", "theme"]
    assert result["applied"][0]["id"] == card["card_id"]
    assert result["applied"][1]["id"] == theme["theme_id"]
    assert owner.cards.view(card["card_id"], token=TOKEN)["title"] == "Revised concept"
    assert owner.themes.view(theme["theme_id"], token=TOKEN)["judgement"] == \
        "The calibrated concept holds."


def test_theme_approval_keeps_trusted_evidence_ids_and_omits_generated_ones(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    source = owner.source(material)
    create = {
        "kind": "theme", "action": "create",
        "fields": {"judgement": "The sensors are calibrated.",
                   "evidence": [{"stance": "supports", "reason": "The report says so.",
                                  "source": source}]},
        "reason": "Record the judgement.", "sources": [source],
    }
    identifier = proposal_id(owner, [{"changes": [create]}], operation="theme-create")
    created = decide(owner, command(owner, identifier, operation="theme-create-decision"))
    theme = owner.themes.view(created["applied"][0]["id"], token=TOKEN)
    assert theme["origin"] == "keeper"
    assert len(theme["evidence"]) == 1
    first_id = theme["evidence"][0]["evidence_id"]
    assert first_id

    later = owner.source(material, locator="p3")
    update = {
        "kind": "theme", "action": "update", "id": theme["theme_id"],
        "expected_version": theme["version"],
        "fields": {"evidence": [
            {"evidence_id": first_id, "stance": "supports",
             "reason": "The report still supports this.",
             "source": {"material_id": source["material_id"],
                        "locator": source["locator"],
                        "excerpt": source["excerpt"]}},
            {"stance": "opposes", "reason": "One sample is narrow.",
             "source": later},
        ]},
        "reason": "Add the opposing reading.", "sources": [later],
    }
    identifier = proposal_id(owner, [{"changes": [update]}], operation="theme-update")
    result = decide(owner, command(owner, identifier, operation="theme-update-decision"))
    updated = owner.themes.view(theme["theme_id"], token=TOKEN)
    assert result["applied"][0]["version"] == updated["version"] == theme["version"] + 1
    assert [entry["evidence_id"] for entry in updated["evidence"]][0] == first_id
    assert len({entry["evidence_id"] for entry in updated["evidence"]}) == 2
    assert updated["evidence"][1]["source"]["anchor"]["id"] == "p3"


def test_rejection_is_terminal_audited_and_never_writes_owner_data(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    identifier = proposal_id(
        owner, [{"changes": [owner.card_change([owner.source(material)])]}],
        operation="reject-request")
    payload = command(owner, identifier, operation="reject-decision",
                      decision="reject", reason="The evidence is not sufficient.")

    result = decide(owner, payload)

    assert result["status"] == "rejected"
    assert result["decision"] == "reject"
    assert result["applied"] == []
    assert result["reason"] == "The evidence is not sufficient."
    assert owner.cards.list(token=TOKEN) == []
    terminal = owner.keeper.view(identifier, token=TOKEN)
    assert terminal["status"] == "rejected"
    assert terminal["decision"] == result
    event = owner.keeper.audit(identifier, token=TOKEN)[0]
    assert event["status"] == "rejected" and event["reason"] == result["reason"]
    assert decide(owner, payload) == result
    with pytest.raises(CommandError) as decided:
        decide(owner, {**payload, "operation_id": "reject-decision-2"})
    assert decided.value.code == "already_decided"


def test_approval_rechecks_two_independently_pending_creations(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    groups = [{"changes": [owner.card_change([owner.source(material)])]}]
    first = proposal_id(owner, groups, operation="queued-first")
    second = proposal_id(owner, groups, operation="queued-second")
    assert decide(owner, command(owner, first, operation="approve-first"))["status"] == "approved"
    with pytest.raises(CommandError) as blocked:
        decide(owner, command(owner, second, operation="approve-second"))
    assert blocked.value.code == "already_applied"
    assert len(owner.cards.list(token=TOKEN)) == 1
    assert owner.keeper.audit(second, token=TOKEN) == []


def test_reconsideration_with_actual_new_evidence_and_an_independent_card(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    first_change = owner.card_change([owner.source(material)])
    first = proposal_id(owner, [{"changes": [first_change]}], operation="first-request")
    decide(owner, command(owner, first, operation="reject-first", decision="reject"))
    repeated = owner.request([{"changes": [first_change]}], operation="same-evidence")
    assert repeated["status"] == "returned" and repeated["reason"] == "duplicate_change"

    new_quote = "An independent study repeated the calibration every week."
    second_source = owner.material(new_quote, operation="new-material")
    followup = {**first_change, "sources": [owner.source(second_source, excerpt=new_quote)]}
    independent = owner.card_change([owner.source(material)], fields={
        "title": "Independent concept", "content": "Another supported observation."})
    second = owner.request([{"changes": [followup, independent]}], operation="second-request",
                           supersedes_proposal_id=first, new_evidence_reason="Independent replication.")
    reviewed = owner.reviewed(second)
    assert len(reviewed["proposal_ids"]) == 2
    reconsidered, independent_id = reviewed["proposal_ids"]
    assert decide(owner, command(owner, independent_id, operation="approve-independent"))["status"] == "approved"
    decide(owner, command(owner, reconsidered, operation="reject-second", decision="reject"))

    reused_old = owner.request([{"changes": [first_change]}], operation="reused-old",
                               supersedes_proposal_id=reconsidered, new_evidence_reason="Old excerpt again.")
    assert reused_old["status"] == "returned" and reused_old["reason"] == "duplicate_change"
    third_quote = "A third laboratory measured a consistent instrument response."
    third_source = owner.material(third_quote, operation="third-material")
    third_change = {**first_change, "sources": [owner.source(third_source, excerpt=third_quote)]}
    third = owner.request([{"changes": [third_change]}], operation="third-request",
                          supersedes_proposal_id=reconsidered, new_evidence_reason="Third laboratory.")
    third_id = owner.reviewed(third)["proposal_ids"][0]
    assert decide(owner, command(owner, third_id, operation="approve-third"))["status"] == "approved"


def test_approval_validation_is_atomic_and_leaves_the_proposal_pending(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    source = owner.source(material)
    card = owner.create_card("Original concept", material)
    theme = owner.themes.create({
        "schema_version": 1, "operation_id": "atomic-theme",
        "judgement": "The concept holds under calibration.",
        "related_cards": [{"card_id": card["card_id"],
                           "version": card["version"]}],
    }, token=TOKEN)
    identifier = proposal_id(owner, [{"changes": [
        {"kind": "card", "action": "update", "id": card["card_id"],
         "expected_version": card["version"],
         "fields": {"title": "Proposed title"}, "reason": "Edit.",
         "sources": [source]},
        {"kind": "theme", "action": "update", "id": theme["theme_id"],
         "expected_version": theme["version"],
         "fields": {"judgement": "Proposed judgement."}, "reason": "Edit.",
         "sources": [source]},
    ]}], operation="atomic-request")
    stale = command(owner, identifier, operation="stale-decision")
    owner.cards.update({
        "schema_version": 1, "operation_id": "manual-update",
        "card_id": card["card_id"], "expected_version": card["version"],
        "title": "Changed by hand",
    }, token=TOKEN)

    with pytest.raises(CommandError) as rejected:
        decide(owner, stale)
    assert rejected.value.code == "version_conflict"
    assert owner.themes.view(theme["theme_id"], token=TOKEN)["version"] == theme["version"]
    assert owner.keeper.view(identifier, token=TOKEN)["status"] == "pending"
    assert owner.keeper.audit(identifier, token=TOKEN) == []

    # The stale revision stays pending and a distinct operation can still reject
    # it without any owner writes; it is never silently rebased.
    rejected = decide(owner, command(owner, identifier,
                                     operation="reject-after-stale",
                                     decision="reject",
                                     reason="Owner changed."))
    assert rejected["status"] == "rejected"
    assert owner.cards.view(card["card_id"], token=TOKEN)["title"] == "Changed by hand"
    assert owner.themes.view(theme["theme_id"], token=TOKEN)["judgement"] == \
        theme["judgement"]


def test_group_cannot_add_a_theme_link_to_a_card_it_archives(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    source = owner.source(material)
    card = owner.create_card("Referenced Card", material)
    theme = owner.themes.create({"schema_version": 1, "operation_id": "theme",
                                 "judgement": "Existing Theme"}, token=TOKEN)
    changes = [
        {"kind": "card", "action": "delete", "id": card["card_id"],
         "expected_version": 1, "fields": {}, "reason": "Archive outdated Card.", "sources": [source]},
        {"kind": "theme", "action": "update", "id": theme["theme_id"],
         "expected_version": 1, "fields": {"related_cards": [{"card_id": card["card_id"], "version": 1}]},
         "reason": "Link the relevant card.", "sources": [source]},
    ]
    identifier = proposal_id(owner, [{"changes": changes}], operation="unsafe-group")
    with pytest.raises(CommandError) as rejected:
        decide(owner, command(owner, identifier, operation="unsafe-approval"))
    assert rejected.value.code == "invalid_relation"
    assert owner.cards.view(card["card_id"], token=TOKEN)["status"] == "active"
    assert owner.themes.view(theme["theme_id"], token=TOKEN)["related_cards"] == []
    assert not owner.keeper.audit(identifier, token=TOKEN)


def test_approval_refuses_a_tampered_material_pin_without_any_write(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    identifier = proposal_id(
        owner, [{"changes": [owner.card_change([owner.source(material)])]}],
        operation="pin-request")
    with database_connection(tmp_path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT result FROM keeper_proposals WHERE proposal_id=?",
                         (identifier,)).fetchone()
        stored = json.loads(row[0])
        stored["changes"][0]["sources"][0]["anchor"] = {
            "id": "p999", "start": 0, "end": 1}
        db.execute("UPDATE keeper_proposals SET result=? WHERE proposal_id=?",
                   (json.dumps(stored), identifier))
        db.commit()

    with pytest.raises(CommandError) as rejected:
        decide(owner, command(owner, identifier, operation="pin-decision"))
    assert rejected.value.code == "invalid_evidence"
    assert owner.cards.list(token=TOKEN) == []
    assert owner.keeper.view(identifier, token=TOKEN)["status"] == "pending"
    assert owner.keeper.audit(identifier, token=TOKEN) == []


def test_delete_approvals_archive_cards_and_themes_and_terminate_frontiers(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    source = owner.source(material)
    card = owner.create_card("Archived concept", material)
    theme = owner.themes.create({
        "schema_version": 1, "operation_id": "delete-theme",
        "judgement": "An archived judgement.",
    }, token=TOKEN)
    frontier = owner.frontiers.create({
        "schema_version": 1, "operation_id": "delete-frontier",
        "question": "Which sensors remain stable?",
        "scope": {"text": "Calibration", "sources": ["fixture"]},
        "hard_constraints": {},
    }, token=TOKEN)
    targets = [
        ("card", card, "card_id"), ("theme", theme, "theme_id"),
        ("frontier", frontier, "frontier_id"),
    ]
    for kind, record, id_field in targets:
        identifier = proposal_id(owner, [{"changes": [{
            "kind": kind, "action": "delete", "id": record[id_field],
            "expected_version": record["version"], "fields": {},
            "reason": "No longer needed.", "sources": [source],
        }]}], operation=f"delete-{kind}")
        result = decide(owner, command(owner, identifier,
                                       operation=f"delete-{kind}-decision"))
        assert result["applied"] == [{
            "kind": kind, "id": record[id_field], "version": record["version"] + 1}]

    assert owner.cards.view(card["card_id"], token=TOKEN)["status"] == "archived"
    assert owner.themes.view(theme["theme_id"], token=TOKEN)["status"] == "archived"
    assert owner.frontiers.view(frontier["frontier_id"], token=TOKEN)["status"] == "deleted"


def test_concurrent_decisions_commit_exactly_one_terminal_outcome(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    identifier = proposal_id(
        owner, [{"changes": [owner.card_change([owner.source(material)])]}],
        operation="race-request")
    results = []
    errors = []

    def run(operation):
        try:
            results.append(decide(
                owner, command(owner, identifier, operation=operation)))
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    threads = [threading.Thread(target=run, args=(f"race-{index}",))
               for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], CommandError)
    assert errors[0].code == "already_decided"
    assert len(owner.cards.list(token=TOKEN)) == 1
    assert len(owner.keeper.audit(identifier, token=TOKEN)) == 1
    assert owner.keeper.view(identifier, token=TOKEN)["status"] == "approved"
    assert decide(owner, command(owner, identifier,
                                 operation=results[0]["operation_id"])) == results[0]
