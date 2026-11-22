"""Focused desktop-bridge tests for Explorer Keeper decisions and audit reads.

The core decision transaction is owned by ``KeeperService``; these checks fix
the renderer-to-Python adapter boundary: no test gate, no caller authority,
the trusted Explorer principal, and flat matching receipts.
"""

import pytest

from hostrun.agent_service import CommandError
from hostrun.desktop_bridge import KeeperCommands, keeper_bridge_command


TOKEN = "keeper-bridge-token"


class FakeKeeper:
    """A minimal service seam with the coordinated decide/audit signatures."""

    def __init__(self):
        self.calls = []
        self.audits = []

    def decide(self, command, *, token, principal):
        self.calls.append((command, token, principal))
        status = "approved" if command["decision"] == "approve" else "rejected"
        result = {
            "operation_id": command["operation_id"],
            "decision": command["decision"],
            "proposal_id": command["proposal_id"],
            "revision": command["revision"],
            "status": status,
            "applied": ([{"kind": "card", "id": "card-1", "version": 2}]
                        if status == "approved" else []),
            "decided_at": "2026-11-22T09:30:00Z",
        }
        if command.get("reason") is not None:
            result["reason"] = command["reason"]
        return result

    def audit(self, proposal_id, *, token):
        self.audits.append((proposal_id, token))
        return [{"event_id": "event-1", "proposal_id": proposal_id,
                 "action": "approved"}]


def bridge(keeper):
    return KeeperCommands(lambda: keeper, object(), token=TOKEN,
                          emit=lambda kind, data: None)


def decide_command(**overrides):
    command = {
        "action": "keeper_decide", "schema_version": 1,
        "operation_id": "decide-1", "proposal_id": "proposal-1",
        "revision": 3, "expected_versions": [None, 4],
        "decision": "approve", "reason": "The evidence covers the same optics.",
    }
    command.update(overrides)
    return command


def test_decide_has_no_test_gate_and_uses_the_trusted_explorer_principal():
    keeper = FakeKeeper()
    kind, data = keeper_bridge_command(
        bridge(keeper), decide_command(), test_enabled=False)

    assert kind == "keeper_decide"
    assert data == {
        "operation_id": "decide-1", "decision": "approve",
        "proposal_id": "proposal-1", "revision": 3, "status": "approved",
        "applied": [{"kind": "card", "id": "card-1", "version": 2}],
        "reason": "The evidence covers the same optics.",
        "decided_at": "2026-11-22T09:30:00Z",
    }
    payload, token, principal = keeper.calls[0]
    assert token == TOKEN
    assert principal.role == "explorer" and principal.explicit_test is False
    assert payload == {
        "schema_version": 1, "operation_id": "decide-1",
        "proposal_id": "proposal-1", "revision": 3,
        "expected_versions": [None, 4], "decision": "approve",
        "reason": "The evidence covers the same optics.",
    }


def test_decide_rejects_caller_authority_and_malformed_binding():
    keeper = FakeKeeper()
    commands = bridge(keeper)
    for field in ("role", "principal", "actor", "output", "provenance"):
        with pytest.raises(CommandError):
            commands.decide(decide_command(**{field: "caller"}))
    assert keeper.calls == []

    for overrides in (
        {"schema_version": 2},
        {"operation_id": ""},
        {"proposal_id": ""},
        {"revision": 0},
        {"decision": "maybe"},
        {"expected_versions": []},
        {"expected_versions": [None, 0]},
        {"expected_versions": [None, True]},
        {"reason": 3},
    ):
        with pytest.raises(CommandError):
            commands.decide(decide_command(**overrides))
    assert keeper.calls == []


def test_decide_rejects_a_receipt_for_another_operation_or_status():
    class WrongReceipt(FakeKeeper):
        def decide(self, command, *, token, principal):
            return {**super().decide(command, token=token, principal=principal),
                    "operation_id": "another-operation"}

    with pytest.raises(CommandError) as mismatch:
        bridge(WrongReceipt()).decide(decide_command())
    assert mismatch.value.code == "invalid_response"

    class WrongStatus(FakeKeeper):
        def decide(self, command, *, token, principal):
            return {**super().decide(command, token=token, principal=principal),
                    "status": "rejected"}

    with pytest.raises(CommandError) as mismatch:
        bridge(WrongStatus()).decide(decide_command())
    assert mismatch.value.code == "invalid_response"


def test_unexpected_service_failure_becomes_a_bounded_command_error():
    class Exploding(FakeKeeper):
        def decide(self, command, *, token, principal):
            raise RuntimeError("C:/private/keeper.sqlite3 token=secret")

    with pytest.raises(CommandError) as failed:
        bridge(Exploding()).decide(decide_command())
    assert failed.value.code == "keeper_failed"
    assert "secret" not in str(failed.value)


def test_audit_reads_events_through_the_service_and_echoes_identity():
    keeper = FakeKeeper()
    command = {"action": "keeper_audit", "operation_id": "audit-1",
               "proposal_id": "proposal-1"}
    kind, data = keeper_bridge_command(bridge(keeper), command, test_enabled=False)

    assert kind == "keeper_audit"
    assert data == {
        "operation_id": "audit-1", "proposal_id": "proposal-1",
        "events": [{"event_id": "event-1", "proposal_id": "proposal-1",
                    "action": "approved"}],
    }
    assert keeper.audits == [("proposal-1", TOKEN)]


def test_revise_uses_trusted_explorer_and_preserves_displayed_choices():
    class Keeper(FakeKeeper):
        def revise(self, command, *, token, principal):
            self.calls.append((command, token, principal))
            return {**command, "status": "revising", "applied": [],
                    "review_id": "review-1"}

    keeper = Keeper()
    commands = bridge(keeper)
    request = {"action": "keeper_revise", "schema_version": 1,
               "operation_id": "feedback-1", "proposal_id": "proposal-1",
               "revision": 3, "decision": "adjust", "feedback": "Keep the latest title.",
               "keep_current_fields": [{"change_index": 0, "fields": ["title"]}]}
    with pytest.raises(CommandError):
        keeper_bridge_command(commands, {**request, "principal": "explorer"},
                              test_enabled=False)
    kind, result = keeper_bridge_command(commands, request, test_enabled=False)
    assert kind == "keeper_revise" and result["review_id"] == "review-1"
    assert len(keeper.calls) == 1
    command, token, principal = keeper.calls[0]
    assert "action" not in command
    assert command["keep_current_fields"] == request["keep_current_fields"]
    assert token == TOKEN and principal.role == "explorer" and principal.explicit_test is False
