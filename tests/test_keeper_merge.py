"""Public K9 tests: explicit merges, source supplements and counterevidence.

The seam under test is ``KeeperService.request`` / ``review`` / ``decide`` and
the existing Knowledge Card and Theme owner services. A merge is an explicit
card update carrying ``fields.merge_from`` plus the donor's archive change and
every required inbound redirect in the same atomic component. Preview is
read-only; approval rebuilds the survivor from both pinned active cards.
"""

import json

import pytest

from hostrun.agent_service import CommandError
from hostrun.keeper import KeeperPrincipal
from hostrun.keeper_runtime import KeeperRuntime
from hostrun.managed_database import database_connection
from tests.test_keeper import TOKEN, Owner

EXPLORER = KeeperPrincipal("explorer", False)
SURVIVOR_TEXT = "The survivor observes the calibration reference each morning."
DONOR_TEXT = "The donor log records a second independent calibration series."
SUPPORT_TEXT = "The first campaign reports a stable calibration."
OPPOSE_TEXT = "The second campaign contradicts the same calibration claim."


def source(owner, material, text):
    return {"material_id": material["material_id"],
            "material_version": material["version"],
            "locator": "p2", "excerpt": text}


def card(owner, *, operation, title, content, material=None, text=None,
         aliases=(), recall_terms=(), relations=(), questionable=False):
    command = {
        "schema_version": 1, "operation_id": operation,
        "title": title, "content": content,
        "aliases": list(aliases), "recall_terms": list(recall_terms),
        "relations": list(relations), "questionable": questionable,
    }
    if material is not None:
        command["sources"] = [{"material_id": material["material_id"],
                               "locator": "p2", "excerpt": text}]
    return owner.cards.create(command, token=TOKEN)


def merge_world(owner):
    survivor_material = owner.material(SURVIVOR_TEXT, operation="merge-survivor-material")
    donor_material = owner.material(DONOR_TEXT, operation="merge-donor-material")
    other_a = card(owner, operation="merge-other-a", title="Other A",
                   content="Another concept.", material=survivor_material,
                   text=SURVIVOR_TEXT)
    other_b = card(owner, operation="merge-other-b", title="Other B",
                   content="A third concept.", material=survivor_material,
                   text=SURVIVOR_TEXT)
    survivor = card(
        owner, operation="merge-survivor", title="Calibrated optics",
        content="Survivor body.", material=survivor_material,
        text=SURVIVOR_TEXT, aliases=["baseline"], recall_terms=["optics"],
        relations=[other_a["card_id"]])
    donor = card(
        owner, operation="merge-donor", title="Calibration field notes",
        content="Donor body.", material=donor_material, text=DONOR_TEXT,
        aliases=["field-notes"], recall_terms=["donor-recall"],
        relations=[other_b["card_id"], survivor["card_id"]], questionable=True)
    inbound = card(
        owner, operation="merge-inbound", title="Downstream usage",
        content="Uses the calibration notes.",
        relations=[donor["card_id"], other_a["card_id"]])
    theme = owner.themes.create({
        "schema_version": 1, "operation_id": "merge-theme",
        "judgement": "Calibration stays testable.",
        "related_cards": [
            {"card_id": donor["card_id"], "version": donor["version"]},
            {"card_id": other_a["card_id"], "version": other_a["version"]}],
    }, token=TOKEN)
    return {
        "survivor": survivor, "donor": donor, "inbound": inbound,
        "theme": theme, "other_a": other_a, "other_b": other_b,
        "survivor_material": survivor_material,
        "donor_material": donor_material,
    }


