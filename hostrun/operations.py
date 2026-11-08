"""The two whitelists: the operation registry and the instruction whitelist.

ADR 0009 §4/§5 make the Feishu surface safe by construction:

- the **operation whitelist** is a closed set of operations, each with an id, a
  parameter schema, a declared execution entry and whether it may appear on
  Feishu.  It lives as data in ``contracts/operations.json`` so the code and the
  human contract (:mod:`contracts/operations.md`) cannot drift;
- the **instruction whitelist** maps a few exact phrases to one operation each.
  Matching is ``text.strip()`` plus dictionary lookup only — no lowercasing, no
  prefix/fuzzy matching, no punctuation stripping.  A near miss is *not*
  executed; it is handed to the intent-judgement seam (#83);
- the **excluded** table names the actions that may never be triggered from
  Feishu (``init``, ``update_kb``, ``stop_service`` and any change to horizon's
  own code/configuration).  They are answered with the console pointer or an
  explicit no-permission receipt;
- **execution accepts an operation id and validated parameters only**.  The raw
  message text never reaches an executor.  This module validates the parameters
  against the registry's schema and then calls the registered function.

The out-of-whitelist request is answered by an explicit refusal, never guessed
at.  The intent-judgement run itself is #83's scope: this module only exposes the
clean seam (``route_text`` -> ``intent_judgement``).

All executables are offline-testable through injectable context seams
(``status``, ``queue``, ``run``, ``write_state``, ``send``, ``target``); no
``lark-cli``, Base, network or real ``dsh`` is involved.
"""

from __future__ import annotations

import re
from pathlib import Path

from common import read_json

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "contracts" / "operations.json"

CONSOLE_ONLY_TEXT = "该操作不在飞书操作白名单内，请在控制台执行。"
NO_PERMISSION_TEXT = (
    "无权限：该操作不在操作白名单内，horizon 不接受通过飞书修改自身程序、配置或权限的请求。"
)
INTENT_PENDING_TEXT = "已收到，正在判断意图。"
EMPTY_MESSAGE_TEXT = "消息为空，未执行任何操作。"
TEST_MESSAGE_TEXT = "horizon 测试消息：飞书通道可用。"
DEFAULT_SUCCESS_TEXT = "操作已完成。"
DEFAULT_FAILURE_TEXT = "操作未完成。"

EXCLUDED_REPLIES = {
    "console_only": CONSOLE_ONLY_TEXT,
    "no_permission": NO_PERMISSION_TEXT,
}

#: JSON-Schema keywords this registry is allowed to use.  Anything else is
#: refused instead of silently ignored, so a typo cannot weaken validation.
SUPPORTED_PARAMETER_KEYWORDS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "pattern",
    "minLength",
    "maxLength",
    "enum",
}


class RegistryError(ValueError):
    """The operation registry itself is invalid; callers must fail closed."""


