"""Console shell: the single resident foreground program (ADR 0009 §1/§3).

This module is the frontend only.  It owns the process-level shell — the
single-instance lock, the heartbeat, the rolling log, the linear event stream,
the slash prompt and the command dispatcher — and nothing else:

- the Feishu event surface (two subscriptions plus the 5/10-minute mechanical
  ticks) lives in :mod:`hostrun.console_events`; ``serve(events=...)`` starts and
  stops it around the prompt loop (#80);
- scheduling and service pause/resume belong to later tickets; the commands that
  need them are registered here as explicit stubs, so an unknown instruction is
  never mistaken for an unimplemented one;
- ``/horizon-status`` reads the persisted runtime area (``intents.status`` plus
  the console state file) instead of probing the system again;
- ``/horizon-logs`` tails the most recent ``logs/agent-<run_id>.log`` written by
  :mod:`hostrun.agent_run`.

The shell is injectable end to end (``isatty``, ``reader``, ``out``,
``set_title``, ``status``, ``start_worker``, and ``emit_event(at=...)``), so the
whole loop is driven offline by tests.  Real TTY behaviour, the Windows window
title and the 60-second heartbeat cadence are live-only and stay ``untested``
until recorded.

Every event line carries a timestamp, a level and a source, on the terminal and
in ``<runtime>/console/console.log``; the log rolls over at 1 MB.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from common import ensure_dir, file_lock, now, read_json, write_json
from hostrun import console_gate, launcher
from hostrun.base import load_config, update_config

SCRIPT_NAME = "horizon-console.cmd"
WINDOW_TITLE = "horizon-console"
PROMPT = "horizon> "

LOCK_NAME = "console.lock"
HEARTBEAT_NAME = "heartbeat.json"
LOG_NAME = "console.log"
LOG_BACKUP_NAME = "console.log.1"
STATE_NAME = "state.json"

HEARTBEAT_SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 1
DEFAULT_HEARTBEAT_SECONDS = 60
LOG_MAX_BYTES = 1024 * 1024
AGENT_LOG_TAIL_LINES = 40
AGENT_LOG_GLOB = "agent-*.log"

LEVELS = ("INFO", "WARN", "ERROR")

#: ``name`` -> summary + whether this ticket implements it.  Order is the help
#: order.  Later tickets flip ``implemented`` and add a handler; the parser and
#: the help text then follow automatically.
COMMANDS = {
    "/horizon-help": {
        "summary": "显示可用控制台指令",
        "implemented": True,
    },
    "/horizon-status": {
        "summary": "查看服务状态、下一次推送与队列概览",
        "implemented": True,
    },
    "/horizon-logs": {
        "summary": "查看最近一次 Agent 运行的日志尾部",
        "implemented": True,
    },
    "/horizon-start": {
        "summary": "启动服务（启动或从暂停恢复）",
        "implemented": True,
    },
    "/horizon-stop": {
        "summary": "暂停服务但保持控制台运行",
        "implemented": True,
    },
    "/horizon-push": {
        "summary": "立即请求一次推送",
        "implemented": True,
    },
    "/horizon-init": {
        "summary": "初始化资料目录、执行 Agent、Lark CLI 与飞书测试消息（后续步骤逐步接入）",
        "implemented": True,
    },
    "/horizon-update-kb": {
        "summary": "读取资料并逐条审阅知识卡片",
        "implemented": True,
    },
    "/exit": {
        "summary": "退出控制台（关闭窗口同样停止服务）",
        "implemented": True,
    },
}

#: Serialises terminal writes and log appends across the heartbeat worker and
#: the prompt loop, so a rolling log line is never interleaved or truncated.
LOG_LOCK = threading.Lock()


# --------------------------------------------------------------------- paths
def console_dir(home: Path) -> Path:
    """The console's private state directory (``<runtime>/console``)."""
    return Path(home) / "console"


def lock_path(home: Path) -> Path:
    return console_dir(home) / LOCK_NAME


def heartbeat_path(home: Path) -> Path:
    return console_dir(home) / HEARTBEAT_NAME


def log_path(home: Path) -> Path:
    return console_dir(home) / LOG_NAME


def state_path(home: Path) -> Path:
    return console_dir(home) / STATE_NAME


def script_path(code: str | Path) -> Path:
    """``<code>/bin/horizon-console.cmd``: the fixed double-click launcher."""
    return Path(code) / "bin" / SCRIPT_NAME


