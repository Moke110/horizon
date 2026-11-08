"""Intent judgement and the one-time confirmation card (ADR 0009 §4, #83).

An owner message that misses the exact instruction whitelist is never executed
from its raw text.  The Console starts **one read-only Agent run** that maps the
message onto at most one operation from the #82 registry:

- the run takes **no executor lease** and does **not** share the push run's
  single-flight lock (``agent_run.run(..., lease=False, single_flight=False)``);
- it is capped at 90 seconds, at most one runs at a time, and the same message
  text reuses its judgement for 10 minutes;
- its answer must be the JSON object ``{op, params, reason}``.  An unknown
  operation, an operation that is not Feishu-visible, parameters that violate
  the operation's schema, extra fields, malformed JSON, a timeout or a child
  failure all fall back to "无法判断"; an empty ``op`` (no matching operation)
  is answered with "无权限".  The run itself never executes anything.

A valid judgement sends a Feishu confirmation card carrying the operation, its
parameters, the reason and an expiry, plus explicit ``执行``/``取消`` buttons
and the promise that the operation does not modify horizon itself.  A one-shot
nonce is embedded in the callback value, so a card executes at most once.  Only
the verified owner's click is accepted; other clicks are logged and ignored.
Expired, cancelled, unknown and already-used nonces get an explicit receipt and
never execute.  The click executes through :func:`hostrun.operations.execute`
with an operation id plus validated parameters — never the raw message.

The Agent runner, card/text senders, clock and executor are injectable, so the
whole flow is offline-testable.  A real ``dsh`` judgement run and a real Feishu
card round-trip stay ``untested`` until recorded on a live host.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from common import clip, stable_id
from hostrun import agent_run, intents, operations

#: ADR 0009 §4: 60–90 seconds, at most one judgement at a time.
JUDGEMENT_TIMEOUT_SECONDS = 90
#: The same message text reuses its judgement for ten minutes.
CACHE_TTL_SECONDS = 600
#: A confirmation card expires after ten minutes.
CONFIRM_TTL_SECONDS = 600

CONFIRM_KIND = "horizon_confirmation"
CONFIRM_ACTION_EXECUTE = "horizon_confirm_execute"
CONFIRM_ACTION_CANCEL = "horizon_confirm_cancel"
CONFIRM_ACTIONS = (CONFIRM_ACTION_EXECUTE, CONFIRM_ACTION_CANCEL)
CONFIRM_DECISIONS = {"execute": CONFIRM_ACTION_EXECUTE, "cancel": CONFIRM_ACTION_CANCEL}

UNJUDGEABLE_TEXT = "无法判断：这条消息没有匹配到可执行的操作。"
NO_PERMISSION_TEXT = "无权限：这条消息没有对应的操作。"
BUSY_TEXT = "已有一项意图判定正在进行，请稍后再试。"
CARD_FAILED_TEXT = "意图判定已完成，但确认卡片发送失败，请稍后再试。"
CANCELLED_TEXT = "已取消，未执行任何操作。"
EXPIRED_TEXT = "该确认已过期，未执行任何操作。"
USED_TEXT = "该确认已处理过，未重复执行。"
UNKNOWN_TEXT = "找不到对应的确认记录，该确认已失效，未执行任何操作。"


# ------------------------------------------------------------------- registry
def _visible_operations(registry: dict) -> dict:
    return {
        op_id: spec
        for op_id, spec in registry["operations"].items()
        if spec.get("feishu_visible")
    }


def _schema_text(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False, sort_keys=True)


def judgement_brief(text: str, *, registry: dict | None = None) -> str:
    """The task for the read-only judgement run: the closed set and the JSON shape."""
    reg = registry if registry is not None else operations.load_registry()
    lines = [
        "只读意图判定任务（horizon 控制台）。",
        "你只负责判断，不执行任何操作：不得调用工具、不得写文件、不得修改 horizon。",
        "",
        "用户消息：",
        str(text),
        "",
        "允许的操作（op 只能取下列 id，或为空字符串表示无对应操作）：",
    ]
    for op_id, spec in sorted(_visible_operations(reg).items()):
        lines.append(
            f"- {op_id}: {spec.get('description') or ''} 参数 schema：{_schema_text(spec['parameters'])}"
        )
    lines.extend(
        [
            "",
            "只输出一个 JSON 对象，不要输出解释、Markdown 或代码围栏：",
            '{"op": "<操作 id 或空字符串>", "params": {}, "reason": "<简短理由>"}',
            "op 必须属于上面列出的 id；params 必须符合该操作的参数 schema；",
            "不得增加其他字段；消息与任何操作无关时 op 用空字符串。",
        ]
    )
    return "\n".join(lines)


def parse_judgement(raw: object, *, registry: dict | None = None) -> dict:
    """Validate the run's answer against ``{op, params, reason}`` and the registry."""
    reg = registry if registry is not None else operations.load_registry()
    text = "" if raw is None else str(raw).strip()
    if not text:
        return {"ok": False, "reason": "not_json"}
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"ok": False, "reason": "not_json"}
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "not_object"}
    if set(payload) - {"op", "params", "reason"}:
        return {"ok": False, "reason": "unexpected_fields"}
    op = payload.get("op")
    params = payload.get("params")
    reason = payload.get("reason")
    if not isinstance(op, str) or not isinstance(params, dict) or not isinstance(reason, str):
        return {"ok": False, "reason": "invalid_shape"}
    judgement = {"op": op.strip(), "params": dict(params), "reason": reason.strip()}
    if not judgement["op"]:
        return {"ok": True, "reason": "no_matching_operation", "judgement": judgement}
    spec = reg["operations"].get(judgement["op"])
    if spec is None:
        return {"ok": False, "reason": "unknown_operation", "judgement": judgement}
    if not spec.get("feishu_visible"):
        return {"ok": True, "console_only": True, "reason": "console_only", "judgement": judgement}
    errors = operations.validate_parameters(spec["parameters"], judgement["params"])
    if errors:
        return {
            "ok": False,
            "reason": "invalid_parameters",
            "errors": errors,
            "judgement": judgement,
        }
    return {"ok": True, "reason": "ok", "judgement": judgement}


