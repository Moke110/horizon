"""Offline tests for the ``/horizon-init`` transport test (issue #86, ADR 0009 §7).

The step sends one test HTML message and one single-button card and waits for the
owner's card callback.  Every external effect is injected: a fake sender records
the payloads (no real message is sent), a fake event source returns scripted card
events (no real ``lark-cli event consume``), and ``lark.user_open_id`` goes
through the fake subprocess runner.  No network, TTY or Feishu tenant is touched.
"""

from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path

from common import write_json
from hostrun import console, console_init, facilities, intents, lark


class Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def ready_runner():
    payload = {
        "identities": {
            "user": {"available": True, "tokenStatus": "valid", "userOpenId": "ou_user"},
            "bot": {"available": True, "tokenStatus": "valid"},
            "tenant": {"matched": True},
        },
        "scopes": [scope for scopes in lark.REQUIRED_SCOPES.values() for scope in scopes],
    }

    def run(argv, **kwargs):
        return Proc(stdout=json.dumps(payload))

    return run


def make_home(tmp_path: Path):
    """A paused console whose archive/agent/lark steps are already recorded."""
    code = tmp_path / "code"
    home = code / ".horizon"
    (home / "install").mkdir(parents=True)
    dsh = tmp_path / "bin" / "dsh.cmd"
    dsh.parent.mkdir(parents=True)
    dsh.write_text("@echo off\n", encoding="utf-8")
    cli = home / "bin" / "lark-cli.cmd"
    cli.parent.mkdir(parents=True)
    cli.write_text("@echo off\n", encoding="utf-8")
    write_json(
        home / "install" / "config.json",
        {
            "schema_version": 1,
            "paths": {"repo": str(code), "dsh": str(dsh), "lark_cli": str(cli)},
            "agent_run": {"dsh_args": []},
        },
    )
    console_init.record_step(
        home,
        "archive",
        {"path": str(tmp_path / "horizon-archive"), "default": True, "outside_code": True},
    )
    console_init.record_step(home, "agent", {"runtime": "horizon-pi", "provider": "test", "model": "test"})
    console_init.record_step(home, "lark", {"cli": {"path": str(cli), "source": "recorded"}})
    record_completion_steps(home)
    console.write_state(home, service="paused", paused=True)
    return home


def record_completion_steps(home: Path) -> None:
    """#87's naming/push-time/knowledge steps are covered by their own test file."""
    console_init.record_step(
        home,
        "naming",
        {
            "names": facilities.default_names(),
            "base": {"token": "bascn_test", "url": "u", "name": "horizon 知识库"},
            "tables": {},
            "folders": {"root": "r", "source": "s", "push": "p"},
            "reused": False,
            "actions": [],
        },
    )
    console_init.record_step(home, "push_time", {"push_time": "08:00", "next_push_at": None})
    console_init.record_step(
        home,
        "knowledge",
        {"choice": "later", "gate": "blocked", "reason": "knowledge_not_ready"},
    )
    console.write_state(home, knowledge_ready=False)


class Sender:
    """A fake sender: records payloads and returns message ids."""

    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls: list[dict] = []

    def __call__(self, *, owner, html, card, html_file=None):
        self.calls.append(
            {"owner": owner, "html": html, "card": card, "html_file": html_file}
        )
        if self.fail:
            return {"ok": False, "reason": "transport_send_failed", "error": "fake failure"}
        return {
            "ok": True,
            "html_message_id": "om_html_1",
            "card_message_id": "om_card_1",
        }


def card_event(*, operator="ou_user", message_id="om_card_1", action="transport_test"):
    return {
        "type": "card.action.trigger",
        "data": {
            "event": {
                "operator": {"open_id": operator},
                "action": {"name": action, "value": {"action_name": action}},
                "context": {"open_message_id": message_id},
            }
        },
    }


def event_source(*events, fail=False):
    """A fake event-wait seam returning the normalised callback shape.

    The real adapter (``wait_card_callback``) parses raw lark NDJSON; that parse
    path is covered separately.  Here the step's matching logic gets the
    normalised ``events`` it receives in production.
    """
    from hostrun.console_events import parse_card_event

    calls: list[dict] = []

    def consume(**kwargs):
        calls.append(dict(kwargs))
        if fail:
            return {"ok": False, "reason": "consume_failed", "events": []}
        normalised = [parse_card_event(event) for event in events]
        delivered = [item for item in normalised if item]
        if not delivered:
            return {"ok": False, "reason": "timeout", "events": []}
        return {"ok": True, "reason": "ok", "events": delivered}

    consume.calls = calls
    return consume


def reader(*answers):
    remaining = list(answers)

    def read(prompt: str = "") -> str:
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    return read