def redirect_changes(owner, world):
    survivor, donor = world["survivor"], world["donor"]
    other = world["other_a"]
    return [
        {"kind": "card", "action": "update",
         "id": world["inbound"]["card_id"],
         "expected_version": world["inbound"]["version"],
         "fields": {"relations": [survivor["card_id"], other["card_id"]]},
         "reason": "Redirect the inbound relation.",
         "sources": [source(owner, world["survivor_material"], SURVIVOR_TEXT)]},
        {"kind": "theme", "action": "update",
         "id": world["theme"]["theme_id"],
         "expected_version": world["theme"]["version"],
         "fields": {"related_cards": [
             {"card_id": survivor["card_id"], "version": survivor["version"]},
             {"card_id": other["card_id"], "version": other["version"]}]},
         "reason": "Redirect the theme relation.",
         "sources": [source(owner, world["survivor_material"], SURVIVOR_TEXT)]},
    ]


def merge_changes(owner, world, *, redirects=None):
    survivor, donor = world["survivor"], world["donor"]
    changes = [
        {"kind": "card", "action": "update", "id": survivor["card_id"],
         "expected_version": survivor["version"],
         "fields": {"merge_from": {"card_id": donor["card_id"],
                                   "version": donor["version"]}},
         "reason": "Merge the duplicate into this card.", "sources": []},
        {"kind": "card", "action": "delete", "id": donor["card_id"],
         "expected_version": donor["version"], "fields": {},
         "reason": "Archive the merged duplicate.",
         "sources": [source(owner, world["donor_material"], DONOR_TEXT)]},
    ]
    if redirects is None:
        redirects = redirect_changes(owner, world)
    changes.extend(redirects)
    return changes


def request_merge(owner, changes, *, operation="merge-request"):
    return owner.request([{"changes": changes}], operation=operation)


def reviewed(owner, receipt):
    return owner.reviewed(receipt)


def decision(owner, identifier, *, operation, decision="approve"):
    stored = owner.keeper.view(identifier, token=TOKEN)
    return {
        "schema_version": 1, "operation_id": operation,
        "proposal_id": identifier, "revision": stored["revision"],
        "expected_versions": [change["expected_version"]
                              for change in stored["changes"]],
        "decision": decision,
    }


