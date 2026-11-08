"""Offline tests for the Console service lifecycle (issue #81, ADR 0009 §6).

The Console is the only scheduler.  These tests drive the lifecycle through its
public seams with an injected clock and injected daily/agent runners — no real
``dsh``, no ``lark-cli``, no network and no real TTY:

- one push per day at the configured time, never twice;
- catch-up at start/resume after the time has passed, with missed days coalesced;
- pause/resume: persisted state, no delivery while paused, pause-only replies;
- reopen restore: paused stays paused, a running session auto-starts;
- window close: terminate the in-flight Agent run, mark the request unfinished,
  release the executor lease and persist ``stopped``;
- the ``console control start|stop|status|push`` control file: consumed within
  one beat, initiator recorded in the event stream, expired/duplicate ignored.

The real Windows close button and a real scheduled Agent run stay ``untested``.
"""

from __future__ import annotations

import io
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from common import read_json, write_json
from hostrun import cli, console, console_events, console_service, executor
from question_queue.store import QueueStore

TZ = timezone(timedelta(hours=8))


def test_default_daily_runner_matches_the_injected_seam(tmp_path, monkeypatch):
    """The default runner takes the same ``(home, intent, **kwargs)`` shape.

    Regression found during #91 real acceptance: with no injected runner the
    Console called ``intents.run`` through a one-argument default and every real
    scheduled push failed with ``TypeError``.
    """
    from hostrun import intents

    calls = []

    def fake_run(home, intent, **kwargs):
        calls.append((Path(home), intent, kwargs))
        return {"ok": True, "brief": "BRIEF"}

    monkeypatch.setattr(intents, "run", fake_run)

    result = console_service._default_run_daily(
        tmp_path, "daily", host="dsh", lease=False
    )

    assert calls == [(tmp_path, "daily", {"host": "dsh", "lease": False})]
    assert result == {"ok": True, "brief": "BRIEF"}


# ------------------------------------------------------------------ fixtures
class Clock:
    """A settable aware clock; the scheduler never reads the wall clock."""

    def __init__(self, moment: datetime):
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def set(self, moment: datetime) -> None:
        self.moment = moment


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "runtime"
    (home / "install").mkdir(parents=True)
    write_json(
        home / "install" / "config.json",
        {
            "schema_version": 1,
            "host": {"product": "dsh"},
            "timezone": "Asia/Shanghai",
            "paths": {"repo": str(tmp_path / "checkout")},
        },
    )
    (tmp_path / "checkout").mkdir(parents=True, exist_ok=True)
    return home


class Daily:
    """A fake ``intents.run('daily')`` that records the call and opens a request."""

    def __init__(self, brief: str | None = "TASK-BRIEF"):
        self.calls: list[tuple[str, dict]] = []
        self.brief = brief

    def __call__(self, home, intent, **kwargs):
        self.calls.append((intent, kwargs))
        store = QueueStore(Path(home))
        store.init()
        store.request_push("scheduled")
        store.try_start(20)
        return {
            "ok": True,
            "intent": intent,
            "brief": self.brief,
            "next": "done",
            "actions": ["request:daily"],
        }


class Agent:
    def __init__(self, result=None):
        self.calls: list[tuple[str, dict]] = []
        self.result = result or {"ok": True, "reason": "ok", "stopped": False}

    def __call__(self, home, task, **kwargs):
        self.calls.append((task, kwargs))
        return dict(self.result)


def _service(home, clock, daily=None, agent=None, **kwargs):
    return console_service.Service(
        home,
        clock=clock,
        run_daily=daily or Daily(),
        run_agent=agent or Agent(),
        start_worker=lambda function: function(),
        ticker_worker=lambda function: None,
        status_provider=lambda repo: {
            "executor": executor.status(repo),
            "queue": {"items": 0, "unfinished": False, "failed": False},
            "install": {"steps": {"knowledge": "ok"}},
        },
        **kwargs,
    )


def _events(home) -> str:
    return console.log_path(home).read_text(encoding="utf-8")