# ------------------------------------------------------------- startup script
def script_text(code: str | Path, archive: str | Path, python: str) -> str:
    """The launcher argv, identical to the one ``install migrate`` writes.

    ``%*`` forwards arguments, so ``horizon-console control start`` reaches
    ``python -m hostrun.cli ... console control start`` while a double-click
    still starts the resident console with no arguments (#81).

    A package installation delegates to ``<code>/bin/horizon.cmd`` so the
    launcher keeps using the bundled runtime rather than a system interpreter;
    a source installation keeps the recorded absolute interpreter form byte for
    byte (#95).
    """
    return launcher.console_script_text(
        code,
        archive,
        python,
        package=launcher.package_mode(code),
    )


def ensure_script(code: str | Path, archive: str | Path, python: str) -> dict:
    """Create or refresh ``<code>/bin/horizon-console.cmd`` idempotently.

    The file is written only when its text changed, so starting the console
    repeatedly does not touch the user's launcher.  A read-only checkout raises
    the underlying ``OSError``; the caller decides how to report it.
    """
    path = script_path(code)
    text = script_text(code, archive, python)
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == text:
                return {"ok": True, "path": str(path), "written": False, "title": WINDOW_TITLE}
        except OSError:
            pass
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")
    return {"ok": True, "path": str(path), "written": True, "title": WINDOW_TITLE}


def install(home: Path, *, resolved: dict, python_path: str | None = None) -> dict:
    """Prepare the Horizon launcher and check the bundled Agent; no process starts."""
    from hostrun import embedded_agent
    code, archive = Path(resolved["code"]), Path(resolved["archive"])
    python = str(python_path or sys.executable)
    update_config(home, paths={"repo": str(code), "python": python},
                  host={"product": "horizon", "mode": "embedded-pi"})
    facts = embedded_agent.runtime(home, code=code)
    try:
        script = ensure_script(code, archive, python)
    except OSError as exc:
        script = {"ok": False, "error": str(exc)}
    ok = facts["ok"] and script.get("ok", False)
    return {"schema_version": 1, "ok": ok, "status": "ok" if ok else "blocked",
            "note": "Horizon 内置 Pi Agent；首次启动时配置模型并绑定飞书" if ok else "；".join(facts["problems"] + [script.get("error", "")]),
            "agent": facts, "script": script, "processes_started": False}


# ------------------------------------------------------------------ state file
def _default_state() -> dict:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "service": "stopped",
        "paused": False,
        "next_push_at": None,
        "push_time": None,
        "started_at": None,
        "updated_at": None,
        "last_command_at": None,
    }


def read_state(home: Path) -> dict:
    """The persisted console state; a missing file means "not running"."""
    stored = read_json(state_path(home)) or {}
    state = _default_state()
    if isinstance(stored, dict):
        state.update(stored)
    state["paused"] = bool(state.get("paused"))
    return state


def write_state(home: Path, **fields) -> dict:
    """Merge one state update and persist it atomically."""
    state = read_state(home)
    state.update(fields)
    state["schema_version"] = STATE_SCHEMA_VERSION
    state["paused"] = bool(state.get("paused"))
    if "paused" in fields and "service" not in fields:
        state["service"] = "paused" if state["paused"] else "running"
    state["updated_at"] = fields.get("updated_at") or now()
    write_json(state_path(home), state)
    return state


# --------------------------------------------------------------- heartbeat
def read_heartbeat(home: Path) -> dict:
    payload = read_json(heartbeat_path(home)) or {}
    return payload if isinstance(payload, dict) else {}


def write_heartbeat(
    home: Path,
    *,
    started_at: str | None = None,
    state: str = "running",
    last_event_at: str | None = None,
    last_success_at: str | None = None,
    consecutive_failures: int = 0,
    last_error: str = "",
    pid: int | None = None,
    at: str | None = None,
) -> dict:
    """Rewrite the console heartbeat with the same fields as the retired one."""
    beat = {
        "schema_version": HEARTBEAT_SCHEMA_VERSION,
        "pid": int(pid if pid is not None else os.getpid()),
        "started_at": started_at or now(),
        "updated_at": at or now(),
        "state": str(state or "running"),
        "last_event_at": last_event_at,
        "last_success_at": last_success_at,
        "consecutive_failures": int(consecutive_failures or 0),
        "last_error": str(last_error or ""),
    }
    write_json(heartbeat_path(home), beat)
    return beat


# -------------------------------------------------------------- event stream
def format_event(at: str, level: str, source: str, message: str) -> str:
    """One linear event line: timestamp, level, source, message."""
    return f"{at} {level} [{source}] {message}"