def run_transport(home, *, sender=None, events=None, answers=(), runner=None):
    out = io.StringIO()
    result = console_init.run(
        home,
        reader=reader(*answers),
        out=out,
        isatty=lambda: True,
        lark_runner=runner or ready_runner(),
        transport_send=sender or Sender(),
        transport_events=events or event_source(card_event()),
        find_same_name=lambda *args: [],
        facility_create=_fake_facility,
        facility_attach=_fake_facility,
    )
    return result, out.getvalue()


def _fake_facility(home, *args, names=None, **kwargs):
    """A facility call that never reaches a live Feishu tenant."""
    names = names or facilities.default_names()
    return {
        "ok": True,
        "names": names,
        "base": {"token": "bascn_test", "url": "u", "name": names["base"]},
        "tables": {},
        "folders": {"root": "r", "source": "s", "push": "p"},
        "actions": [],
        "reused": False,
    }


# ------------------------------------------------------------ happy path
def test_transport_sends_one_html_and_one_button_card_and_records_the_owner(tmp_path):
    home = make_home(tmp_path)
    sender = Sender()

    result, rendered = run_transport(home, sender=sender, events=event_source(card_event()))

    assert result["ok"] is True
    assert result["completed"] == [
        "archive",
        "agent",
        "lark",
        "transport",
        "naming",
        "push_time",
        "knowledge",
    ]
    assert len(sender.calls) == 1
    call = sender.calls[0]
    assert call["owner"] == "ou_user"
    assert "<html" in call["html"].lower()
    # The test HTML is written to the code directory runtime area and the file
    # (not the HTML text) is what gets sent (#86 acceptance fix).
    html_file = Path(call["html_file"])
    assert html_file == home / "install" / "transport-test.html"
    assert html_file.is_file()
    assert "<html" in html_file.read_text(encoding="utf-8").lower()
    card = call["card"]
    buttons = [
        element
        for row in card["body"]["elements"]
        for element in (row.get("elements") or [])
        if element.get("tag") == "button"
    ]
    assert len(buttons) == 1
    assert buttons[0]["name"] == console_init.TRANSPORT_ACTION
    # The verified owner is persisted for later Feishu owner gating.
    recipient = json.loads((home / "install" / "recipient.json").read_text(encoding="utf-8"))
    assert recipient["owner"] == "ou_user"
    assert recipient["schema_version"] == 1
    assert recipient["source"] == "init.transport"
    assert recipient["verified_at"]
    assert intents.verified_owner(home) == "ou_user"
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["transport"]["status"] == "ok"
    assert state["steps"]["transport"]["value"]["channel"] == "ok"
    assert state["steps"]["transport"]["value"]["owner"]["verified"] is True
    # The open id original never lands in the initialization state or the terminal.
    assert "ou_user" not in json.dumps(state, ensure_ascii=False)
    assert "ou_user" not in rendered
    assert "通道测试通过" in rendered


def test_the_transport_step_is_the_implemented_fourth_step(tmp_path):
    home = make_home(tmp_path)

    result, _rendered = run_transport(home, events=event_source(card_event()))

    assert console_init.STEPS[3]["id"] == "transport"
    assert console_init.STEPS[3]["implemented"] is True
    assert console_init.STEPS[3]["ticket"] == "#86"
    assert result["next_step"] is None
    assert result["not_yet"] == []
    assert result["completed"] == [
        "archive",
        "agent",
        "lark",
        "transport",
        "naming",
        "push_time",
        "knowledge",
    ]


def test_a_rerun_skips_the_completed_transport_and_does_not_resend(tmp_path):
    home = make_home(tmp_path)
    sender = Sender()
    run_transport(home, sender=sender, events=event_source(card_event()))
    assert len(sender.calls) == 1
    # US23: a completed init is re-entered only while the formal service is paused.
    console.write_state(home, service="paused", paused=True)

    again = Sender()
    result, rendered = run_transport(home, sender=again, events=event_source())

    assert result["ok"] is True
    assert again.calls == []
    assert "[已完成] 飞书测试消息与 owner 验证：通道测试通过，owner 已核实" in rendered


# ------------------------------------------------------ no click / retry path
def test_a_timeout_is_reported_unverified_and_a_retry_resends_and_succeeds(tmp_path):
    home = make_home(tmp_path)
    sender = Sender()
    events = [event_source(), event_source(card_event())]
    calls = {"n": 0}

    def next_events(**_kwargs):
        source = events[min(calls["n"], len(events) - 1)]
        calls["n"] += 1
        return source()

    result, rendered = run_transport(home, sender=sender, events=next_events, answers=("r",))

    assert result["ok"] is True
    assert len(sender.calls) == 2
    assert "未在 5 分钟内收到测试卡片的按钮点击" in rendered
    assert intents.verified_owner(home) == "ou_user"


