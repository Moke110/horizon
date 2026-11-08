"""The Console's Feishu event surface (ADR 0009 §1/§4).

The Console is the single resident program, so it owns the two Feishu
subscriptions and the mechanical background ticks:

- ``im.message.receive_v1`` and ``card.action.trigger`` are consumed by two
  **independent** bounded subscriptions.  Reconnecting one key never stops the
  other, and neither a disconnect nor a tick ever takes the process down.
- only the verified owner's direct message is actionable; a group chat, another
  sender, a bot message or a non-message event gets one log line and no receipt
  (``intents.verified_owner`` is the only owner source);
- owner message handling is the two-layer whitelist (#82): a phrase in the exact
  instruction whitelist executes one registered operation and answers with that
  operation's receipt; an excluded action is refused; anything else is *not*
  executed and is handed to the intent-judgement seam (#83).
- card callbacks are normalised and handed to the existing
  ``publisher.service.handle_event`` route, so rating/approval buttons keep
  their current semantics.  A #83 intent-confirmation callback is routed to the
  intent-judgement handler first; its operation never touches the publisher.
- the 5-minute feedback poll and the 10-minute knowledge pull run beside the
  event loops on workers; a tick that is still in flight or blocked by the
  executor lease is skipped and logged, never queued or retried.

Every dependency (event source, messenger, card handler, owner lookup, clock,
sleep, worker, pause state) is injectable, so the whole surface is driven
offline.  The live ``lark-cli`` argv/envelope, real reconnects and the real
knowledge pull stay ``untested`` until recorded against a real host.

The message parser and feedback-poll gate are Console-owned code: no retired
resident module is imported (#91).
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

from hostrun import console, executor, intent_judgement, intents, operations
from hostrun.base import load_config
from publisher import feishu

MESSAGE_EVENT_KEY = "im.message.receive_v1"
CARD_EVENT_KEY = "card.action.trigger"
EVENT_KEYS = (MESSAGE_EVENT_KEY, CARD_EVENT_KEY)

CONSUME_TIMEOUT = "30s"
TICK_PAUSE_SECONDS = 1.0
FEEDBACK_INTERVAL_SECONDS = 300
PULL_INTERVAL_SECONDS = 600
#: Reconnect delays for a broken subscription; the last value repeats.
BACKOFF_SECONDS = (5, 15, 30, 60)
#: Receipt for a message outside both whitelists; #83 replaces it with a card.
INTENT_PENDING_TEXT = operations.INTENT_PENDING_TEXT
EMPTY_MESSAGE_TEXT = operations.EMPTY_MESSAGE_TEXT
#: The only reply while the service is paused; nothing is derived (#81, ADR 0009 §6).
PAUSED_TEXT = "服务已暂停。"

SUBSCRIPTION_NAMES = {
    MESSAGE_EVENT_KEY: "消息订阅",
    CARD_EVENT_KEY: "卡片订阅",
}


# --------------------------------------------------------------- event parsing
def _message_envelope(raw: object) -> tuple[dict | None, str]:
    """Return ``(envelope, event_type)`` for a message event, else ``(None, type)``."""
    if not isinstance(raw, dict):
        return None, ""
    event_type = ""
    for key in ("type", "event_type"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            event_type = value
            break
    header = raw.get("header")
    if not event_type and isinstance(header, dict) and header.get("event_type"):
        event_type = str(header["event_type"])
    data = raw.get("data")
    envelope = None
    if isinstance(data, dict) and isinstance(data.get("event"), dict):
        envelope = data["event"]
    elif isinstance(raw.get("event"), dict):
        envelope = raw["event"]
    elif isinstance(raw.get("message"), dict):
        envelope = raw
    if event_type and event_type != MESSAGE_EVENT_KEY:
        return None, event_type
    return envelope, event_type or MESSAGE_EVENT_KEY


def _message_text(content: object) -> str:
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, str):
        stripped = content.strip()
        if not stripped:
            return ""
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
        if isinstance(parsed, dict):
            return str(parsed.get("text") or "")
        if isinstance(parsed, str):
            return parsed
        return ""
    return ""


def parse_message_event(raw: object) -> dict | None:
    """Normalise one ``im.message.receive_v1`` payload; non-message events are ``None``.

    Accepts the lark-cli wrapper ``{"type", "data": {"event"}}``, the
    ``{"event": {...}}`` envelope and the bare event dict.
    """
    envelope, event_type = _message_envelope(raw)
    if envelope is None:
        return None
    message = envelope.get("message")
    if not isinstance(message, dict) or not message:
        return None
    sender = envelope.get("sender") if isinstance(envelope.get("sender"), dict) else {}
    sender_ids = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
    sender_id = (
        sender_ids.get("open_id")
        or sender_ids.get("user_id")
        or envelope.get("sender_id")
        or ""
    )
    sender_type = sender.get("sender_type") or envelope.get("sender_type") or ""
    chat_type = message.get("chat_type") or envelope.get("chat_type") or ""
    chat_id = message.get("chat_id") or envelope.get("chat_id") or ""
    message_id = message.get("message_id") or envelope.get("message_id") or ""
    message_type = message.get("message_type") or envelope.get("message_type") or "text"
    return {
        "event_type": event_type,
        "chat_type": str(chat_type or ""),
        "chat_id": str(chat_id or ""),
        "message_id": str(message_id or ""),
        "sender_type": str(sender_type or ""),
        "sender_id": str(sender_id or ""),
        "text": _message_text(message.get("content")),
        "message_type": str(message_type or "text"),
    }


def _card_type(raw: dict) -> str:
    event_type = ""
    for key in ("type", "event_type"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            event_type = value
            break
    if not event_type:
        header = raw.get("header")
        if isinstance(header, dict) and header.get("event_type"):
            event_type = str(header["event_type"])
    return event_type


def parse_card_event(raw: object) -> dict | None:
    """Normalise a ``card.action.trigger`` payload into the publisher event shape.

    Accepts the same ``{"type", "data": {"event"}}`` wrapper as the message
    stream, the legacy flat event (``message_id`` / ``action_name`` /
    ``operator_id`` / ``token``) and Card 2.0's nested
    ``event.operator`` / ``event.action`` / ``event.context`` form.  A payload
    without any callback marker is not a callback and returns ``None``.
    """
    if not isinstance(raw, dict):
        return None
    event_type = _card_type(raw)
    if event_type and event_type != CARD_EVENT_KEY:
        return None
    data = raw.get("data")
    if isinstance(data, dict) and isinstance(data.get("event"), dict):
        envelope = data["event"]
    elif isinstance(raw.get("event"), dict):
        envelope = raw["event"]
    else:
        envelope = raw

    action = envelope.get("action") if isinstance(envelope.get("action"), dict) else {}
    action_value = envelope.get("action_value")
    if action_value is None:
        action_value = action.get("value")
    value_name = ""
    if isinstance(action.get("value"), dict):
        value_name = str(action["value"].get("action_name") or "")
    action_name = envelope.get("action_name") or action.get("name") or value_name
    operator = envelope.get("operator") if isinstance(envelope.get("operator"), dict) else {}
    context = envelope.get("context") if isinstance(envelope.get("context"), dict) else {}
    message_id = (
        envelope.get("message_id")
        or envelope.get("open_message_id")
        or context.get("open_message_id")
        or context.get("message_id")
        or ""
    )
    operator_id = (
        envelope.get("operator_id")
        or operator.get("open_id")
        or operator.get("user_id")
        or ""
    )
    chat_id = (
        envelope.get("chat_id")
        or envelope.get("open_chat_id")
        or context.get("open_chat_id")
        or ""
    )
    token = envelope.get("token") or ""
    form_value = envelope.get("form_value")
    if form_value is None:
        form_value = ""
    normalised = {
        "event_type": CARD_EVENT_KEY,
        "message_id": str(message_id or ""),
        "action_name": str(action_name or ""),
        "operator_id": str(operator_id or ""),
        "token": str(token or ""),
        "action_value": action_value if action_value is not None else "",
        "form_value": form_value,
        "chat_id": str(chat_id or ""),
    }
    markers = (
        normalised["action_name"],
        normalised["operator_id"],
        normalised["token"],
        normalised["message_id"],
    )
    if not any(markers) and not normalised["action_value"]:
        return None
    return normalised


# ------------------------------------------------------------- message handling
def classify_message(home: Path, event: dict | None, *, owner: str | None = None) -> dict:
    """Decide whether one parsed message is the verified owner's direct message."""
    resolved_owner = str(
        intents.verified_owner(Path(home)) if owner is None else owner
    ).strip()
    decision = {
        "ok": True,
        "action": "ignored",
        "reason": "",
        "target": None,
        "text": "",
        "owner": resolved_owner,
    }
    if not event:
        decision["reason"] = "not_a_message"
        return decision
    text = str(event.get("text") or "").strip()
    decision["text"] = text
    if str(event.get("chat_type") or "") != "p2p":
        decision["reason"] = "not_owner_dm"
        return decision
    sender_type = str(event.get("sender_type") or "")
    if sender_type and sender_type != "user":
        decision["reason"] = "not_owner"
        return decision
    if not resolved_owner or str(event.get("sender_id") or "") != resolved_owner:
        decision["reason"] = "not_owner"
        return decision
    decision.update(action="received", reason="owner_dm", target=resolved_owner)
    return decision


