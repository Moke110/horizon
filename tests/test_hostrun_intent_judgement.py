"""Intent judgement and the one-time confirmation card (issue #83, ADR 0009 §4).

A message outside the instruction whitelist starts **one** read-only Agent run
(no executor lease, no push single-flight lock, 90 s cap, one at a time).  The
run must answer with ``{op, params, reason}`` JSON whose operation belongs to
the #82 registry and whose parameters pass that operation's schema; anything
else is answered "无法判断".  A hit sends a confirmation card with the
operation, its parameters and an expiry, and only the owner's click executes it
through the registry.  Duplicate, expired, cancelled and non-owner clicks never
execute.

Every test is offline: the Agent runner, card sender, text sender, clock and
executor are injected, so no ``dsh``, ``lark-cli``, network or credential is
involved.  A real Feishu card round-trip stays ``untested``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hostrun import console_events, intent_judgement, operations

OWNER = "ou_owner"
START = 1_000_000.0
VALID = json.dumps(
    {"op": "change_schedule", "params": {"time": "09:30"}, "reason": "把推送时间改到九点半"},
    ensure_ascii=False,
)
NO_OP = json.dumps({"op": "", "params": {}, "reason": "与任何操作无关"}, ensure_ascii=False)


# ------------------------------------------------------------------ fixtures
def _home(tmp_path: Path) -> Path:
    home = tmp_path / "runtime"
    (home / "install").mkdir(parents=True, exist_ok=True)
    (home / "install" / "recipient.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": OWNER,
                "verified_at": "2026-10-30T10:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    return home


def _buttons(node) -> list[dict]:
    found: list[dict] = []
    if isinstance(node, dict):
        if node.get("tag") == "button":
            found.append(node)
        for value in node.values():
            found.extend(_buttons(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_buttons(value))
    return found


def _markdown(card: dict) -> str:
    lines: list[str] = []
    for element in card.get("body", {}).get("elements", []):
        if element.get("tag") == "markdown":
            lines.append(str(element.get("content") or ""))
    return "\n".join(lines)


def _callback(card: dict, decision: str) -> dict:
    for button in _buttons(card):
        for behavior in button.get("behaviors") or []:
            value = behavior.get("value") or {}
            if value.get("decision") == decision:
                return value
    raise AssertionError(f"no {decision} button in card")


def _click(card: dict, decision: str = "execute", *, operator: str = OWNER) -> dict:
    value = _callback(card, decision)
    return {
        "event_type": "card.action.trigger",
        "message_id": f"om_{value.get('nonce')}",
        "action_name": "horizon_confirm_" + decision,
        "operator_id": operator,
        "token": "tok_1",
        "action_value": json.dumps(value, ensure_ascii=False),
        "form_value": "",
        "chat_id": "",
    }


class Harness:
    """One ``IntentJudge`` with every external seam recorded, never spawned."""

    def __init__(self, home: Path, *, output: str | None = VALID, runner=None):
        self.home = home
        self.now = START
        self.runs: list[dict] = []
        self.cards: list[dict] = []
        self.texts: list[tuple[str, str]] = []
        self.executions: list[dict] = []

        def run(home_arg, task, **kwargs):
            self.runs.append({"home": home_arg, "task": task, **kwargs})
            if runner is not None:
                return runner(task, **kwargs)
            return {"ok": True, "reason": "ok", "final_text": output or ""}

        def send_card(target, card, *args, **kwargs):
            self.cards.append(card)
            return {"message_id": f"om_card_{len(self.cards)}"}

        def send_text(target, text, *args, **kwargs):
            self.texts.append((target, text))
            return {"ok": True, "message_id": "om_text"}

        def execute(home_arg, operation, params, **kwargs):
            self.executions.append(
                {"home": home_arg, "operation": operation, "params": params, **kwargs}
            )
            return {
                "ok": True,
                "operation": operation,
                "params": params,
                "reason": "",
                "text": f"操作 {operation} 已完成。",
            }

        self.send_text = send_text
        self.judge = intent_judgement.IntentJudge(
            home,
            run=run,
            send_card=send_card,
            send_text=send_text,
            execute=execute,
            clock=lambda: self.now,
        )

    def ask(self, text: str = "把推送时间改到九点半", *, target: str = OWNER) -> dict:
        return self.judge.judge(self.home, text, target=target, send=self.send_text)


# ------------------------------------------------------- valid judgement → card
def test_a_valid_judgement_sends_a_confirmation_card_and_executes_nothing(tmp_path):
    harness = Harness(_home(tmp_path))

    outcome = harness.ask()

    assert outcome["ok"] is True
    assert outcome["skip_reply"] is True
    assert len(harness.cards) == 1
    card = harness.cards[0]
    text = _markdown(card)
    assert "change_schedule" in text
    assert "09:30" in text
    assert "不会修改 horizon 程序本身" in text
    assert "有效期" in text
    assert {_callback(card, "execute").get("decision"), _callback(card, "cancel").get("decision")} == {
        "execute",
        "cancel",
    }
    assert _callback(card, "execute").get("nonce")
    assert harness.executions == []


def test_the_judgement_run_is_one_read_only_bounded_agent_run(tmp_path):
    harness = Harness(_home(tmp_path))

    harness.ask("把推送时间改到九点半")

    assert len(harness.runs) == 1
    call = harness.runs[0]
    assert call["kind"] == "intent_judgement"
    assert call["lease"] is False
    assert call["single_flight"] is False
    assert call["timeout_seconds"] == 90
    assert "只读" in call["task"]
    assert "change_schedule" in call["task"]
    assert "JSON" in call["task"]


# --------------------------------------------------------- invalid judgements
@pytest.mark.parametrize(
    "output",
    [
        "我觉得你想改推送时间。",
        "{not json",
        "[]",
        '{"op": "change_schedule", "params": {"time": "09:30"}}',
        '{"op": "delete_everything", "params": {}, "reason": "x"}',
        '{"op": "change_schedule", "params": {"time": "25:99"}, "reason": "x"}',
        '{"op": "status", "params": {}, "reason": "x", "command": "rm -rf"}',
    ],
)
def test_an_invalid_judgement_replies_unable_to_judge_and_sends_no_card(tmp_path, output):
    harness = Harness(_home(tmp_path), output=output)

    outcome = harness.ask()

    assert harness.cards == []
    assert outcome.get("skip_reply") is not True
    assert "无法判断" in str(outcome.get("text") or "")
    assert harness.executions == []


def test_a_timed_out_or_failed_run_is_unable_to_judge(tmp_path):
    harness = Harness(
        _home(tmp_path),
        runner=lambda task, **kwargs: {"ok": False, "reason": "timeout", "timed_out": True},
    )

    outcome = harness.ask()

    assert harness.cards == []
    assert "无法判断" in str(outcome.get("text") or "")


def test_no_matching_operation_gets_the_no_permission_reply(tmp_path):
    harness = Harness(_home(tmp_path), output=NO_OP)

    outcome = harness.ask("帮我改一下你的源代码")

    assert harness.cards == []
    assert "无权限" in str(outcome.get("text") or "")


def test_a_console_only_operation_is_refused_with_the_console_pointer(tmp_path):
    # The registry has no console-only operation today; the judgement parser must
    # still honour the contract's visibility flag.
    registry = operations.load_registry()
    registry["operations"]["status"]["feishu_visible"] = False
    harness = Harness(_home(tmp_path), output=json.dumps({"op": "status", "params": {}, "reason": "x"}))
    harness.judge._registry = registry

    outcome = harness.ask()

    assert harness.cards == []
    assert operations.CONSOLE_ONLY_TEXT in str(outcome.get("text") or "")


# --------------------------------------------------------------------- caching
def test_the_same_text_reuses_the_judgement_for_ten_minutes(tmp_path):
    harness = Harness(_home(tmp_path))

    first = harness.ask("把推送时间改到九点半")
    harness.now = START + 599
    second = harness.ask("把推送时间改到九点半")

    assert len(harness.runs) == 1
    assert len(harness.cards) == 2
    assert first["skip_reply"] is True and second["skip_reply"] is True
    assert _callback(harness.cards[0], "execute")["nonce"] != _callback(harness.cards[1], "execute")["nonce"]

    harness.now = START + 601
    harness.ask("把推送时间改到九点半")
    assert len(harness.runs) == 2


def test_only_one_judgement_runs_at_a_time(tmp_path):
    harness = Harness(_home(tmp_path))
    nested: list[dict] = []
    calls: list[str] = []

    def runner(home_arg, task, **kwargs):
        calls.append(task)
        nested.append(harness.ask("同一个时刻的第二条消息"))
        return {"ok": True, "reason": "ok", "final_text": VALID}

    harness.judge._run = runner

    harness.ask("把推送时间改到九点半")

    assert len(calls) == 1
    assert "稍后" in str(nested[0].get("text") or "")


def test_a_failed_confirmation_card_send_is_reported_without_crashing(tmp_path):
    home = _home(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("lark down")

    judge = intent_judgement.IntentJudge(
        home,
        run=lambda home_arg, task, **kwargs: {"ok": True, "final_text": VALID},
        send_card=boom,
        send_text=lambda target, text: {"ok": True},
        clock=lambda: START,
    )

    outcome = judge.judge(home, "把推送时间改到九点半", target=OWNER)

    assert outcome["ok"] is False
    assert "卡片发送失败" in outcome["text"]
    assert "skip_reply" not in outcome


# ---------------------------------------------------------------------- clicks
def _card_harness(home: Path):
    harness = Harness(home)
    harness.ask()
    return harness, harness.cards[0]


def test_the_owner_click_executes_through_the_registry_and_receipts(tmp_path):
    harness, card = _card_harness(_home(tmp_path))

    outcome = harness.judge.handle_card(harness.home, _click(card, "execute"))

    assert outcome["ok"] is True
    assert outcome["action"] == "executed"
    assert len(harness.executions) == 1
    execution = harness.executions[0]
    assert execution["operation"] == "change_schedule"
    assert execution["params"] == {"time": "09:30"}
    assert execution["source"] == "feishu"
    assert harness.texts[-1] == (OWNER, "操作 change_schedule 已完成。")


def test_a_bound_lifecycle_receives_the_confirmed_push_dispatch(tmp_path):
    home = _home(tmp_path)
    output = json.dumps({"op": "push_again", "params": {}, "reason": "用户想再推一篇"})
    harness = Harness(home, output=output)

    class Lifecycle:
        def request_push(self, trigger="manual"):
            return {"ok": True, "started": True}

    harness.judge.bind_lifecycle(Lifecycle())
    harness.ask("再推一篇吧")

    harness.judge.handle_card(home, _click(harness.cards[0], "execute"))

    context = harness.executions[0]["context"]
    assert callable(context["start_push"])
    assert context["target"] == OWNER


def test_a_duplicate_click_executes_only_once(tmp_path):
    harness, card = _card_harness(_home(tmp_path))

    harness.judge.handle_card(harness.home, _click(card, "execute"))
    duplicate = harness.judge.handle_card(harness.home, _click(card, "execute"))

    assert len(harness.executions) == 1
    assert duplicate["action"] == "already_used"
    assert "已处理" in harness.texts[-1][1]


def test_an_expired_click_is_refused(tmp_path):
    harness, card = _card_harness(_home(tmp_path))
    harness.now = START + intent_judgement.CONFIRM_TTL_SECONDS + 1

    outcome = harness.judge.handle_card(harness.home, _click(card, "execute"))

    assert harness.executions == []
    assert outcome["action"] == "expired"
    assert "过期" in harness.texts[-1][1]


def test_a_cancel_click_never_executes(tmp_path):
    harness, card = _card_harness(_home(tmp_path))

    outcome = harness.judge.handle_card(harness.home, _click(card, "cancel"))

    assert harness.executions == []
    assert outcome["action"] == "cancelled"
    assert "取消" in harness.texts[-1][1]

    # The used-up nonce cannot be replayed as an execute afterwards.
    harness.judge.handle_card(harness.home, _click(card, "execute"))
    assert harness.executions == []


def test_a_non_owner_click_is_only_logged(tmp_path):
    harness, card = _card_harness(_home(tmp_path))
    seen: list[str] = []

    outcome = harness.judge.handle_card(
        harness.home, _click(card, "execute", operator="ou_someone_else"),
        log=lambda level, message: seen.append(message),
    )

    assert harness.executions == []
    assert harness.texts == []
    assert outcome["action"] == "ignored"
    assert any("owner" in line for line in seen)


def test_an_unknown_nonce_is_refused(tmp_path):
    harness = Harness(_home(tmp_path))
    event = {
        "event_type": "card.action.trigger",
        "message_id": "om_forged",
        "action_name": intent_judgement.CONFIRM_ACTION_EXECUTE,
        "operator_id": OWNER,
        "token": "tok",
        "action_value": json.dumps(
            {
                "kind": intent_judgement.CONFIRM_KIND,
                "decision": "execute",
                "nonce": "conf_deadbeef",
            }
        ),
        "form_value": "",
        "chat_id": "",
    }

    outcome = harness.judge.handle_card(harness.home, event)

    assert harness.executions == []
    assert outcome["action"] == "unknown"
    assert "失效" in harness.texts[-1][1]


def test_a_non_confirmation_card_is_not_handled(tmp_path):
    harness = Harness(_home(tmp_path))
    event = {
        "event_type": "card.action.trigger",
        "message_id": "om_rating",
        "action_name": "score_high",
        "operator_id": OWNER,
        "token": "tok",
        "action_value": json.dumps({"queue_item_id": "q_1"}),
        "form_value": "",
        "chat_id": "",
    }

    assert harness.judge.handle_card(harness.home, event) is None


# ------------------------------------------------------- event-surface routing
def test_confirmation_callbacks_route_through_the_card_surface(tmp_path):
    home = _home(tmp_path)
    harness, card = _card_harness(_home(tmp_path))
    publisher_seen: list[dict] = []

    def publisher(repo, event):
        publisher_seen.append(event)
        return {"ok": True, "kind": "rating"}

    result = console_events.handle_card(
        home,
        _click(card, "execute"),
        handle=publisher,
        confirm=harness.judge.handle_card,
    )

    assert publisher_seen == []
    assert result["action"] == "executed"
    assert len(harness.executions) == 1


def test_an_ordinary_rating_callback_still_reaches_the_publisher(tmp_path):
    home = _home(tmp_path)
    harness = Harness(home)
    publisher_seen: list[dict] = []

    def publisher(repo, event):
        publisher_seen.append(event)
        return {"ok": True, "kind": "rating"}

    event = {
        "event_type": "card.action.trigger",
        "message_id": "om_rating",
        "action_name": "score_high",
        "operator_id": OWNER,
        "token": "tok",
        "action_value": json.dumps({"queue_item_id": "q_1"}),
        "form_value": "",
        "chat_id": "",
    }

    result = console_events.handle_card(
        home, event, handle=publisher, confirm=harness.judge.handle_card
    )

    assert len(publisher_seen) == 1
    assert result["kind"] == "rating"


def test_a_paused_service_skips_confirmation_callbacks(tmp_path):
    home = _home(tmp_path)
    harness, card = _card_harness(_home(tmp_path))

    result = console_events.handle_card(
        home, _click(card, "execute"), confirm=harness.judge.handle_card, paused=True
    )

    assert result["skipped"] == "service_paused"
    assert harness.executions == []


# ------------------------------------------------- whitelist-miss message flow
def _owner_message(text: str) -> dict:
    return {
        "chat_type": "p2p",
        "sender_type": "user",
        "sender_id": OWNER,
        "text": text,
    }


def test_a_whitelist_miss_on_the_message_surface_sends_a_card_and_writes_no_text(tmp_path):
    home = _home(tmp_path)
    harness = Harness(home)

    outcome = console_events.handle_message(
        home,
        _owner_message("把推送时间改到九点半"),
        owner=OWNER,
        send=harness.send_text,
        judge=harness.judge.judge,
    )

    assert outcome["route_action"] == "intent_judgement"
    assert outcome["reason"] == "confirmation_sent"
    assert outcome["replied"] is False
    assert harness.texts == []
    assert len(harness.cards) == 1
    assert harness.executions == []


def test_a_paused_message_never_starts_the_judgement(tmp_path):
    home = _home(tmp_path)
    from hostrun import console

    console.write_state(home, paused=True)
    calls: list[tuple] = []

    outcome = console_events.handle_message(
        home,
        _owner_message("把推送时间改到九点半"),
        owner=OWNER,
        send=lambda target, text: {"ok": True},
        judge=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert outcome["action"] == "paused"
    assert calls == []