# --------------------------------------------------------------------- loading
def _load_payload(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise RegistryError(f"找不到操作注册表：{path}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise RegistryError(f"操作注册表不是 JSON 对象：{path}")
    return payload


def _index_operations(rows: object) -> dict:
    if not isinstance(rows, list) or not rows:
        raise RegistryError("操作注册表的 operations 必须是非空列表")
    indexed: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RegistryError("每个操作必须是对象")
        op_id = str(row.get("id") or "").strip()
        if not op_id:
            raise RegistryError("操作缺少 id")
        if op_id in indexed:
            raise RegistryError(f"操作 id 重复：{op_id}")
        schema = row.get("parameters")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise RegistryError(f"操作 {op_id} 的 parameters 必须是 object schema")
        unknown = set(schema) - SUPPORTED_PARAMETER_KEYWORDS - {"properties"}
        if unknown:
            raise RegistryError(
                f"操作 {op_id} 的参数 schema 使用了不支持的字段：{', '.join(sorted(unknown))}"
            )
        for rule in (schema.get("properties") or {}).values():
            unsupported = set(rule) - SUPPORTED_PARAMETER_KEYWORDS
            if unsupported:
                raise RegistryError(
                    f"操作 {op_id} 的参数规则使用了不支持的字段：{', '.join(sorted(unsupported))}"
                )
        indexed[op_id] = row
    return indexed


def _index_instructions(rows: object) -> dict:
    if not isinstance(rows, list):
        raise RegistryError("操作注册表的 instructions 必须是列表")
    indexed: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RegistryError("每条指令必须是对象")
        phrase = str(row.get("phrase") or "").strip()
        operation = str(row.get("operation") or "").strip()
        if not phrase or not operation:
            raise RegistryError("指令必须同时有 phrase 与 operation")
        if phrase in indexed:
            raise RegistryError(f"指令短语重复：{phrase}")
        indexed[phrase] = {"operation": operation, "params": dict(row.get("params") or {})}
    return indexed


def _index_excluded(rows: object, operations_index: dict) -> dict:
    if rows is None:
        return {}
    if not isinstance(rows, list):
        raise RegistryError("操作注册表的 excluded 必须是列表")
    indexed: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RegistryError("每条排除项必须是对象")
        phrase = str(row.get("phrase") or "").strip()
        operation = str(row.get("operation") or "").strip()
        reply = str(row.get("reply") or "").strip()
        if not phrase or not operation:
            raise RegistryError("排除项必须同时有 phrase 与 operation")
        if reply not in EXCLUDED_REPLIES:
            raise RegistryError(f"排除项 {phrase} 的 reply 未知：{reply}")
        if phrase in indexed:
            raise RegistryError(f"排除短语重复：{phrase}")
        if operation in operations_index:
            raise RegistryError(f"操作 {operation} 不能既在操作白名单又被排除")
        indexed[phrase] = {"operation": operation, "reply": reply}
    return indexed


def load_registry(path: str | Path | None = None) -> dict:
    """Load and structurally validate the machine-readable registry."""
    payload = _load_payload(Path(path) if path is not None else DEFAULT_REGISTRY_PATH)
    if payload.get("schema_version") != 1:
        raise RegistryError("操作注册表的 schema_version 必须为 1")
    operations = _index_operations(payload.get("operations"))
    instructions = _index_instructions(payload.get("instructions"))
    for phrase, spec in instructions.items():
        if spec["operation"] not in operations:
            raise RegistryError(f"指令 {phrase} 指向未注册操作 {spec['operation']}")
    excluded = _index_excluded(payload.get("excluded"), operations)
    return {
        "schema_version": 1,
        "version": str(payload.get("version") or ""),
        "operations": operations,
        "instructions": instructions,
        "excluded": excluded,
    }


# ------------------------------------------------------------- routing (pure)
def route_text(text: object, *, registry: dict | None = None) -> dict:
    """Classify one owner message without executing anything.

    ``execute`` carries the operation id and the registry's fixed parameters;
    ``intent_judgement`` carries the trimmed message for #83's read-only run;
    ``refused`` is an excluded action; ``empty`` was whitespace only.
    """
    reg = registry if registry is not None else load_registry()
    raw = "" if text is None else str(text)
    trimmed = raw.strip()
    if not trimmed:
        return {
            "ok": False,
            "action": "empty",
            "reason": "empty",
            "operation": None,
            "params": {},
            "text": "",
        }
    instruction = reg["instructions"].get(trimmed)
    if instruction is not None:
        return {
            "ok": True,
            "action": "execute",
            "reason": "",
            "operation": instruction["operation"],
            "params": dict(instruction["params"]),
            "text": trimmed,
        }
    excluded = reg["excluded"].get(trimmed)
    if excluded is not None:
        return {
            "ok": False,
            "action": "refused",
            "reason": excluded["reply"],
            "operation": excluded["operation"],
            "params": {},
            "text": EXCLUDED_REPLIES[excluded["reply"]],
        }
    return {
        "ok": True,
        "action": "intent_judgement",
        "reason": "not_whitelisted",
        "operation": None,
        "params": {},
        "text": trimmed,
    }


# --------------------------------------------------------- parameter validation
def validate_parameters(schema: dict, params: object) -> list[str]:
    """Return the list of human-readable violations of one registry schema."""
    errors: list[str] = []
    if not isinstance(params, dict):
        return ["参数必须是对象"]
    properties = schema.get("properties") or {}
    for name in schema.get("required") or ():
        if name not in params:
            errors.append(f"缺少参数 {name}")
    if schema.get("additionalProperties") is False:
        for name in params:
            if name not in properties:
                errors.append(f"未知参数 {name}")
    for name, value in params.items():
        rule = properties.get(name)
        if isinstance(rule, dict):
            errors.extend(_validate_value(name, value, rule))
    return errors


def _validate_value(name: str, value: object, rule: dict) -> list[str]:
    errors: list[str] = []
    expected = rule.get("type")
    if expected == "string":
        if not isinstance(value, str):
            return [f"参数 {name} 必须是字符串"]
        if "minLength" in rule and len(value) < int(rule["minLength"]):
            errors.append(f"参数 {name} 不能为空")
        if "maxLength" in rule and len(value) > int(rule["maxLength"]):
            errors.append(f"参数 {name} 过长")
        pattern = rule.get("pattern")
        if pattern and re.search(str(pattern), value) is None:
            errors.append(f"参数 {name} 格式不正确")
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"参数 {name} 必须是整数"]
    elif expected is not None:
        errors.append(f"参数 {name} 的类型 {expected} 不受支持")
    if "enum" in rule and value not in (rule["enum"] or []):
        errors.append(f"参数 {name} 不在允许的取值内")
    return errors


# ------------------------------------------------------------------- execution
def execute(
    home: Path,
    operation: object,
    params: object = None,
    *,
    source: str = "feishu",
    registry: dict | None = None,
    executors: dict | None = None,
    context: dict | None = None,
) -> dict:
    """Execute one operation by id with validated parameters.

    The caller must never pass the raw message: only ``operation`` and
    ``params`` reach this function, and only the registry's executor receives
    them.  Every refusal is a result dict, never an exception.
    """
    reg = registry if registry is not None else load_registry()
    op_id = str(operation or "").strip()
    spec = reg["operations"].get(op_id)
    if spec is None:
        return {
            "ok": False,
            "operation": op_id,
            "reason": "unknown_operation",
            "text": NO_PERMISSION_TEXT,
        }
    if source == "feishu" and not spec["feishu_visible"]:
        return {
            "ok": False,
            "operation": op_id,
            "reason": "not_visible_in_feishu",
            "text": CONSOLE_ONLY_TEXT,
        }
    values = {} if params is None else params
    errors = validate_parameters(spec["parameters"], values)
    if errors:
        return {
            "ok": False,
            "operation": op_id,
            "reason": "invalid_parameters",
            "text": "参数不合法：" + "；".join(errors),
            "errors": errors,
        }
    table = dict(EXECUTORS)
    if executors:
        table.update(executors)
    executor = table.get(str(spec["executor"]))
    if not callable(executor):
        return {
            "ok": False,
            "operation": op_id,
            "reason": "executor_missing",
            "text": f"操作 {op_id} 没有可用的执行入口，未执行。",
        }
    try:
        result = executor(Path(home), dict(values), dict(context or {}))
    except Exception as exc:  # noqa: BLE001 - an executor failure is a receipt, not a crash
        return {
            "ok": False,
            "operation": op_id,
            "reason": "executor_failed",
            "text": f"操作执行失败（{type(exc).__name__}: {exc}）。",
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not isinstance(result, dict):
        return {
            "ok": False,
            "operation": op_id,
            "reason": "executor_result_invalid",
            "text": DEFAULT_FAILURE_TEXT,
        }
    ok = bool(result.get("ok", False))
    text = str(result.get("text") or (DEFAULT_SUCCESS_TEXT if ok else DEFAULT_FAILURE_TEXT))
    return {
        "ok": ok,
        "operation": op_id,
        "params": dict(values),
        "reason": str(result.get("reason") or ("" if ok else "executor_failed")),
        "text": text,
        "result": result,
    }


# ------------------------------------------------------------------- executors
def status(home: Path, params: dict, context: dict) -> dict:
    """Render the same snapshot ``/horizon-status`` uses."""
    from hostrun import console

    snapshot = console.status_snapshot(Path(home), status=context.get("status"))
    return {"ok": True, "text": console.status_text(snapshot), "snapshot": snapshot}


def push_again(home: Path, params: dict, context: dict) -> dict:
    """Record one pending push; ``start_push`` is the dispatcher seam."""
    store = context.get("queue")
    if store is None:
        from question_queue.store import QueueStore

        store = QueueStore(Path(home))
        store.init()
    request = store.request_push("feishu")
    if isinstance(request, dict) and request.get("ok") is False:
        return {
            "ok": False,
            "reason": str(request.get("reason") or "request_failed"),
            "text": "推送请求未能记录，请查看日志。",
            "request": request,
        }
    starter = context.get("start_push")
    if starter is not None:
        started = starter(Path(home)) or {}
        if isinstance(started, dict) and started.get("ok"):
            return {
                "ok": True,
                "reason": "",
                "text": "已开始推送一篇。",
                "request": request,
                "start": started,
            }
        reason = str((started or {}).get("reason") or "push_failed")
        text = "推送请求已记录，但本次启动未成功；稍后再试。"
        if reason == "knowledge_not_ready":
            text = (
                "每日推送被知识库门禁挡住：待初始化知识库；"
                "请在控制台执行 /horizon-update-kb 完成知识库初始化。"
            )
        return {
            "ok": False,
            "reason": reason,
            "text": text,
            "request": request,
            "start": started,
        }
    return {
        "ok": True,
        "reason": "",
        "text": "已请求再推一篇（推送请求已记录）。",
        "request": request,
    }


def process_feedback(home: Path, params: dict, context: dict) -> dict:
    """Run the bounded feedback intent and report the actions it performed."""
    from hostrun import intents

    run = context.get("run") or intents.run
    kwargs = {}
    host = context.get("host")
    if host:
        kwargs["host"] = host
    result = run(Path(home), "feedback", **kwargs)
    if not result.get("ok"):
        reason = str(result.get("reason") or "failed")
        if reason == "executor_busy":
            return {"ok": False, "reason": reason, "text": "执行端正忙，本轮反馈处理未启动。"}
        return {"ok": False, "reason": reason, "text": f"反馈处理失败（{reason}）。"}
    actions = [str(action) for action in (result.get("actions") or []) if action]
    detail = "，".join(actions) if actions else "无变化"
    return {"ok": True, "reason": "", "text": f"反馈已处理（{detail}）。", "run": result}


def pause(home: Path, params: dict, context: dict) -> dict:
    """Persist the paused service state (the Console state file is the seam)."""
    from hostrun import console

    writer = context.get("write_state") or console.write_state
    state = writer(Path(home), paused=True)
    return {"ok": True, "reason": "", "text": "服务已暂停。", "state": state}


def resume(home: Path, params: dict, context: dict) -> dict:
    """Persist the running service state (the Console state file is the seam)."""
    from hostrun import console

    writer = context.get("write_state") or console.write_state
    state = writer(Path(home), paused=False)
    return {"ok": True, "reason": "", "text": "服务已恢复。", "state": state}


def test_message(home: Path, params: dict, context: dict) -> dict:
    """Send one fixed test message to the verified owner (never to raw text)."""
    from hostrun import intents
    from publisher import feishu

    target = str(context.get("target") or intents.verified_owner(Path(home))).strip()
    if not target:
        return {"ok": False, "reason": "no_owner", "text": "找不到已核实的 owner，测试消息未发送。"}
    sender = context.get("send") or feishu.send_text
    try:
        sender(target, TEST_MESSAGE_TEXT)
    except Exception as exc:  # noqa: BLE001 - a failed test message is a receipt
        return {
            "ok": False,
            "reason": "send_failed",
            "text": f"测试消息发送失败（{type(exc).__name__}）。",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {"ok": True, "reason": "", "text": "测试消息已发送。"}


def change_schedule(home: Path, params: dict, context: dict) -> dict:
    """Persist the validated daily push time."""
    from hostrun import console

    time = str(params.get("time") or "").strip()
    writer = context.get("write_state") or console.write_state
    state = writer(Path(home), push_time=time)
    return {
        "ok": True,
        "reason": "",
        "text": f"每日推送时间已改为 {time}。",
        "state": state,
    }


def relocate_archive(home: Path, params: dict, context: dict) -> dict:
    """Validate and remember the new archive location; existing files are not moved.

    The console launcher embeds ``--archive``, so relocating also refreshes it:
    otherwise the next double-click would pass the old archive explicitly and the
    remembered location would never take effect.  The two archive directories are
    created, but existing material is deliberately not copied or moved.
    """
    import sys

    from hostrun import console, layout
    from hostrun.base import HostRunError, load_config

    runtime = Path(home).resolve()
    code = runtime.parent
    target = str(params.get("path") or "").strip()
    resolve = context.get("resolve") or layout.resolve
    try:
        resolved = resolve(code=str(code), archive=target)
    except HostRunError as exc:
        return {"ok": False, "reason": "invalid_archive", "text": f"资料目录不合法：{exc}"}
    remember = context.get("remember") or layout.remember
    remember(resolved)
    ensure = context.get("ensure") or layout.ensure
    try:
        ensure(resolved)
    except HostRunError as exc:
        return {
            "ok": False,
            "reason": "archive_unwritable",
            "text": f"资料目录无法建立：{exc}",
            "layout": resolved,
        }
    ensure_script = context.get("ensure_script") or console.ensure_script
    script_note = ""
    try:
        python = str((load_config(runtime).get("paths") or {}).get("python") or sys.executable)
        script = ensure_script(code, resolved["archive"], python)
        if isinstance(script, dict) and script.get("ok"):
            script_note = f"启动脚本已更新：{script.get('path')}。"
        else:
            script_note = "启动脚本未能更新，请手动检查。"
    except OSError as exc:
        script_note = f"启动脚本未能更新（{type(exc).__name__}）。"
    return {
        "ok": True,
        "reason": "",
        "text": (
            f"资料目录已改为 {resolved['archive']}；重启控制台后生效。"
            "既有资料不会自动搬移。" + script_note
        ),
        "layout": resolved,
    }


#: Registry ``executor`` name -> callable.  The test suite asserts every registry
#: entry has one, so a new operation cannot land without an execution entry.
EXECUTORS = {
    "status": status,
    "push_again": push_again,
    "process_feedback": process_feedback,
    "pause": pause,
    "resume": resume,
    "test_message": test_message,
    "change_schedule": change_schedule,
    "relocate_archive": relocate_archive,
}