def _preview(text: str, limit: int = 80) -> str:
    value = str(text or "").strip().replace("\n", " ")
    return value if len(value) <= limit else value[:limit] + "…"


def _send_receipt(
    sender, target: str, text: str, *, log
) -> dict:
    """Send one receipt; a failed send is reported, never raised."""
    try:
        result = sender(target, text)
    except Exception as exc:  # noqa: BLE001 - a failed receipt must not kill the surface
        log("WARN", "feishu", f"回执发送失败：{type(exc).__name__}: {exc}")
        return {"sent": False, "reason": "send_failed", "reply": None, "reply_text": text}
    if isinstance(result, dict) and result.get("ok") is False:
        log("WARN", "feishu", f"回执未送达（{result.get('reason') or 'failed'}）")
        return {"sent": False, "reason": "send_failed", "reply": result, "reply_text": text}
    return {"sent": True, "reason": "", "reply": result, "reply_text": text}


def _service_paused(home: Path, paused=None) -> bool:
    """Whether the persisted service state is paused (#81 owns the writer)."""
    if paused is not None:
        try:
            return bool(paused() if callable(paused) else paused)
        except Exception:  # noqa: BLE001 - an unreadable state is not a pause
            return False
    try:
        return bool(console.read_state(home).get("paused"))
    except Exception:  # noqa: BLE001 - an unreadable state is not a pause
        return False