# ---------------------------------------------------------------- daily slot
def test_scheduled_time_fires_one_push_per_day(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 8, 0, tzinfo=TZ))
    daily = Daily()
    service = _service(home, clock, daily=daily)

    first = service.tick()
    second = service.tick()

    assert first["pushed"] == "scheduled"
    assert second["pushed"] is None
    assert len(daily.calls) == 1
    assert console.read_state(home)["last_push_date"] == "2026-10-30"


def test_nothing_is_pushed_before_the_configured_time(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 7, 30, tzinfo=TZ))
    daily = Daily()
    service = _service(home, clock, daily=daily)

    result = service.tick()

    assert result["pushed"] is None
    assert daily.calls == []
    assert console.read_state(home)["next_push_at"].startswith("2026-10-30T08:00:00")


def test_the_configured_push_time_is_honoured(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 8, 30, tzinfo=TZ))
    console.write_state(home, push_time="09:15")
    daily = Daily()
    service = _service(home, clock, daily=daily)

    service.tick()
    assert daily.calls == []

    clock.set(datetime(2026, 10, 30, 9, 15, tzinfo=TZ))
    service.tick()
    assert len(daily.calls) == 1


# ----------------------------------------------------------------- catch-up
def test_start_after_the_time_catches_up_once(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 11, 0, tzinfo=TZ))
    daily = Daily()
    service = _service(home, clock, daily=daily)

    service.tick()
    service.tick()

    assert len(daily.calls) == 1
    assert console.read_state(home)["last_push_date"] == "2026-10-30"


def test_missed_days_coalesce_into_one_catch_up(tmp_path):
    home = _home(tmp_path)
    console.write_state(home, last_push_date="2026-10-25")
    clock = Clock(datetime(2026, 10, 30, 11, 0, tzinfo=TZ))
    daily = Daily()
    service = _service(home, clock, daily=daily)

    service.tick()
    service.tick()

    assert len(daily.calls) == 1


def test_a_new_day_fires_again(tmp_path):
    home = _home(tmp_path)
    console.write_state(home, last_push_date="2026-10-30")
    clock = Clock(datetime(2026, 10, 31, 8, 0, tzinfo=TZ))
    daily = Daily()
    service = _service(home, clock, daily=daily)

    service.tick()

    assert len(daily.calls) == 1
    assert console.read_state(home)["last_push_date"] == "2026-10-31"


# ------------------------------------------------------------ pause / resume
def test_paused_service_never_dispatches_and_resume_catches_up(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 9, 0, tzinfo=TZ))
    daily = Daily()
    service = _service(home, clock, daily=daily)

    service.pause()
    paused = service.tick()
    pushed = service.request_push("manual")

    assert paused["pushed"] is None
    assert daily.calls == []
    assert pushed["ok"] is False and pushed["reason"] == "service_paused"
    state = console.read_state(home)
    assert state["paused"] is True and state["service"] == "paused"
    assert state["next_push_at"] is None

    service.start()
    service.tick()

    assert len(daily.calls) == 1
    assert console.read_state(home)["paused"] is False


def test_start_and_pause_persist_the_service_state(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 7, 0, tzinfo=TZ))
    service = _service(home, clock)

    service.start()
    running = console.read_state(home)
    service.pause()
    paused = console.read_state(home)

    assert running["service"] == "running" and running["paused"] is False
    assert paused["service"] == "paused" and paused["paused"] is True


# --------------------------------------------------------------- restore
def test_reopen_restores_paused_without_dispatching(tmp_path):
    home = _home(tmp_path)
    console.write_state(home, service="paused", paused=True)
    clock = Clock(datetime(2026, 10, 30, 11, 0, tzinfo=TZ))
    daily = Daily()
    service = _service(home, clock, daily=daily)

    restored = service.restore()
    service.tick()

    assert restored["paused"] is True
    assert console.read_state(home)["paused"] is True
    assert daily.calls == []