def test_exact_merge_builds_survivor_union_and_redirects_with_read_only_preview(tmp_path):
    owner = Owner(tmp_path)
    world = merge_world(owner)
    survivor, donor = world["survivor"], world["donor"]

    receipt = request_merge(owner, merge_changes(owner, world))
    result = reviewed(owner, receipt)
    assert result["status"] == "pending"
    proposal = owner.keeper.view(result["proposal_ids"][0], token=TOKEN)
    changes = {(change["kind"], change["action"], change["id"]): change
               for change in proposal["changes"]}
    merged = changes[("card", "update", survivor["card_id"])]
    archived = changes[("card", "delete", donor["card_id"])]
    inbound = changes[("card", "update", world["inbound"]["card_id"])]
    theme = changes[("theme", "update", world["theme"]["theme_id"])]

    assert merged["merge_from"] == {
        "card_id": donor["card_id"], "version": donor["version"],
        "title": donor["title"]}
    assert merged["after"]["title"] == survivor["title"]
    assert merged["after"]["content"] == "Survivor body.\n\nDonor body."
    assert set(merged["after"]["aliases"]) == {
        "baseline", "field-notes", donor["title"]}
    assert "recall_terms" not in merged["after"]
    assert merged["after"]["questionable"] is True
    assert merged["after"]["relations"] == [
        world["other_a"]["card_id"], world["other_b"]["card_id"]]
    assert donor["card_id"] not in merged["after"]["relations"]
    assert survivor["card_id"] not in merged["after"]["relations"]
    pins = {(item["material_id"], item["material_version"],
             item["content_hash"]) for item in merged["after"]["sources"]}
    assert pins == {
        (world["survivor_material"]["material_id"],
         world["survivor_material"]["version"],
         world["survivor_material"]["snapshot"]["content_hash"]),
        (world["donor_material"]["material_id"],
         world["donor_material"]["version"],
         world["donor_material"]["snapshot"]["content_hash"]),
    }
    assert {item["material_id"] for item in merged["sources"] + archived["sources"]} == {
        world["survivor_material"]["material_id"],
        world["donor_material"]["material_id"]}
    assert archived["after"]["status"] == "archived"
    assert inbound["after"]["relations"] == [
        survivor["card_id"], world["other_a"]["card_id"]]
    assert [item["card_id"] for item in theme["after"]["related_cards"]] == [
        survivor["card_id"], world["other_a"]["card_id"]]
    assert [item["version"] for item in theme["after"]["related_cards"]] == [
        survivor["version"], world["other_a"]["version"]]

    # Preview writes nothing.
    assert owner.cards.view(survivor["card_id"], token=TOKEN) == survivor
    assert owner.cards.view(donor["card_id"], token=TOKEN)["status"] == "active"
    assert owner.cards.view(world["inbound"]["card_id"],
                            token=TOKEN)["relations"] == [
        donor["card_id"], world["other_a"]["card_id"]]
    assert owner.themes.view(world["theme"]["theme_id"],
                             token=TOKEN)["related_cards"] == [
        {"card_id": donor["card_id"], "version": donor["version"]},
        {"card_id": world["other_a"]["card_id"],
         "version": world["other_a"]["version"]}]

    approved = owner.keeper.decide(
        decision(owner, proposal["proposal_id"], operation="merge-approve"),
        token=TOKEN, principal=EXPLORER)
    assert approved["status"] == "approved"
    merged_card = owner.cards.view(survivor["card_id"], token=TOKEN)
    assert merged_card["version"] == survivor["version"] + 1
    assert merged_card["title"] == survivor["title"]
    assert merged_card["content"] == "Survivor body.\n\nDonor body."
    assert {item["material_id"] for item in merged_card["sources"]} == {
        world["survivor_material"]["material_id"],
        world["donor_material"]["material_id"]}
    assert owner.cards.view(donor["card_id"], token=TOKEN)["status"] == "archived"
    assert owner.cards.view(world["inbound"]["card_id"],
                            token=TOKEN)["relations"] == [
        survivor["card_id"], world["other_a"]["card_id"]]
    assert {item["card_id"] for item in owner.themes.view(
        world["theme"]["theme_id"], token=TOKEN)["related_cards"]} == {
        survivor["card_id"], world["other_a"]["card_id"]}


def test_merge_donor_archive_reuses_the_donor_card_citations(tmp_path):
    owner = Owner(tmp_path)
    world = merge_world(owner)
    changes = merge_changes(owner, world)
    changes[1]["sources"] = []
    request = request_merge(owner, changes, operation="merge-donor-citations")
    runtime = KeeperRuntime(owner.agent, token=TOKEN)
    result = owner.keeper.review(request["request_id"], token=TOKEN,
                                 dispatch=runtime.dispatch)
    assert result["status"] == "pending"
    proposal = owner.keeper.view(result["proposal_ids"][0], token=TOKEN)
    archived = next(change for change in proposal["changes"]
                    if change["action"] == "delete")
    assert {item["material_id"] for item in archived["sources"]} == {
        world["donor_material"]["material_id"]}
    assert archived["sources"][0]["material_version"] == \
        world["donor_material"]["version"]


def test_merge_rejects_a_missing_or_nonmatching_inbound_redirect(tmp_path):
    owner = Owner(tmp_path)
    world = merge_world(owner)
    survivor, donor = world["survivor"], world["donor"]
    partial = redirect_changes(owner, world)[:1]

    missing = request_merge(owner, merge_changes(owner, world, redirects=partial),
                            operation="merge-missing-redirect")
    assert missing["status"] == "returned"
    assert missing["reason"] == "incomplete_merge"
    assert owner.cards.view(survivor["card_id"], token=TOKEN) == survivor

    stale = redirect_changes(owner, world)
    stale[1]["fields"]["related_cards"] = [
        {"card_id": donor["card_id"], "version": donor["version"]}]
    rejected = request_merge(owner, merge_changes(owner, world, redirects=stale),
                             operation="merge-stale-redirect")
    assert rejected["status"] == "returned"
    assert rejected["reason"] == "incomplete_merge"
    assert owner.cards.view(donor["card_id"], token=TOKEN)["status"] == "active"