def test_aborting_after_a_timeout_leaves_the_step_unrecorded_with_a_retry_hint(tmp_path):
    home = make_home(tmp_path)

    result, rendered = run_transport(
        home, events=event_source(), answers=("a",)
    )

    assert result["ok"] is False
    assert result["reason"] == "recipient_unverified"
    assert result["next_step"] == "transport"
    assert "未收到测试卡片点击" in result["text"]
    assert "可重试重发" in result["text"]
    assert "5 分钟内" in rendered
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert "transport" not in state["steps"]
    assert not (home / "install" / "recipient.json").exists()


def test_a_callback_for_another_card_is_ignored_and_never_records_the_owner(tmp_path):
    home = make_home(tmp_path)
    other = card_event(message_id="om_some_other_card")

    result, _rendered = run_transport(home, events=event_source(other), answers=("a",))

    assert result["ok"] is False
    assert result["reason"] == "recipient_unverified"
    assert intents.verified_owner(home) == ""
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert "transport" not in state["steps"]


# --------------------------------------------------------- non-owner clicks
def test_a_non_owner_click_is_a_mismatch_and_never_writes_the_owner(tmp_path):
    home = make_home(tmp_path)

    result, rendered = run_transport(
        home, events=event_source(card_event(operator="ou_someone_else")), answers=("a",)
    )

    assert result["ok"] is False
    assert result["reason"] == "recipient_mismatch"
    assert "点击者与当前登录用户不一致" in rendered
    assert "点击者与当前登录用户不一致" in result["text"]
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert "transport" not in state["steps"]
    assert not (home / "install" / "recipient.json").exists()


def test_a_non_owner_click_can_be_retried_with_a_new_card_click(tmp_path):
    home = make_home(tmp_path)
    events = [event_source(card_event(operator="ou_other")), event_source(card_event())]
    calls = {"n": 0}

    def next_events(**_kwargs):
        source = events[min(calls["n"], len(events) - 1)]
        calls["n"] += 1
        return source()

    result, _rendered = run_transport(home, events=next_events, answers=("r",))

    assert result["ok"] is True
    assert intents.verified_owner(home) == "ou_user"


# ------------------------------------------------------------- send failures
def test_a_send_failure_can_be_retried_and_reports_the_reason_when_aborted(tmp_path):
    home = make_home(tmp_path)
    failing = Sender(fail=True)

    aborted, rendered = run_transport(home, sender=failing, answers=("a",))

    assert aborted["ok"] is False
    assert aborted["reason"] == "transport_send_failed"
    assert "fake failure" in rendered
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert "transport" not in state["steps"]

    sender = Sender()
    retried, _rendered = run_transport(home, sender=sender, events=event_source(card_event()))
    assert retried["ok"] is True
    assert len(sender.calls) == 1


def test_an_unavailable_event_source_is_reported_honestly(tmp_path):
    home = make_home(tmp_path)

    result, rendered = run_transport(home, events=event_source(fail=True), answers=("a",))

    assert result["ok"] is False
    assert result["reason"] == "transport_event_failed"
    assert "卡片事件源不可用" in rendered


def test_an_unlogged_user_cannot_pass_the_transport_step(tmp_path):
    home = make_home(tmp_path)
    sender = Sender()

    def anonymous_runner(argv, **kwargs):
        return Proc(stdout=json.dumps({"identities": {"user": {"available": False}}}))

    result, rendered = run_transport(
        home, sender=sender, events=event_source(), runner=anonymous_runner, answers=("a",)
    )

    assert result["ok"] is False
    assert result["reason"] == "recipient_unverified"
    assert sender.calls == []
    assert "无法核对当前登录的飞书用户身份" in rendered


# --------------------------------------------------- the raw lark event parser
def test_wait_card_callback_parses_card_2_0_and_bounds_the_consume(monkeypatch):
    seen: list[dict] = []

    def fake_consume(**kwargs):
        seen.append(dict(kwargs))
        kwargs["on_line"](json.dumps(card_event()))
        return {"ok": True, "reason": "stopped", "events": 1}

    result = console_init.wait_card_callback(timeout_seconds=60, consume=fake_consume)

    assert result["ok"] is True
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["message_id"] == "om_card_1"
    assert event["operator_id"] == "ou_user"
    assert event["action_name"] == "transport_test"
    assert seen[0]["event_key"] == "card.action.trigger"
    assert seen[0]["max_events"] == 1
    assert seen[0]["deadline_seconds"] == 60


def test_wait_card_callback_reports_timeout_and_source_failure(monkeypatch):
    timed_out = console_init.wait_card_callback(
        consume=lambda **kwargs: {"ok": True, "reason": "timeout"}
    )
    failed = console_init.wait_card_callback(
        consume=lambda **kwargs: {"ok": False, "reason": "consume_lost"}
    )

    assert timed_out["ok"] is False
    assert timed_out["reason"] == "timeout"
    assert failed["ok"] is False
    assert failed["reason"] == "consume_failed"