def test_reopen_of_a_running_session_auto_starts_and_catches_up(tmp_path):
    home = _home(tmp_path)
    console.write_state(home, service="stopped", paused=False)
    clock = Clock(datetime(2026, 10, 30, 11, 0, tzinfo=TZ))
    daily = Daily()
    service = _service(home, clock, daily=daily)

    restored = service.restore()
    service.tick()

    assert restored["paused"] is False
    assert console.read_state(home)["service"] == "running"
    assert len(daily.calls) == 1


# -------------------------------------------------------------- shutdown
def test_window_close_terminates_the_run_and_marks_the_request_unfinished(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 9, 0, tzinfo=TZ))
    entered = threading.Event()
    observed: dict = {}

    def blocking_agent(repo, task, **kwargs):
        observed["should_stop"] = kwargs.get("should_stop")
        entered.set()
        while not kwargs["should_stop"]():
            threading.Event().wait(0.01)
        return {"ok": False, "reason": "stopped", "stopped": True}

    service = console_service.Service(
        home,
        clock=clock,
        run_daily=Daily(),
        run_agent=blocking_agent,
        status_provider=lambda repo: {},
    )

    service.request_push("manual")
    assert entered.wait(2), "the fake Agent run did not start"
    summary = service.shutdown()

    assert summary["ok"] is True
    request = read_json(QueueStore(home).request_path)
    assert request is not None, "the unfinished request must be preserved"
    assert request["attempts"] == 1
    assert request["last_error"] == "console_shutdown"
    assert executor.status(home)["state"] == "free"
    state = console.read_state(home)
    assert state["service"] == "stopped"
    assert "在飞" in _events(home) or "未完成" in _events(home)


def _failed_request(home: Path, reason: str) -> None:
    store = QueueStore(home)
    store.init()
    store.request_push("manual")
    store.try_start(20)
    store.finish_start(False, reason)


def test_a_failed_push_is_reported_but_not_replayed_on_the_next_start(tmp_path):
    """S3/US35: only a console-shutdown request may be recovered automatically."""
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 7, 0, tzinfo=TZ))
    _failed_request(home, "child_failed")
    daily = Daily()
    agent = Agent()
    service = _service(home, clock, daily=daily, agent=agent)

    tick = service.tick()

    assert tick["pushed"] is None
    assert daily.calls == []
    assert agent.calls == []
    text = console.status_text(console.status_snapshot(home))
    assert "child_failed" in text
    assert "不会自动重试" in text


def test_a_console_shutdown_request_is_recovered_once(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 7, 0, tzinfo=TZ))
    _failed_request(home, "console_shutdown")
    daily = Daily()
    service = _service(home, clock, daily=daily)

    tick = service.tick()

    assert tick["pushed"] == "recovery"
    assert len(daily.calls) == 1


def test_a_push_requested_during_a_run_is_drained_after_it_finishes(tmp_path):
    """S4: a second 推一篇 must not wait for a console restart."""
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 6, 0, tzinfo=TZ))
    daily = Daily()
    service = _service(home, clock, daily=daily)

    first = service.request_push("manual")
    assert first["ok"] is True
    assert len(daily.calls) == 1
    # The second request is recorded while the first run holds the lease, then
    # the successful finish preserves it as the next queued request.
    store = QueueStore(home)
    store.init()
    store.request_push("feishu")
    store.finish_start(True)
    preserved = read_json(store.request_path)
    assert preserved is not None
    assert preserved["started_at"] is None

    tick = service.tick()

    assert tick["pushed"] == "queued"
    assert len(daily.calls) == 2


# ------------------------------------------------------------ control file
def test_control_request_is_consumed_within_one_tick_and_names_the_initiator(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 7, 0, tzinfo=TZ))
    service = _service(home, clock)

    request = console_service.write_control(
        home, "stop", initiator="host-agent", clock=clock
    )
    result = service.tick()

    assert result["control"]["action"] == "stop"
    assert console.read_state(home)["paused"] is True
    log = _events(home)
    assert "host-agent" in log
    response = console_service.read_control_response(home)
    assert response["nonce"] == request["nonce"]
    assert response["ok"] is True
    assert not console_service.control_path(home).exists()