def test_approval_revalidates_a_new_inbound_relation_before_any_write(tmp_path):
    owner = Owner(tmp_path)
    world = merge_world(owner)
    survivor, donor = world["survivor"], world["donor"]
    result = reviewed(owner, request_merge(
        owner, merge_changes(owner, world), operation="merge-revalidate"))
    identifier = result["proposal_ids"][0]

    card(owner, operation="late-inbound", title="Late inbound",
         content="Created after preview.", relations=[donor["card_id"]])

    with pytest.raises(CommandError) as rejected:
        owner.keeper.decide(
            decision(owner, identifier, operation="merge-revalidate-approve"),
            token=TOKEN, principal=EXPLORER)
    assert rejected.value.code == "incomplete_merge"
    assert owner.cards.view(survivor["card_id"], token=TOKEN) == survivor
    assert owner.cards.view(donor["card_id"], token=TOKEN)["status"] == "active"
    assert owner.keeper.view(identifier, token=TOKEN)["status"] == "pending"


def test_merge_refuses_a_new_link_to_its_archive_target(tmp_path):
    owner = Owner(tmp_path)
    world = merge_world(owner)
    changes = merge_changes(owner, world)
    changes.append(owner.card_change(
        [source(owner, world["survivor_material"], SURVIVOR_TEXT)],
        fields={"title": "New incoming reference", "content": "A new link.",
                "relations": [world["donor"]["card_id"]]}))
    returned = request_merge(owner, changes)
    assert returned["status"] == "returned" and returned["reason"] == "incomplete_merge"
    assert owner.cards.view(world["donor"]["card_id"], token=TOKEN)["status"] == "active"


def test_exact_duplicate_creation_supplements_only_new_citations(tmp_path):
    owner = Owner(tmp_path)
    first = owner.material(SURVIVOR_TEXT, operation="supplement-first")
    second = owner.material(DONOR_TEXT, operation="supplement-second")
    third = owner.material(SUPPORT_TEXT, operation="supplement-third")
    existing = card(owner, operation="supplement-card",
                    title="Calibrated optics", content="Existing body.",
                    material=first, text=SURVIVOR_TEXT)

    receipt = owner.request([{"changes": [{
        "kind": "card", "action": "create",
        "fields": {"title": "calibrated   OPTICS",
                   "content": "A conflicting new body."},
        "reason": "Supplement the existing record.",
        "sources": [source(owner, second, DONOR_TEXT)],
    }]}], operation="supplement-request")
    result = reviewed(owner, receipt)
    assert result["status"] == "pending"
    proposal = owner.keeper.view(result["proposal_ids"][0], token=TOKEN)
    change = proposal["changes"][0]
    assert change["action"] == "update"
    assert change["id"] == existing["card_id"]
    assert change["add_sources"] is True
    assert change["after"]["content"] == "Existing body."
    assert change["after"]["title"] == "Calibrated optics"
    assert {item["material_id"] for item in change["after"]["sources"]} == {
        first["material_id"], second["material_id"]}

    owner.keeper.decide(
        decision(owner, proposal["proposal_id"],
                 operation="supplement-approve"),
        token=TOKEN, principal=EXPLORER)
    augmented = owner.cards.view(existing["card_id"], token=TOKEN)
    assert augmented["content"] == "Existing body."
    assert {item["material_id"] for item in augmented["sources"]} == {
        first["material_id"], second["material_id"]}

    same = owner.request([{"changes": [{
        "kind": "card", "action": "create",
        "fields": {"title": "Calibrated optics", "content": "Same evidence."},
        "reason": "Repeat the same evidence.",
        "sources": [source(owner, first, SURVIVOR_TEXT)],
    }]}], operation="supplement-same")
    assert same["status"] == "returned"
    assert same["reason"] == "duplicate_entity"
    assert same["evidence"][0]["id"] == existing["card_id"]

    near = owner.request([{"changes": [{
        "kind": "card", "action": "create",
        "fields": {"title": "Calibrated optics 2", "content": "Independent."},
        "reason": "A near name is independent.",
        "sources": [source(owner, second, DONOR_TEXT)],
    }]}], operation="supplement-near")
    assert near["status"] == "queued"

    explicit = owner.request([{"changes": [{
        "kind": "card", "action": "update", "id": existing["card_id"],
        "expected_version": augmented["version"],
        "fields": {"add_sources": True},
        "reason": "Explicitly add one source.",
        "sources": [source(owner, third, SUPPORT_TEXT)],
    }]}], operation="supplement-explicit")
    explicit_result = reviewed(owner, explicit)
    assert explicit_result["status"] == "pending"
    explicit_proposal = owner.keeper.view(
        explicit_result["proposal_ids"][0], token=TOKEN)
    owner.keeper.decide(
        decision(owner, explicit_proposal["proposal_id"],
                 operation="supplement-explicit-approve"),
        token=TOKEN, principal=EXPLORER)
    final = owner.cards.view(existing["card_id"], token=TOKEN)
    assert {item["material_id"] for item in final["sources"]} == {
        first["material_id"], second["material_id"], third["material_id"]}
    assert final["content"] == "Existing body."