def handle_message(
    home: Path,
    event: dict | None,
    *,
    send=None,
    owner: str | None = None,
    emit=None,
    execute=None,
    judge=None,
    paused=None,
    start_push=None,
) -> dict:
    """Route one owner message through the instruction whitelist.

    While the service is paused the only reply is :data:`PAUSED_TEXT`; the
    message is never routed (no derived run, no intent judgement).  Otherwise a
    whitelisted phrase executes its registered operation and returns that
    operation's receipt.  An excluded phrase is refused.  Every other message is
    handed to ``judge`` (the #83 intent-judgement seam) or, when that seam is not
    wired yet, answered with ``INTENT_PENDING_TEXT`` — it is never executed and
    never guessed at.  Non-owner messages keep the owner gate and get no receipt.
    """
    log = emit or (lambda level, source, message: None)
    decision = classify_message(home, event, owner=owner)
    outcome = {
        **decision,
        "replied": False,
        "reply": None,
        "reply_text": None,
        "route_action": None,
        "operation": None,
        "execute_result": None,
    }
    if decision["action"] == "ignored":
        log("INFO", "feishu", f"忽略消息（{decision['reason']}）：{_preview(decision['text'])}")
        return outcome

    log("INFO", "feishu", f"收到 owner 消息：{_preview(decision['text']) or '（空）'}")
    sender = send or feishu.send_text
    target = str(decision.get("target") or "")
    route = operations.route_text(decision["text"])
    outcome["route_action"] = route["action"]
    # #81 pause gate comes first: a paused service replies only the pause notice
    # and never routes, executes or derives a run (ADR 0009 §6).  The one
    # exception is the exact `恢复` instruction, otherwise a Feishu pause would
    # be unrecoverable without the console (US8/US12, S2).
    resume_from_feishu = (
        route["action"] == "execute" and route["operation"] == "resume"
    )
    if _service_paused(Path(home), paused) and not resume_from_feishu:
        log("INFO", "feishu", "服务已暂停，只回复暂停提示（不派生任何运行）")
        try:
            result = sender(target, PAUSED_TEXT)
        except Exception as exc:  # noqa: BLE001 - a failed receipt must not kill the surface
            log("WARN", "feishu", f"暂停提示发送失败：{type(exc).__name__}: {exc}")
            outcome.update(action="paused", reason="send_failed", reply_text=PAUSED_TEXT)
            return outcome
        outcome.update(
            action="paused",
            reason="service_paused",
            replied=True,
            reply=result,
            reply_text=PAUSED_TEXT,
        )
        return outcome

    if route["action"] == "execute":
        runner = execute or operations.execute
        context = {"send": sender, "target": target}
        if start_push is not None:
            # Let the registry's push_again executor dispatch through #81.
            context["start_push"] = start_push
        executed = runner(
            home,
            route["operation"],
            route["params"],
            source="feishu",
            context=context,
        )
        if not isinstance(executed, dict):
            executed = {
                "ok": False,
                "reason": "execute_result_invalid",
                "text": operations.DEFAULT_FAILURE_TEXT,
            }
        outcome["operation"] = route["operation"]
        outcome["execute_result"] = executed
        text = str(executed.get("text") or operations.DEFAULT_FAILURE_TEXT)
        level = "INFO" if executed.get("ok") else "WARN"
        log(
            level,
            "feishu",
            f"执行操作 {route['operation']} -> "
            f"{'ok' if executed.get('ok') else executed.get('reason') or 'failed'}",
        )
    elif route["action"] == "refused":
        text = str(route["text"])
        level = "WARN"
        log(level, "feishu", f"拒绝操作（{route['reason']}）：{_preview(decision['text'])}")
    elif route["action"] == "empty":
        text = EMPTY_MESSAGE_TEXT
        level = "INFO"
        log(level, "feishu", "空消息未执行任何操作")
    else:
        judged: dict | None = None
        text = ""
        if judge is not None:
            try:
                judged = judge(home, route["text"], target=target, send=sender)
            except Exception as exc:  # noqa: BLE001 - judgement failure is a receipt
                log("WARN", "feishu", f"意图判定失败：{type(exc).__name__}: {exc}")
                text = "意图判定失败，请稍后再试。"
                judged = {"ok": False, "reason": "judge_failed"}
            if judged is not None and not isinstance(judged, dict):
                judged = {"ok": False, "reason": "judge_result_invalid"}
        if judged is None:
            log("INFO", "feishu", "指令未命中，交由意图判定（#83）")
            text = INTENT_PENDING_TEXT
        elif judged.get("skip_reply"):
            outcome.update(reason=str(judged.get("reason") or "judged"))
            log("INFO", "feishu", "意图判定已自行回执，未重复发送")
            return outcome
        elif not text:
            text = str(judged.get("text") or INTENT_PENDING_TEXT)
        if judged is not None:
            level = "INFO" if judged.get("ok", True) else "WARN"
            log(level, "feishu", f"意图判定结果：{judged.get('reason') or 'delivered'}")

    receipt = _send_receipt(sender, target, text, log=log)
    outcome.update(
        replied=bool(receipt["sent"]),
        reply=receipt["reply"],
        reply_text=receipt["reply_text"],
    )
    if not receipt["sent"]:
        outcome["reason"] = receipt["reason"]
    elif receipt["sent"]:
        log("INFO", "feishu", f"已回执 owner（{target}）")
    return outcome