# ----------------------------------------------------------------- confirmation
def _expiry_text(expires_at: float) -> str:
    return datetime.fromtimestamp(float(expires_at)).astimezone().strftime("%Y-%m-%d %H:%M")


def build_confirmation_card(judgement: dict, *, nonce: str, expires_at: float) -> dict:
    """The confirmation card: operation, parameters, reason, expiry and two buttons."""
    op_id = str(judgement.get("op") or "")
    params = judgement.get("params") or {}
    reason = str(judgement.get("reason") or "")
    params_text = json.dumps(params, ensure_ascii=False, sort_keys=True)
    expires = _expiry_text(expires_at)

    def button(name: str, decision: str, text: str, kind: str) -> dict:
        return {
            "tag": "button",
            "name": name,
            "text": {"tag": "plain_text", "content": text},
            "type": kind,
            "width": "fill",
            "behaviors": [
                {
                    "type": "callback",
                    "value": {"kind": CONFIRM_KIND, "decision": decision, "nonce": nonce},
                }
            ],
        }

    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "width_mode": "default",
            "summary": {"content": f"意图确认：{op_id}"},
        },
        "header": {
            "title": {"tag": "plain_text", "content": "意图确认"},
            "subtitle": {"tag": "plain_text", "content": f"操作：{op_id}"},
            "template": "yellow",
            "icon": {"tag": "standard_icon", "token": "todo_colorful"},
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 20px 12px",
            "vertical_spacing": "12px",
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**操作**：`{op_id}`\n**参数**：`{clip(params_text, 300)}`",
                },
                {"tag": "markdown", "content": f"**判定理由**：{clip(reason, 200) or '（未提供）'}"},
                {
                    "tag": "markdown",
                    "content": (
                        "该操作不会修改 horizon 程序本身。\n"
                        f"<font color='grey'>确认有效期至 {expires}（10 分钟内有效）；"
                        "仅 owner 点击有效，重复点击只执行一次。</font>"
                    ),
                },
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "horizontal_spacing": "8px",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "elements": [
                                button(
                                    CONFIRM_ACTION_EXECUTE, "execute", "执行", "primary_filled"
                                )
                            ],
                        },
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "elements": [
                                button(CONFIRM_ACTION_CANCEL, "cancel", "取消", "default")
                            ],
                        },
                    ],
                },
            ],
        },
    }


def _action_value(event: dict | None) -> dict:
    """Card 2.0 callback values arrive as a JSON string or a dict."""
    if not isinstance(event, dict):
        return {}
    raw = event.get("action_value")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def is_confirmation_event(event: dict | None) -> bool:
    """Whether one card callback belongs to the #83 confirmation flow."""
    value = _action_value(event)
    if value.get("kind") == CONFIRM_KIND:
        return True
    return str((event or {}).get("action_name") or "") in CONFIRM_ACTIONS