def test_duplicate_supplement_rechecks_the_same_card_after_a_manual_edit(tmp_path):
    owner = Owner(tmp_path)
    previous = owner.material(SURVIVOR_TEXT, operation="original-source")
    addition = owner.material(DONOR_TEXT, operation="another-source")
    saved = card(owner, operation="existing-card", title="Original title",
                 content="Original body.", material=previous, text=SURVIVOR_TEXT)
    request = owner.request([{"changes": [{
        "kind": "card", "action": "create",
        "fields": {"title": " ORIGINAL TITLE ", "content": "Different proposed body."},
        "reason": "Supplement the reference.", "sources": [source(owner, addition, DONOR_TEXT)],
    }]}], operation="exact-duplicate")
    identifier, = reviewed(owner, request)["proposal_ids"]
    owner.cards.update({
        "schema_version": 1, "operation_id": "manual-title",
        "card_id": saved["card_id"], "expected_version": 1,
        "title": "New manual title", "content": "New manual body.",
    }, token=TOKEN)
    conflict = owner.keeper.decide(decision(owner, identifier, operation="old-supplement"),
                                   token=TOKEN, principal=EXPLORER)
    assert conflict["status"] == "revising"
    revised = owner.keeper.review_revision(
        conflict["review_id"], token=TOKEN,
        dispatch=lambda context: {"status": "reviewed", "child_id": "fresh-keeper"},
    )
    assert revised["status"] == "completed"
    approved = owner.keeper.decide(decision(owner, identifier, operation="new-supplement"),
                                   token=TOKEN, principal=EXPLORER)
    assert approved["applied"] == [
        {"kind": "card", "id": saved["card_id"], "version": 3},
    ]
    final = owner.cards.view(saved["card_id"], token=TOKEN)
    assert final["title"] == "New manual title" and final["content"] == "New manual body."
    assert [item["material_id"] for item in final["sources"]] == [
        previous["material_id"], addition["material_id"],
    ]