def _append_log(home: Path, line: str) -> None:
    """Append one rolling log line; logging never takes the console down."""
    path = log_path(home)
    ensure_dir(path.parent)
    try:
        with LOG_LOCK:
            if path.exists() and path.stat().st_size >= LOG_MAX_BYTES:
                backup = console_dir(home) / LOG_BACKUP_NAME
                try:
                    backup.unlink()
                except FileNotFoundError:
                    pass
                path.replace(backup)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line.rstrip("\r\n") + "\n")
    except OSError:
        pass


def emit_event(
    home: Path,
    level: str,
    source: str,
    message: str,
    *,
    at: str | None = None,
    out=None,
) -> dict:
    """Write one event to the terminal stream and the rolling log.

    ``out`` is the injectable terminal handle; the default is ``sys.stdout``.
    Returns the event dict so a caller (or a test) can assert the exact line.
    """
    stamp = str(at or now())
    name = str(level or "INFO").upper()
    if name not in LEVELS:
        name = "INFO"
    event = {
        "at": stamp,
        "level": name,
        "source": str(source or "console"),
        "message": str(message),
    }
    event["line"] = format_event(stamp, name, event["source"], event["message"])
    _append_log(home, event["line"])
    target = sys.stdout if out is None else out
    try:
        target.write(event["line"] + "\n")
        target.flush()
    except (OSError, ValueError, AttributeError):
        pass
    return event


# ------------------------------------------------------------------ command set
def parse_command(line: str) -> dict:
    """Classify one prompt line without executing anything.

    Known commands are matched on the first whitespace token, case-insensitively;
    every other input is ``unknown_command`` (or ``empty``) so the caller can
    print usage instead of running something.
    """
    raw = "" if line is None else str(line)
    text = raw.strip()
    if not text:
        return {"ok": False, "reason": "empty", "command": "", "args": [], "raw": raw}
    if not text.startswith("/"):
        return {"ok": False, "reason": "unknown_command", "command": "", "args": [], "raw": raw}
    parts = text.split()
    name = parts[0].lower()
    if name not in COMMANDS:
        return {
            "ok": False,
            "reason": "unknown_command",
            "command": name,
            "args": parts[1:],
            "raw": raw,
        }
    return {"ok": True, "command": name, "args": parts[1:], "raw": raw}


def help_text() -> str:
    lines = ["可用控制台指令："]
    for name, spec in COMMANDS.items():
        suffix = "" if spec["implemented"] else "（尚未实现）"
        lines.append(f"  {name:<22} {spec['summary']}{suffix}")
    return "\n".join(lines)


def usage_hint(command: str = "") -> str:
    prefix = f"未知指令 {command}。" if command else "指令必须以 / 开头。"
    return f"{prefix}\n输入 /horizon-help 查看可用指令。"


def _not_implemented(name: str) -> str:
    return (
        f"{name} 尚未实现：该能力由后续票据接入，本次不执行任何操作。\n"
        "输入 /horizon-help 查看当前可用指令。"
    )


# -------------------------------------------------------------------- status
def status_snapshot(home: Path, *, status=None) -> dict:
    """One read-only snapshot: console, service state, executor, queue, knowledge.

    ``intents.status`` remains the single source for the host subsystems; the
    console only adds the persisted service state and its own heartbeat view.
    """
    provider = status or intents_status
    subsystems = provider(Path(home)) or {}
    state = read_state(home)
    beat = read_heartbeat(home)
    install_section = subsystems.get("install") or {}
    steps = install_section.get("steps") or {}
    paused = bool(state.get("paused"))
    gate = console_gate.knowledge_gate(Path(home))
    return {
        "console": {
            "state": "stopped" if state.get("service") == "stopped" else "running",
            "pid": beat.get("pid") or os.getpid(),
            "heartbeat_at": beat.get("updated_at"),
        },
        "service": {
            "state": "paused" if paused else "running",
            "paused": paused,
            "next_push_at": state.get("next_push_at"),
            "push_time": state.get("push_time"),
        },
        "executor": subsystems.get("executor") or {},
        "queue": subsystems.get("queue") or {},
        "knowledge": steps.get("knowledge") or "unknown",
        "knowledge_gate": gate,
        "at": now(),
    }


