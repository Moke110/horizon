"""Offline tests for the last ``/horizon-init`` steps and the knowledge gate (#87).

The step machine is driven through its injectable seams only: a fake line reader,
a fake facility creator/attacher, a fake same-name lookup and an explicit TTY
answer.  No real terminal, no Feishu tenant, no ``dsh`` and no network are
touched.

The service gating tests use the same fake clock and the same persisted console
state as ``test_hostrun_console_service.py``; they assert the *external* push
behaviour (no scheduled run is started, the refusal reason is readable) rather
than any internal call order.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

from common import write_json
from hostrun import console, console_gate, console_init, console_service, facilities, lark, layout


# ------------------------------------------------------------------ fixtures
def make_home(tmp_path: Path, *, service: str = "paused") -> Path:
    code = tmp_path / "code"
    home = code / ".horizon"
    (home / "install").mkdir(parents=True)
    (code / "agent/dist").mkdir(parents=True)
    (code / "agent/dist/main.js").write_text("")
    (code / "runtime").mkdir()
    (code / "runtime/node.exe").write_text("")
    from hostrun.embedded_agent import configure
    configure(home, provider="test", model="test", api_key="test")
    dsh = tmp_path / "bin" / "dsh.cmd"
    dsh.parent.mkdir(parents=True)
    dsh.write_text("@echo off\n", encoding="utf-8")
    write_json(
        home / "install" / "config.json",
        {
            "schema_version": 1,
            "host": {"product": "dsh", "version": "0.2.1-rc.5"},
            "paths": {"repo": str(code), "dsh": str(dsh), "lark_cli": "lark-cli"},
            "permissions": {"scope": ["base:read"], "host_scope": ["filesystem"]},
        },
    )
    if service:
        console.write_state(home, service=service, paused=service == "paused")
    return home


def record_early_steps(home: Path) -> Path:
    """Record steps 1-4 as done; these tests start at the naming step."""
    archive = Path(home).parent.parent / "horizon-archive"
    console_init.record_step(
        home, "archive", {"path": str(archive), "default": True, "outside_code": True}
    )
    console_init.record_step(home, "agent", {"runtime": "horizon-pi", "provider": "test", "model": "test"})
    console_init.record_step(home, "lark", {"cli": {"path": "lark-cli", "source": "recorded"}})
    console_init.record_step(
        home,
        "transport",
        {"channel": "ok", "owner": {"verified": True, "source": "init.transport"}},
    )
    return archive


def reader(*answers: str):
    remaining = list(answers)

    def read(prompt: str = "") -> str:
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    return read


def _outcome(names: dict, *, reused: bool) -> dict:
    return {
        "ok": True,
        "names": names,
        "base": {
            "token": "bascn_test",
            "url": "https://feishu.cn/base/bascn_test",
            "name": names["base"],
        },
        "tables": {key: {"id": f"tbl_{key}", "name": name} for key, name in names["tables"].items()},
        "folders": {"root": "fld_root", "source": "fld_source", "push": "fld_push"},
        "actions": ["create Base"],
        "reused": reused,
    }


class FakeFacilities:
    def __init__(self, *, same_name=(), ok=True, note=""):
        self.same_name = list(same_name)
        self.ok = ok
        self.note = note
        self.created: list[dict] = []
        self.attached: list[dict] = []

    def find(self, home, name, cli_bin):
        return list(self.same_name)

    def create(self, home, *, names=None, confirmed=False, **kwargs):
        self.created.append({"names": names, "confirmed": confirmed, **kwargs})
        if not self.ok:
            return {"ok": False, "reason": "base_create_failed", "note": self.note or "创建失败"}
        return _outcome(names, reused=False)

    def attach(self, home, link, *, names=None, confirmed=False, **kwargs):
        self.attached.append({"link": link, "names": names, "confirmed": confirmed, **kwargs})
        return _outcome(names, reused=True)


def completion_run(home, answers, facilities_fake=None, **kwargs):
    out = io.StringIO()
    fake = facilities_fake or FakeFacilities()
    kwargs.setdefault("isatty", lambda: True)
    kwargs.setdefault("find_same_name", fake.find)
    kwargs.setdefault("facility_create", fake.create)
    kwargs.setdefault("facility_attach", fake.attach)
    result = console_init.run(home, reader=reader(*answers), out=out, **kwargs)
    return result, out.getvalue(), fake


def state(home: Path) -> dict:
    return json.loads(console_init.state_path(home).read_text(encoding="utf-8"))


# --------------------------------------------------------------- naming step
def test_enter_accepts_the_three_default_names_and_creates_the_facilities(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)

    result, rendered, fake = completion_run(home, ["", "", "", "", ""])

    assert len(fake.created) == 1
    defaults = facilities.default_names()
    created = fake.created[0]["names"]
    assert created["base"] == defaults["base"]
    assert created["folders"] == defaults["folders"]
    # Table names stay fixed and are never asked or displayed.
    assert created["tables"] == defaults["tables"]
    assert "表名固定" in rendered
    assert str(defaults["base"]) in rendered
    step = state(home)["steps"]["naming"]
    assert step["status"] == "ok"
    assert step["value"]["base"]["token"] == "bascn_test"
    assert step["value"]["tables"]["cards"] == "tbl_cards"
    assert step["value"]["folders"] == {"root": "fld_root", "source": "fld_source", "push": "fld_push"}
    assert step["value"]["reused"] is False


def test_typed_names_override_the_defaults(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)

    _result, _rendered, fake = completion_run(
        home, ["我的知识库", "原文资料", "推送资料", "", ""]
    )

    names = fake.created[0]["names"]
    assert names["base"] == "我的知识库"
    assert names["folders"]["source"] == "原文资料"
    assert names["folders"]["push"] == "推送资料"
    # The root folder is not one of the three user-visible names.
    assert names["folders"]["root"] == facilities.default_names()["folders"]["root"]
    assert state(home)["steps"]["naming"]["value"]["names"]["base"] == "我的知识库"


def test_an_illegal_name_is_rejected_and_asked_again(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)

    _result, rendered, fake = completion_run(
        home, ["bad/name", "合法名字", "", "", "", ""]
    )

    assert console_init.INVALID_NAME_TEXT in rendered
    assert fake.created[0]["names"]["base"] == "合法名字"


def test_duplicate_folder_names_are_rejected_and_asked_again(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)

    _result, rendered, fake = completion_run(
        home, ["", "相同名字", "相同名字", "另一个名字", "", ""]
    )

    assert "不能重复" in rendered
    names = fake.created[0]["names"]
    assert names["folders"]["source"] == "相同名字"
    assert names["folders"]["push"] == "另一个名字"


def test_an_existing_same_name_base_asks_and_creates_a_new_one_by_default(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)
    fake = FakeFacilities(
        same_name=[{"base_token": "bascn_old", "name": "horizon 知识库", "url": "u"}]
    )

    _result, rendered, fake = completion_run(home, ["", "", "", "", "", ""], fake)

    assert "同名" in rendered
    assert len(fake.created) == 1
    assert fake.attached == []


def test_reusing_an_existing_same_name_base_does_not_create_a_second_one(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)
    fake = FakeFacilities(
        same_name=[
            {"base_token": "bascn_old", "name": "horizon 知识库", "url": "https://feishu.cn/base/bascn_old"}
        ]
    )

    _result, rendered, fake = completion_run(home, ["", "", "", "r", "", ""], fake)

    assert "复用" in rendered
    assert fake.created == []
    assert len(fake.attached) == 1
    assert fake.attached[0]["link"] == "https://feishu.cn/base/bascn_old"
    assert fake.attached[0]["create_missing"] is True
    assert state(home)["steps"]["naming"]["value"]["reused"] is True


def test_a_failed_creation_keeps_the_step_unfinished_and_asks_again(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)
    fake = FakeFacilities(ok=False, note="飞书凭据不可用")

    result, rendered, fake = completion_run(home, ["", "", "", "", ""], fake)

    assert result["ok"] is False
    assert result["reason"] == "facilities_failed"
    assert "飞书凭据不可用" in result["text"]
    assert "naming" not in state(home)["steps"]
    assert result["next_step"] == "naming"


# ------------------------------------------------------------ push time step
def test_the_default_push_time_is_0800_and_persisted(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)

    result, rendered, _fake = completion_run(home, ["", "", "", "", ""])

    assert "08:00" in rendered
    persisted = console.read_state(home)
    assert persisted["push_time"] == "08:00"
    assert persisted["next_push_at"]
    assert state(home)["steps"]["push_time"]["value"]["push_time"] == "08:00"
    assert result["ok"] is True


def test_a_custom_push_time_is_persisted_with_the_next_occurrence(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)

    _result, rendered, _fake = completion_run(home, ["", "", "", "21:30", ""])

    persisted = console.read_state(home)
    assert persisted["push_time"] == "21:30"
    assert "T21:30:00" in persisted["next_push_at"]
    assert state(home)["steps"]["push_time"]["value"]["push_time"] == "21:30"


def test_an_out_of_range_push_time_is_rejected_and_asked_again(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)

    _result, rendered, _fake = completion_run(home, ["", "", "", "25:00", "9:00", "07:05", ""])

    assert "不合法" in rendered
    assert "HH:MM" in rendered
    assert console.read_state(home)["push_time"] == "07:05"


# ------------------------------------------------------------ knowledge gate
def test_choosing_not_to_initialize_knowledge_blocks_pushes_and_starts_the_service(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)

    result, rendered, _fake = completion_run(home, ["", "", "", "", ""])

    assert result["ok"] is True
    assert result["reason"] == "knowledge_pending"
    assert console.read_state(home)["knowledge_ready"] is False
    assert console.read_state(home)["service"] == "running"
    gate = console_gate.knowledge_gate(home)
    assert gate["ready"] is False
    assert gate["reason"] == "knowledge_not_ready"
    assert "待初始化知识库" in result["text"]
    assert "/horizon-update-kb" in result["text"]
    assert state(home)["steps"]["knowledge"]["value"]["choice"] == "later"
    assert state(home)["steps"]["knowledge"]["value"]["gate"] == "blocked"


def test_choosing_to_initialize_knowledge_enters_the_update_kb_handoff(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)
    calls: list[Path] = []

    def handoff():
        calls.append(home)
        return {"ok": True, "action": "update_kb", "text": "已进入 /horizon-update-kb 读取阶段。"}

    result, rendered, _fake = completion_run(home, ["", "", "", "", "y"], update_kb=handoff)

    assert calls == [home]
    assert result["reason"] == "update_kb"
    assert "已进入 /horizon-update-kb" in result["text"]
    assert state(home)["steps"]["knowledge"]["value"]["choice"] == "update_kb"
    # The pending initialization keeps the gate closed until the review completes.
    assert console.read_state(home)["knowledge_ready"] is False
    assert console_gate.knowledge_gate(home)["ready"] is False


def write_confirmation(home: Path, *, aborted: bool = False, remaining: int = 0) -> Path:
    path = home / "update-kb" / "review" / "confirmation.json"
    write_json(
        path,
        {
            "schema_version": 1,
            "aborted": aborted,
            "batch_hash": "batch_test",
            "counts": {"total": 2, "accepted": 2, "discarded": 0, "kept": 0, "remaining": remaining},
            "decisions": [],
            "written": {},
        },
    )
    return path


def test_the_gate_is_released_only_by_a_completed_review(tmp_path):
    home = make_home(tmp_path)
    console.write_state(home, knowledge_ready=False)
    assert console_gate.knowledge_gate(home)["ready"] is False

    write_confirmation(home, aborted=True, remaining=0)
    assert console_gate.knowledge_gate(home)["ready"] is False

    write_confirmation(home, aborted=False, remaining=1)
    assert console_gate.knowledge_gate(home)["ready"] is False

    write_confirmation(home, aborted=False, remaining=0)
    gate = console_gate.knowledge_gate(home)
    assert gate["ready"] is True
    assert gate["reason"] == "update_kb_completed"


def test_an_unset_gate_does_not_block_the_console_but_the_daily_run_checks_install(tmp_path):
    home = make_home(tmp_path)
    from hostrun import install

    # No explicit decision: the console itself has nothing to enforce.
    assert console_gate.knowledge_gate(home)["ready"] is True
    # The daily run keeps the legacy installation gate until knowledge exists.
    assert console_gate.daily_allowed(home)["ready"] is False

    install.record(home, "knowledge", "ok")
    assert console_gate.daily_allowed(home)["ready"] is True


def test_an_explicit_closed_gate_blocks_the_daily_run_even_with_a_legacy_install(tmp_path):
    home = make_home(tmp_path)
    from hostrun import install

    install.record(home, "knowledge", "ok")
    console.write_state(home, knowledge_ready=False)

    gate = console_gate.daily_allowed(home)
    assert gate["ready"] is False
    assert gate["reason"] == "knowledge_not_ready"


def test_a_completed_review_releases_the_daily_run_without_a_legacy_install(tmp_path):
    home = make_home(tmp_path)
    console.write_state(home, knowledge_ready=False)

    write_confirmation(home)

    assert console_gate.daily_allowed(home)["ready"] is True


# --------------------------------------------- push path honours the gate
TZ = timezone.utc


class Clock:
    def __init__(self, moment: datetime):
        self.moment = moment

    def __call__(self):
        return self.moment

    def set(self, moment):
        self.moment = moment


class Daily:
    def __init__(self):
        self.calls = []

    def __call__(self, home, intent=None, **kwargs):
        self.calls.append(intent)
        return {"ok": True, "brief": "任务：推送一篇"}


class Agent:
    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return {"ok": True}


def service_for(home, clock, daily, agent):
    return console_service.Service(
        home,
        clock=clock,
        run_daily=daily,
        run_agent=agent,
        start_worker=lambda function: function(),
        ticker_worker=lambda function: None,
        status_provider=lambda repo: {
            "executor": {},
            "queue": {"items": 0, "unfinished": False, "failed": False},
            "install": {"steps": {"knowledge": "ok"}},
        },
    )


def test_a_blocked_gate_stops_the_scheduled_push_and_reports_the_reason(tmp_path):
    home = make_home(tmp_path, service="running")
    console.write_state(home, push_time="08:00", knowledge_ready=False, paused=False)
    clock = Clock(datetime(2026, 10, 30, 9, 0, tzinfo=TZ))
    daily, agent = Daily(), Agent()
    service = service_for(home, clock, daily, agent)

    tick = service.tick()
    refusal = service.request_push("manual")

    assert daily.calls == []
    assert agent.calls == 0
    assert tick["pushed"] is None
    assert console.read_state(home).get("last_push_date") is None
    assert refusal["ok"] is False
    assert refusal["reason"] == "knowledge_not_ready"
    assert service.knowledge_gate()["ready"] is False


def test_completing_the_knowledge_base_releases_the_gate_and_the_push_runs(tmp_path):
    home = make_home(tmp_path, service="running")
    console.write_state(home, push_time="08:00", knowledge_ready=False, paused=False)
    clock = Clock(datetime(2026, 10, 30, 9, 0, tzinfo=TZ))
    daily, agent = Daily(), Agent()
    service = service_for(home, clock, daily, agent)

    assert service.tick()["pushed"] is None
    assert daily.calls == []

    write_confirmation(home)

    assert service.tick()["pushed"] == "scheduled"
    assert daily.calls == ["daily"]
    assert agent.calls == 1


def test_the_console_status_shows_the_blocked_knowledge_gate(tmp_path):
    home = make_home(tmp_path, service="running")
    console.write_state(home, knowledge_ready=False)

    def status(repo):
        return {
            "executor": {"state": "free"},
            "queue": {"items": 0, "unfinished": False, "failed": False},
            "install": {"steps": {"knowledge": "pending"}},
        }

    snapshot = console.status_snapshot(home, status=status)
    text = console.status_text(snapshot)

    assert snapshot["knowledge_gate"]["ready"] is False
    assert "知识库门禁：待初始化知识库" in text
    assert "/horizon-update-kb" in text


def test_horizon_push_says_why_the_gate_blocks_it(tmp_path):
    home = make_home(tmp_path, service="running")
    console.write_state(home, knowledge_ready=False)

    result = console.dispatch(home, "/horizon-push", service=service_for(home, Clock(datetime.now(TZ)), Daily(), Agent()))

    assert result["ok"] is False
    assert "待初始化知识库" in result["text"]
    assert "/horizon-update-kb" in result["text"]


# ---------------------------------------------------------------- completion
def test_completion_applies_the_chosen_archive_and_marks_the_state(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)
    chosen = tmp_path / "chosen-archive"
    chosen.mkdir()
    console_init.record_step(
        home, "archive", {"path": str(chosen), "default": False, "outside_code": True}
    )

    result, _rendered, _fake = completion_run(home, ["", "", "", "", ""])

    persisted = state(home)
    assert persisted["complete"] is True
    assert persisted["completed_at"]
    assert persisted["archive"] == str(chosen)
    assert persisted["knowledge"] == "later"
    assert result["state"]["complete"] is True
    # The archive is applied: its two directories exist and the layout is remembered.
    assert (chosen / "originals").is_dir()
    assert (chosen / "pushes").is_dir()
    remembered = layout.remembered_archive(home.parent)
    assert remembered is not None and str(remembered) == str(chosen)
    assert result["reason"] == "knowledge_pending"
    # The service is started by initialization (it had to be paused to run it).
    assert console.read_state(home)["service"] == "running"


def test_an_archive_that_became_unwritable_is_rejected_at_finalize(tmp_path):
    """S5: the write probe is re-run before the archive is persisted."""
    home = make_home(tmp_path)
    record_early_steps(home)
    chosen = tmp_path / "chosen-archive"
    chosen.mkdir()
    console_init.record_step(
        home, "archive", {"path": str(chosen), "default": False, "outside_code": True}
    )

    def denied(directory):
        return {"ok": False, "reason": "permission_denied", "error": "access denied"}

    result, _rendered, _fake = completion_run(
        home, ["", "", "", "", ""], write_probe=denied
    )

    assert result["ok"] is False
    assert result["reason"] == "archive_unwritable"
    assert "access denied" in result["text"]
    assert state(home)["complete"] is False
    assert console.read_state(home)["service"] == "paused"


def test_a_second_run_reports_completion_without_recreating_anything(tmp_path):
    home = make_home(tmp_path)
    record_early_steps(home)
    completion_run(home, ["", "", "", "", ""])
    fake = FakeFacilities()
    # US23: re-entering a completed init requires the formal service paused.
    console.write_state(home, service="paused", paused=True)

    result, rendered, fake = completion_run(home, [], fake)

    assert result["ok"] is True
    assert result["reason"] == "already_complete"
    assert "已完成" in rendered
    assert fake.created == []
    assert result["next_step"] is None


# ------------------------------------------------------- whole ADR §7 flow
class Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def ready_lark_runner():
    """A fake ``lark-cli``: installed, logged in and fully authorized."""
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


def test_the_whole_seven_step_initialization_runs_offline(tmp_path):
    home = make_home(tmp_path, service="paused")
    fake = FakeFacilities()
    out = io.StringIO()

    result = console_init.run(
        home,
        reader=reader("", "", "", "", "", "", "06:45", ""),
        out=out,
        isatty=lambda: True,
        pick_folder=lambda default: None,
        lark_runner=ready_lark_runner(),
        transport_send=lambda **kwargs: {
            "ok": True,
            "html_message_id": "om_html",
            "card_message_id": "om_card",
        },
        transport_events=lambda **kwargs: {
            "ok": True,
            "reason": "ok",
            "events": [
                {
                    "message_id": "om_card",
                    "operator_id": "ou_user",
                    "action_name": console_init.TRANSPORT_ACTION,
                }
            ],
        },
        find_same_name=fake.find,
        facility_create=fake.create,
        facility_attach=fake.attach,
    )

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
    assert result["next_step"] is None
    assert len(fake.created) == 1
    persisted = state(home)
    assert persisted["complete"] is True
    assert persisted["knowledge"] == "later"
    assert persisted["steps"]["push_time"]["value"]["push_time"] == "06:45"
    assert console.read_state(home)["service"] == "running"
    assert console_gate.knowledge_gate(home)["ready"] is False
    assert "待初始化知识库" in result["text"]
    assert "[完成] 设施已就绪" in out.getvalue()