# ---------------------------------------------------------------- card handling
def _publisher_handle_event(home: Path, event: dict) -> dict:
    from publisher import service

    return service.handle_event(Path(home), event)


def handle_card(
    home: Path,
    event: dict | None,
    *,
    handle=None,
    emit=None,
    paused=None,
    confirm=None,
) -> dict:
    """Hand one normalised card callback to its owner route.

    A #83 confirmation callback (``kind``/``nonce`` in its action value) is
    offered to ``confirm`` first; when that handler returns a result the
    publisher route is not touched, so rating/approval buttons keep their
    current semantics.  Paused service: the callback is logged and skipped,
    because pause means no Feishu response and no derived run (ADR 0009 §6).
    """
    log = emit or (lambda level, source, message: None)
    if not event:
        log("INFO", "feishu", "忽略空的卡片回调")
        return {"ok": False, "reason": "not_a_card"}
    if _service_paused(Path(home), paused):
        log("INFO", "feishu", "服务已暂停，忽略卡片回调（不派生任何运行）")
        return {"ok": True, "skipped": "service_paused"}
    if confirm is not None:
        try:
            confirmed = confirm(Path(home), event)
        except Exception as exc:  # noqa: BLE001 - one bad callback must not kill the surface
            log("WARN", "feishu", f"确认卡片回调处理失败：{type(exc).__name__}: {exc}")
            return {
                "ok": False,
                "reason": "confirmation_failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        if confirmed is not None:
            if not isinstance(confirmed, dict):
                confirmed = {"ok": False, "reason": "confirmation_result_invalid"}
            action = confirmed.get("action") or confirmed.get("reason") or "handled"
            level = "INFO" if confirmed.get("ok") else "WARN"
            log(level, "feishu", f"确认卡片回调：{action}")
            return confirmed
    handler = handle or _publisher_handle_event
    try:
        result = handler(Path(home), event)
    except Exception as exc:  # noqa: BLE001 - one bad callback must not kill the surface
        log("WARN", "feishu", f"卡片回调处理失败：{type(exc).__name__}: {exc}")
        return {"ok": False, "reason": "card_failed", "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(result, dict):
        result = {"ok": False, "reason": "card_result_invalid", "result": result}
    if result.get("ok"):
        detail = result.get("kind") or result.get("action") or "ok"
        log("INFO", "feishu", f"卡片回调已处理（{detail}）")
    else:
        log("WARN", "feishu", f"卡片回调未处理（{result.get('reason') or 'unknown'}）")
    return result


# ------------------------------------------------------------- mechanical ticks
def poll_feedback(home: Path, *, host: str | None = None, run=None) -> dict:
    """One feedback poll; skip while another process holds the executor lease.

    The lease is the single-executor guard: if it is held, this round is skipped
    and logged by the caller.  The poll never waits, never steals and never
    retries (the next 5-minute tick is the retry).
    """
    home = Path(home)
    if executor.status(home).get("state") == "held":
        return {"ok": True, "skipped": "executor_busy"}
    config = load_config(home)
    if not host:
        host = str((config.get("host") or {}).get("product") or "host")
    agent_run_config = config.get("agent_run") or {}
    lease_minutes = int(
        agent_run_config.get("feedback_lease_minutes") or executor.DEFAULT_MINUTES
    )
    result = (run or intents.run)(home, "feedback", host=host, minutes=lease_minutes)
    if not result.get("ok") and result.get("reason") == "executor_busy":
        return {"ok": True, "skipped": "executor_busy", "run": result}
    return {
        "ok": bool(result.get("ok", True)),
        "skipped": None,
        "run": result,
    }


def _bitable_pull(home: Path) -> dict:
    from knowledge import bitable

    return bitable.pull(Path(home))


def pull_knowledge(home: Path, *, pull=None) -> dict:
    """The 10-minute knowledge pull; the caller owns error reporting."""
    return (pull or _bitable_pull)(Path(home))


# ---------------------------------------------------------------- event surface
def _chunk_lifecycle(chunk: dict) -> tuple[str, str]:
    kind = str(chunk.get("kind") or chunk.get("reason") or "consume_failed")
    if chunk.get("ok"):
        return "ok", "ok"
    if chunk.get("retryable") and not chunk.get("fatal"):
        return "transient", kind
    return "fatal", kind


def _start_thread(function, name: str) -> threading.Thread:
    thread = threading.Thread(target=function, name=name, daemon=True)
    thread.start()
    return thread


class EventSurface:
    """The two Feishu subscriptions plus the 5/10-minute mechanical ticks.

    ``start`` launches three daemon workers (message, card, ticks); ``stop``
    signals each one and joins it.  The subscriptions inspect an independent
    stop event, so a disconnect on one key never touches the other.  The prompt
    loop keeps running on the main thread: neither events nor ticks block a
    ``/horizon-status`` reply.
    """

    def __init__(
        self,
        home: Path,
        *,
        consume=None,
        send=None,
        handle_card=None,
        owner=None,
        paused=None,
        emit=None,
        execute=None,
        judge=None,
        out=None,
        clock=None,
        sleep=None,
        start_worker=None,
        poll_feedback=None,
        pull=None,
        feedback_seconds: int = FEEDBACK_INTERVAL_SECONDS,
        pull_seconds: int = PULL_INTERVAL_SECONDS,
        host: str | None = None,
        identity: str = "bot",
        start_push=None,
        confirm=None,
        intent_judgement=None,
    ):
        self.home = Path(home)
        self.out = out
        self.identity = identity
        self.host = host
        self._consume = consume or feishu.listen_events
        self._send = send or feishu.send_text
        self._handle_card = handle_card
        self._owner = owner
        self._paused = paused
        self._execute = execute
        self._judge = judge
        self._start_push = start_push
        self._confirm = confirm
        self._intent_judgement = intent_judgement
        if intent_judgement is not None:
            # The production #83 owner: one judgement cache and its cards.
            self._judge = intent_judgement.judge
            self._confirm = intent_judgement.handle_card
        self._emit = emit or console.emit_event
        self._clock = clock or time.monotonic
        self._sleep = sleep
        self._start_worker = start_worker or (
            lambda function: _start_thread(function, "console-worker")
        )
        self._poll_feedback = poll_feedback
        self._pull = pull
        self._feedback_seconds = max(1, int(feedback_seconds))
        self._pull_seconds = max(1, int(pull_seconds))
        self._stop = threading.Event()
        self._threads: dict[str, threading.Thread] = {}
        self.inflight: dict[str, bool] = {}
        # One queue per transport test currently waiting for its card callback
        # (#86): the console already owns the card subscription, so a second
        # ``lark-cli event consume`` must never be spawned during initialization.
        self._card_waiters: list[queue.Queue] = []
        now = float(self._clock())
        self._last_feedback = now
        self._last_pull = now

    def bind_lifecycle(self, service) -> None:
        """Bind #81's lifecycle so a Feishu ``推一篇`` really starts a push.

        Without the binding the ``push_again`` executor only records the queue
        request (the #82 contract); with it, the request is dispatched through
        :meth:`hostrun.console_service.Service.request_push` — #81's scheduler
        stays the only push dispatcher.
        """
        starter = getattr(service, "request_push", None)
        if callable(starter):
            self._start_push = starter
        judge = self._intent_judgement
        if judge is not None and callable(getattr(judge, "bind_lifecycle", None)):
            judge.bind_lifecycle(service)

    # -- logging ---------------------------------------------------------
    def _log(self, level: str, source: str, message: str) -> None:
        try:
            self._emit(self.home, level, source, message, out=self.out)
        except Exception:  # noqa: BLE001 - logging never kills the surface
            pass

    # -- lifecycle -------------------------------------------------------
    def start(self) -> dict:
        """Start both subscriptions and the tick worker (idempotent per key)."""
        started = []
        for event_key in EVENT_KEYS:
            if event_key in self._threads and self._threads[event_key].is_alive():
                continue
            name = f"console-{event_key}"
            thread = _start_thread(
                lambda key=event_key: self.run_subscription(key), name=name
            )
            self._threads[event_key] = thread
            started.append(event_key)
            self._log(
                "INFO",
                "console",
                f"{SUBSCRIPTION_NAMES[event_key]}开始监听（{event_key}）",
            )
        if "ticks" not in self._threads or not self._threads["ticks"].is_alive():
            self._threads["ticks"] = _start_thread(self.run_ticks, "console-ticks")
            started.append("ticks")
        return {"ok": True, "started": started}

    def stop(self, timeout: float = 5) -> dict:
        """Signal every worker and wait for it to leave (idempotent)."""
        self._stop.set()
        for name, thread in list(self._threads.items()):
            if thread is threading.current_thread():
                continue
            try:
                thread.join(timeout=timeout)
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass
        alive = self.threads_alive()
        self._threads = {
            name: thread for name, thread in self._threads.items() if thread.is_alive()
        }
        return {"ok": not alive, "threads_alive": alive}

    def threads_alive(self) -> list[str]:
        return [name for name, thread in self._threads.items() if thread.is_alive()]

    def request_stop(self) -> None:
        self._stop.set()

    # -- transport test bridge (#86) -------------------------------------
    def wait_card(self, *, timeout_seconds: float = 300, identity: str = "bot") -> dict:
        """Block until the next card callback arrives on this surface.

        ``/horizon-init`` uses this instead of spawning a second
        ``lark-cli event consume``: the console's single card subscription is the
        only consumer.  ``identity`` is accepted for the event-source contract and
        is not used (the surface already consumes as bot).
        """
        waiter: queue.Queue = queue.Queue()
        self._card_waiters.append(waiter)
        try:
            event = waiter.get(timeout=max(0.0, float(timeout_seconds)))
        except queue.Empty:
            return {"ok": False, "reason": "timeout", "events": []}
        finally:
            try:
                self._card_waiters.remove(waiter)
            except ValueError:
                pass
        return {"ok": True, "reason": "ok", "events": [event]}

    def _notify_card(self, event: dict) -> None:
        for waiter in list(self._card_waiters):
            try:
                waiter.put_nowait(event)
            except Exception:  # noqa: BLE001 - a dead waiter never kills the surface
                pass

    # -- event routing ---------------------------------------------------
    def run_subscription(self, event_key: str, *, stop=None) -> dict:
        """Consume one event key until stopped, with independent backoff."""
        stop = stop or self._stop
        name = SUBSCRIPTION_NAMES.get(event_key, "事件订阅")
        failures = 0
        while not stop.is_set():
            try:
                chunk = self._consume(
                    identity=self.identity,
                    event_key=event_key,
                    max_events=0,
                    timeout=CONSUME_TIMEOUT,
                    on_line=lambda line, key=event_key: self.handle_line(key, line),
                    on_ready=None,
                    stop_event=stop,
                )
            except Exception as exc:  # noqa: BLE001 - a spawn failure is a disconnect
                chunk = {
                    "ok": False,
                    "reason": "consume_failed",
                    "kind": "consume_failed",
                    "fatal": False,
                    "retryable": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            if stop.is_set():
                break
            lifecycle, kind = _chunk_lifecycle(chunk or {})
            if lifecycle == "ok":
                if failures:
                    self._log(
                        "INFO",
                        "event",
                        f"{name}已恢复（{event_key}）",
                    )
                failures = 0
                continue
            failures += 1
            delay = BACKOFF_SECONDS[min(failures - 1, len(BACKOFF_SECONDS) - 1)]
            level = "ERROR" if lifecycle == "fatal" else "WARN"
            self._log(
                level,
                "event",
                f"{name}断开（{kind}），{delay} 秒后重连（{event_key}）",
            )
            self._pause(delay, stop=stop)
        return {"ok": True, "event_key": event_key, "failures": failures}

    def handle_line(self, event_key: str, line: str) -> dict:
        """Route one NDJSON line for one subscription; never raises."""
        try:
            raw = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            self._log("WARN", "feishu", f"坏事件行（{event_key}）已丢弃")
            return {"ok": False, "action": "ignored", "reason": "bad_event_line"}
        if event_key == MESSAGE_EVENT_KEY:
            event = parse_message_event(raw)
            if event is None:
                self._log("INFO", "feishu", f"忽略非消息事件（{event_key}）")
                return {"ok": True, "action": "ignored", "reason": "not_a_message"}
            return handle_message(
                self.home,
                event,
                send=self._send,
                owner=self._owner,
                emit=self._log,
                execute=self._execute,
                judge=self._judge,
                paused=self._paused,
                start_push=self._start_push,
            )
        if event_key == CARD_EVENT_KEY:
            event = parse_card_event(raw)
            if event is None:
                self._log("INFO", "feishu", f"忽略非卡片回调事件（{event_key}）")
                return {"ok": True, "action": "ignored", "reason": "not_a_card"}
            # Hand the callback to an in-flight /horizon-init transport test
            # before routing it.  Pause only suppresses replies/runs; the click
            # itself is still the evidence the init step is waiting for.
            self._notify_card(event)
            return handle_card(
                self.home,
                event,
                handle=self._handle_card,
                emit=self._log,
                paused=self._paused,
                confirm=self._confirm,
            )
        self._log("WARN", "feishu", f"未知事件键 {event_key}，已丢弃")
        return {"ok": False, "action": "ignored", "reason": "unknown_event_key"}

    # -- ticks -----------------------------------------------------------
    def run_ticks(self) -> None:
        while not self._stop.wait(TICK_PAUSE_SECONDS):
            self.tick()

    def tick(self, now: float | None = None) -> dict:
        """Run whichever mechanical ticks are due; never start two of a kind."""
        moment = float(self._clock() if now is None else now)
        started: list[str] = []
        skipped: list[dict] = []
        if moment - self._last_feedback >= self._feedback_seconds:
            self._last_feedback = moment
            reason = self._skip_reason("feedback", "反馈轮询")
            if reason:
                skipped.append({"tick": "feedback", "reason": reason})
            elif self._start_tick("feedback", self._run_feedback):
                started.append("feedback")
        if moment - self._last_pull >= self._pull_seconds:
            self._last_pull = moment
            reason = self._skip_reason("pull", "知识库拉取")
            if reason:
                skipped.append({"tick": "pull", "reason": reason})
            elif self._start_tick("pull", self._run_pull):
                started.append("pull")
        return {"now": moment, "started": started, "skipped": skipped}

    def _service_paused(self) -> bool:
        if self._paused is not None:
            try:
                return bool(self._paused())
            except Exception:  # noqa: BLE001 - an unreadable state is not a pause
                return False
        try:
            return bool(console.read_state(self.home).get("paused"))
        except Exception:  # noqa: BLE001 - an unreadable state is not a pause
            return False

    def _skip_reason(self, tick: str, label: str) -> str:
        if self._service_paused():
            self._log("INFO", tick, f"{label}已跳过（服务已暂停）")
            return "service_paused"
        if self.inflight.get(tick):
            self._log("INFO", tick, f"{label}已跳过（in_flight）")
            return "in_flight"
        return ""

    def _start_tick(self, tick: str, function) -> bool:
        self.inflight[tick] = True

        def worker() -> None:
            try:
                function()
            except Exception as exc:  # noqa: BLE001 - a tick never kills the surface
                self._log("WARN", tick, f"{tick} 异常：{type(exc).__name__}: {exc}")
            finally:
                self.inflight.pop(tick, None)

        try:
            self._start_worker(worker)
        except Exception as exc:  # noqa: BLE001 - report, do not kill the tick loop
            self.inflight.pop(tick, None)
            self._log("WARN", tick, f"{tick} 启动失败：{type(exc).__name__}: {exc}")
            return False
        return True

    def _run_feedback(self) -> None:
        poll = self._poll_feedback or poll_feedback
        outcome = poll(self.home, host=self.host)
        if not isinstance(outcome, dict):
            self._log("WARN", "feedback", "反馈轮询返回格式无效")
            return
        if outcome.get("skipped"):
            self._log("INFO", "feedback", f"反馈轮询已跳过（{outcome['skipped']}）")
            return
        if not outcome.get("ok", True):
            run = outcome.get("run") or {}
            self._log(
                "WARN",
                "feedback",
                f"反馈轮询失败（{run.get('reason') or outcome.get('reason') or 'failed'}）",
            )
            return
        run = outcome.get("run") or {}
        actions = [str(action) for action in (run.get("actions") or []) if action]
        detail = "，".join(actions) if actions else "无变化"
        self._log("INFO", "feedback", f"反馈轮询完成（{detail}）")

    def _run_pull(self) -> None:
        pull = self._pull or pull_knowledge
        try:
            result = pull(self.home)
        except Exception as exc:  # noqa: BLE001 - report, keep the tick loop alive
            self._log("WARN", "pull", f"知识库拉取失败：{type(exc).__name__}: {exc}")
            return
        if isinstance(result, dict) and result.get("ok") is False:
            self._log(
                "WARN",
                "pull",
                f"知识库拉取失败（{result.get('reason') or 'failed'}）",
            )
            return
        self._log("INFO", "pull", "知识库拉取完成")

    def _pause(self, seconds: float, *, stop) -> None:
        if self._sleep is not None:
            self._sleep(seconds)
        else:
            stop.wait(max(0.0, float(seconds)))


def live(home: Path, *, out=None) -> EventSurface:
    """The production surface: real lark-cli consume, real receipts and ticks.

    Also builds the #83 intent judge: a whitelist miss starts one read-only
    Agent run whose hit becomes a one-time confirmation card.
    """

    def emit(level: str, message: str) -> None:
        try:
            console.emit_event(Path(home), level, "feishu", message, out=out)
        except Exception:  # noqa: BLE001 - judgement logging never kills the surface
            pass

    judge = intent_judgement.IntentJudge(Path(home), emit=emit)
    return EventSurface(home, out=out, intent_judgement=judge)
