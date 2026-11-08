"""Offline tests for the Console shell (issue #79, ADR 0009 §1/§3).

The Console is the single resident frontend.  These tests drive its public
surface without a real TTY, a real ``dsh``, a network or a second process:

- the slash-command parser and dispatcher (known, unknown, stub),
- ``/horizon-status`` backed by ``intents.status`` and the persisted console
  state (never a fresh probe),
- ``/horizon-logs`` tailing the most recent ``logs/agent-<run_id>.log``,
- the linear event stream (timestamp, level, source) and the rolling log,
- the single-instance lock and the non-TTY refusal,
- the ``<code>/bin/horizon-console.cmd`` launcher (title and argv fixed).

The real Windows console window, the real 60-second heartbeat cadence and a
real double-click startup stay ``untested``; the ticket report records that.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from common import file_lock
from hostrun import cli, console


# ------------------------------------------------------------------ fixtures
def _home(tmp_path: Path) -> Path:
    home = tmp_path / "runtime"
    (home / "install").mkdir(parents=True)
    return home


def _resolved(tmp_path: Path) -> dict:
    code = tmp_path / "checkout"
    archive = tmp_path / "papers"
    return {
        "mode": "code",
        "code": str(code),
        "runtime": str(code / ".horizon"),
        "home": str(code / ".horizon"),
        "archive": str(archive),
    }


def _status(**overrides) -> dict:
    base = {
        "executor": {"state": "free"},
        "queue": {"items": 0, "unfinished": False, "failed": False},
        "install": {"steps": {"knowledge": "ok"}},
    }
    base.update(overrides)
    return base


def _quiet(title=None) -> bool:
    return True


def _no_worker(function) -> None:
    """Never start a background thread: the heartbeat cadence is live-only."""
    return None


# --------------------------------------------------------------- command parser
def test_known_console_commands_are_recognised():
    for name in (
        "/horizon-help",
        "/horizon-status",
        "/horizon-logs",
        "/exit",
    ):
        parsed = console.parse_command(f"  {name}  ")
        assert parsed["ok"] is True, name
        assert parsed["command"] == name


def test_unknown_console_command_is_rejected_with_the_command_name():
    parsed = console.parse_command("/horizon-delete-everything")

    assert parsed["ok"] is False
    assert parsed["reason"] == "unknown_command"
    assert parsed["command"] == "/horizon-delete-everything"


def test_non_slash_and_blank_input_give_usage_instead_of_executing():
    blank = console.parse_command("   ")
    text = console.parse_command("推一篇")

    assert blank["ok"] is False
    assert blank["reason"] == "empty"
    assert text["ok"] is False
    assert text["reason"] == "unknown_command"


def test_unknown_command_prints_a_usage_hint_and_runs_nothing(tmp_path):
    home = _home(tmp_path)
    called: list[Path] = []

    def status(repo):
        called.append(repo)
        return _status()

    result = console.dispatch(home, "/horizon-nope", status=status)

    assert result["ok"] is False
    assert result["reason"] == "unknown_command"
    assert "/horizon-help" in result["text"]
    assert called == []


def test_help_lists_every_console_command(tmp_path):
    text = console.dispatch(_home(tmp_path), "/horizon-help")["text"]

    for name in console.COMMANDS:
        assert name in text


# --------------------------------------------------------------- status query
def test_horizon_status_reports_service_next_push_and_queue(tmp_path):
    home = _home(tmp_path)
    console.write_state(
        home,
        paused=False,
        next_push_at="2026-10-31T08:00:00+08:00",
    )

    def status(repo):
        return _status(
            queue={"items": 2, "unfinished": True, "failed": False},
            executor={"state": "free"},
        )

    result = console.dispatch(home, "/horizon-status", status=status)

    assert result["ok"] is True
    assert "服务：运行中" in result["text"]
    assert "暂停：否" in result["text"]
    assert "下一次推送：2026-10-31T08:00:00+08:00" in result["text"]
    assert "队列：2 项" in result["text"]
    assert "未完成：是" in result["text"]
    assert "执行端：free" in result["text"]


def test_horizon_status_shows_a_paused_service_and_an_unscheduled_push(tmp_path):
    home = _home(tmp_path)
    console.write_state(home, paused=True)

    text = console.dispatch(home, "/horizon-status", status=lambda repo: _status())["text"]

    assert "服务：已暂停" in text
    assert "暂停：是" in text
    assert "下一次推送：未排期" in text


# ----------------------------------------------------------------- log tail
def test_horizon_logs_tails_the_latest_agent_run(tmp_path):
    home = _home(tmp_path)
    logs = home / "logs"
    logs.mkdir()
    old = logs / "agent-old.log"
    new = logs / "agent-new.log"
    old.write_text("old-1\nold-2\n", encoding="utf-8")
    new.write_text("a\nb\nc\n", encoding="utf-8")
    os.utime(old, (100, 100))
    os.utime(new, (200, 200))

    tail = console.tail_agent_log(home, lines=2)

    assert tail["ok"] is True
    assert tail["path"] == str(new)
    assert tail["lines"] == ["b", "c"]


def test_horizon_logs_reports_when_no_agent_run_exists(tmp_path):
    home = _home(tmp_path)

    tail = console.tail_agent_log(home)

    assert tail["ok"] is False
    assert tail["reason"] == "no_agent_log"
    assert "暂无" in tail["text"]


def test_horizon_logs_slash_command_prints_the_tail(tmp_path):
    home = _home(tmp_path)
    logs = home / "logs"
    logs.mkdir()
    (logs / "agent-only.log").write_text("line-1\nline-2\n", encoding="utf-8")

    result = console.dispatch(home, "/horizon-logs", status=lambda repo: _status())

    assert result["ok"] is True
    assert "line-2" in result["text"]
    assert "line-1" in result["text"]


# --------------------------------------------------------------- event stream
def test_event_stream_writes_timestamp_level_and_source_to_terminal_and_log(tmp_path):
    home = _home(tmp_path)
    out = io.StringIO()

    event = console.emit_event(
        home,
        "info",
        "console",
        "控制台已启动",
        at="2026-10-30T10:00:00+08:00",
        out=out,
    )

    line = "2026-10-30T10:00:00+08:00 INFO [console] 控制台已启动"
    assert event["line"] == line
    assert out.getvalue().strip() == line
    written = console.log_path(home).read_text(encoding="utf-8")
    assert written.strip() == line


# ------------------------------------------------------------ startup script
def test_console_start_script_is_written_in_the_code_directory(tmp_path):
    code = tmp_path / "checkout"
    archive = tmp_path / "papers"
    python = "C:/python/python.exe"

    first = console.ensure_script(code, archive, python)
    path = code / "bin" / "horizon-console.cmd"
    text = path.read_text(encoding="utf-8")

    assert first["written"] is True
    assert path == console.script_path(code)
    assert text.startswith("title horizon-console\n")
    assert f'cd /d "{code}"' in text
    assert f'-m hostrun.cli --code "{code}" --archive "{archive}" console' in text

    second = console.ensure_script(code, archive, python)
    assert second["written"] is False


# --------------------------------------------------------- single instance lock
def test_second_console_is_refused_by_the_single_instance_lock(tmp_path):
    home = _home(tmp_path)

    with file_lock(console.lock_path(home), blocking=False) as acquired:
        assert acquired
        result = console.serve(
            home,
            isatty=lambda: True,
            reader=lambda prompt: pytest.fail("the second console must not prompt"),
            out=io.StringIO(),
            status=lambda repo: _status(),
            set_title=_quiet,
            start_worker=_no_worker,
        )

    assert result["ok"] is False
    assert result["reason"] == "already_running"
    assert "已在运行" in result["note"]


# --------------------------------------------------------------- serve loop
def test_console_serves_a_status_command_then_exits_cleanly(tmp_path):
    home = _home(tmp_path)
    resolved = _resolved(tmp_path)
    code = Path(resolved["code"])
    reader = iter(["/horizon-status", "/exit"])
    out = io.StringIO()

    result = console.serve(
        home,
        resolved=resolved,
        isatty=lambda: True,
        reader=lambda prompt: next(reader),
        out=out,
        status=lambda repo: _status(),
        set_title=_quiet,
        start_worker=_no_worker,
    )

    assert result["ok"] is True
    assert result["reason"] == "stopped"
    assert result["commands"] == 2
    assert (code / "bin" / "horizon-console.cmd").is_file()
    rendered = out.getvalue()
    assert "horizon>" in rendered
    assert "服务：运行中" in rendered
    assert "正在退出控制台" in rendered

    log = console.log_path(home).read_text(encoding="utf-8")
    assert "[console] 控制台已启动" in log
    assert "/horizon-status" in log

    beat = json.loads(console.heartbeat_path(home).read_text(encoding="utf-8"))
    assert beat["state"] == "stopped"
    assert beat["pid"] == os.getpid()
    assert console.read_state(home)["service"] == "stopped"

    # The lock is released, so the next console can start.
    with file_lock(console.lock_path(home), blocking=False) as acquired:
        assert acquired


def test_console_writes_a_running_heartbeat_and_service_state_before_the_prompt(tmp_path):
    home = _home(tmp_path)
    observed: dict = {}

    def reader(prompt):
        observed["beat"] = json.loads(console.heartbeat_path(home).read_text(encoding="utf-8"))
        observed["state"] = console.read_state(home)
        raise EOFError

    console.serve(
        home,
        isatty=lambda: True,
        reader=reader,
        out=io.StringIO(),
        status=lambda repo: _status(),
        set_title=_quiet,
        start_worker=_no_worker,
    )

    assert observed["beat"]["state"] == "running"
    assert observed["beat"]["pid"] == os.getpid()
    assert observed["state"]["service"] == "running"


def test_console_sets_the_fixed_window_title_before_the_prompt(tmp_path):
    home = _home(tmp_path)
    titles: list[str] = []

    def reader(prompt):
        raise EOFError

    console.serve(
        home,
        isatty=lambda: True,
        reader=reader,
        out=io.StringIO(),
        status=lambda repo: _status(),
        set_title=titles.append,
        start_worker=_no_worker,
    )

    assert titles == [console.WINDOW_TITLE]
    assert console.WINDOW_TITLE == "horizon-console"


def test_eof_stops_the_console_without_an_error(tmp_path):
    home = _home(tmp_path)

    def reader(prompt):
        raise EOFError

    result = console.serve(
        home,
        isatty=lambda: True,
        reader=reader,
        out=io.StringIO(),
        status=lambda repo: _status(),
        set_title=_quiet,
        start_worker=_no_worker,
    )

    assert result["ok"] is True
    assert result["reason"] in ("eof", "stopped")


def test_non_tty_is_rejected_with_the_start_script_hint(tmp_path):
    home = _home(tmp_path)
    resolved = _resolved(tmp_path)
    out = io.StringIO()

    result = console.serve(
        home,
        resolved=resolved,
        isatty=lambda: False,
        reader=lambda prompt: pytest.fail("a non-TTY console must not prompt"),
        out=out,
        status=lambda repo: _status(),
        set_title=_quiet,
        start_worker=_no_worker,
    )

    assert result["ok"] is False
    assert result["reason"] == "not_a_tty"
    assert "horizon-console.cmd" in result["note"]
    assert not console.lock_path(home).exists()
    assert console.read_state(home)["service"] != "running"


# ------------------------------------------------------------------ CLI wiring
def test_cli_exposes_the_console_command():
    args = cli.build_parser().parse_args(["console"])

    assert args.cmd == "console"


def test_cli_dispatches_console_with_the_resolved_layout(tmp_path, monkeypatch):
    code = tmp_path / "checkout"
    archive = tmp_path / "papers"
    captured: dict = {}

    def fake_serve(home, **kwargs):
        captured["home"] = Path(home)
        captured.update(kwargs)
        return {"ok": True, "reason": "stopped", "commands": 0}

    monkeypatch.setattr(console, "serve", fake_serve)

    rc = cli.main(["--code", str(code), "--archive", str(archive), "console"])

    assert rc == 0
    assert captured["home"] == code.resolve() / ".horizon"
    assert captured["resolved"]["archive"] == str(archive.resolve())