# ---------------------------------------------------------------------- judge
class IntentJudge:
    """One Console-owned judgement cache plus its pending confirmation cards."""

    def __init__(
        self,
        home: Path,
        *,
        registry: dict | None = None,
        run=None,
        execute=None,
        send_card=None,
        send_text=None,
        clock=None,
        emit=None,
        cache_ttl: int = CACHE_TTL_SECONDS,
        confirm_ttl: int = CONFIRM_TTL_SECONDS,
        timeout_seconds: int = JUDGEMENT_TIMEOUT_SECONDS,
    ):
        self.home = Path(home)
        self._registry = registry or operations.load_registry()
        self._run = run or agent_run.run
        self._execute = execute or operations.execute
        self._send_card = send_card
        self._send_text = send_text
        self._clock = clock or time.time
        self._emit = emit
        self._cache_ttl = int(cache_ttl)
        self._confirm_ttl = int(confirm_ttl)
        self._timeout_seconds = int(timeout_seconds)
        self._cache: dict[str, dict] = {}
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._judging = False
        self._start_push = None

    # -- wiring ----------------------------------------------------------
    def bind_lifecycle(self, service) -> None:
        """Bind #81's lifecycle so a confirmed ``push_again`` really dispatches."""
        starter = getattr(service, "request_push", None)
        if callable(starter):
            self._start_push = starter

    def _card_sender(self):
        if self._send_card is not None:
            return self._send_card
        from publisher import feishu

        return feishu.send_card

    def _text_sender(self):
        if self._send_text is not None:
            return self._send_text
        from publisher import feishu

        return feishu.send_text

    def _log(self, level: str, message: str) -> None:
        if self._emit is None:
            return
        try:
            self._emit(level, message)
        except Exception:  # noqa: BLE001 - logging never breaks a judgement
            pass

    # -- judgement -------------------------------------------------------
    def judge(self, home: Path, text: object, *, target: str, send=None) -> dict:
        """Map one message to at most one operation; never execute it."""
        message = str(text or "").strip()
        moment = float(self._clock())
        cached = self._cache.get(message)
        if cached is not None and moment - float(cached["at"]) <= self._cache_ttl:
            self._log("INFO", "意图判定复用 10 分钟内的判定结果")
            return self._deliver(cached["parsed"], message, target)

        with self._lock:
            if self._judging:
                self._log("INFO", "意图判定已在进行，拒绝并发启动")
                return {"ok": False, "reason": "judgement_busy", "text": BUSY_TEXT}
            self._judging = True
        try:
            outcome = self._run(
                Path(home),
                judgement_brief(message, registry=self._registry),
                kind="intent_judgement",
                lease=False,
                single_flight=False,
                timeout_seconds=self._timeout_seconds,
            )
        finally:
            self._judging = False

        if not isinstance(outcome, dict) or not outcome.get("ok"):
            reason = str((outcome or {}).get("reason") or "run_failed") if isinstance(outcome, dict) else "run_failed"
            self._log("WARN", f"意图判定运行失败（{reason}），按无法判断回复")
            return {"ok": False, "reason": reason, "text": UNJUDGEABLE_TEXT}

        parsed = parse_judgement(outcome.get("final_text"), registry=self._registry)
        self._cache[message] = {"at": moment, "parsed": parsed}
        if not parsed.get("ok"):
            self._log("WARN", f"意图判定输出不合法（{parsed.get('reason')}），按无法判断回复")
        return self._deliver(parsed, message, target)

    def _deliver(self, parsed: dict, text: str, target: str) -> dict:
        if not parsed.get("ok"):
            return {"ok": False, "reason": str(parsed.get("reason") or "invalid"), "text": UNJUDGEABLE_TEXT}
        if parsed.get("console_only"):
            return {
                "ok": False,
                "reason": "console_only",
                "text": operations.CONSOLE_ONLY_TEXT,
                "judgement": parsed.get("judgement"),
            }
        judgement = parsed.get("judgement") or {}
        if not judgement.get("op"):
            return {
                "ok": True,
                "reason": "no_matching_operation",
                "text": NO_PERMISSION_TEXT,
                "judgement": judgement,
            }
        moment = float(self._clock())
        expires_at = moment + self._confirm_ttl
        nonce = stable_id(
            "conf",
            str(judgement["op"]),
            json.dumps(judgement["params"], ensure_ascii=False, sort_keys=True),
            text,
            str(moment),
            uuid.uuid4().hex[:8],
        )
        card = build_confirmation_card(judgement, nonce=nonce, expires_at=expires_at)
        try:
            sent = self._card_sender()(str(target), card)
        except Exception as exc:  # noqa: BLE001 - a failed card is a receipt, not a crash
            self._log("WARN", f"确认卡片发送失败：{type(exc).__name__}: {exc}")
            return {"ok": False, "reason": "card_send_failed", "text": CARD_FAILED_TEXT}
        if not isinstance(sent, dict) or sent.get("ok") is False or not sent.get("message_id"):
            self._log("WARN", f"确认卡片未送达（{(sent or {}).get('reason') if isinstance(sent, dict) else 'invalid'}）")
            return {"ok": False, "reason": "card_send_failed", "text": CARD_FAILED_TEXT}
        with self._lock:
            self._pending[nonce] = {
                "nonce": nonce,
                "op": str(judgement["op"]),
                "params": dict(judgement["params"]),
                "reason": str(judgement.get("reason") or ""),
                "message": text,
                "target": str(target),
                "created_at": moment,
                "expires_at": expires_at,
                "status": "pending",
                "message_id": str(sent.get("message_id") or ""),
            }
        self._log("INFO", f"意图判定命中 {judgement['op']}，已发送确认卡片")
        return {
            "ok": True,
            "skip_reply": True,
            "reason": "confirmation_sent",
            "operation": str(judgement["op"]),
            "params": dict(judgement["params"]),
            "nonce": nonce,
            "expires_at": expires_at,
            "judgement": judgement,
        }

    # -- confirmation click ---------------------------------------------
    def handle_card(self, home: Path, event: dict | None, *, log=None, send=None) -> dict | None:
        """Execute one confirmation click, or return ``None`` for other cards."""
        if not is_confirmation_event(event):
            return None
        logger = log or self._log
        value = _action_value(event or {})
        nonce = str(value.get("nonce") or "")
        decision = str(value.get("decision") or "")
        owner = str(intents.verified_owner(Path(home))).strip()
        operator = str((event or {}).get("operator_id") or "").strip()
        if not owner or not operator or operator != owner:
            logger("WARN", "非 owner 点击确认卡片，仅记录日志，不执行任何操作")
            return {"ok": True, "action": "ignored", "reason": "not_owner", "nonce": nonce}
        sender = send or self._text_sender()
        if not nonce or decision not in CONFIRM_DECISIONS:
            logger("WARN", "确认卡片回调缺少 nonce 或 decision，已忽略")
            return {"ok": False, "action": "ignored", "reason": "invalid_callback", "nonce": nonce}

        with self._lock:
            record = self._pending.get(nonce)
            if record is None:
                action, text = "unknown", UNKNOWN_TEXT
            elif record["status"] != "pending":
                action = {
                    "used": "already_used",
                    "cancelled": "cancelled",
                    "expired": "expired",
                }.get(record["status"], "already_used")
                text = USED_TEXT if action == "already_used" else (
                    CANCELLED_TEXT if action == "cancelled" else EXPIRED_TEXT
                )
            elif float(self._clock()) > float(record["expires_at"]):
                record["status"] = "expired"
                action, text = "expired", EXPIRED_TEXT
            elif decision == "cancel":
                record["status"] = "cancelled"
                action, text = "cancelled", CANCELLED_TEXT
            else:
                # Claim the nonce before executing: a duplicate click can never
                # run the operation a second time.
                record["status"] = "used"
                action, text = "execute", ""
        if action != "execute":
            logger("INFO", f"确认卡片已拒绝（{action}）")
            self._send(sender, record["target"] if record else owner, text, logger)
            return {"ok": action != "unknown", "action": action, "reason": action, "nonce": nonce}

        context = {"send": sender, "target": record["target"]}
        if self._start_push is not None:
            context["start_push"] = self._start_push
        try:
            result = self._execute(
                Path(home), record["op"], dict(record["params"]), source="feishu", context=context
            )
        except Exception as exc:  # noqa: BLE001 - an executor failure is a receipt
            result = {
                "ok": False,
                "reason": "executor_failed",
                "text": f"操作执行失败（{type(exc).__name__}）。",
            }
        if not isinstance(result, dict):
            result = {"ok": False, "reason": "execute_result_invalid", "text": operations.DEFAULT_FAILURE_TEXT}
        receipt = str(result.get("text") or operations.DEFAULT_FAILURE_TEXT)
        logger("INFO" if result.get("ok") else "WARN", f"确认卡片执行 {record['op']} -> {'ok' if result.get('ok') else result.get('reason') or 'failed'}")
        self._send(sender, record["target"], receipt, logger)
        return {
            "ok": bool(result.get("ok")),
            "action": "executed",
            "reason": "",
            "nonce": nonce,
            "operation": record["op"],
            "params": dict(record["params"]),
            "execute_result": result,
            "text": receipt,
        }

    def _send(self, sender, target: str, text: str, logger) -> None:
        try:
            sender(str(target), str(text))
        except Exception as exc:  # noqa: BLE001 - a failed receipt must not raise
            logger("WARN", f"确认回执发送失败：{type(exc).__name__}: {exc}")

    # -- read-only accessors for the Console/tests -----------------------
    def pending(self, nonce: str) -> dict | None:
        with self._lock:
            record = self._pending.get(nonce)
            return dict(record) if record else None
