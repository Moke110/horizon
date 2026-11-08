"""Run the bundled Horizon Pi agent with bounded execution, events and leases."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

from common import append_jsonl, ensure_dir, file_lock, now
from hostrun import executor, layout, embedded_agent
from hostrun.base import HostRunError, load_config

DEFAULT_TIMEOUT_SECONDS = 1200
DEFAULT_LEASE_MINUTES = 20
POLL_INTERVAL_SECONDS = 1.0
LOGS_REL = Path("logs")
LOCK_REL = Path("agent") / "run.lock"
RUNS_REL = Path("hostrun") / "runs.jsonl"
EVENT_TYPES = (
    "session",
    "status",
    "text",
    "thinking",
    "tool_call",
    "tool_result",
    "final",
    "error",
)

#: Serialises appends from the two pipe-drain threads and the main thread.
LOG_LOCK = threading.Lock()


# --------------------------------------------------------------------- paths
def lock_path(home: Path) -> Path:
    """The single-flight lock for Agent runs (a later run returns busy)."""
    return Path(home) / LOCK_REL


def log_path(home: Path, run_id: str) -> Path:
    """``logs/agent-<run_id>.log``: the complete output of one run."""
    return Path(home) / LOGS_REL / f"agent-{run_id}.log"


# ------------------------------------------------------------------- parsing
def parse_event_line(line: str) -> dict | None:
    """Parse one NDJSON event line; malformed or non-event lines return ``None``.

    The parser never raises: a noisy stderr line mixed into the stream must not
    take the run down.  The raw line is still written to the log.
    """
    text = str(line).strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
        return None
    return payload


def summarize(events: list[dict], *, malformed: int = 0) -> dict:
    """The run record's summary: session id, answer, counts and failure signals."""
    summary = {
        "events": len(events),
        "malformed_lines": int(malformed),
        "session_id": "",
        "final_text": "",
        "text_events": 0,
        "thinking_events": 0,
        "tool_calls": 0,
        "tool_results": 0,
        "error": "",
        "turn_end": "",
    }
    for event in events:
        kind = event.get("type")
        if kind == "session":
            summary["session_id"] = str(event.get("sessionId") or "")
        elif kind == "text":
            summary["text_events"] += 1
        elif kind == "thinking":
            summary["thinking_events"] += 1
        elif kind == "tool_call":
            summary["tool_calls"] += 1
        elif kind == "tool_result":
            summary["tool_results"] += 1
        elif kind == "final":
            summary["final_text"] = str(event.get("text") or "")
        elif kind == "error":
            summary["error"] = str(event.get("message") or "")
        elif kind == "status" and event.get("phase") == "turn_end":
            summary["turn_end"] = str(event.get("reason") or "")
    return summary


# ------------------------------------------------------------------ recording
def record_run(home: Path, row: dict) -> dict:
    """Append one run row to ``hostrun/runs.jsonl`` (same schema as intents)."""
    entry = {"schema_version": 1, "at": now(), **row}
    append_jsonl(Path(home) / RUNS_REL, entry)
    return entry


# ------------------------------------------------------------------- logging
def _append_log(path: Path, line: str) -> None:
    """Append one line, serialised between the drain threads and the run."""
    with LOG_LOCK:
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(str(line).rstrip("\r\n") + "\n")
        except OSError:
            pass


# ---------------------------------------------------------------- child I/O
def _spawn(argv: list[str], **kwargs):
    """Default spawner: recorded executable, piped stdio, caller's console."""
    return subprocess.Popen(argv, **kwargs)


def _kill(proc) -> None:
    try:
        if proc.poll() is None:
            if os.name == "nt" and getattr(proc, "pid", None):
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            proc.kill()
    except Exception:  # noqa: BLE001 - killing an already-dead child is fine
        pass


def _wait(proc) -> int | None:
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001 - poll() still reports what is known
        pass
    try:
        return proc.poll()
    except Exception:  # noqa: BLE001
        return None