def status_text(snapshot: dict) -> str:
    console_state = snapshot.get("console") or {}
    service = snapshot.get("service") or {}
    queue = snapshot.get("queue") or {}
    executor_state = (snapshot.get("executor") or {}).get("state") or "unknown"
    paused = bool(service.get("paused"))
    next_push = service.get("next_push_at") or "未排期"
    yes = lambda value: "是" if value else "否"  # noqa: E731 - compact table
    gate = snapshot.get("knowledge_gate") or {}
    gate_ready = bool(gate.get("ready", True))
    gate_text = "已就绪" if gate_ready else "待初始化知识库（每日推送已阻塞）"
    lines = [
        f"控制台：{'已停止' if console_state.get('state') == 'stopped' else '运行中'}"
        f"（pid={console_state.get('pid')}）",
        f"服务：{'已暂停' if paused else '运行中'}",
        f"暂停：{yes(paused)}",
        f"下一次推送：{next_push}",
        f"执行端：{executor_state}",
        f"队列：{queue.get('items', 0)} 项，未完成：{yes(queue.get('unfinished'))}，"
        f"失败：{yes(queue.get('failed'))}",
        f"知识：{snapshot.get('knowledge') or 'unknown'}",
        f"知识库门禁：{gate_text}",
    ]
    if not gate_ready:
        lines.append(f"  下一步：{gate.get('note') or '/horizon-update-kb'}")
    request = queue.get("request") or {}
    last_error = str(request.get("last_error") or "")
    if last_error and last_error != "console_shutdown":
        lines.append(
            f"最近一次推送失败：{last_error}"
            f"（尝试 {request.get('attempts') or 0} 次；不会自动重试，"
            "需要重试请执行 /horizon-push）"
        )
    return "\n".join(lines)


# ------------------------------------------------------------------ log tail
def tail_agent_log(home: Path, *, lines: int = AGENT_LOG_TAIL_LINES) -> dict:
    """The tail of the most recently modified ``logs/agent-<run_id>.log``."""
    logs_dir = Path(home) / "logs"
    candidates = sorted(
        logs_dir.glob(AGENT_LOG_GLOB),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    ) if logs_dir.is_dir() else []
    if not candidates:
        return {
            "ok": False,
            "reason": "no_agent_log",
            "path": "",
            "lines": [],
            "text": "暂无 Agent 运行日志。",
        }
    path = candidates[0]
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "ok": False,
            "reason": "log_unreadable",
            "path": str(path),
            "lines": [],
            "text": f"日志不可读：{path}（{type(exc).__name__}）",
        }
    tail = content.splitlines()[-max(1, int(lines)) :]
    return {
        "ok": True,
        "reason": "",
        "path": str(path),
        "lines": tail,
        "text": "\n".join(tail) if tail else f"日志为空：{path}",
    }


# ------------------------------------------------------------------ dispatch
def intents_status(home: Path) -> dict:
    """Late import so tests can inject a status provider without a data root."""
    from hostrun import intents

    return intents.status(Path(home))


