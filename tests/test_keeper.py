"""Public-behavior tests for K6: persistent Keeper requests and proposals.

The seam under test is ``KeeperService`` beside the existing owner services in
the stable data home. A trusted Editor test principal submits a completed
Agent turn as its explicit report, Keeper validates every task/report/owner
pin and saved source read-only, and a trusted dispatcher turns a queued request
into pending proposals or a returned outcome. Pending proposals never write
knowledge and never affect search.
"""

import json
import threading

import pytest

from hostrun.agent_service import AgentService, CommandError
from hostrun.frontier import FrontierService
from hostrun.keeper import KeeperPrincipal, KeeperService
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_themes import KnowledgeThemeService
from hostrun.knowledge_workbench import KnowledgeWorkbenchService


TOKEN = "keeper-token"
REPORT_TEXT = "The report proposes a sourced change but is private to the task."
EXCERPT = "Calibrated optical sensors measure received radiation every day."


class Owner:
    """A local knowledge home with a completed Agent report."""

    def __init__(self, home):
        self.home = home
        self.agent = AgentService(home, token=TOKEN)
        self.task = self.agent.create_task(
            {"schema_version": 1, "operation_id": "task-1", "title": "Keeper"},
            token=TOKEN)
        turn = self.agent.begin_turn({
            "schema_version": 1, "operation_id": "turn-1",
            "task_id": self.task["task_id"], "expected_version": 1,
            "text": "Please keep these findings.",
        }, token=TOKEN)
        self.report = self.agent.commit_turn({
            "schema_version": 1, "operation_id": "turn-complete",
            "turn_id": turn["turn_id"], "expected_version": turn["version"],
            "status": "completed", "result": REPORT_TEXT,
        }, token=TOKEN)
        self.task = self.agent.read_task(self.task["task_id"], token=TOKEN)
        self.cards = KnowledgeCardService(home, token=TOKEN)
        self.materials = self.cards.materials
        self.themes = KnowledgeThemeService(home, token=TOKEN, cards=self.cards)
        self.frontiers = FrontierService(home, token=TOKEN)
        self.keeper = KeeperService(
            self.agent, self.materials, self.cards, self.themes, self.frontiers,
            token=TOKEN)

    def material(self, text=EXCERPT, *, operation="material-1"):
        return self.materials.register_text({
            "schema_version": 1, "operation_id": operation,
            "title": "Observatory report", "source_label": "Fixtures",
            "text": f"Observatory report\n{text}\n{text}\n",
        }, token=TOKEN)

    def source(self, material, *, locator="p2", excerpt=EXCERPT):
        return {"material_id": material["material_id"],
                "material_version": material["version"],
                "locator": locator, "excerpt": excerpt}

    def request(self, groups, *, operation="keeper-1", **overrides):
        command = {
            "schema_version": 1, "operation_id": operation,
            "task_id": self.task["task_id"],
            "task_version": self.task["version"],
            "report_id": self.report["turn_id"],
            "report_version": self.report["version"],
            "groups": groups,
        }
        command.update(overrides)
        return self.keeper.request(command, token=TOKEN,
                                   principal=KeeperPrincipal("editor", True))

    def create_card(self, title, material, *, operation="card-create"):
        return self.cards.create({
            "schema_version": 1, "operation_id": operation,
            "title": title, "content": "A manually recorded concept.",
            "sources": [{"material_id": material["material_id"],
                         "locator": "p2", "excerpt": EXCERPT}],
        }, token=TOKEN)

    def card_change(self, sources, **overrides):
        change = {
            "kind": "card", "action": "create",
            "fields": {"title": "Calibrated optics", "content": "Measured daily."},
            "reason": "The report supports this concept.",
            "sources": sources,
        }
        change.update(overrides)
        return change

    def reviewed(self, receipt, *, child_id="keeper-child"):
        return self.keeper.review(
            receipt["request_id"], token=TOKEN,
            dispatch=lambda context: {"status": "reviewed", "child_id": child_id})