def test_control_push_fires_immediately_even_before_the_daily_time(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 7, 0, tzinfo=TZ))
    daily = Daily()
    service = _service(home, clock, daily=daily)

    console_service.write_control(home, "push", initiator="host-agent", clock=clock)
    service.tick()

    assert len(daily.calls) == 1


def test_duplicate_control_request_is_ignored(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 7, 0, tzinfo=TZ))
    daily = Daily()
    service = _service(home, clock, daily=daily)

    request = console_service.write_control(
        home, "push", initiator="host-agent", clock=clock
    )
    service.tick()
    console_service.write_control(
        home,
        "push",
        initiator="host-agent",
        clock=clock,
        nonce=request["nonce"],
    )
    result = service.tick()

    assert len(daily.calls) == 1
    assert result["control"]["action"] == "duplicate"
    assert "重复" in _events(home)


def test_expired_control_request_is_ignored(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 7, 0, tzinfo=TZ))
    daily = Daily()
    service = _service(home, clock, daily=daily)

    console_service.write_control(
        home,
        "push",
        initiator="host-agent",
        clock=clock,
        requested_at="2026-10-29T07:00:00+08:00",
    )
    result = service.tick()

    assert daily.calls == []
    assert result["control"]["action"] == "expired"
    assert "过期" in _events(home)


def test_control_status_writes_a_response_for_the_caller(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 7, 0, tzinfo=TZ))
    service = _service(home, clock)
    service.start()

    request = console_service.write_control(
        home, "status", initiator="host-agent", clock=clock
    )
    service.tick()

    response = console_service.read_control_response(home)
    assert response["nonce"] == request["nonce"]
    assert response["result"]["service"]["state"] == "running"
    assert response["result"]["service"]["next_push_at"]


# ------------------------------------------------- pause-aware Feishu replies
def test_paused_message_replies_only_the_pause_notice(tmp_path):
    home = _home(tmp_path)
    console.write_state(home, paused=True)
    sent: list[tuple[str, str]] = []

    outcome = console_events.handle_message(
        home,
        {
            "chat_type": "p2p",
            "sender_type": "user",
            "sender_id": "ou_owner",
            "text": "推一篇",
        },
        owner="ou_owner",
        send=lambda target, text: sent.append((target, text)) or {"ok": True},
    )

    assert outcome["action"] == "paused"
    assert sent == [("ou_owner", console_events.PAUSED_TEXT)]
    # #82 removed MESSAGE_ACK_TEXT; the non-pause fallback receipt is now the
    # intent-pending text, and the pause notice must stay distinct from it.
    assert console_events.PAUSED_TEXT != console_events.INTENT_PENDING_TEXT


def test_paused_service_is_recoverable_with_the_feishu_resume_instruction(tmp_path):
    """S2: `恢复` is an exact instruction and must still work while paused."""
    home = _home(tmp_path)
    console.write_state(home, paused=True)
    sent: list[tuple[str, str]] = []
    executed: list[str] = []

    def execute(home, operation, params, **kwargs):
        executed.append(operation)
        console.write_state(home, service="running", paused=False)
        return {"ok": True, "text": "服务已恢复。"}

    outcome = console_events.handle_message(
        home,
        {
            "chat_type": "p2p",
            "sender_type": "user",
            "sender_id": "ou_owner",
            "text": "恢复",
        },
        owner="ou_owner",
        send=lambda target, text: sent.append((target, text)) or {"ok": True},
        execute=execute,
    )

    assert executed == ["resume"]
    assert outcome["operation"] == "resume"
    assert sent == [("ou_owner", "服务已恢复。")]
    assert console.read_state(home)["paused"] is False


def test_paused_card_callback_does_not_derive_a_run(tmp_path):
    home = _home(tmp_path)
    console.write_state(home, paused=True)
    handled: list[dict] = []

    outcome = console_events.handle_card(
        home,
        {"action_name": "rating", "operator_id": "ou_owner"},
        handle=lambda repo, event: handled.append(event) or {"ok": True},
    )

    assert outcome["skipped"] == "service_paused"
    assert handled == []