def test_rejected_supplement_fingerprint_accounts_for_its_exact_sources(tmp_path):
    owner = Owner(tmp_path)
    first = owner.material(SURVIVOR_TEXT, operation="fingerprint-first")
    second = owner.material(DONOR_TEXT, operation="fingerprint-second")
    third = owner.material(SUPPORT_TEXT, operation="fingerprint-third")
    card(owner, operation="fingerprint-card", title="Calibrated optics",
         content="Existing body.", material=first, text=SURVIVOR_TEXT)

    def supplement(material, text, operation):
        return {
            "kind": "card", "action": "create",
            "fields": {"title": "Calibrated optics", "content": "Ignored."},
            "reason": "Supplement with one more campaign.",
            "sources": [source(owner, material, text)],
        }

    receipt = owner.request([{"changes": [
        supplement(second, DONOR_TEXT, "fingerprint")]}],
        operation="fingerprint-request")
    pending = reviewed(owner, receipt)
    owner.keeper.decide(
        decision(owner, pending["proposal_ids"][0],
                 operation="fingerprint-reject", decision="reject"),
        token=TOKEN, principal=EXPLORER)

    same = owner.request([{"changes": [
        supplement(second, DONOR_TEXT, "fingerprint")]}],
        operation="fingerprint-repeat")
    assert same["status"] == "returned"
    assert same["reason"] == "duplicate_change"

    genuinely_new = owner.request([{"changes": [
        supplement(third, SUPPORT_TEXT, "fingerprint")]}],
        operation="fingerprint-new")
    assert genuinely_new["status"] == "queued"


def test_counterevidence_keeps_previous_verified_support_and_adds_opposition(tmp_path):
    owner = Owner(tmp_path)
    support_material = owner.material(SUPPORT_TEXT,
                                      operation="counter-support-material")
    oppose_material = owner.material(OPPOSE_TEXT,
                                     operation="counter-oppose-material")
    theme = owner.themes.create({
        "schema_version": 1, "operation_id": "counter-theme",
        "judgement": "The calibration is stable.",
        "conditions": ["Only in daylight."],
        "evidence": [{
            "stance": "supports", "reason": "The first campaign agrees.",
            "source": source(owner, support_material, SUPPORT_TEXT)}],
    }, token=TOKEN)
    support_id = theme["evidence"][0]["evidence_id"]

    update = {
        "kind": "theme", "action": "update", "id": theme["theme_id"],
        "expected_version": theme["version"],
        "fields": {"evidence": [{
            "stance": "opposes", "reason": "The second campaign disagrees.",
            "source": source(owner, oppose_material, OPPOSE_TEXT)}]},
        "reason": "Record the contradictory campaign.",
        "sources": [source(owner, oppose_material, OPPOSE_TEXT)],
    }
    result = reviewed(owner, owner.request([{"changes": [update]}],
                                           operation="counter-request"))
    assert result["status"] == "pending"
    proposal = owner.keeper.view(result["proposal_ids"][0], token=TOKEN)
    evidence = proposal["changes"][0]["after"]["evidence"]
    assert [entry["evidence_id"] for entry in evidence][0] == support_id
    assert [(entry["stance"], entry["source"]["material_id"])
            for entry in evidence] == [
        ("supports", support_material["material_id"]),
        ("opposes", oppose_material["material_id"])]
    assert evidence[0]["reason"] == "The first campaign agrees."
    assert proposal["changes"][0]["after"]["conditions"] == ["Only in daylight."]

    owner.keeper.decide(
        decision(owner, proposal["proposal_id"], operation="counter-approve"),
        token=TOKEN, principal=EXPLORER)
    stored = owner.themes.view(theme["theme_id"], token=TOKEN)
    assert stored["conditions"] == ["Only in daylight."]
    assert stored["evidence_sufficiency"] == "balanced"
    assert stored["evidence"][0]["evidence_id"] == support_id
    assert stored["evidence"][0]["stance"] == "supports"
    assert stored["evidence"][0]["source"]["material_id"] == \
        support_material["material_id"]

    refine = {
        "kind": "theme", "action": "update", "id": theme["theme_id"],
        "expected_version": stored["version"],
        "fields": {"evidence": [{
            "evidence_id": support_id,
            "stance": "supports",
            "reason": "The first campaign agrees on a refined reading.",
            "source": source(owner, support_material, SUPPORT_TEXT)}]},
        "reason": "Refine the existing citation's reason.",
        "sources": [source(owner, support_material, SUPPORT_TEXT)],
    }
    refined = reviewed(owner, owner.request([{"changes": [refine]}],
                                            operation="counter-refine"))
    assert refined["status"] == "pending"
    refined_proposal = owner.keeper.view(refined["proposal_ids"][0], token=TOKEN)
    refined_evidence = refined_proposal["changes"][0]["after"]["evidence"]
    assert len(refined_evidence) == 2
    assert refined_evidence[0]["evidence_id"] == support_id
    assert refined_evidence[0]["reason"] == \
        "The first campaign agrees on a refined reading."
    assert refined_evidence[0]["source"]["material_id"] == \
        support_material["material_id"]