def test_unauthorized_roles_and_command_provenance_are_rejected(tmp_path):
    owner = Owner(tmp_path)
    command = {
        "schema_version": 1, "operation_id": "nope",
        "task_id": owner.task["task_id"],
        "task_version": owner.task["version"],
        "report_id": owner.report["turn_id"],
        "report_version": owner.report["version"],
        "groups": [{"changes": [owner.card_change([])]}],
    }

    with pytest.raises(PermissionError):
        owner.keeper.request(command, token=TOKEN,
                             principal=KeeperPrincipal("voyager", False))
    with pytest.raises(PermissionError):
        owner.keeper.request(command, token=TOKEN,
                             principal=KeeperPrincipal("editor", False))
    with pytest.raises(PermissionError):
        owner.keeper.request(command, token="wrong",
                             principal=KeeperPrincipal("editor", True))
    with pytest.raises(CommandError) as rejected:
        owner.keeper.request({**command, "role": "editor"}, token=TOKEN,
                             principal=KeeperPrincipal("editor", True))
    assert rejected.value.code == "invalid_command"
    assert owner.keeper.list(token=TOKEN, status="pending") == []


def test_request_persists_receipt_and_replays_identical_operation(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    groups = [{"changes": [owner.card_change([owner.source(material)])]}]

    receipt = owner.request(groups)

    assert receipt["status"] == "queued"
    assert receipt["request_id"]
    assert receipt["task_id"] == owner.task["task_id"]
    assert receipt["task_version"] == owner.task["version"]
    assert receipt["report_id"] == owner.report["turn_id"]
    assert receipt["report_version"] == owner.report["version"]
    assert receipt["proposal_ids"] == []
    assert owner.request(groups) == receipt
    assert owner.keeper.receipt(receipt["request_id"], token=TOKEN) == receipt
    with pytest.raises(CommandError) as conflict:
        owner.request([{"changes": [owner.card_change(
            [owner.source(material)],
            fields={"title": "Different", "content": "Different."})]}])
    assert conflict.value.code == "operation_conflict"
    assert owner.cards.list(token=TOKEN) == []


@pytest.mark.parametrize("field,value,code", [
    ("task_version", 99, "version_conflict"),
    ("report_version", 99, "version_conflict"),
    ("task_id", "unknown-task", "not_found"),
    ("report_id", "unknown-report", "not_found"),
])
def test_request_refuses_missing_or_stale_task_and_report_pins(tmp_path, field, value, code):
    owner = Owner(tmp_path)
    material = owner.material()
    groups = [{"changes": [owner.card_change([owner.source(material)])]}]
    with pytest.raises(CommandError) as rejected:
        owner.request(groups, **{field: value})
    assert rejected.value.code == code


def test_request_refuses_a_report_that_is_not_a_completed_nonempty_turn(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    groups = [{"changes": [owner.card_change([owner.source(material)])]}]
    running = owner.agent.begin_turn({
        "schema_version": 1, "operation_id": "running-turn",
        "task_id": owner.task["task_id"],
        "expected_version": owner.task["version"],
        "text": "Still running",
    }, token=TOKEN)
    current = owner.agent.read_task(owner.task["task_id"], token=TOKEN)["version"]

    with pytest.raises(CommandError) as rejected:
        owner.request(groups, task_version=current,
                      report_id=running["turn_id"],
                      report_version=running["version"])
    assert rejected.value.code == "invalid_report"

    empty = owner.agent.commit_turn({
        "schema_version": 1, "operation_id": "empty-complete",
        "turn_id": running["turn_id"], "expected_version": running["version"],
        "status": "completed", "result": "",
    }, token=TOKEN)
    current = owner.agent.read_task(owner.task["task_id"], token=TOKEN)["version"]
    with pytest.raises(CommandError) as rejected:
        owner.request(groups, task_version=current,
                      report_id=empty["turn_id"],
                      report_version=empty["version"])
    assert rejected.value.code == "invalid_report"


def test_review_turns_a_queued_request_into_pending_proposals_without_writing_owners(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    source = owner.source(material)
    groups = [{"changes": [owner.card_change([source])]}]
    receipt = owner.request(groups)
    contexts = []

    result = owner.keeper.review(
        receipt["request_id"], token=TOKEN,
        dispatch=lambda context: contexts.append(context) or {
            "status": "reviewed", "child_id": "keeper-child"})

    assert result == {"request_id": receipt["request_id"],
                      "status": "pending",
                      "child_id": "keeper-child",
                      "proposal_ids": result["proposal_ids"]}
    assert len(result["proposal_ids"]) == 1
    proposal = owner.keeper.list(token=TOKEN, status="pending")[0]
    assert proposal["proposal_id"] == result["proposal_ids"][0]
    assert proposal["revision"] == 1
    assert proposal["status"] == "pending"
    assert proposal["request_id"] == receipt["request_id"]
    assert proposal["task_id"] == owner.task["task_id"]
    assert proposal["task_version"] == owner.task["version"]
    assert proposal["report_id"] == owner.report["turn_id"]
    assert proposal["report_version"] == owner.report["version"]
    assert proposal["provenance"] == "editor_test"
    assert proposal["child_id"] == "keeper-child"
    change = proposal["changes"][0]
    assert set(change) == {"kind", "action", "id", "expected_version", "before",
                           "after", "reason", "sources"}
    assert (change["kind"], change["action"], change["id"]) == ("card", "create", None)
    assert change["expected_version"] is None
    assert change["before"] is None
    assert change["after"]["title"] == "Calibrated optics"
    assert {key: change["sources"][0][key] for key in source} == source
    assert owner.keeper.view(proposal["proposal_id"], token=TOKEN) == proposal

    context = contexts[0]
    assert REPORT_TEXT not in json.dumps(context)
    assert "result" not in context and "messages" not in context
    assert context["report_id"] == owner.report["turn_id"]
    assert owner.cards.list(token=TOKEN) == []
    assert owner.keeper.receipt(receipt["request_id"], token=TOKEN)["status"] == "pending"

    # A terminal review does not call the dispatcher again.
    assert owner.keeper.review(receipt["request_id"], token=TOKEN,
                               dispatch=lambda context: pytest.fail("re-dispatched")) == result


def test_invalid_dispatch_cannot_invent_proposals_or_leave_work_queued(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    receipt = owner.request([{"changes": [
        owner.card_change([owner.source(material)])]}])

    result = owner.keeper.review(receipt["request_id"], token=TOKEN,
                                 dispatch=lambda context: {
                                     "status": "reviewed", "changes": [{"kind": "card"}]})
    assert result["status"] == "failed" and result["reason"] == "invalid_dispatch"
    assert owner.keeper.receipt(receipt["request_id"], token=TOKEN)["status"] == "failed"
    assert owner.keeper.queued(token=TOKEN) == []
    assert owner.keeper.review(receipt["request_id"], token=TOKEN,
                               dispatch=lambda context: pytest.fail("re-dispatched")) == result


def test_dispatcher_can_return_the_request_without_creating_proposals(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    receipt = owner.request([{"changes": [
        owner.card_change([owner.source(material)])]}])

    result = owner.keeper.review(
        receipt["request_id"], token=TOKEN,
        dispatch=lambda context: {"status": "returned",
                                  "reason": "evidence_insufficient",
                                  "child_id": "keeper-child"})

    assert result == {"request_id": receipt["request_id"],
                      "status": "returned", "proposal_ids": [],
                      "child_id": "keeper-child",
                      "reason": "evidence_insufficient"}
    assert owner.keeper.list(token=TOKEN, status="pending") == []
    assert owner.cards.list(token=TOKEN) == []
    assert owner.keeper.review(receipt["request_id"], token=TOKEN,
                               dispatch=lambda context: pytest.fail("re-dispatched")) == result


def test_missing_or_invalid_evidence_is_persistently_returned(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()

    missing = owner.request([{"changes": [owner.card_change([])]}],
                            operation="missing-evidence")
    assert missing["status"] == "queued"
    result = owner.reviewed(missing)
    assert result["reason"] == "missing_evidence"
    assert owner.keeper.receipt(missing["request_id"], token=TOKEN)["status"] == "returned"

    wrong_excerpt = owner.source(material, excerpt="An excerpt that is not saved.")
    invalid = owner.request([{"changes": [owner.card_change([wrong_excerpt])]}],
                            operation="invalid-excerpt")
    assert invalid["status"] == "returned"
    assert invalid["reason"] == "invalid_evidence"
    assert invalid["evidence"][0]["problem"] == "invalid_source"

    stale_version = owner.source(material)
    stale_version["material_version"] = 99
    stale = owner.request([{"changes": [owner.card_change([stale_version])]}],
                          operation="stale-material")
    assert stale["status"] == "returned"
    assert stale["reason"] == "invalid_evidence"
    assert stale["evidence"][0]["problem"] == "version_conflict"

    wrong_locator = owner.source(material, locator="p999")
    locator = owner.request([{"changes": [owner.card_change([wrong_locator])]}],
                            operation="wrong-locator")
    assert locator["status"] == "returned"
    assert locator["reason"] == "invalid_evidence"
    assert owner.keeper.list(token=TOKEN, status="pending") == []


def test_identical_normalized_creation_is_returned_but_near_names_are_not_merged(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    existing = owner.create_card("Alpha  Beta", material)
    source = owner.source(material)

    duplicate = owner.request([{"changes": [owner.card_change(
        [source], fields={"title": "alpha beta", "content": "Measured daily."})]}],
        operation="duplicate")
    assert duplicate["status"] == "returned"
    assert duplicate["reason"] == "duplicate_entity"
    assert duplicate["evidence"][0]["id"] == existing["card_id"]
    assert duplicate["evidence"][0]["version"] == existing["version"]

    near = owner.request([{"changes": [owner.card_change(
        [source], fields={"title": "Alpha Beta 2", "content": "Measured daily."})]}],
        operation="near-name")
    assert near["status"] == "queued"


def test_update_and_delete_previews_pin_versions_and_existing_terminal_actions(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    card = owner.create_card("Original concept", material)
    justification = "The later survey checked the same optical method."
    related_material = owner.material(justification, operation="separate-evidence")
    update = {
        "kind": "card", "action": "update", "id": card["card_id"],
        "expected_version": card["version"],
        "fields": {"title": "Revised concept"}, "reason": "Clarify the concept.",
        "sources": [owner.source(related_material, excerpt=justification)],
    }
    result = owner.reviewed(owner.request([{"changes": [update]}],
                                          operation="update-request"))
    proposal = owner.keeper.view(result["proposal_ids"][0], token=TOKEN)
    change = proposal["changes"][0]
    assert change["before"]["title"] == "Original concept"
    assert change["after"]["title"] == "Revised concept"
    assert change["after"]["sources"] == card["sources"]
    assert change["sources"][0]["material_id"] == related_material["material_id"]
    assert change["after"]["version"] == card["version"] + 1
    assert owner.cards.view(card["card_id"], token=TOKEN)["version"] == card["version"]

    delete = {
        "kind": "card", "action": "delete", "id": card["card_id"],
        "expected_version": card["version"], "fields": {},
        "reason": "This concept is no longer used.", "sources": [owner.source(material)],
    }
    result = owner.reviewed(owner.request([{"changes": [delete]}],
                                          operation="delete-request"))
    delete_change = owner.keeper.view(result["proposal_ids"][0], token=TOKEN)["changes"][0]
    assert delete_change["action"] == "delete"
    assert delete_change["after"]["status"] == "archived"
    assert delete_change["after"]["known"] is False
    assert delete_change["after"]["version"] == card["version"] + 1
    assert owner.cards.view(card["card_id"], token=TOKEN)["status"] == "active"


def test_stale_or_missing_owner_pins_are_refused(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    card = owner.create_card("Original concept", material)
    change = {
        "kind": "card", "action": "update", "id": card["card_id"],
        "expected_version": card["version"] + 1,
        "fields": {"title": "Stale"}, "reason": "Stale edit.", "sources": [],
    }
    with pytest.raises(CommandError) as rejected:
        owner.request([{"changes": [change]}], operation="stale")
    assert rejected.value.code == "version_conflict"

    change["id"] = "unknown-card"
    with pytest.raises(CommandError) as rejected:
        owner.request([{"changes": [change]}], operation="unknown")
    assert rejected.value.code == "not_found"


def test_duplicate_targets_are_refused_before_any_proposal(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    card = owner.create_card("Original concept", material)
    base = {"kind": "card", "action": "update", "id": card["card_id"],
            "expected_version": card["version"], "fields": {"title": "One"},
            "reason": "Edit.", "sources": []}
    with pytest.raises(CommandError) as rejected:
        owner.request([{"changes": [base, {**base, "fields": {"title": "Two"}}]}])
    assert rejected.value.code == "duplicate_target"


def test_unrelated_group_changes_split_but_directly_related_changes_stay_together(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    first = owner.create_card("First concept", material, operation="first-card")
    second = owner.create_card("Second concept", material, operation="second-card")
    first_update = {"kind": "card", "action": "update", "id": first["card_id"],
                    "expected_version": first["version"],
                    "fields": {"title": "First revised"}, "reason": "Edit.",
                    "sources": [owner.source(material)]}
    second_update = {"kind": "card", "action": "update", "id": second["card_id"],
                     "expected_version": second["version"],
                     "fields": {"title": "Second revised"}, "reason": "Edit.",
                     "sources": [owner.source(material)]}

    split = owner.reviewed(owner.request(
        [{"changes": [first_update, second_update]}], operation="split-group"))
    assert len(split["proposal_ids"]) == 2

    linked = {**second_update, "fields": {
        "title": "Second revised", "relations": [first["card_id"]]}}
    together = owner.reviewed(owner.request(
        [{"changes": [first_update]}, {"changes": [linked]}], operation="linked-group"))
    assert len(together["proposal_ids"]) == 1

    unrelated = owner.card_change([owner.source(material)], fields={
        "title": "Independent concept", "content": "Stored independently."})
    mixed = owner.reviewed(owner.request(
        [{"changes": [first_update, linked, unrelated]}], operation="mixed-group"))
    groups = [owner.keeper.view(identifier, token=TOKEN)["changes"]
              for identifier in mixed["proposal_ids"]]
    assert [len(group) for group in groups] == [2, 1]


def test_review_rechecks_owner_pins_atomically_and_returns_stale_request(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    card = owner.create_card("Original concept", material)
    change = {"kind": "card", "action": "update", "id": card["card_id"],
              "expected_version": card["version"], "fields": {"title": "Later"},
              "reason": "Edit.", "sources": [owner.source(material)]}
    receipt = owner.request([{"changes": [change]}], operation="review-race")
    owner.cards.update({
        "schema_version": 1, "operation_id": "manual-edit",
        "card_id": card["card_id"], "expected_version": card["version"],
        "title": "Manually changed",
    }, token=TOKEN)

    result = owner.keeper.review(receipt["request_id"], token=TOKEN,
                                 dispatch=lambda context: {"status": "reviewed"})
    assert result["status"] == "returned" and result["reason"] == "version_conflict"
    assert owner.keeper.receipt(receipt["request_id"], token=TOKEN)["status"] == "returned"
    assert owner.keeper.list(token=TOKEN, status="pending") == []


def test_new_turn_can_start_and_stale_review_keeps_the_child_in_its_receipt(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    receipt = owner.request([{"changes": [owner.card_change([owner.source(material)])]}])

    def dispatch(context):
        owner.agent.begin_turn({
            "schema_version": 1, "operation_id": "new-plan", "task_id": owner.task["task_id"],
            "expected_version": owner.task["version"], "text": "An independent plan",
        }, token=TOKEN)
        return {"status": "reviewed", "child_id": "keeper-child"}

    result = owner.keeper.review(receipt["request_id"], token=TOKEN, dispatch=dispatch)
    assert result["status"] == "returned" and result["reason"] == "version_conflict"
    assert result["child_id"] == "keeper-child"
    assert owner.agent.read_turn("new-plan", token=TOKEN)["status"] == "running"
    assert owner.agent.read_turn(owner.report["turn_id"], token=TOKEN)["status"] == "completed"


def test_pending_proposals_survive_reopen_and_do_not_affect_known_or_search_state(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    receipt = owner.request([{"changes": [
        owner.card_change([owner.source(material)])]}])
    result = owner.reviewed(receipt)
    workbench = KnowledgeWorkbenchService(tmp_path, token=TOKEN)
    before = workbench.search("", token=TOKEN, scope="pending")
    assert before["total"] == 0

    reopened = KeeperService(
        AgentService(tmp_path, token=TOKEN), owner.materials,
        KnowledgeCardService(tmp_path, token=TOKEN),
        KnowledgeThemeService(tmp_path, token=TOKEN),
        FrontierService(tmp_path, token=TOKEN), token=TOKEN)
    proposal = reopened.view(result["proposal_ids"][0], token=TOKEN)
    assert proposal["status"] == "pending"
    assert [item["proposal_id"] for item in reopened.list(
        token=TOKEN, status="pending")] == result["proposal_ids"]
    assert reopened.receipt(receipt["request_id"], token=TOKEN)["status"] == "pending"
    assert KnowledgeCardService(tmp_path, token=TOKEN).query(
        token=TOKEN) == []
    assert workbench.search("", token=TOKEN, scope="pending")["total"] == 0


def test_theme_and_frontier_previews_use_owner_shapes_and_terminal_actions(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    card = owner.create_card("Related concept", material)
    theme = owner.themes.create({
        "schema_version": 1, "operation_id": "theme-create",
        "judgement": "The concept holds under calibration.",
        "related_cards": [{"card_id": card["card_id"],
                           "version": card["version"]}],
        "evidence": [],
    }, token=TOKEN)
    source = owner.source(material)
    theme_update = {
        "kind": "theme", "action": "update", "id": theme["theme_id"],
        "expected_version": theme["version"],
        "fields": {"judgement": "The concept holds under daily calibration."},
        "reason": "Refine the judgement.", "sources": [source],
    }
    proposal = owner.keeper.view(
        owner.reviewed(owner.request([{"changes": [theme_update]}],
                                     operation="theme-update"))["proposal_ids"][0],
        token=TOKEN)
    change = proposal["changes"][0]
    assert change["before"]["judgement"] == theme["judgement"]
    assert change["after"]["judgement"] == "The concept holds under daily calibration."
    assert change["after"]["version"] == theme["version"] + 1
    assert change["after"]["evidence"] == []
    assert change["sources"][0]["material_version"] == material["version"]
    assert change["sources"][0]["anchor"]["id"] == "p2"
    assert owner.themes.view(theme["theme_id"], token=TOKEN)["version"] == theme["version"]

    frontier = owner.frontiers.create({
        "schema_version": 1, "operation_id": "frontier-create",
        "question": "How stable are the sensors?",
        "scope": {"text": "Calibration studies", "sources": ["fixture"]},
        "hard_constraints": {},
    }, token=TOKEN)
    frontier_update = {
        "kind": "frontier", "action": "update", "id": frontier["frontier_id"],
        "expected_version": frontier["version"],
        "fields": {"question": "How stable are the calibrated sensors?"},
        "reason": "Narrow the question.", "sources": [source],
    }
    frontier_delete = {
        "kind": "frontier", "action": "delete", "id": frontier["frontier_id"],
        "expected_version": frontier["version"], "fields": {},
        "reason": "Resolved elsewhere.", "sources": [source],
    }
    result = owner.reviewed(owner.request([{"changes": [frontier_update]}],
                                          operation="frontier-update"))
    update_change = owner.keeper.view(result["proposal_ids"][0], token=TOKEN)["changes"][0]
    assert update_change["after"]["question"] == "How stable are the calibrated sensors?"
    assert update_change["after"]["version"] == frontier["version"] + 1

    result = owner.reviewed(owner.request([{"changes": [frontier_delete]}],
                                          operation="frontier-delete"))
    delete_change = owner.keeper.view(result["proposal_ids"][0], token=TOKEN)["changes"][0]
    assert delete_change["after"]["status"] == "deleted"
    assert owner.frontiers.view(frontier["frontier_id"], token=TOKEN)["status"] == "following"


def test_concurrent_identical_requests_create_one_logical_request(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    command = {
        "schema_version": 1, "operation_id": "concurrent",
        "task_id": owner.task["task_id"],
        "task_version": owner.task["version"],
        "report_id": owner.report["turn_id"],
        "report_version": owner.report["version"],
        "groups": [{"changes": [
            owner.card_change([owner.source(material)])]}],
    }
    results = []
    errors = []

    def submit():
        try:
            results.append(owner.keeper.request(
                command, token=TOKEN, principal=KeeperPrincipal("editor", True)))
        except BaseException as error:  # pragma: no cover - reported below
            errors.append(error)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert results[0] == results[1]
    assert len(owner.keeper.list(token=TOKEN, status="pending")) == 0
