"""Console service lifecycle: scheduling, pause/resume, control file and shutdown.

ADR 0009 §6 makes the Console the only scheduler and the only component that
starts an Agent run.  This module owns that lifecycle:

- **one daily slot.**  The console pushes once per calendar day at the persisted
  ``push_time`` (default ``08:00`` in the user's local timezone).  The time is
  ``console.state.push_time`` (``set_push_time`` writes it) and the day is
  ``last_push_date``; both survive a restart, so the same day never fires twice.
- **catch-up.**  Starting the service (or resuming from pause) after today's
  slot marks one push for the day.  Missed days are not replayed one by one:
  ``last_push_date`` being older than today still produces exactly one catch-up.
- **pause/resume.**  ``/horizon-stop`` pauses (persisted ``paused`` + service
  ``paused``); no scheduled push, no manual push and no Feishu-derived run is
  started while paused, and the Feishu surface replies only the pause notice.
  ``/horizon-start`` resumes and lets the next tick catch up.
- **knowledge gate.**  A daily push is refused (``knowledge_not_ready``) until
  the knowledge base is initialized; the Console and ``/horizon-status`` say
  "待初始化知识库" and the next step (:mod:`hostrun.console_gate`).  A completed
  ``/horizon-update-kb`` review releases it automatically.
- **restore.**  ``restore()`` keeps a paused session paused and starts a session
  that was running when the window was closed.
- **shutdown.**  ``shutdown()`` sets the stop signal (``agent_run.run``'s
  ``should_stop`` kills the in-flight child on its next poll), releases the
  executor lease, marks the started push request ``unfinished`` with the reason
  ``console_shutdown`` (``QueueStore.finish_start`` — the existing recovery
  record), and persists ``service=stopped``.  A request preserved that way is
  picked up once on the next start, so no new recovery format is invented.
- **control file.**  ``console control start|stop|status|push`` writes
  ``<runtime>/console/control.json``; the console consumes it within one tick
  (1 s), records the initiator in the event stream, and writes
  ``control-response.json`` for the caller.  Duplicate nonces and requests older
  than five minutes are ignored.

The clock, the daily runner (``intents.run('daily')``), the Agent runner
(``agent_run.run``), the worker starter and the status provider are injectable,
so the whole lifecycle is driven offline.  What stays ``untested``: the real
Windows close button, a real scheduled run against live credentials, and the
real one-second beat under a live TTY.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

from common import ensure_dir, read_json, write_json
from hostrun import console, console_gate, executor
from hostrun.base import load_config
from question_queue.store import QueueStore

DEFAULT_PUSH_TIME = "08:00"
CONTROL_TICK_SECONDS = 1
CONTROL_TTL_SECONDS = 300
CONTROL_NAME = "control.json"
CONTROL_RESPONSE_NAME = "control-response.json"
CONTROL_ACTIONS = ("start", "stop", "status", "push")
PUSH_LEASE_MINUTES = 20
PUSH_SHUTDOWN_REASON = "console_shutdown"
#: How long ``shutdown`` waits for the Agent-run worker to observe the stop
#: signal and for the tick worker to leave; the child kill happens on the next
#: one-second poll, so a few seconds is generous.
SHUTDOWN_JOIN_SECONDS = 30


# ---------------------------------------------------------------- control file
def control_path(home: Path) -> Path:
    return console.console_dir(home) / CONTROL_NAME


def control_response_path(home: Path) -> Path:
    return console.console_dir(home) / CONTROL_RESPONSE_NAME


def read_control(home: Path) -> dict:
    payload = read_json(control_path(home))
    return payload if isinstance(payload, dict) else {}


def read_control_response(home: Path) -> dict:
    payload = read_json(control_response_path(home))
    return payload if isinstance(payload, dict) else {}


def valid_push_time(value: object) -> bool:
    text = str(value or "")
    if len(text) != 5 or text[2] != ":":
        return False
    hour, minute = text[:2], text[3:]
    return hour.isdigit() and minute.isdigit() and 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59


def write_control(
    home: Path,
    action: str,
    *,
    initiator: str = "",
    clock=None,
    requested_at: str | None = None,
    nonce: str | None = None,
) -> dict:
    """Write one control request atomically; the console consumes it next tick."""
    name = str(action or "").strip().lower()
    if name not in CONTROL_ACTIONS:
        raise ValueError(f"unknown console control action: {action!r}")
    moment = (clock or _local_now)()
    stamp = str(requested_at) if requested_at else _iso(moment)
    payload = {
        "schema_version": 1,
        "action": name,
        "initiator": str(initiator or ""),
        "requested_at": stamp,
        "nonce": str(nonce or uuid.uuid4().hex),
    }
    path = control_path(home)
    ensure_dir(path.parent)
    write_json(path, payload)
    return {**payload, "path": str(path)}


# --------------------------------------------------------------------- clock
def _local_now() -> datetime:
    return datetime.now().astimezone()


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


def _as_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------- push time
def configured_push_time(home: Path, *, state: dict | None = None, config: dict | None = None) -> str:
    """The persisted slot, then ``console.push_time`` config, then 08:00."""
    current = state if isinstance(state, dict) else console.read_state(home)
    candidate = str(current.get("push_time") or "").strip()
    if valid_push_time(candidate):
        return candidate
    settings = config if isinstance(config, dict) else load_config(home)
    candidate = str((settings.get("console") or {}).get("push_time") or "").strip()
    return candidate if valid_push_time(candidate) else DEFAULT_PUSH_TIME


def set_push_time(home: Path, value: str, *, clock=None) -> dict:
    """Persist the daily slot (``HH:MM``); used by init/change_schedule later."""
    text = str(value or "").strip()
    if not valid_push_time(text):
        return {"ok": False, "reason": "invalid_push_time", "push_time": text}
    state = console.write_state(home, push_time=text)
    moment = (clock or _local_now)()
    next_at = None if state.get("paused") else _next_occurrence(moment, text)
    console.write_state(home, next_push_at=_iso(next_at) if next_at else None)
    return {
        "ok": True,
        "push_time": text,
        "next_push_at": console.read_state(home).get("next_push_at"),
    }


def _slot_on(moment: datetime, text: str) -> datetime:
    hour, minute = (int(part) for part in text.split(":"))
    return datetime.combine(moment.date(), dt_time(hour, minute), tzinfo=moment.tzinfo)


def _next_occurrence(moment: datetime, text: str) -> datetime:
    candidate = _slot_on(moment, text)
    if moment >= candidate:
        candidate += timedelta(days=1)
    return candidate


# ------------------------------------------------------------------ defaults
def _default_run_daily(home: Path, intent: str = "daily", **kwargs):
    from hostrun import intents

    return intents.run(Path(home), intent or "daily", **kwargs)


def _default_run_agent(home: Path, task: str, **kwargs):
    from hostrun import agent_run

    return agent_run.run(Path(home), task, **kwargs)


def _thread_worker(function):
    thread = threading.Thread(target=function, name="console-service", daemon=True)
    thread.start()
    return thread


# -------------------------------------------------------------------- service
class Service:
    """The Console's scheduling and service-state machine.

    The tick worker calls :meth:`tick` once per second.  Tests call ``tick``
    directly with a fake clock; ``start_worker`` (the push worker) is injectable
    so a push can run synchronously in a test.
    """

    def __init__(
        self,
        home: Path,
        *,
        clock=None,
        emit=None,
        run_daily=None,
        run_agent=None,
        start_worker=None,
        ticker_worker=None,
        status_provider=None,
        config: dict | None = None,
        host: str | None = None,
        push_time: str | None = None,
    ):
        self.home = Path(home)
        self._clock = clock or _local_now
        self._emit = emit or (lambda level, source, message: console.emit_event(self.home, level, source, message))
        self._run_daily = run_daily or _default_run_daily
        self._run_agent = run_agent or _default_run_agent
        self._start_worker = start_worker or _thread_worker
        self._ticker_worker = ticker_worker or _thread_worker
        self._status_provider = status_provider
        self._config = config
        self._host = host
        self._push_time_override = push_time if valid_push_time(push_time) else None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._ticker: threading.Thread | None = None
        self._push_thread: threading.Thread | None = None
        self._push_running = False
        self._recovery_done = False
        self._unfinished_marked = False
        self._last_next_push_at: str | None = None
        self._gate_noted = False

    # -- small helpers ---------------------------------------------------
    def _config_now(self) -> dict:
        return self._config if isinstance(self._config, dict) else load_config(self.home)

    def _moment(self) -> datetime:
        return self._clock()

    def _log(self, level: str, source: str, message: str) -> None:
        try:
            self._emit(level, source, message)
        except Exception:  # noqa: BLE001 - logging never stops the service
            pass

    def _state(self) -> dict:
        return console.read_state(self.home)

    @property
    def paused(self) -> bool:
        return bool(self._state().get("paused"))

    def push_time(self) -> str:
        if self._push_time_override:
            return self._push_time_override
        return configured_push_time(
            self.home, state=self._state(), config=self._config_now()
        )

    def _host_name(self) -> str:
        if self._host:
            return str(self._host)
        return str((self._config_now().get("host") or {}).get("product") or "host")

    def _sync_next_push(self, moment: datetime, *, paused: bool | None = None) -> None:
        """Persist ``next_push_at`` only when it actually changes."""
        is_paused = self.paused if paused is None else bool(paused)
        desired = None
        if not is_paused:
            desired = _iso(_next_occurrence(moment, self.push_time()))
        if desired == self._last_next_push_at and self._state().get("next_push_at") == desired:
            return
        self._last_next_push_at = desired
        console.write_state(self.home, next_push_at=desired)

    # -- service state ---------------------------------------------------
    def restore(self) -> dict:
        """Apply the last persisted state: paused stays paused, running starts."""
        state = self._state()
        paused = bool(state.get("paused"))
        moment = self._moment()
        if paused:
            console.write_state(self.home, service="paused", paused=True, next_push_at=None)
            self._log("INFO", "service", "服务保持暂停（按上次状态恢复）")
        else:
            console.write_state(self.home, service="running", paused=False)
            self._log("INFO", "service", "服务已恢复（按上次状态自动启动）")
            self._sync_next_push(moment, paused=False)
        self._recovery_done = False
        return {"ok": True, "paused": paused, "service": "paused" if paused else "running"}

    def start(self) -> dict:
        """Start/resume the service; the next tick performs any catch-up."""
        moment = self._moment()
        with self._lock:
            console.write_state(self.home, service="running", paused=False)
            self._recovery_done = False
            self._unfinished_marked = False
            self._sync_next_push(moment, paused=False)
        self._log("INFO", "service", "服务已启动（定时推送与飞书响应已恢复）")
        return {
            "ok": True,
            "action": "start",
            "service": "running",
            "paused": False,
            "next_push_at": console.read_state(self.home).get("next_push_at"),
        }

    def pause(self) -> dict:
        """Pause the service: no scheduled push and no Feishu-derived run."""
        with self._lock:
            console.write_state(self.home, service="paused", paused=True, next_push_at=None)
            self._last_next_push_at = None
        self._log("INFO", "service", "服务已暂停（控制台仍在前台；定时推送与飞书响应已停止）")
        return {"ok": True, "action": "stop", "service": "paused", "paused": True}

    # -- push ------------------------------------------------------------
    def knowledge_gate(self) -> dict:
        """The daily-push gate: blocked until the knowledge base is initialized."""
        return console_gate.knowledge_gate(self.home)

    def _note_gate(self, gate: dict) -> None:
        """Log the blocked gate once per blocked episode, not on every tick."""
        if gate.get("ready"):
            self._gate_noted = False
            return
        if self._gate_noted:
            return
        self._gate_noted = True
        self._log("INFO", "service", f"每日推送被知识库门禁挡住：{gate.get('note') or ''}")

    def request_push(self, trigger: str = "manual") -> dict:
        """Ask for one push now (manual/control).  Refused while paused/busy/blocked."""
        with self._lock:
            if self.paused:
                self._log("INFO", "service", f"推送请求被拒绝（服务已暂停，trigger={trigger}）")
                return {"ok": False, "reason": "service_paused", "trigger": trigger}
            if self._push_running:
                return {"ok": False, "reason": "push_busy", "trigger": trigger}
            started = self._start_push(trigger)
            if not started.get("ok"):
                return {"ok": False, "reason": started.get("reason") or "push_refused", "trigger": trigger}
        return {"ok": True, "action": "push", "started": True, "trigger": trigger}

    def _start_push(self, trigger: str) -> dict:
        """Mark the day and start the push worker (lock already held)."""
        gate = self.knowledge_gate()
        if not gate.get("ready"):
            self._note_gate(gate)
            return {"ok": False, "reason": console_gate.REASON, "trigger": trigger}
        self._gate_noted = False
        moment = self._moment()
        console.write_state(
            self.home,
            last_push_date=moment.date().isoformat(),
            last_push_at=_iso(moment),
            last_push_trigger=trigger,
        )
        self._push_running = True
        # Any push (including a control/manual one) serves recovery for this
        # service session; it must not also be replayed by ``_maybe_recover``.
        self._recovery_done = True
        self._sync_next_push(moment)
        self._log("INFO", "service", f"触发推送（trigger={trigger}）")

        def runner() -> None:
            try:
                self._push_worker(trigger)
            except Exception as exc:  # noqa: BLE001 - a worker reports, never crashes
                self._log("ERROR", "service", f"推送运行异常：{type(exc).__name__}: {exc}")
            finally:
                with self._lock:
                    self._push_running = False

        try:
            started = self._start_worker(runner)
        except Exception as exc:  # noqa: BLE001 - report a failed worker start
            self._push_running = False
            self._log("WARN", "service", f"推送运行启动失败：{type(exc).__name__}: {exc}")
            return {"ok": False, "reason": "worker_failed"}
        if isinstance(started, threading.Thread):
            self._push_thread = started
        return {"ok": True, "started": True}

    def _push_worker(self, trigger: str) -> None:
        """Prepare the daily run and start at most one Agent run; no retry."""
        home = self.home
        host = self._host_name()
        acquired = executor.acquire(home, host=host, minutes=PUSH_LEASE_MINUTES)
        if not acquired.get("ok"):
            self._log(
                "WARN",
                "service",
                f"推送未启动：执行端租约不可用（{acquired.get('reason') or 'busy'}）",
            )
            return
        ok = False
        reason = ""
        try:
            prepared = self._run_daily(home, "daily", host=host, lease=False)
            if not prepared.get("ok"):
                reason = str(prepared.get("reason") or "run_failed")
                self._log("WARN", "service", f"推送准备失败（{reason}），不会自动重试")
                return
            brief = prepared.get("brief")
            if not brief:
                retrieval = prepared.get("retrieval") or {}
                reason = str(retrieval.get("reason") or "no_brief")
                self._log("INFO", "service", f"本次没有可派发的推送（{reason}）")
                return
            outcome = self._run_agent(
                home,
                str(brief),
                kind="push",
                host=host,
                lease=False,
                single_flight=True,
                should_stop=self._stop.is_set,
                on_wait=lambda: executor.heartbeat(home, host=host, minutes=PUSH_LEASE_MINUTES),
            )
            ok = bool(outcome.get("ok"))
            reason = str(outcome.get("reason") or ("ok" if ok else "child_failed"))
            if ok:
                self._log("INFO", "service", f"推送 Agent 运行完成（trigger={trigger}）")
            elif outcome.get("stopped"):
                self._log("WARN", "service", "推送 Agent 运行已终止（控制台关闭），不会自动重试")
            else:
                self._log("WARN", "service", f"推送 Agent 运行失败（{reason}），不会自动重试")
        finally:
            executor.release(home, host=host, ok=ok, reason=reason or "push")
            if self._stop.is_set():
                self._mark_unfinished()

    # -- recovery / shutdown --------------------------------------------
    def _pending_request(self) -> dict:
        try:
            store = QueueStore(self.home)
            store.init()
            payload = read_json(store.request_path)
            return payload if isinstance(payload, dict) else {}
        except Exception:  # noqa: BLE001 - recovery must not block the service
            return {}

    def _maybe_recover(self, moment: datetime) -> dict | None:
        """Serve a preserved request; drain one queued during the last push.

        Recovery is once per start for a shutdown-interrupted request, but a
        request that arrived while a push was running (``after_start`` in the
        queue record) is preserved and drained on the next tick after that push
        finishes, instead of waiting for a console restart (S4).
        """
        request = self._pending_request()
        if not request or self._push_running:
            return None
        last_error = str(request.get("last_error") or "")
        if last_error and last_error != PUSH_SHUTDOWN_REASON:
            # US35: a failed Agent run is reported, never retried automatically.
            # The queue record stays visible in /horizon-status so the owner
            # can decide to request a push again.
            self._recovery_done = True
            self._log(
                "WARN",
                "service",
                "发现上次失败的推送请求"
                f"（reason={last_error}，attempts={request.get('attempts') or 0}），"
                "按“不自动重试”保留，不恢复；可用 /horizon-push 重新请求",
            )
            return None
        # A still-started record belongs to the run that is finishing; wait for
        # its success/failure cleanup before deciding to drain it.
        if self._recovery_done and request.get("started_at"):
            return None
        queued_during_push = self._recovery_done
        self._recovery_done = True
        self._log(
            "INFO",
            "service",
            "发现上次未完成的推送请求，恢复一次"
            if not queued_during_push
            else "发现推送运行期间记录的下一篇请求，继续派发一次",
        )
        with self._lock:
            if self.paused or self._push_running:
                return None
            started = self._start_push("recovery" if not queued_during_push else "queued")
            if not started.get("ok"):
                return None
        return {"pushed": "recovery" if not queued_during_push else "queued"}

    def _mark_unfinished(self) -> None:
        """Mark the started request ``unfinished`` using the existing queue record."""
        with self._lock:
            if self._unfinished_marked:
                return
            self._unfinished_marked = True
        request = self._pending_request()
        if not request or not (request.get("started_at") or request.get("lease_until")):
            return
        try:
            store = QueueStore(self.home)
            store.init()
            outcome = store.finish_start(False, PUSH_SHUTDOWN_REASON)
        except Exception as exc:  # noqa: BLE001 - marking must not block the shutdown
            self._log("WARN", "service", f"未完成请求标记失败：{type(exc).__name__}: {exc}")
            return
        attempts = (read_json(store.request_path) or {}).get("attempts")
        self._log(
            "WARN",
            "service",
            "在飞推送已终止；当前请求标记为未完成"
            f"（reason={PUSH_SHUTDOWN_REASON}, attempts={attempts}），下次启动将续接",
        )

    def shutdown(self) -> dict:
        """Window close: stop scheduling, terminate the run, flush every lock."""
        self._stop.set()
        current = threading.current_thread()
        for thread in (self._ticker, self._push_thread):
            if thread is None or thread is current or not thread.is_alive():
                continue
            try:
                thread.join(timeout=SHUTDOWN_JOIN_SECONDS)
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass
        self._mark_unfinished()
        state = self._state()
        paused = bool(state.get("paused"))
        console.write_state(self.home, service="stopped", paused=paused, next_push_at=None)
        self._last_next_push_at = None
        self._log("INFO", "service", "服务已停止（在飞运行已终止、状态已写入、锁已释放）")
        return {
            "ok": True,
            "action": "shutdown",
            "paused": paused,
            "push_running": self._push_running,
            "service": "stopped",
        }

    # -- ticker ----------------------------------------------------------
    def start_ticker(self) -> dict:
        """Start the once-per-second worker; the first tick runs immediately."""
        if self._ticker is not None and self._ticker.is_alive():
            return {"ok": True, "started": False}
        try:
            started = self._ticker_worker(self._tick_loop)
        except Exception as exc:  # noqa: BLE001 - report, keep the prompt usable
            self._log("WARN", "service", f"调度工作线程启动失败：{type(exc).__name__}: {exc}")
            return {"ok": False, "reason": "ticker_failed"}
        if isinstance(started, threading.Thread):
            self._ticker = started
        return {"ok": True, "started": True}

    def _tick_loop(self) -> None:
        try:
            self.tick()
        except Exception:  # noqa: BLE001 - one bad beat is not a dead console
            pass
        while not self._stop.wait(CONTROL_TICK_SECONDS):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - a bad beat is logged, not fatal
                pass

    def tick(self, moment: datetime | None = None) -> dict:
        """One beat: consume the control file, then schedule/recover."""
        moment = moment or self._moment()
        result: dict = {
            "at": _iso(moment),
            "control": None,
            "pushed": None,
            "paused": self.paused,
        }
        with self._lock:
            if self._stop.is_set():
                return result
            control = self._consume_control(moment)
            if control is not None:
                result["control"] = control
            if not self.paused and not self._stop.is_set():
                recovered = self._maybe_recover(moment)
                if recovered:
                    result["pushed"] = recovered["pushed"]
                else:
                    result["pushed"] = self._maybe_schedule(moment)
            self._sync_next_push(moment)
            result["paused"] = self.paused
            result["next_push_at"] = console.read_state(self.home).get("next_push_at")
        return result

    def _maybe_schedule(self, moment: datetime) -> str | None:
        state = self._state()
        if state.get("paused"):
            return None
        push_time = self.push_time()
        if moment < _slot_on(moment, push_time):
            return None
        today = moment.date().isoformat()
        if str(state.get("last_push_date") or "") == today:
            return None
        if self._push_running:
            return None
        started = self._start_push("scheduled")
        return "scheduled" if started.get("ok") else None

    # -- control consumption --------------------------------------------
    def _consume_control(self, moment: datetime) -> dict | None:
        payload = read_control(self.home)
        if not payload:
            return None
        path = control_path(self.home)
        action = str(payload.get("action") or "").strip().lower()
        nonce = str(payload.get("nonce") or "")
        initiator = str(payload.get("initiator") or "") or "未知"
        state = self._state()
        if nonce and nonce == str(state.get("last_control_nonce") or ""):
            self._log("WARN", "control", f"重复的控制请求已忽略（action={action}, nonce={nonce}）")
            path.unlink(missing_ok=True)
            return {"action": "duplicate", "ok": True, "nonce": nonce}
        requested = _as_datetime(payload.get("requested_at"))
        if requested is None:
            self._log("WARN", "control", f"控制请求缺少有效时间，已忽略（action={action}）")
            path.unlink(missing_ok=True)
            return {"action": "invalid", "ok": False, "nonce": nonce}
        if requested.tzinfo is None:
            requested = requested.replace(tzinfo=moment.tzinfo)
        age = (moment - requested).total_seconds()
        if age > CONTROL_TTL_SECONDS:
            self._log(
                "WARN",
                "control",
                f"控制请求已过期（action={action}，{int(age)} 秒前），已忽略",
            )
            path.unlink(missing_ok=True)
            return {"action": "expired", "ok": False, "nonce": nonce}
        if action not in CONTROL_ACTIONS:
            self._log("WARN", "control", f"未知控制动作 {action!r}，已忽略")
            path.unlink(missing_ok=True)
            return {"action": "invalid", "ok": False, "nonce": nonce}

        self._log("INFO", "control", f"收到控制请求：{action}（发起者：{initiator}）")
        console.write_state(self.home, last_control_nonce=nonce, control_at=_iso(moment))
        try:
            if action == "start":
                outcome = self.start()
            elif action == "stop":
                outcome = self.pause()
            elif action == "push":
                outcome = self.request_push("control")
            else:
                outcome = self.status()
        except Exception as exc:  # noqa: BLE001 - report a failed control request
            self._log(
                "WARN",
                "control",
                f"控制请求执行失败：{type(exc).__name__}: {exc}",
            )
            outcome = {"ok": False, "reason": "control_failed", "error": str(exc)}
        response = {
            "schema_version": 1,
            "nonce": nonce,
            "action": action,
            "initiator": initiator,
            "at": _iso(moment),
            "ok": bool(outcome.get("ok")),
            "result": outcome,
        }
        write_json(control_response_path(self.home), response)
        path.unlink(missing_ok=True)
        return {
            "action": action,
            "ok": bool(outcome.get("ok")),
            "nonce": nonce,
            "initiator": initiator,
            "result": outcome,
        }

    def status(self) -> dict:
        """The same read-only snapshot ``/horizon-status`` renders."""
        snapshot = console.status_snapshot(self.home, status=self._status_provider)
        state = self._state()
        snapshot["schedule"] = {
            "push_time": self.push_time(),
            "last_push_date": state.get("last_push_date"),
            "last_push_at": state.get("last_push_at"),
            "last_push_trigger": state.get("last_push_trigger"),
        }
        snapshot["knowledge_gate"] = self.knowledge_gate()
        return snapshot


# ------------------------------------------------------------------ CLI helper
def run_control(
    home: Path,
    action: str,
    *,
    initiator: str = "",
    wait_seconds: float = 2.0,
    clock=None,
    sleep=None,
) -> dict:
    """Write one control request and wait (bounded) for the console's response.

    ``status`` falls back to a local read-only snapshot when no console is
    running, so the host agent always gets an answer.  ``start``/``stop``/``push``
    report ``consumed: false`` rather than pretending the console acted.
    """
    home = Path(home)
    name = str(action or "").strip().lower()
    if name not in CONTROL_ACTIONS:
        return {"ok": False, "reason": "unknown_action", "action": name}
    request = write_control(home, name, initiator=initiator, clock=clock)
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    pause = sleep or time.sleep
    response = {}
    while True:
        candidate = read_control_response(home)
        if candidate and candidate.get("nonce") == request["nonce"]:
            response = candidate
            break
        if time.monotonic() >= deadline:
            break
        pause(0.05)
    if response:
        result = response.get("result") or {}
        payload = {
            "ok": bool(response.get("ok")),
            "action": name,
            "consumed": True,
            "initiator": initiator,
            "request": {"nonce": request["nonce"], "requested_at": request["requested_at"]},
            "result": result,
        }
        if name == "status":
            payload["status"] = result
        return payload
    if name == "status":
        return {
            "ok": True,
            "action": name,
            "consumed": False,
            "initiator": initiator,
            "request": {"nonce": request["nonce"], "requested_at": request["requested_at"]},
            "status": console.status_snapshot(home),
        }
    return {
        "ok": True,
        "action": name,
        "consumed": False,
        "initiator": initiator,
        "note": "控制台未运行或尚未消费；请求已写入控制文件（5 分钟内有效）。",
        "request": {"nonce": request["nonce"], "requested_at": request["requested_at"]},
    }