# ----------------------------------- the console's single card subscription
def test_event_surface_wait_card_receives_the_console_card_callback(tmp_path):
    from hostrun.console_events import EventSurface

    home = make_home(tmp_path)
    surface = EventSurface(
        home,
        consume=lambda **kwargs: {"ok": True},
        send=lambda *args, **kwargs: {"ok": True},
        handle_card=lambda *args, **kwargs: {"ok": True},
        emit=lambda *args, **kwargs: None,
    )
    holder: dict = {}

    def waiter():
        holder.update(surface.wait_card(timeout_seconds=5))

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    raw = json.dumps(card_event())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not holder:
        surface.handle_line("card.action.trigger", raw)
        time.sleep(0.005)
    thread.join(timeout=5)

    assert holder["ok"] is True
    event = holder["events"][0]
    assert event["operator_id"] == "ou_user"
    assert event["message_id"] == "om_card_1"


def test_event_surface_wait_card_times_out_without_a_callback(tmp_path):
    from hostrun.console_events import EventSurface

    surface = EventSurface(
        make_home(tmp_path),
        consume=lambda **kwargs: {"ok": True},
        send=lambda *args, **kwargs: {"ok": True},
        handle_card=lambda *args, **kwargs: {"ok": True},
        emit=lambda *args, **kwargs: None,
    )

    result = surface.wait_card(timeout_seconds=0.01)

    assert result == {"ok": False, "reason": "timeout", "events": []}


def test_default_transport_sender_writes_the_html_file_and_sends_it_as_a_file(
    tmp_path, monkeypatch
):
    """The transport test must deliver an HTML *file*, not an HTML text message."""
    from publisher import feishu

    calls: list[tuple] = []

    def fake_send_file(target, path, identity="bot", to_chat=False):
        calls.append(("file", target, Path(path).name, Path(path).read_text(encoding="utf-8")))
        return {"message_id": "om_file_1", "chat_id": "oc_1"}

    def fake_send_card(target, card, identity="bot", to_chat=False):
        calls.append(("card", target))
        return {"message_id": "om_card_1", "chat_id": "oc_1"}

    def fail_send_text(*args, **kwargs):
        raise AssertionError("HTML text must not be sent when a file path is available")

    monkeypatch.setattr(feishu, "send_file", fake_send_file)
    monkeypatch.setattr(feishu, "send_card", fake_send_card)
    monkeypatch.setattr(feishu, "send_text", fail_send_text)

    target = tmp_path / "code" / "install" / "transport-test.html"
    result = console_init.send_transport_test(
        owner="ou_user",
        html=console_init.TEST_HTML,
        card=console_init.transport_test_card(),
        html_file=str(target),
    )

    assert result["ok"] is True
    assert result["html_file"] == str(target)
    assert target.is_file()
    assert "<html" in target.read_text(encoding="utf-8").lower()
    assert calls[0][0] == "file"
    assert calls[1][0] == "card"

    """The live shell must wait on its own card subscription, not a second one."""
    home = make_home(tmp_path)
    sent: list[dict] = []

    def fake_send(*, owner, html, card, html_file=None):
        sent.append({"owner": owner, "html": html, "card": card, "html_file": html_file})
        return {"ok": True, "html_message_id": "om_html_1", "card_message_id": "om_card_1"}

    class FakeSurface:
        def __init__(self):
            self.waited = 0

        def bind_lifecycle(self, service):
            return None

        def start(self):
            return {"ok": True}

        def stop(self):
            return {"ok": True}

        def wait_card(self, *, timeout_seconds=300, identity="bot"):
            self.waited += 1
            return {
                "ok": True,
                "reason": "ok",
                "events": [
                    {
                        "message_id": "om_card_1",
                        "operator_id": "ou_user",
                        "action_name": console_init.TRANSPORT_ACTION,
                    }
                ],
            }

    surface = FakeSurface()
    monkeypatch.setattr(console_init, "send_transport_test", fake_send)
    monkeypatch.setattr(
        console_init.lark,
        "user_open_id",
        lambda cli, runner=None: {"ok": True, "open_id": "ou_user"},
    )
    lines = iter(["/horizon-init", "/exit"])
    out = io.StringIO()

    result = console.serve(
        home,
        isatty=lambda: True,
        reader=lambda prompt: next(lines),
        out=out,
        set_title=lambda title: True,
        start_worker=lambda function: None,
        events=surface,
    )

    assert result["ok"] is True
    assert surface.waited == 1
    assert len(sent) == 1
    assert intents.verified_owner(home) == "ou_user"
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["transport"]["status"] == "ok"