def test_counterevidence_preserves_two_existing_stances_on_one_passage(tmp_path):
    owner = Owner(tmp_path)
    first = owner.material(SUPPORT_TEXT, operation="shared-passage")
    second = owner.material(OPPOSE_TEXT, operation="independent-passage")
    earlier = source(owner, first, SUPPORT_TEXT)
    latest = source(owner, second, OPPOSE_TEXT)
    theme = owner.themes.create({
        "schema_version": 1, "operation_id": "interpreted-theme",
        "judgement": "The calibration is stable.",
        "evidence": [
            {"stance": "supports", "reason": "The campaign's own interpretation.", "source": earlier},
            {"stance": "opposes", "reason": "An independent reading.", "source": earlier},
        ],
    }, token=TOKEN)
    change = {
        "kind": "theme", "action": "update", "id": theme["theme_id"],
        "expected_version": 1, "reason": "Preserve the alternative reading.",
        "sources": [], "fields": {"evidence": [
            {"stance": "opposes", "reason": "Other observations disagree.", "source": latest},
        ]},
    }
    receipt = owner.request([{"changes": [change]}], operation="new-interpretation")
    runtime = KeeperRuntime(owner.agent, token=TOKEN)
    result = owner.keeper.review(receipt["request_id"], token=TOKEN,
                                 dispatch=runtime.dispatch)
    assert result["status"] == "pending"
    identifier, = result["proposal_ids"]
    owner.keeper.decide(decision(owner, identifier, operation="approve-both"),
                        token=TOKEN, principal=EXPLORER)
    updated = owner.themes.view(theme["theme_id"], token=TOKEN)["evidence"]
    assert updated[:2] == theme["evidence"]
    assert len(updated) == 3 and updated[2]["source"]["material_id"] == second["material_id"]


def test_merge_refuses_an_unverifiable_historical_donor_pin(tmp_path):
    owner = Owner(tmp_path)
    world = merge_world(owner)
    donor = world["donor"]
    with database_connection(owner.home) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT result FROM knowledge_cards WHERE card_id=?",
                         (donor["card_id"],)).fetchone()
        record = json.loads(row[0])
        record["sources"][0]["material_version"] = 2
        db.execute("UPDATE knowledge_cards SET result=? WHERE card_id=?",
                   (json.dumps(record), donor["card_id"]))
        db.commit()

    with pytest.raises(CommandError) as conflict:
        request_merge(owner, merge_changes(owner, world),
                      operation="merge-stale-donor-pin")
    assert conflict.value.code == "version_conflict"
    assert owner.cards.view(world["survivor"]["card_id"],
                            token=TOKEN) == world["survivor"]
    assert owner.cards.view(donor["card_id"], token=TOKEN)["status"] == "active"