def dispatch(
    home: Path,
    line: str,
    *,
    status=None,
    logs=None,
    service=None,
    init=None,
    update_kb=None,
    review=None,
) -> dict:
    """Route one prompt line to one bounded action and return its text.

    The dispatcher never raises on a bad command.  The lifecycle commands
    (``/horizon-start|stop|push``) delegate to :class:`hostrun.console_service.Service`;
    when a test or a plain dispatch call supplies no service, a one-shot service
    for the same runtime area is used.  ``/horizon-init`` delegates to the
    initialization step machine (:mod:`hostrun.console_init`); ``init`` is an
    injectable zero-argument runner so the console can hand it the prompt's
    reader/out/TTY seams.  ``/horizon-update-kb`` delegates to the segmented
    read (:mod:`hostrun.update_kb`), which pauses and restores the service around
    the read; ``update_kb`` is an injectable runner with the same call shape.
    When the read completed, the per-item review (:mod:`hostrun.update_kb_review`)
    consumes the candidate area through the injectable ``review`` runner; the
    console shell binds it to the prompt's output/TTY seams.  An interrupted read
    is not reviewed (its pending shards resume first).
    A registered-but-not-yet-implemented command reports that explicitly.
    """
    parsed = parse_command(line)
    if not parsed["ok"]:
        reason = parsed["reason"]
        return {
            "ok": False,
            "action": "empty" if reason == "empty" else "unknown",
            "reason": reason,
            "command": parsed["command"],
            "text": "" if reason == "empty" else usage_hint(parsed["command"]),
        }

    name = parsed["command"]
    spec = COMMANDS[name]
    if not spec["implemented"]:
        return {
            "ok": False,
            "action": "not_implemented",
            "reason": "not_implemented",
            "command": name,
            "text": _not_implemented(name),
        }
    if name == "/horizon-help":
        return {"ok": True, "action": "help", "command": name, "text": help_text()}
    if name == "/horizon-status":
        try:
            snapshot = status_snapshot(home, status=status)
            text = status_text(snapshot)
        except Exception as exc:  # noqa: BLE001 - a status read must not kill the console
            return {
                "ok": False,
                "action": "status",
                "reason": "status_failed",
                "command": name,
                "text": f"状态读取失败：{type(exc).__name__}: {exc}",
            }
        return {"ok": True, "action": "status", "command": name, "text": text}
    if name == "/horizon-logs":
        try:
            tail = (logs or tail_agent_log)(home)
        except Exception as exc:  # noqa: BLE001 - a log read must not kill the console
            return {
                "ok": False,
                "action": "logs",
                "reason": "logs_failed",
                "command": name,
                "text": f"日志读取失败：{type(exc).__name__}: {exc}",
            }
        return {
            "ok": True,
            "action": "logs",
            "command": name,
            "text": tail.get("text") or "暂无 Agent 运行日志。",
            "path": tail.get("path") or "",
        }
    if name in ("/horizon-start", "/horizon-stop", "/horizon-push"):
        from hostrun import console_service

        lifecycle = service if service is not None else console_service.Service(home)
        if name == "/horizon-start":
            outcome = lifecycle.start()
            text = "服务已启动；定时推送与飞书响应已恢复。"
        elif name == "/horizon-stop":
            outcome = lifecycle.pause()
            text = "服务已暂停；控制台仍在前台，定时推送与飞书响应已停止。"
        else:
            outcome = lifecycle.request_push("manual")
            if outcome.get("ok"):
                text = "已发起一次推送请求。"
            elif outcome.get("reason") == "service_paused":
                text = "服务已暂停，未派发推送；先执行 /horizon-start。"
            elif outcome.get("reason") == "push_busy":
                text = "已有推送正在运行，稍后再试。"
            elif outcome.get("reason") == "knowledge_not_ready":
                text = (
                    "每日推送被知识库门禁挡住：待初始化知识库。"
                    "先执行 /horizon-update-kb 完成知识库初始化（完成后自动放行）。"
                )
            else:
                text = "推送请求未派发：" + str(outcome.get("reason") or "unknown")
        return {
            "ok": bool(outcome.get("ok")),
            "action": name.lstrip("/").replace("horizon-", ""),
            "reason": outcome.get("reason") or "",
            "command": name,
            "text": text,
            "service": outcome,
        }
    if name == "/horizon-init":
        runner = init
        if runner is None:
            from hostrun import console_init

            outcome = console_init.run(home)
        else:
            try:
                outcome = runner()
            except Exception as exc:  # noqa: BLE001 - init must not kill the console
                outcome = {
                    "ok": False,
                    "reason": "init_failed",
                    "text": f"初始化失败：{type(exc).__name__}: {exc}",
                }
        if not isinstance(outcome, dict):
            outcome = {"ok": False, "reason": "init_failed", "text": "初始化未返回结果。"}
        return {
            "ok": bool(outcome.get("ok")),
            "action": "init",
            "reason": outcome.get("reason") or "",
            "command": name,
            "text": str(outcome.get("text") or ""),
            "init": outcome,
        }
    if name == "/horizon-update-kb":
        from hostrun import update_kb as update_kb_mod

        source_arg = " ".join(parsed["args"]).strip().strip('"')
        runner = update_kb if update_kb is not None else update_kb_mod.run
        deferred = review is not None and service is not None
        try:
            extra = {}
            if deferred:
                # #90 owns the pause bracket: it restores only after the
                # review (and before the first-push drill), so the scheduler
                # cannot fire while the owner is still deciding (US25).
                extra["restore_service"] = False
            outcome = runner(
                home,
                source_dir=source_arg or None,
                service=service,
                **extra,
            )
        except Exception as exc:  # noqa: BLE001 - a failed read must not kill the console
            return {
                "ok": False,
                "action": "update_kb",
                "reason": "update_kb_failed",
                "command": name,
                "text": f"知识库读取失败：{type(exc).__name__}: {exc}",
            }
        review_outcome = None
        if (
            review is not None
            and outcome.get("ok")
            and not outcome.get("pending_shards")
        ):
            try:
                review_outcome = review(home, service=service)
            except Exception as exc:  # noqa: BLE001 - review must not kill the console
                review_outcome = {
                    "ok": False,
                    "reason": "review_failed",
                    "text": f"逐条审阅失败：{type(exc).__name__}: {exc}",
                }
        # A deferred pause must never be left behind: when no review ran (read
        # cancelled/failed/interrupted) or the review itself failed before
        # restoring, release it here.
        if deferred and not (
            isinstance(review_outcome, dict) and "restored" in review_outcome
        ):
            if not outcome.get("paused_before"):
                try:
                    restored = service.start()
                    outcome["restored"] = bool((restored or {}).get("ok", True))
                except Exception as exc:  # noqa: BLE001 - restore is reported
                    outcome["restored"] = False
                    outcome["restore_error"] = f"{type(exc).__name__}: {exc}"
        text = outcome.get("text") or "知识库更新已结束。"
        if isinstance(review_outcome, dict) and review_outcome.get("text"):
            text = f"{text}\n{review_outcome['text']}"
        return {
            "ok": bool(outcome.get("ok"))
            and (review_outcome is None or bool(review_outcome.get("ok"))),
            "action": "update_kb",
            "reason": (review_outcome or {}).get("reason")
            or outcome.get("reason")
            or "",
            "command": name,
            "text": text,
            "update_kb": outcome,
            "review": review_outcome,
        }
    if name == "/exit":
        return {
            "ok": True,
            "action": "exit",
            "command": name,
            "text": "正在退出控制台。",
            "exit": True,
        }
    # Reachable only if a stub is flipped to implemented without a handler.
    return {
        "ok": False,
        "action": "not_implemented",
        "reason": "not_implemented",
        "command": name,
        "text": _not_implemented(name),
    }


