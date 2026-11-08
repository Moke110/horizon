"""Offline tests for console ``/horizon-init`` (issue #84, ADR 0009 §7).

The step machine is driven through its injectable seams only: a fake line reader,
a fake folder dialog, a fake write probe and an explicit TTY answer.  No real
terminal, no real Windows shell dialog, no network and no ``dsh`` run is touched.

The initialization state lives in the code directory's runtime area
(``<code>/.horizon/install/initial/state.json``), separately from
``install.STEPS``; these tests assert that separation and the resume behaviour.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from common import write_json
from hostrun import console, console_init, facilities, install, lark


# ------------------------------------------------------------------ fixtures
class Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def ready_lark_runner():
    """A fake ``lark-cli`` that is installed, logged in and fully authorized.

    #84's tests are about the archive/agent steps; #85 adds the Lark step right
    after them, so every full run needs a ready CLI and never a real process.
    """
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


def make_home(tmp_path: Path, *, service: str = "stopped", permissions: dict | None = None):
    """A code directory with a runtime area and a recorded dsh + patch overlay."""
    code = tmp_path / "code"
    home = code / ".horizon"
    (home / "install").mkdir(parents=True)
    (code / "runtime").mkdir(parents=True)
    (code / "runtime/node.exe").write_text("")
    (code / "agent/dist").mkdir(parents=True)
    (code / "agent/dist/main.js").write_text("")
    from hostrun.embedded_agent import configure
    configure(home, provider="test", model="test-model", api_key="test-only")
    cli = home / "bin" / ("lark-cli.cmd" if os.name == "nt" else "lark-cli")
    cli.parent.mkdir(parents=True)
    cli.write_text("@echo off\n", encoding="utf-8")
    write_json(
        home / "install" / "config.json",
        {
            "schema_version": 1,
            "host": {"product": "horizon"},
            "paths": {"repo": str(code), "lark_cli": str(cli)},
            "permissions": permissions
            if permissions is not None
            else {"scope": ["base:read"], "host_scope": ["filesystem"]},
        },
    )
    if service != "stopped":
        console.write_state(home, service=service, paused=service == "paused")
    return home


def record_remaining_steps(home: Path) -> None:
    """#87's steps live in ``test_hostrun_console_init_completion.py``; mark them done.

    These tests exercise the archive/agent/Lark/transport steps and their resume
    behaviour, so the naming, push-time and knowledge decisions are recorded as
    completed instead of being re-asked on every run.
    """
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
    console_init.record_step(
        home, "push_time", {"push_time": "08:00", "next_push_at": None}
    )
    console_init.record_step(
        home,
        "knowledge",
        {"choice": "later", "gate": "blocked", "reason": "knowledge_not_ready"},
    )
    console.write_state(home, knowledge_ready=False)


def mark_initialized(home: Path) -> None:
    """Record every init step and the completion flag (the #23 running rule)."""
    for step_id in console_init.STEP_IDS:
        console_init.record_step(home, step_id, {})
    console_init.mark_complete(home, knowledge="later", archive="")


def reader(*answers: str):
    """A line reader that yields the scripted answers then reports EOF."""
    remaining = list(answers)

    def read(prompt: str = "") -> str:
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    return read


def transport_send(*, owner, html, card, html_file=None):
    """A fake transport sender: no message is ever sent in a test."""
    return {"ok": True, "html_message_id": "om_test_html", "card_message_id": "om_test_card"}


def transport_events(**_kwargs):
    """A fake card callback for the fake card, clicked by the logged-in user."""
    return {
        "ok": True,
        "reason": "ok",
        "events": [
            {
                "message_id": "om_test_card",
                "operator_id": "ou_user",
                "action_name": console_init.TRANSPORT_ACTION,
            }
        ],
    }


def init_run(home, answers, **kwargs):
    out = io.StringIO()
    kwargs.setdefault("isatty", lambda: True)
    kwargs.setdefault("pick_folder", lambda default: None)
    kwargs.setdefault("lark_runner", ready_lark_runner())
    kwargs.setdefault("transport_send", transport_send)
    kwargs.setdefault("transport_events", transport_events)
    result = console_init.run(
        home,
        reader=reader(*answers),
        out=out,
        **kwargs,
    )
    return result, out.getvalue()


# -------------------------------------------------------------- run guards
def test_init_is_refused_while_the_service_is_running(tmp_path):
    home = make_home(tmp_path, service="running")
    mark_initialized(home)

    result, rendered = init_run(home, [""])

    assert result["ok"] is False
    assert result["reason"] == "service_running"
    assert "先停止服务" in result["text"]
    assert "/horizon-stop" in result["text"]
    # A refusal changes nothing and asks nothing.
    assert console_init.is_complete(home) is True
    assert rendered == ""


def test_a_fresh_console_start_does_not_block_the_first_init(tmp_path):
    """B1: a new install starts the console (service=running) before any init."""
    home = make_home(tmp_path)
    observed: dict = {}

    def reader(prompt):
        observed["state"] = console.read_state(home)
        raise EOFError()

    console.serve(
        home,
        isatty=lambda: True,
        reader=reader,
        out=io.StringIO(),
        status=lambda repo: {},
        set_title=lambda title: None,
        start_worker=lambda function: None,
    )
    assert observed["state"]["service"] == "running"
    assert console_init.is_complete(home) is False
    # The console is resident again while the first /horizon-init runs.
    console.write_state(home, service="running", paused=False)
    record_remaining_steps(home)

    class StartService:
        def start(self) -> dict:
            return {"ok": True, "action": "start", "service": "running"}

    result, _rendered = init_run(home, ["", ""], service=StartService())

    assert result["ok"] is True
    assert result["state"]["complete"] is True
    assert console_init.is_complete(home) is True


def test_init_is_allowed_while_the_service_is_paused(tmp_path):
    home = make_home(tmp_path, service="paused")
    record_remaining_steps(home)

    result, _rendered = init_run(home, ["", ""])

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


def test_init_refuses_a_non_tty_with_the_launcher_hint(tmp_path):
    home = make_home(tmp_path)

    result, rendered = init_run(home, [""], isatty=lambda: False)

    assert result["ok"] is False
    assert result["reason"] == "not_a_tty"
    assert "horizon-console.cmd" in result["text"]
    assert rendered == ""
    assert not console_init.state_path(home).exists()


# --------------------------------------------------------- first run / state
def test_first_run_confirms_the_default_archive_then_the_agent(tmp_path):
    home = make_home(tmp_path)
    record_remaining_steps(home)
    default_archive = tmp_path / "horizon-archive"

    result, rendered = init_run(home, ["", ""])

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
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["archive"]["status"] == "ok"
    assert state["steps"]["archive"]["value"]["path"] == str(default_archive)
    assert state["steps"]["archive"]["value"]["default"] is True
    assert state["steps"]["agent"]["status"] == "ok"
    assert state["steps"]["agent"]["value"]["runtime"] == "horizon-pi"
    assert state["steps"]["agent"]["value"]["model"] == "test-model"
    # ADR §7 now has no unimplemented step: #87 finished the remaining three.
    assert result["next_step"] is None
    assert result["not_yet"] == []
    assert "尚未实现" not in rendered
    # The completion state is written and the service is started by init.
    assert state["complete"] is True
    assert state["completed_at"]
    assert console.read_state(home)["service"] == "running"
    # The agent confirmation shows the absolute dsh path, the --patch overlay
    # and the authorization scope before asking.
    assert "内置 Pi Agent" in rendered
    assert "test-model" in rendered
    assert "test" in rendered


def test_init_state_lives_in_the_code_runtime_area_not_install_steps(tmp_path):
    home = make_home(tmp_path)
    record_remaining_steps(home)

    init_run(home, ["", ""])

    assert console_init.state_path(home) == home / "install" / "initial" / "state.json"
    assert not (home / "install" / "state.json").exists()
    # install.STEPS is asserted by other tests; /horizon-init must not stretch it.
    assert "init" not in install.STEPS
    assert install.load_state(home)["steps"] == {}


# ------------------------------------------------------- archive directory choice
def test_y_uses_the_default_archive_directory(tmp_path):
    home = make_home(tmp_path)
    record_remaining_steps(home)

    def pick(default):  # pragma: no cover - must never open the dialog
        raise AssertionError("the folder dialog must not open for Y/y")

    result, rendered = init_run(home, ["y", ""], pick_folder=pick)

    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["archive"]["value"]["path"] == str(tmp_path / "horizon-archive")
    assert state["steps"]["archive"]["value"]["default"] is True
    assert result["completed"] == [
        "archive",
        "agent",
        "lark",
        "transport",
        "naming",
        "push_time",
        "knowledge",
    ]
    assert "默认资料目录" in rendered


def test_n_opens_the_folder_dialog_and_a_cancel_falls_back_to_the_default(tmp_path):
    home = make_home(tmp_path)
    record_remaining_steps(home)
    seen: list[str] = []

    def pick(default):
        seen.append(str(default))
        return None  # the user cancelled the system dialog

    result, rendered = init_run(home, ["n", ""], pick_folder=pick)

    assert seen == [str(tmp_path / "horizon-archive")]
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["archive"]["value"] == {
        "path": str(tmp_path / "horizon-archive"),
        "default": True,
        "outside_code": True,
    }
    assert "已取消" in rendered
    assert result["completed"] == [
        "archive",
        "agent",
        "lark",
        "transport",
        "naming",
        "push_time",
        "knowledge",
    ]


def test_a_directory_outside_the_code_directory_is_accepted_after_a_write_probe(tmp_path):
    home = make_home(tmp_path)
    record_remaining_steps(home)
    chosen = tmp_path / "custom-archive"
    chosen.mkdir()
    probed: list[Path] = []

    def probe(path):
        probed.append(Path(path))
        return {"ok": True, "path": str(path), "reason": ""}

    result, _rendered = init_run(home, ["n", ""], pick_folder=lambda default: str(chosen), write_probe=probe)

    assert probed == [chosen, chosen]
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["archive"]["value"] == {
        "path": str(chosen),
        "default": False,
        "outside_code": True,
    }
    assert result["completed"] == [
        "archive",
        "agent",
        "lark",
        "transport",
        "naming",
        "push_time",
        "knowledge",
    ]


def test_a_chosen_directory_inside_the_code_directory_is_refused(tmp_path):
    home = make_home(tmp_path)
    inside = home.parent / "papers"

    result, _rendered = init_run(home, ["n"], pick_folder=lambda default: str(inside))

    assert result["ok"] is False
    assert result["reason"] == "archive_invalid"
    assert "资料目录不能放在代码目录内" in result["text"]
    assert not console_init.state_path(home).exists()


def test_the_real_write_probe_accepts_a_writable_directory_and_removes_its_file(tmp_path):
    chosen = tmp_path / "papers"
    chosen.mkdir()

    result = console_init.probe_write(chosen)

    assert result["ok"] is True
    assert result["path"] == str(chosen)
    assert list(chosen.iterdir()) == []


def test_the_real_write_probe_reports_a_missing_directory(tmp_path):
    result = console_init.probe_write(tmp_path / "missing")

    assert result["ok"] is False
    assert result["reason"] == "not_a_directory"


def test_an_unwritable_outside_directory_asks_for_a_full_permission_restart(tmp_path):
    home = make_home(tmp_path)
    chosen = tmp_path / "readonly-archive"
    chosen.mkdir()

    result, _rendered = init_run(
        home,
        ["n"],
        pick_folder=lambda default: str(chosen),
        write_probe=lambda path: {"ok": False, "path": str(path), "reason": "unwritable", "error": "PermissionError"},
    )

    assert result["ok"] is False
    assert result["reason"] == "archive_unwritable"
    assert "资料目录不可写" in result["text"]
    assert str(home.parent / "bin" / "horizon-console.cmd") in result["text"]
    assert "以完整权限重启" in result["text"]
    assert "保留进度" in result["text"]
    # Nothing was recorded, so a later run starts the same step again.
    assert not console_init.state_path(home).exists()


# --------------------------------------------------------------- agent confirm
def test_declining_the_agent_keeps_the_archive_progress(tmp_path):
    home = make_home(tmp_path)

    result, _rendered = init_run(home, ["", "n"])

    assert result["ok"] is False
    assert result["reason"] == "declined"
    assert "已保留" in result["text"]
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["archive"]["status"] == "ok"
    assert "agent" not in state["steps"]


def test_a_missing_dsh_blocks_the_agent_step_without_recording_it(tmp_path):
    home = make_home(tmp_path)
    config_path = home / "install" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    (home.parent / "agent/dist/main.js").unlink()
    write_json(config_path, config)

    result, rendered = init_run(home, [""])

    assert result["ok"] is False
    assert result["reason"] == "agent_unavailable"
    assert "Agent" in result["text"]
    assert "不可用" in rendered
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["archive"]["status"] == "ok"
    assert "agent" not in state["steps"]


# ------------------------------------------------------------- resume behaviour
def test_an_interruption_resumes_at_the_unfinished_step(tmp_path):
    home = make_home(tmp_path)
    record_remaining_steps(home)

    first, _rendered = init_run(home, [""], )  # EOF at the agent question
    assert first["ok"] is False
    assert first["reason"] == "aborted"
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["archive"]["status"] == "ok"
    assert "agent" not in state["steps"]

    second, rendered = init_run(home, [""])

    assert second["ok"] is True
    assert second["completed"] == [
        "archive",
        "agent",
        "lark",
        "transport",
        "naming",
        "push_time",
        "knowledge",
    ]
    assert "[已完成] 确认资料目录" in rendered
    # The archive step is not asked again.
    assert "使用默认资料目录" not in rendered


def test_a_rerun_skips_every_completed_step_and_shows_done(tmp_path):
    home = make_home(tmp_path)
    record_remaining_steps(home)
    init_run(home, ["", ""])
    # US23: a completed init is only re-entered while the formal service is
    # paused; a running service is refused before this skip/report path.
    console.write_state(home, service="paused", paused=True)

    result, rendered = init_run(home, [])

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
    assert rendered.count("[已完成]") == 7
    assert "尚未实现" not in rendered
    assert result["reason"] == "already_complete"


# ------------------------------------------------------------------ dispatch
def test_console_dispatch_runs_the_init_state_machine(tmp_path):
    home = make_home(tmp_path)
    calls: list[Path] = []

    def fake_init():
        calls.append(home)
        return {"ok": True, "reason": "not_yet", "text": "初始化骨架已运行。"}

    result = console.dispatch(home, "/horizon-init", init=fake_init)

    assert calls == [home]
    assert result["ok"] is True
    assert result["action"] == "init"
    assert result["text"] == "初始化骨架已运行。"
    assert result["init"]["reason"] == "not_yet"


def test_horizon_init_is_no_longer_a_stub_in_the_help(tmp_path):
    text = console.dispatch(make_home(tmp_path), "/horizon-help")["text"]

    assert console.COMMANDS["/horizon-init"]["implemented"] is True
    assert "/horizon-init" in text
    assert "/horizon-init（尚未实现）" not in text


def test_serve_wires_the_init_step_machine_to_the_prompt_reader(tmp_path):
    home = make_home(tmp_path)
    record_remaining_steps(home)
    console.write_state(home, paused=True, service="paused")
    # The Lark and transport steps have their own offline test files; record them
    # as done here so the serve test exercises the prompt wiring without a
    # Lark subprocess or a real test message.
    console_init.record_step(home, "lark", {"cli": {"path": "lark-cli", "source": "recorded"}})
    console_init.record_step(
        home,
        "transport",
        {"channel": "ok", "owner": {"verified": True, "source": "init.transport"}},
    )
    resolved = {
        "code": str(home.parent),
        "runtime": str(home),
        "archive": str(tmp_path / "horizon-archive"),
    }
    lines = iter(["/horizon-init", "", "", "/exit"])
    out = io.StringIO()

    result = console.serve(
        home,
        resolved=resolved,
        isatty=lambda: True,
        reader=lambda prompt: next(lines),
        out=out,
        set_title=lambda title: True,
        start_worker=lambda function: None,
    )

    assert result["ok"] is True
    rendered = out.getvalue()
    assert "[完成] 资料目录" in rendered
    assert "[完成] 内置 Agent 模型已确认" in rendered
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["archive"]["status"] == "ok"
    assert state["steps"]["agent"]["status"] == "ok"