def _pump(stream, *, on_line, on_fail) -> None:
    """Drain one pipe line by line; a stream error is reported, never raised."""
    try:
        for raw in stream:
            on_line(str(raw).rstrip("\r\n"))
    except (OSError, ValueError) as exc:
        on_fail(exc)


def _new_run_id(host: str) -> str:
    stamp = now().replace("-", "").replace(":", "").replace("+", "").replace("T", "-")
    return f"{host}-{stamp}-{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------------- run
def run(
    home: Path,
    task: str,
    *,
    run_id: str | None = None,
    kind: str = "agent",
    host: str | None = None,
    timeout_seconds: int | None = None,
    lease: bool = True,
    single_flight: bool = True,
    lease_minutes: int = DEFAULT_LEASE_MINUTES,
    env: dict | None = None,
    cwd: str | Path | None = None,
    spawn=None,
    report=None,
    clock=None,
    sleep=None,
    should_stop=None,
    on_wait=None,
    on_event=None,
) -> dict:
    """Start one bounded Agent run and return its record.

    Guards, in order: single-flight (``agent_busy``), install report blocked
    (``install_blocked``), bundled Pi runtime and model configuration (``agent_unavailable``), executor
    lease (``executor_busy`` with the holder and its expiry).  A refusal never
    queues, never preempts, never spawns and writes no run row.

    Once spawned, the run always writes ``logs/agent-<run_id>.log`` and a
    ``hostrun/runs.jsonl`` row.  A timeout or a non-zero exit is reported and
    **never retried**.  ``lease=False``/``single_flight=False`` are for the
    read-only judgement run (ADR 0009 §4): it neither takes the executor lease
    nor shares the push run's lock.

    ``on_event`` runs on the stdout drain thread, so a Console callback must be
    thread-safe; ``on_wait`` runs on the waiting thread once per poll tick.
    """
    home = Path(home)
    config = load_config(home)
    host = str(host or (config.get("host") or {}).get("product") or "host")
    run_id = str(run_id or _new_run_id(host))
    paths = config.get("paths") or {}
    agent_run_config = config.get("agent_run") or {}
    timeout = int(
        timeout_seconds
        if timeout_seconds is not None
        else (agent_run_config.get("derived_timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    )
    spawn = spawn or _spawn
    report = report or _install_report
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    cwd = str(cwd or paths.get("repo") or home)
    child_env = (
        dict(env)
        if env is not None
        else {**os.environ, **layout.handoff(home)}
    )
    started = now()
    result: dict = {
        "schema_version": 1,
        "run_id": run_id,
        "kind": kind,
        "host": host,
        "at": started,
        "ok": False,
        "reason": "",
        "session_id": "",
        "returncode": None,
        "timed_out": False,
        "stopped": False,
        "log_path": "",
        "final_text": "",
        "events": 0,
        "summary": {},
    }

    def refuse(reason: str, **extra) -> dict:
        result.update(ok=False, reason=reason, **extra)
        return result

    lock = (
        file_lock(lock_path(home), blocking=False)
        if single_flight
        else nullcontext(True)
    )
    with lock as acquired:
        if not acquired:
            return refuse("agent_busy")

        report_state = report(home) or {}
        blocked = report_state.get("blocked") or []
        if blocked:
            return refuse("install_blocked", blocked=list(blocked))

        try:
            argv = embedded_agent.argv(home, task, kind=kind, code=Path(cwd))
        except HostRunError as exc:
            return refuse("agent_unavailable", error=str(exc))
        child_env["HORIZON_PYTHON"] = str(paths.get("python") or sys.executable)
        path = log_path(home, run_id)

        lease_taken = False
        if lease:
            acquired_lease = executor.acquire(
                home, host=host, minutes=lease_minutes, run_id=run_id
            )
            if not acquired_lease.get("ok"):
                return refuse(
                    "executor_busy",
                    holder=acquired_lease.get("holder"),
                    lease_until=acquired_lease.get("lease_until"),
                )
            lease_taken = True

        ensure_dir(path.parent)
        _append_log(path, f"# horizon agent run {run_id} kind={kind} host={host} at={started}")
        _append_log(path, "# argv: " + json.dumps(argv, ensure_ascii=False))

        events: list[dict] = []
        malformed: list[str] = []
        stream_errors: list[str] = []
        spawn_error = ""
        timed_out = False
        stopped = False
        returncode: int | None = None

        try:
            proc = None
            try:
                proc = spawn(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=cwd,
                    env=child_env,
                )
            except OSError as exc:
                spawn_error = f"{type(exc).__name__}: {exc}"
            result["log_path"] = str(path)

            if proc is not None:
                def on_stdout(line: str) -> None:
                    _append_log(path, line)
                    event = parse_event_line(line)
                    if event is None:
                        if line.strip():
                            malformed.append(line)
                        return
                    events.append(event)
                    if on_event is not None:
                        try:
                            on_event(event)
                        except Exception:  # noqa: BLE001 - the run is not the callback
                            pass

                def on_stderr(line: str) -> None:
                    _append_log(path, line)

                def on_fail(exc: Exception) -> None:
                    stream_errors.append(f"{type(exc).__name__}: {exc}")

                pumps = [
                    threading.Thread(
                        target=_pump,
                        args=(proc.stdout,),
                        kwargs={"on_line": on_stdout, "on_fail": on_fail},
                        name=f"agent-{run_id}-stdout",
                        daemon=True,
                    ),
                    threading.Thread(
                        target=_pump,
                        args=(proc.stderr,),
                        kwargs={"on_line": on_stderr, "on_fail": on_fail},
                        name=f"agent-{run_id}-stderr",
                        daemon=True,
                    ),
                ]
                for pump_thread in pumps:
                    pump_thread.start()

                deadline = clock() + timeout
                while proc.poll() is None:
                    if should_stop is not None and should_stop():
                        stopped = True
                        _kill(proc)
                        break
                    if clock() >= deadline:
                        timed_out = True
                        _kill(proc)
                        break
                    sleep(POLL_INTERVAL_SECONDS)
                    if lease_taken:
                        _renew_lease(home, host=host, minutes=lease_minutes)
                    if on_wait is not None:
                        on_wait()

                for pump_thread in pumps:
                    pump_thread.join(timeout=10)

            returncode = _wait(proc) if proc is not None else None
        finally:
            if lease_taken:
                executor.release(home, host=host, ok=not timed_out, reason="agent")

        summary = summarize(events, malformed=len(malformed))
        if stream_errors:
            summary["stream_errors"] = stream_errors

        if timed_out:
            reason, ok = "timeout", False
        elif stopped:
            reason, ok = "stopped", False
        elif spawn_error:
            reason, ok = "spawn_failed", False
        elif returncode == 0:
            reason, ok = "ok", True
        else:
            reason, ok = "child_failed", False

        _append_log(
            path,
            f"# horizon agent run {run_id} finished reason={reason} exit={returncode} "
            f"timed_out={timed_out} session={summary['session_id']}",
        )
        result.update(
            ok=ok,
            reason=reason,
            session_id=summary["session_id"],
            returncode=returncode,
            timed_out=timed_out,
            stopped=stopped,
            final_text=summary["final_text"],
            events=len(events),
            summary=summary,
        )
        if spawn_error:
            result["error"] = spawn_error
        record_run(
            home,
            {
                "at": started,
                "kind": kind,
                "host": host,
                "run_id": run_id,
                "ok": ok,
                "reason": reason,
                "session_id": summary["session_id"],
                "returncode": returncode,
                "timed_out": timed_out,
                "stopped": stopped,
                "log_path": str(path),
                "events": len(events),
            },
        )
        return result


# ------------------------------------------------------------------ internals
def _renew_lease(home: Path, *, host: str, minutes: int) -> None:
    """Keep the run's lease alive for the whole child wait; never raises."""
    try:
        executor.heartbeat(home, host=host, minutes=minutes)
    except Exception:  # noqa: BLE001 - a failed renewal is not a run failure
        pass


def _install_report(home: Path) -> dict:
    from hostrun import install

    return install.report(Path(home))
