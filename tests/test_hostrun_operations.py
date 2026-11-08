"""The two whitelists: the operation registry and the exact instruction map (#82).

The registry is data in ``contracts/operations.json`` and the human contract is
``contracts/operations.md``; this file proves they agree, that the instruction
whitelist matches only after ``strip()`` (never fuzzily), that execution accepts
an operation id plus validated parameters and never the raw message, and that
an unmatched message is handed to the intent-judgement seam without executing
anything.

Every test is offline: operation executors receive fake status/queue/run/send
seams, so no ``lark-cli``, Base, network, credentials or real TTY is involved.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from hostrun import operations

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "contracts" / "operations.json"
CONTRACT_PATH = ROOT / "contracts" / "operations.md"

V1_OPERATIONS = {
    "status",
    "push_again",
    "process_feedback",
    "pause",
    "resume",
    "test_message",
    "change_schedule",
    "relocate_archive",
}
V1_INSTRUCTIONS = {
    "推一篇": "push_again",
    "处理反馈": "process_feedback",
    "状态": "status",
    "暂停": "pause",
    "恢复": "resume",
    "测试飞书消息": "test_message",
}


# ------------------------------------------------------------------ fixtures
def _registry() -> dict:
    return operations.load_registry(REGISTRY_PATH)


def _table(markdown: str, heading: str) -> list[list[str]]:
    """The rows of the first markdown table under ``heading`` (no header/rule)."""
    lines = markdown.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:  # pragma: no cover - a missing heading is an assertion below
        return []
    rows: list[list[str]] = []
    seen_table = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            if seen_table and stripped:
                break
            continue
        seen_table = True
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if set("".join(cells)) <= {"-", " "}:
            continue
        rows.append(cells)
    return rows[1:]  # drop the header row


def _code(text: str) -> str:
    return text.strip().strip("`").strip()


# --------------------------------------------------------------- registry data
def test_the_registry_lists_exactly_the_v1_operations():
    assert set(_registry()["operations"]) == V1_OPERATIONS


def test_every_operation_is_machine_readable_and_has_a_wired_executor():
    registry = _registry()
    for op_id, spec in registry["operations"].items():
        assert spec["description"], op_id
        assert isinstance(spec["feishu_visible"], bool), op_id
        assert spec["parameters"]["type"] == "object", op_id
        assert spec["executor"] in operations.EXECUTORS, op_id
        assert operations.EXECUTORS[spec["executor"]].__name__ == spec["executor"]


def test_the_instruction_whitelist_is_the_documented_direct_phrases():
    registry = _registry()
    assert {
        phrase: spec["operation"] for phrase, spec in registry["instructions"].items()
    } == V1_INSTRUCTIONS


def test_the_operations_that_need_terminal_interaction_are_excluded():
    excluded = _registry()["excluded"]
    ids = {spec["operation"] for spec in excluded.values()}
    assert {"init", "update_kb", "stop_service"} <= ids
    for phrase, spec in excluded.items():
        assert spec["reply"] in operations.EXCLUDED_REPLIES
        assert phrase and spec["operation"]


# ------------------------------------------------------------- human contract
def test_the_contract_table_matches_the_registry():
    markdown = CONTRACT_PATH.read_text(encoding="utf-8")
    rows = _table(markdown, "## 操作注册表")
    documented = {
        _code(row[0]): (row[3], _code(row[4]))
        for row in rows
        if row and _code(row[0]) in V1_OPERATIONS
    }
    assert set(documented) == V1_OPERATIONS
    registry = _registry()
    for op_id, spec in registry["operations"].items():
        visible, executor = documented[op_id]
        assert visible == ("是" if spec["feishu_visible"] else "否"), op_id
        assert executor == spec["executor"], op_id


def test_the_contract_instruction_table_matches_the_registry():
    markdown = CONTRACT_PATH.read_text(encoding="utf-8")
    rows = _table(markdown, "## 指令白名单")
    documented = {
        _code(row[0]): (_code(row[1]), _code(row[2]))
        for row in rows
        if len(row) >= 3 and row[0]
    }
    for phrase, spec in _registry()["instructions"].items():
        op_id, params = documented[phrase]
        assert op_id == spec["operation"]
        assert (params in ("无", "{}")) == (not spec["params"])


# ------------------------------------------------------------------- matching
@pytest.mark.parametrize(
    "text,operation",
    [
        ("状态", "status"),
        (" 状态 ", "status"),
        ("\t状态\n", "status"),
        ("推一篇", "push_again"),
        (" 推一篇 ", "push_again"),
        ("处理反馈", "process_feedback"),
        ("\t处理反馈  ", "process_feedback"),
        ("暂停", "pause"),
        ("恢复", "resume"),
        ("测试飞书消息", "test_message"),
    ],
)
def test_a_whitelisted_phrase_matches_after_trim_only(text, operation):
    route = operations.route_text(text)
    assert route["action"] == "execute"
    assert route["operation"] == operation
    assert route["params"] == {}


@pytest.mark.parametrize(
    "text",
    [
        "请帮我推一下",
        "推 一篇",
        "推一篇。",
        "推一篇！",
        "状态：",
        "STATUS",
        "Status",
        "处理 反馈",
        "推两篇",
        "帮我处理反馈",
    ],
)
def test_a_near_miss_is_never_executed_and_goes_to_intent_judgement(text):
    route = operations.route_text(text)
    assert route["action"] == "intent_judgement"
    assert route["operation"] is None
    assert route["text"] == text


def test_an_empty_message_is_not_an_instruction():
    route = operations.route_text("   ")
    assert route["action"] == "empty"


@pytest.mark.parametrize("phrase", ["初始化", "/horizon-init", "更新知识库", "/horizon-update-kb"])
def test_console_only_phrases_are_refused_with_the_console_pointer(phrase):
    route = operations.route_text(phrase)
    assert route["action"] == "refused"
    assert route["reason"] == "console_only"
    assert route["text"] == operations.CONSOLE_ONLY_TEXT


@pytest.mark.parametrize("phrase", ["停止服务", "改代码", "修改代码"])
def test_stopping_the_service_or_changing_code_is_refused(phrase):
    route = operations.route_text(phrase)
    assert route["action"] == "refused"
    assert route["text"] in (
        operations.CONSOLE_ONLY_TEXT,
        operations.NO_PERMISSION_TEXT,
    )


# ------------------------------------------------------------------ execution
def _status_snapshot() -> dict:
    return {
        "console": {"pid": 7},
        "service": {"paused": False, "next_push_at": "2026-10-31T08:00:00+08:00"},
        "executor": {"state": "free"},
        "queue": {"items": 0, "unfinished": False, "failed": False},
        "knowledge": "ok",
    }


def test_execution_accepts_an_operation_id_and_validated_parameters(tmp_path):
    seen: list[tuple[Path, dict]] = []

    def fake_executor(home, params, context):
        seen.append((Path(home), params))
        return {"ok": True, "text": "已执行。"}

    result = operations.execute(
        tmp_path,
        "change_schedule",
        {"time": "09:30"},
        executors={"change_schedule": fake_executor},
    )

    assert result["ok"] is True
    assert result["text"] == "已执行。"
    assert seen == [(Path(tmp_path), {"time": "09:30"})]


def test_an_unknown_operation_id_is_refused_without_calling_anything(tmp_path):
    result = operations.execute(tmp_path, "init", {})
    assert result["ok"] is False
    assert result["reason"] == "unknown_operation"
    assert result["text"] == operations.NO_PERMISSION_TEXT


@pytest.mark.parametrize(
    "operation,params,reason",
    [
        ("change_schedule", {}, "缺少参数 time"),
        ("change_schedule", {"time": ""}, "参数 time"),
        ("change_schedule", {"time": "9:30"}, "参数 time"),
        ("change_schedule", {"time": "24:00"}, "参数 time"),
        ("change_schedule", {"time": "09:30", "timezone": "UTC"}, "未知参数 timezone"),
        ("relocate_archive", {}, "缺少参数 path"),
        ("relocate_archive", {"path": ""}, "参数 path"),
        ("relocate_archive", {"path": 42}, "参数 path"),
    ],
)
def test_invalid_parameters_are_refused_before_any_execution(
    tmp_path, operation, params, reason
):
    called: list[dict] = []

    result = operations.execute(
        tmp_path,
        operation,
        params,
        executors={
            operation: lambda home, values, context: called.append(values)
            or {"ok": True, "text": "不该执行"}
        },
    )

    assert result["ok"] is False
    assert result["reason"] == "invalid_parameters"
    assert reason in result["text"]
    assert "参数不合法" in result["text"]
    assert called == []


def test_an_operation_hidden_from_feishu_cannot_be_executed_from_feishu(tmp_path):
    registry = _registry()
    registry["operations"]["status"]["feishu_visible"] = False
    seen: list[dict] = []

    block = operations.execute(
        tmp_path,
        "status",
        {},
        source="feishu",
        registry=registry,
        executors={"status": lambda home, params, context: seen.append(params) or {"ok": True, "text": "ok"}},
    )
    allowed = operations.execute(
        tmp_path,
        "status",
        {},
        source="console",
        registry=registry,
        executors={"status": lambda home, params, context: seen.append(params) or {"ok": True, "text": "ok"}},
    )

    assert block["ok"] is False
    assert block["reason"] == "not_visible_in_feishu"
    assert block["text"] == operations.CONSOLE_ONLY_TEXT
    assert allowed["ok"] is True
    assert seen == [{}]


def test_a_failing_executor_is_reported_in_the_receipt(tmp_path):
    def broken(home, params, context):
        raise RuntimeError("boom")

    result = operations.execute(
        tmp_path, "status", {}, executors={"status": broken}
    )

    assert result["ok"] is False
    assert result["reason"] == "executor_failed"
    assert "操作执行失败" in result["text"]


# ---------------------------------------------------------------- executors
def test_status_renders_the_console_status_snapshot(tmp_path):
    result = operations.execute(
        tmp_path,
        "status",
        {},
        context={"status": lambda home: _status_snapshot()},
    )

    assert result["ok"] is True
    assert "服务：运行中" in result["text"]
    assert "下一次推送" in result["text"]


def test_pause_and_resume_persist_the_service_state(tmp_path):
    writes: list[dict] = []

    def fake_write(home, **fields):
        writes.append(fields)
        return {"paused": bool(fields.get("paused"))}

    pause = operations.execute(
        tmp_path, "pause", {}, context={"write_state": fake_write}
    )
    resume = operations.execute(
        tmp_path, "resume", {}, context={"write_state": fake_write}
    )

    assert pause["ok"] is True and "已暂停" in pause["text"]
    assert resume["ok"] is True and "已恢复" in resume["text"]
    assert writes == [{"paused": True}, {"paused": False}]


def test_change_schedule_persists_the_validated_time(tmp_path):
    writes: list[dict] = []

    result = operations.execute(
        tmp_path,
        "change_schedule",
        {"time": "09:30"},
        context={"write_state": lambda home, **fields: writes.append(fields) or {}},
    )

    assert result["ok"] is True
    assert "09:30" in result["text"]
    assert writes == [{"push_time": "09:30"}]


def test_relocate_archive_resolves_and_records_the_new_location(tmp_path):
    calls: list[dict] = []
    ensured: list[dict] = []
    scripts: list[tuple[Path, str]] = []
    target = tmp_path / "docs"

    result = operations.execute(
        tmp_path,
        "relocate_archive",
        {"path": str(target)},
        context={
            "resolve": lambda **kwargs: {"code": str(kwargs["code"]), "archive": str(target)},
            "remember": lambda resolved: calls.append(resolved),
            "ensure": lambda resolved: ensured.append(resolved),
            "ensure_script": lambda code, archive, python: scripts.append((Path(code), archive))
            or {"ok": True, "path": "bin/horizon-console.cmd"},
        },
    )

    assert result["ok"] is True
    assert str(target) in result["text"]
    assert "启动脚本已更新" in result["text"]
    assert calls == [{"code": str(Path(tmp_path).resolve().parent), "archive": str(target)}]
    assert ensured == calls
    assert scripts == [(Path(tmp_path).resolve().parent, str(target))]


def test_push_again_records_one_push_request(tmp_path):
    requests: list[str] = []

    class FakeQueue:
        def request_push(self, source):
            requests.append(source)
            return {"ok": True, "request": {"sources": [source]}}

    result = operations.execute(
        tmp_path, "push_again", {}, context={"queue": FakeQueue()}
    )

    assert result["ok"] is True
    assert "推送" in result["text"]
    assert requests == ["feishu"]


def test_process_feedback_runs_the_bounded_feedback_intent(tmp_path):
    calls: list[tuple[Path, str]] = []

    def fake_run(home, intent, **kwargs):
        calls.append((Path(home), intent))
        return {"ok": True, "actions": ["feedback:1 processed"]}

    result = operations.execute(
        tmp_path, "process_feedback", {}, context={"run": fake_run}
    )

    assert result["ok"] is True
    assert "反馈已处理" in result["text"]
    assert calls == [(Path(tmp_path), "feedback")]


def test_test_message_is_sent_to_the_verified_owner(tmp_path):
    sent: list[tuple[str, str]] = []

    result = operations.execute(
        tmp_path,
        "test_message",
        {},
        context={
            "target": "ou_owner",
            "send": lambda target, text: sent.append((target, text)) or {"ok": True},
        },
    )

    assert result["ok"] is True
    assert "测试消息已发送" in result["text"]
    assert sent and sent[0][0] == "ou_owner"


def test_the_registry_file_is_valid_json():
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert re.fullmatch(r"\d+\.\d+\.\d+", payload["version"])