def test_k8_adjustment_cannot_omit_part_of_a_merge_before_publish(tmp_path):
    owner = Owner(tmp_path)
    world = merge_world(owner)
    result = reviewed(owner, request_merge(
        owner, merge_changes(owner, world), operation="merge-omit"))
    identifier = result["proposal_ids"][0]
    stored = owner.keeper.view(identifier, token=TOKEN)
    donor_index = next(position for position, change in enumerate(stored["changes"])
                       if change["action"] == "delete")
    adjusted = owner.keeper.revise({
        "schema_version": 1, "operation_id": "merge-omit-adjust",
        "proposal_id": identifier, "revision": stored["revision"],
        "decision": "adjust", "feedback": "Leave the donor active.",
        "omit_changes": [donor_index],
    }, token=TOKEN, principal=EXPLORER)
    assert adjusted["status"] == "revising"

    returned = owner.keeper.review_revision(
        adjusted["review_id"], token=TOKEN,
        dispatch=lambda context: {"status": "reviewed",
                                  "child_id": "merge-omit-child"})
    assert returned["status"] == "returned"
    assert returned["reason"] == "incomplete_merge"
    assert owner.cards.view(world["donor"]["card_id"],
                            token=TOKEN)["status"] == "active"
    assert owner.cards.view(world["survivor"]["card_id"],
                            token=TOKEN) == world["survivor"]


def test_k8_rebuild_recalculates_survivor_and_returns_on_a_moved_donor_pin(tmp_path):
    owner = Owner(tmp_path)
    world = merge_world(owner)
    survivor, donor = world["survivor"], world["donor"]
    result = reviewed(owner, request_merge(
        owner, merge_changes(owner, world), operation="merge-drift"))
    identifier = result["proposal_ids"][0]

    owner.cards.update({
        "schema_version": 1, "operation_id": "merge-manual-survivor",
        "card_id": survivor["card_id"], "expected_version": survivor["version"],
        "title": "Changed by hand",
    }, token=TOKEN)

    revising = owner.keeper.decide(
        decision(owner, identifier, operation="merge-drift-approve"),
        token=TOKEN, principal=EXPLORER)
    assert revising["status"] == "revising"
    completed = owner.keeper.review_revision(
        revising["review_id"], token=TOKEN,
        dispatch=lambda context: {"status": "reviewed",
                                  "child_id": "merge-revision-child"})
    assert completed["status"] == "completed"

    revised = owner.keeper.view(identifier, token=TOKEN)
    assert revised["revision"] == 2
    merge_change = next(change for change in revised["changes"]
                        if change.get("merge_from") is not None)
    assert merge_change["merge_from"]["card_id"] == donor["card_id"]
    assert merge_change["after"]["title"] == "Changed by hand"
    assert merge_change["after"]["content"] == "Survivor body.\n\nDonor body."

    approved = owner.keeper.decide(
        decision(owner, identifier, operation="merge-drift-confirm"),
        token=TOKEN, principal=EXPLORER)
    assert approved["status"] == "approved"
    final = owner.cards.view(survivor["card_id"], token=TOKEN)
    assert final["title"] == "Changed by hand"
    assert final["content"] == "Survivor body.\n\nDonor body."

    # A moved donor is an explicit old pin, not a silent rebase to latest.
    moved_owner = Owner(tmp_path / "moved")
    second = merge_world(moved_owner)
    proposal = reviewed(moved_owner, request_merge(
        moved_owner, merge_changes(moved_owner, second),
        operation="merge-moved-donor"))
    moved_identifier = proposal["proposal_ids"][0]
    moved_owner.cards.update({
        "schema_version": 1, "operation_id": "merge-manual-donor",
        "card_id": second["donor"]["card_id"],
        "expected_version": second["donor"]["version"],
        "title": "Donor changed by hand",
    }, token=TOKEN)
    moved_result = moved_owner.keeper.decide(
        decision(moved_owner, moved_identifier,
                 operation="merge-moved-donor-approve"),
        token=TOKEN, principal=EXPLORER)
    assert moved_result["status"] == "revising"
    returned = moved_owner.keeper.review_revision(
        moved_result["review_id"], token=TOKEN,
        dispatch=lambda context: {"status": "reviewed",
                                  "child_id": "moved-donor-child"})
    assert returned["status"] == "returned"
    assert returned["reason"] == "version_conflict"
    assert moved_owner.cards.view(
        second["donor"]["card_id"], token=TOKEN)["status"] == "active"
    assert moved_owner.cards.view(
        second["survivor"]["card_id"], token=TOKEN) == second["survivor"]