# ------------------------------------------------------------------ title
def set_console_title(title: str, *, platform: str | None = None) -> bool:
    """Set the Windows console title; a guarded no-op everywhere else."""
    if (platform or os.name) != "nt":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleTitleW.argtypes = [ctypes.c_wchar_p]
        kernel32.SetConsoleTitleW.restype = ctypes.c_bool
        return bool(kernel32.SetConsoleTitleW(str(title)))
    except Exception:  # noqa: BLE001 - a missing console is not a console failure
        return False


# ------------------------------------------------------------------ heartbeat worker
def _heartbeat_loop(home: Path, beat: dict, stop: threading.Event, interval: int) -> None:
    while not stop.wait(max(1, int(interval))):
        try:
            write_heartbeat(home, **beat)
        except OSError:
            return


def _start_daemon(function) -> threading.Thread:
    worker = threading.Thread(target=function, name="console-heartbeat", daemon=True)
    worker.start()
    return worker


def _default_reader(prompt: str) -> str:
    return input()


def _default_isatty() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


# -------------------------------------------------------------------- serve
def serve(
    home: Path,
    *,
    resolved: dict | None = None,
    isatty=None,
    reader=None,
    out=None,
    title: str = WINDOW_TITLE,
    set_title=None,
    status=None,
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    start_worker=None,
    events=None,
    service=None,
    init=None,
    review=None,
) -> dict:
    """Run the resident console shell until ``/exit``, EOF or Ctrl+C.

    Refusals happen before the prompt: a non-TTY stdin is rejected with the
    launcher hint, and a second instance is rejected by the single-instance
    lock.  Once running, every command is dispatched, rendered on the terminal,
    recorded in the rolling log, and mirrored in the heartbeat/state files.  The
    lock is always released and the heartbeat is left as ``stopped``.

    ``events`` is the Feishu event surface (#80).  It may be an already-built
    object with ``start()``/``stop()``, or a factory called as
    ``events(home, out=out)``.  The default is ``None``: the shell then runs
    without an event surface, which keeps offline shell tests free of every
    subprocess and lets the CLI decide when the live surface is wanted.

    ``service`` is the lifecycle scheduler (#81); the default builds one for the
    same runtime area.  It restores the last service state, starts the one-second
    tick worker, dispatches the lifecycle slash commands, and shuts down (kill
    in-flight run, release lease, mark the request unfinished) in ``finally``.

    ``init`` is an optional zero-argument runner for ``/horizon-init``.  The
    default binds the interactive step machine to the same reader/out/TTY seams
    as the prompt, so tests can drive a whole initialization through ``serve``.

    ``review`` is an optional runner for ``/horizon-update-kb``'s per-item
    review.  The default binds the single-key review to the same out/TTY seams as
    the prompt; the key source stays the review module's live reader unless a
    test injects one.
    """
    home = Path(home)
    ensure_dir(console_dir(home))
    out = sys.stdout if out is None else out
    detect_tty = isatty or _default_isatty
    read = reader or _default_reader
    title_setter = set_title or set_console_title
    start_worker = start_worker or _start_daemon
    tty = False
    try:
        tty = bool(detect_tty())
    except Exception:  # noqa: BLE001 - an undetectable stdin is not a TTY
        tty = False

    layout = resolved if isinstance(resolved, dict) else {}
    code = layout.get("code")
    archive = layout.get("archive")
    script_hint = str(script_path(code)) if code else SCRIPT_NAME

    if not tty:
        note = (
            "控制台需要交互式终端；请双击 "
            f"{script_hint} 启动，或在一个真实的终端窗口里运行 "
            "python -m hostrun.cli console。"
        )
        emit_event(home, "WARN", "console", "非交互式终端，控制台已拒绝启动", out=out)
        return {
            "ok": False,
            "reason": "not_a_tty",
            "note": note,
            "console": str(console_dir(home)),
        }

    with file_lock(lock_path(home), blocking=False) as acquired:
        if not acquired:
            beat = read_heartbeat(home)
            pid = beat.get("pid")
            note = f"控制台已在运行（pid={pid or '未知'}）；请勿重复启动。"
            emit_event(home, "WARN", "console", note, out=out)
            return {
                "ok": False,
                "reason": "already_running",
                "note": note,
                "pid": pid,
                "lock": str(lock_path(home)),
            }

        started_at = now()
        previous = read_state(home)
        paused = bool(previous.get("paused"))
        write_state(
            home,
            service="paused" if paused else "running",
            paused=paused,
            started_at=started_at,
        )

        script = None
        if code and archive:
            python = sys.executable
            try:
                recorded = str((load_config(home).get("paths") or {}).get("python") or "")
                python = recorded or python
            except Exception:  # noqa: BLE001 - a missing config uses this interpreter
                pass
            try:
                script = ensure_script(code, archive, python)
            except OSError as exc:
                script = {"ok": False, "reason": "script_unwritable", "error": str(exc)}

        title_setter(title)
        beat = {
            "started_at": started_at,
            "state": "running",
            "last_event_at": None,
            "last_success_at": started_at,
            "consecutive_failures": 0,
            "last_error": "",
            "pid": os.getpid(),
        }
        write_heartbeat(home, **beat)
        emit_event(
            home,
            "INFO",
            "console",
            f"控制台已启动（{started_at}，pid={os.getpid()}，服务={'已暂停' if paused else '运行中'}）",
            out=out,
        )
        emit_event(home, "INFO", "console", "输入 /horizon-help 查看可用指令", out=out)
        if script is not None:
            if script.get("ok"):
                emit_event(
                    home,
                    "INFO",
                    "console",
                    f"启动脚本：{script['path']}（{'已写入' if script.get('written') else '已存在'}）",
                    out=out,
                )
            else:
                emit_event(
                    home,
                    "WARN",
                    "console",
                    f"启动脚本写入失败：{script.get('error') or script.get('reason')}",
                    out=out,
                )

        lifecycle = service
        if lifecycle is None:
            from hostrun import console_service

            lifecycle = console_service.Service(
                home,
                emit=lambda level, source, message: emit_event(
                    home, level, source, message, out=out
                ),
                ticker_worker=start_worker,
            )
        try:
            restore = lifecycle.restore()
        except Exception as exc:  # noqa: BLE001 - the shell survives a bad state file
            restore = {"ok": False, "reason": "restore_failed"}
            emit_event(
                home,
                "WARN",
                "console",
                f"服务状态恢复失败：{type(exc).__name__}: {exc}",
                out=out,
            )
        if restore.get("ok") and service is None:
            emit_event(
                home,
                "INFO",
                "service",
                "服务已按上次状态恢复（暂停保持暂停，运行态自动启动）",
                out=out,
            )
        ticker = lifecycle.start_ticker()
        if not ticker.get("ok"):
            emit_event(home, "WARN", "console", "调度工作线程未启动", out=out)

        surface = None
        if events is not None:
            built = None
            try:
                built = events(home, out=out) if callable(events) else events
                # #82's routing and #81's scheduler meet here: a Feishu
                # ``推一篇`` executes the registry's push_again executor, which
                # dispatches through this lifecycle's request_push.
                if built is not None and hasattr(built, "bind_lifecycle"):
                    built.bind_lifecycle(lifecycle)
                built.start()
                surface = built
                emit_event(
                    home,
                    "INFO",
                    "console",
                    "飞书事件面已启动（消息与卡片订阅、反馈轮询与知识库拉取）",
                    out=out,
                )
            except Exception as exc:  # noqa: BLE001 - the shell survives a failed surface
                surface = None
                if built is not None and hasattr(built, "stop"):
                    try:
                        built.stop()
                    except Exception:  # noqa: BLE001 - a failed start is already reported
                        pass
                emit_event(
                    home,
                    "WARN",
                    "console",
                    f"飞书事件面启动失败：{type(exc).__name__}: {exc}",
                    out=out,
                )

        stop_event = threading.Event()
        worker = None
        try:
            worker = start_worker(
                lambda: _heartbeat_loop(home, beat, stop_event, heartbeat_seconds)
            )
        except Exception:  # noqa: BLE001 - no worker still leaves a working console
            worker = None

        commands = 0
        event_count = 0
        reason = "stopped"
        init_runner = init
        if init_runner is None:
            def init_runner():
                from hostrun import console_init

                return console_init.run(
                    home,
                    resolved=layout,
                    reader=read,
                    out=out,
                    isatty=detect_tty,
                    # The console's own card subscription is the single event
                    # consumer; a transport test must not spawn a second one.
                    transport_events=(
                        getattr(surface, "wait_card", None) if surface is not None else None
                    ),
                    # The knowledge-base choice "初始化知识库" enters the real
                    # /horizon-update-kb flow (read + review) through the same
                    # dispatcher, so #87 hands off to #89/#90 and not to a copy.
                    update_kb=lambda: dispatch(
                        home,
                        "/horizon-update-kb",
                        service=lifecycle,
                        review=review_runner,
                    ),
                    service=lifecycle,
                )
        review_runner = review
        if review_runner is None:
            def review_runner(runtime, *, service=None):
                from hostrun import update_kb_review

                return update_kb_review.run(
                    runtime,
                    out=out,
                    isatty=detect_tty,
                    service=service,
                )
        try:
            while True:
                try:
                    out.write(PROMPT)
                    out.flush()
                except (OSError, ValueError, AttributeError):
                    pass
                try:
                    line = read(PROMPT)
                except EOFError:
                    reason = "eof"
                    break
                except KeyboardInterrupt:
                    reason = "interrupted"
                    break
                text = "" if line is None else str(line).strip()
                if not text:
                    continue
                commands += 1
                emit_event(home, "INFO", "command", text, out=out)
                event_count += 1
                result = dispatch(
                    home,
                    text,
                    status=status,
                    service=lifecycle,
                    init=init_runner,
                    review=review_runner,
                )
                if result.get("text"):
                    try:
                        out.write(result["text"] + "\n")
                        out.flush()
                    except (OSError, ValueError, AttributeError):
                        pass
                level = "INFO" if result.get("ok") else "WARN"
                summary = result.get("action") or "unknown"
                if result.get("reason"):
                    summary = f"{summary}({result['reason']})"
                emit_event(home, level, "command", f"{text} -> {summary}", out=out)
                event_count += 1
                # Never clobber the pause flag another command just wrote:
                # only the lifecycle commands own ``paused``/``service``.
                write_state(home, last_command_at=now())
                beat["last_event_at"] = now()
                write_heartbeat(home, **beat)
                if result.get("exit"):
                    reason = "stopped"
                    break
        finally:
            try:
                lifecycle.shutdown()
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                emit_event(
                    home,
                    "WARN",
                    "console",
                    f"服务收尾失败：{type(exc).__name__}: {exc}",
                    out=out,
                )
            if surface is not None:
                try:
                    surface.stop()
                    emit_event(
                        home,
                        "INFO",
                        "console",
                        "飞书事件面已停止（两条订阅已结束）",
                        out=out,
                    )
                except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                    emit_event(
                        home,
                        "WARN",
                        "console",
                        f"飞书事件面停止失败：{type(exc).__name__}: {exc}",
                        out=out,
                    )
            stop_event.set()
            if worker is not None and hasattr(worker, "join"):
                try:
                    worker.join(timeout=1)
                except Exception:  # noqa: BLE001 - shutdown must not raise
                    pass
            write_heartbeat(home, **{**beat, "state": "stopped"})
            final = read_state(home)
            write_state(
                home,
                service="stopped",
                paused=bool(final.get("paused")),
                next_push_at=None,
            )
            emit_event(home, "INFO", "console", "控制台已退出", out=out)

        return {
            "ok": True,
            "reason": reason,
            "commands": commands,
            "events": event_count,
            "pid": os.getpid(),
            "started_at": started_at,
            "paused": bool(read_state(home).get("paused")),
            "console": str(console_dir(home)),
            "log_path": str(log_path(home)),
            "heartbeat_path": str(heartbeat_path(home)),
            "script": script,
        }