# --------------------------------------------------------------- CLI surface
def test_cli_console_control_writes_the_control_file(tmp_path, capsys):
    code = tmp_path / "checkout"
    archive = tmp_path / "papers"
    code.mkdir()
    archive.mkdir()

    rc = cli.main(
        [
            "--code",
            str(code),
            "--archive",
            str(archive),
            "console",
            "control",
            "stop",
            "--by",
            "host-agent",
            "--wait",
            "0",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["action"] == "stop"
    request = read_json(console_service.control_path(code.resolve() / ".horizon"))
    assert request["action"] == "stop"
    assert request["initiator"] == "host-agent"


def test_cli_status_without_a_console_still_answers_from_persisted_state(tmp_path, capsys):
    code = tmp_path / "checkout"
    archive = tmp_path / "papers"
    code.mkdir()
    archive.mkdir()
    runtime = code.resolve() / ".horizon"
    console.write_state(runtime, service="paused", paused=True)

    rc = cli.main(
        [
            "--code",
            str(code),
            "--archive",
            str(archive),
            "console",
            "control",
            "status",
            "--wait",
            "0",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["consumed"] is False
    assert payload["status"]["service"]["paused"] is True


# ---------------------------------------------------------- slash commands
def test_horizon_start_and_stop_flip_the_persisted_service_state(tmp_path):
    home = _home(tmp_path)

    stopped = console.dispatch(home, "/horizon-stop")
    assert stopped["ok"] is True
    assert console.read_state(home)["paused"] is True

    started = console.dispatch(home, "/horizon-start")
    assert started["ok"] is True
    assert console.read_state(home)["paused"] is False


def test_horizon_push_uses_the_service_and_reports_paused(tmp_path):
    home = _home(tmp_path)
    console.write_state(home, paused=True)

    result = console.dispatch(home, "/horizon-push")

    assert result["ok"] is False
    assert "暂停" in result["text"]


# ---------------------------------------------------------- serve integration
def _status(repo):
    return {
        "executor": executor.status(repo),
        "queue": {"items": 0, "unfinished": False, "failed": False},
        "install": {"steps": {"knowledge": "ok"}},
    }


def _no_worker(function):
    """Never start a background thread in the serve-level shell tests."""
    return None


def test_closing_the_console_keeps_the_paused_state_for_the_next_open(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 9, 0, tzinfo=TZ))
    daily = Daily()
    service = _service(home, clock, daily=daily)
    reader = iter(["/horizon-stop", "/exit"])

    console.serve(
        home,
        isatty=lambda: True,
        reader=lambda prompt: next(reader),
        out=io.StringIO(),
        status=_status,
        set_title=lambda title: None,
        start_worker=_no_worker,
        service=service,
    )

    state = console.read_state(home)
    assert state["service"] == "stopped"
    assert state["paused"] is True

    reopened = _service(home, clock, daily=daily)
    reopened.restore()
    reopened.tick()
    assert daily.calls == []
    assert console.read_state(home)["paused"] is True


def test_console_launcher_forwards_a_control_argument(tmp_path):
    code = tmp_path / "checkout"
    archive = tmp_path / "papers"
    console.ensure_script(code, archive, "C:/python/python.exe")

    text = (code / "bin" / "horizon-console.cmd").read_text(encoding="utf-8")

    assert text.rstrip().endswith("console %*")


def test_push_time_can_be_changed_and_is_persisted(tmp_path):
    home = _home(tmp_path)
    clock = Clock(datetime(2026, 10, 30, 7, 0, tzinfo=TZ))

    result = console_service.set_push_time(home, "09:30", clock=clock)

    assert result["ok"] is True
    assert console.read_state(home)["push_time"] == "09:30"
    assert console.read_state(home)["next_push_at"].startswith("2026-10-30T09:30:00")
    invalid = console_service.set_push_time(home, "25:00", clock=clock)
    assert invalid["ok"] is False
