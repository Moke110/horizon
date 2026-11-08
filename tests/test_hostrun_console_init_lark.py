"""Offline tests for the ``/horizon-init`` Lark CLI step (issue #85, ADR 0009 §7).

Everything goes through the documented seams: an injected subprocess ``runner``
(fake ``lark-cli`` and npm), an injectable private installer, a fake clock and a
fake sleep, plus the console's reader/out seams.  No real ``lark-cli``, no npm,
no browser, no network and no TTY is touched, and no system terminal is opened.

The step implements the ADR 0009 §7 three-level detection:

1. an already configured (recorded, running) CLI is reused — ADR 0007;
2. a CLI installed elsewhere is adopted explicitly, or privately installed into
   the code directory's runtime area when none exists;
3. a logged-in CLI with a scope gap gets the Feishu developer-console link and
   the missing list, and waits for the user's re-check.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from common import write_json
from hostrun import console, console_init, facilities, lark


# ------------------------------------------------------------------ fixtures
class Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


ALL_SCOPES = [
    scope for scopes in lark.REQUIRED_SCOPES.values() for scope in scopes
]


@pytest.fixture(autouse=True)
def _isolated_path(monkeypatch):
    """Never let a real ``lark-cli`` on this machine leak into a test.

    The tests that need an installer add their own fake npm/node directory to
    ``PATH``; everything else sees an empty PATH so discovery is deterministic
    and offline.
    """
    monkeypatch.setenv("PATH", "")


def auth_payload(*, user=True, bot=True, scopes=None):
    """An ``auth status --json`` payload: booleans and scopes, never a token."""
    return {
        "identities": {
            "user": {
                "available": user,
                "tokenStatus": "valid" if user else "expired",
                "userOpenId": "ou_user" if user else "",
            },
            "bot": {"available": bot, "tokenStatus": "valid" if bot else "missing"},
            "tenant": {"matched": True},
        },
        "scopes": list(ALL_SCOPES if scopes is None else scopes),
    }


class FakeLark:
    """A scripted ``lark-cli``: auth status, device login and commands."""

    def __init__(
        self,
        *,
        auth=None,
        poll=None,
    ):
        self.auth = list(auth or [auth_payload()])
        self.poll = list(poll or [True])
        self.argv: list[list[str]] = []
        self.login_starts = 0
        self.login_polls: list[str] = []
        self.auth_checks = 0

    def next_auth(self):
        index = min(self.auth_checks, len(self.auth) - 1)
        self.auth_checks += 1
        return self.auth[index]

    def next_poll(self):
        index = min(len(self.login_polls), len(self.poll) - 1)
        self.login_polls.append("polled")
        return bool(self.poll[index])

    def __call__(self, argv, **kwargs):
        self.argv.append(list(argv))
        args = [str(item) for item in argv]
        if "install" in args and "--prefix" in args:
            prefix = Path(args[args.index("--prefix") + 1])
            entry = prefix / "node_modules" / "@larksuite" / "cli" / "bin" / "lark-cli.js"
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text("// fake lark-cli\n", encoding="utf-8")
            return Proc(stdout="added 1 package")
        if len(args) >= 3 and args[1:3] == ["auth", "status"]:
            return Proc(stdout=json.dumps(self.next_auth()))
        if len(args) >= 4 and args[1:4] == ["auth", "login", "--domain"]:
            self.login_starts += 1
            code = f"dev-code-{self.login_starts}"
            return Proc(
                stdout=json.dumps(
                    {
                        "verification_url": f"https://open.feishu.cn/device/{code}",
                        "device_code": code,
                        "expires_in": 900,
                    }
                )
            )
        if len(args) >= 4 and args[1:3] == ["auth", "login"] and "--device-code" in args:
            code = args[args.index("--device-code") + 1]
            if self.next_poll():
                return Proc(stdout=json.dumps({"ok": True}))
            return Proc(returncode=1, stderr="authorization_pending")
        if "--version" in args:
            return Proc(stdout="lark-cli 1.0.98")
        return Proc(returncode=1, stderr=f"unexpected argv: {args}")

    # ------------------------------------------------------------- assertions
    def commands(self):
        return [list(item) for item in self.argv]

    def auth_login_calls(self):
        return [item for item in self.argv if len(item) > 2 and item[1:3] == ["auth", "login"]]

    def install_calls(self):
        return [item for item in self.argv if "install" in item and "--prefix" in item]


class Clock:
    """A monotonic fake clock whose sleep advances it."""

    def __init__(self, start=0.0):
        self.value = float(start)
        self.slept = 0.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        seconds = float(seconds)
        self.slept += seconds
        self.value += seconds


def make_home(tmp_path: Path, *, recorded_cli: bool = True, service: str = "paused"):
    """A code directory whose archive and agent steps are already recorded."""
    code = tmp_path / "code"
    home = code / ".horizon"
    (home / "install").mkdir(parents=True)
    dsh = tmp_path / "bin" / "dsh.cmd"
    dsh.parent.mkdir(parents=True)
    dsh.write_text("@echo off\n", encoding="utf-8")
    patch = home / "install" / "dsh-headless-account.patch.yml"
    patch.write_text("authorization: true\n", encoding="utf-8")
    cli = home / "bin" / "lark-cli.cmd"
    cli.parent.mkdir(parents=True)
    cli.write_text("@echo off\n", encoding="utf-8")
    write_json(
        home / "install" / "config.json",
        {
            "schema_version": 1,
            "host": {"product": "dsh", "version": "0.2.1-rc.5"},
            "paths": {
                "repo": str(code),
                "dsh": str(dsh),
                "lark_cli": str(cli) if recorded_cli else "",
            },
            "agent_run": {"dsh_args": ["--patch", str(patch)]},
            "permissions": {"scope": ["base:read"], "host_scope": ["filesystem"]},
        },
    )
    console_init.record_step(
        home, "archive", {"path": str(tmp_path / "horizon-archive"), "default": True, "outside_code": True}
    )
    console_init.record_step(home, "agent", {"runtime": "horizon-pi", "provider": "test", "model": "test"})
    record_completion_steps(home)
    if service != "stopped":
        console.write_state(home, service=service, paused=service == "paused")
    return home, code, cli


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


def reader(*answers):
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


def lark_run(home, answers, *, fake=None, clock=None, **kwargs):
    out = io.StringIO()
    clock = clock or Clock()
    kwargs.setdefault("isatty", lambda: True)
    kwargs.setdefault("pick_folder", lambda default: None)
    kwargs.setdefault("lark_runner", fake or FakeLark())
    kwargs.setdefault("clock", clock)
    kwargs.setdefault("sleep", clock.sleep)
    kwargs.setdefault("transport_send", transport_send)
    kwargs.setdefault("transport_events", transport_events)
    kwargs.setdefault("find_same_name", lambda *args: [])
    kwargs.setdefault("facility_create", _fake_facility)
    kwargs.setdefault("facility_attach", _fake_facility)
    result = console_init.run(home, reader=reader(*answers), out=out, **kwargs)
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


# -------------------------------------------------------- device-code request
def test_request_login_reads_the_authorization_link_and_device_code():
    fake = FakeLark()

    result = lark.request_login("lark-cli", runner=fake)

    assert result["ok"] is True
    assert result["verification_url"] == "https://open.feishu.cn/device/dev-code-1"
    assert result["device_code"] == "dev-code-1"
    assert fake.argv[0][:5] == ["lark-cli", "auth", "login", "--domain", "all"]
    assert "--no-wait" in fake.argv[0] and "--json" in fake.argv[0]


def test_request_login_accepts_a_nested_data_payload():
    def runner(argv, **kwargs):
        return Proc(
            stdout=json.dumps(
                {
                    "ok": True,
                    "data": {
                        "verificationUrl": "https://open.feishu.cn/device/nested",
                        "deviceCode": "nested-code",
                    },
                }
            )
        )

    result = lark.request_login("lark-cli", runner=runner)

    assert result["ok"] is True
    assert result["verification_url"] == "https://open.feishu.cn/device/nested"
    assert result["device_code"] == "nested-code"


def test_request_login_reports_a_non_json_or_incomplete_answer():
    def runner(argv, **kwargs):
        return Proc(stdout="not json")

    result = lark.request_login("lark-cli", runner=runner)

    assert result["ok"] is False
    assert result["note"]
    # A failed request never invents a link or a device code.
    assert "verification_url" not in result
    assert "device_code" not in result


# --------------------------------------------------- level 1: an already configured CLI
def test_a_configured_and_logged_in_cli_is_reused_without_install_or_login(tmp_path):
    home, _code, cli = make_home(tmp_path)
    fake = FakeLark()

    result, rendered = lark_run(home, [], fake=fake)

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
    assert fake.install_calls() == []
    assert fake.auth_login_calls() == []
    assert str(cli) in rendered
    assert "复用" in rendered
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    value = state["steps"]["lark"]["value"]
    assert state["steps"]["lark"]["status"] == "ok"
    assert value["cli"]["path"] == str(cli)
    assert value["cli"]["source"] == "recorded"
    assert value["identity"]["user_ready"] is True
    assert value["identity"]["bot_ready"] is True
    assert value["authorization"]["status"] == "ok"
    assert value["authorization"]["missing"] == []


# ----------------------------------- level 1b: missing CLI -> private install in code runtime
def _fake_node_tools(tmp_path, monkeypatch):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    (bindir / ("npm.cmd" if os.name == "nt" else "npm")).write_text("", encoding="utf-8")
    (bindir / ("node.exe" if os.name == "nt" else "node")).write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(bindir))


def test_a_missing_cli_is_privately_installed_into_the_code_runtime_area(tmp_path, monkeypatch):
    home, code, cli = make_home(tmp_path, recorded_cli=False)
    cli.unlink()
    _fake_node_tools(tmp_path, monkeypatch)
    fake = FakeLark()

    result, rendered = lark_run(home, [], fake=fake)

    launcher = code / ".horizon" / "bin" / ("lark-cli.cmd" if os.name == "nt" else "lark-cli")
    assert result["ok"] is True
    assert "私有安装" in rendered
    assert launcher.is_file()
    assert fake.install_calls(), "a private npm install was expected"
    argv = fake.install_calls()[0]
    assert str(code / ".horizon" / "lark") in argv
    config = json.loads((home / "install" / "config.json").read_text(encoding="utf-8"))
    assert config["paths"]["lark_cli"] == str(launcher)
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["lark"]["value"]["cli"]["path"] == str(launcher)
    assert state["steps"]["lark"]["value"]["cli"]["source"] == "private-install"
    # The install is private: no global package, no PATH write.
    assert not any("--global" in item or "-g" in item for item in argv)


# ------------------------------- level 2: a CLI installed elsewhere is adopted explicitly
def test_a_cli_installed_elsewhere_is_adopted_only_after_confirmation(tmp_path):
    home, _code, cli = make_home(tmp_path, recorded_cli=False)
    fake = FakeLark()

    result, rendered = lark_run(home, ["y"], fake=fake)

    assert result["ok"] is True
    assert fake.install_calls() == []
    assert str(cli) in rendered
    assert "采用" in rendered
    config = json.loads((home / "install" / "config.json").read_text(encoding="utf-8"))
    assert config["paths"]["lark_cli"] == str(cli)
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["lark"]["value"]["cli"]["source"] == "adopted"


def test_declining_the_adoption_falls_back_to_a_private_install(tmp_path, monkeypatch):
    home, code, cli = make_home(tmp_path, recorded_cli=False)
    _fake_node_tools(tmp_path, monkeypatch)
    fake = FakeLark()

    result, rendered = lark_run(home, ["n"], fake=fake)

    assert result["ok"] is True
    assert fake.install_calls(), "declining the discovered CLI must install a private one"
    assert "私有安装" in rendered
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["lark"]["value"]["cli"]["source"] == "private-install"


def test_a_stale_recorded_path_is_never_silently_replaced(tmp_path):
    home, _code, cli = make_home(tmp_path)
    config_path = home / "install" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    stale = tmp_path / "retired" / "lark-cli.cmd"
    config["paths"]["lark_cli"] = str(stale)
    write_json(config_path, config)
    fake = FakeLark()

    result, rendered = lark_run(home, ["y"], fake=fake)

    assert result["ok"] is True
    assert "已失效" in rendered
    assert "不会自动降级" in rendered
    assert "采用该 CLI" in rendered
    recorded = json.loads(config_path.read_text(encoding="utf-8"))
    assert recorded["paths"]["lark_cli"] == str(cli)
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["lark"]["value"]["cli"]["source"] == "adopted"


# --------------------------------------- level 2: device-code login inside the console
def test_device_login_shows_the_link_and_code_and_continues_after_polling(tmp_path):
    home, _code, _cli = make_home(tmp_path)
    clock = Clock()
    fake = FakeLark(
        auth=[auth_payload(user=False, bot=True), auth_payload()],
        poll=[False, True],
    )

    result, rendered = lark_run(home, [], fake=fake, clock=clock)

    assert result["ok"] is True
    assert fake.login_starts == 1
    assert len(fake.login_polls) == 2
    assert clock.slept == console_init.LARK_POLL_SECONDS
    assert "https://open.feishu.cn/device/dev-code-1" in rendered
    assert "dev-code-1" in rendered
    assert "授权链接" in rendered and "设备码" in rendered
    assert "不新开" in rendered
    state_text = console_init.state_path(home).read_text(encoding="utf-8")
    value = json.loads(state_text)["steps"]["lark"]["value"]
    assert value["login"]["device"] is True
    assert value["login"]["polls"] == 2
    # The device code and any token are never persisted.
    assert "dev-code" not in state_text
    assert "accessToken" not in state_text


def test_login_timeout_offers_retry_and_a_second_round_succeeds(tmp_path):
    home, _code, _cli = make_home(tmp_path)
    clock = Clock()
    # 15 minutes at a 5-second poll interval: the whole window fails, then retry.
    fake = FakeLark(
        auth=[auth_payload(user=False, bot=True), auth_payload()],
        poll=[False] * (console_init.LARK_WAIT_SECONDS // console_init.LARK_POLL_SECONDS)
        + [True],
    )

    result, rendered = lark_run(home, ["r"], fake=fake, clock=clock)

    assert result["ok"] is True
    assert fake.login_starts == 2
    assert clock.slept >= console_init.LARK_WAIT_SECONDS
    assert "15 分钟" in rendered
    assert "重试" in rendered
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["lark"]["status"] == "ok"


def test_login_timeout_abort_keeps_progress_and_resumes_at_the_lark_step(tmp_path):
    home, _code, _cli = make_home(tmp_path)
    clock = Clock()
    fake = FakeLark(
        auth=[auth_payload(user=False, bot=True)],
        poll=[False] * 200,
    )

    result, rendered = lark_run(home, ["a"], fake=fake, clock=clock)

    assert result["ok"] is False
    assert result["reason"] == "lark_aborted"
    assert "进度" in result["text"]
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert "lark" not in state["steps"]
    assert state["steps"]["archive"]["status"] == "ok"
    assert state["steps"]["agent"]["status"] == "ok"

    resumed, rendered = lark_run(home, [], fake=FakeLark())
    assert resumed["ok"] is True
    assert resumed["completed"] == [
        "archive",
        "agent",
        "lark",
        "transport",
        "naming",
        "push_time",
        "knowledge",
    ]
    assert "使用默认资料目录" not in rendered


def test_an_unknown_retry_answer_is_refused_and_never_skips(tmp_path):
    home, _code, _cli = make_home(tmp_path)
    clock = Clock()
    fake = FakeLark(
        auth=[auth_payload(user=False, bot=True), auth_payload()],
        poll=[False] * (console_init.LARK_WAIT_SECONDS // console_init.LARK_POLL_SECONDS)
        + [True],
    )

    result, rendered = lark_run(home, ["s", "a"], fake=fake, clock=clock)

    assert result["ok"] is False
    assert result["reason"] == "lark_aborted"
    assert "不能跳过" in rendered


# ------------------------------------- level 3: a scope gap gets the developer console
def test_a_scope_gap_prints_the_console_link_and_missing_list_then_rechecks(tmp_path):
    home, _code, _cli = make_home(tmp_path)
    partial = [scope for scope in ALL_SCOPES if not scope.startswith("base:record")]
    fake = FakeLark(auth=[auth_payload(scopes=partial), auth_payload()])

    result, rendered = lark_run(home, [""], fake=fake)

    assert result["ok"] is True
    assert fake.auth_login_calls() == []
    assert lark.DEVELOPER_CONSOLE_URL in rendered
    assert "base:record:create" in rendered
    assert "缺失" in rendered
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["lark"]["value"]["authorization"]["status"] == "ok"


def test_a_scope_gap_can_be_aborted_with_progress_preserved(tmp_path):
    home, _code, _cli = make_home(tmp_path)
    partial = [scope for scope in ALL_SCOPES if not scope.startswith("base:record")]
    fake = FakeLark(auth=[auth_payload(scopes=partial)])

    result, _rendered = lark_run(home, ["a"], fake=fake)

    assert result["ok"] is False
    assert result["reason"] == "lark_aborted"
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert "lark" not in state["steps"]


def test_a_missing_bot_identity_uses_the_console_guidance_not_device_login(tmp_path):
    home, _code, _cli = make_home(tmp_path)
    fake = FakeLark(auth=[auth_payload(bot=False), auth_payload()])

    result, rendered = lark_run(home, [""], fake=fake)

    assert result["ok"] is True
    assert fake.auth_login_calls() == []
    assert lark.DEVELOPER_CONSOLE_URL in rendered
    assert "bot" in rendered
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["lark"]["value"]["identity"]["bot_ready"] is True


# --------------------------------------------------------- install failure / abort
def test_a_failed_private_install_offers_retry_then_records_the_launcher(tmp_path):
    home, code, cli = make_home(tmp_path, recorded_cli=False)
    cli.unlink()
    calls: list[str] = []

    def install(root, *, runner=None):
        calls.append(str(root))
        if len(calls) == 1:
            return {"ok": False, "reason": "npm_missing", "note": "未找到 npm，请先安装 Node.js"}
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_text("@echo off\n", encoding="utf-8")
        return {"ok": True, "path": str(cli), "source": "private"}

    result, rendered = lark_run(home, ["r"], fake=FakeLark(), lark_install=install)

    assert result["ok"] is True
    assert calls == [str(code), str(code)]
    assert "npm" in rendered
    assert "重试" in rendered
    config = json.loads((home / "install" / "config.json").read_text(encoding="utf-8"))
    assert config["paths"]["lark_cli"] == str(cli)
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert state["steps"]["lark"]["value"]["cli"]["path"] == str(cli)


def test_aborting_a_failed_install_leaves_the_step_unfinished(tmp_path):
    home, _code, cli = make_home(tmp_path, recorded_cli=False)
    cli.unlink()

    def install(root, *, runner=None):
        return {"ok": False, "reason": "npm_missing", "note": "未找到 npm"}

    result, _rendered = lark_run(home, ["a"], fake=FakeLark(), lark_install=install)

    assert result["ok"] is False
    assert result["reason"] == "lark_aborted"
    assert result["next_step"] == "lark"
    state = json.loads(console_init.state_path(home).read_text(encoding="utf-8"))
    assert "lark" not in state["steps"]


# ------------------------------------------------------------- step registration
def test_the_lark_step_is_implemented_and_every_adr_step_is_reached(tmp_path):
    home, _code, _cli = make_home(tmp_path)

    result, _rendered = lark_run(home, [], fake=FakeLark())

    assert console_init.STEPS[2]["id"] == "lark"
    assert console_init.STEPS[2]["implemented"] is True
    assert all(step["implemented"] for step in console_init.STEPS)
    assert result["next_step"] is None
    assert result["not_yet"] == []

