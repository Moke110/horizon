"""Bounded host runs: one intent, one process, then exit.

The Console starts these runs (ADR 0009); the host itself no longer fires a
scheduled task.  Every run does the same fixed order, per ADR 0005:

    feedback → resume unfinished work → maintenance (if due) → retrieval gate

Intents:
  daily           每日推送时刻：处理反馈、恢复、再请求并（闸门允许时）开始一篇推送
  feedback        每 5 分钟：只处理反馈与恢复；**不创建**检索请求
  maintenance     每天至多一次：原文/推送库对账与配额；不删除、不创建检索请求

No daemon, no detached child, no OS scheduler write. If the Console is not
running, the run simply does not happen; missed daily requests coalesce into one.
"""

from __future__ import annotations

from pathlib import Path

from common import append_jsonl, ensure_dir, now, read_json, today, write_json
from hostrun import executor, feedback as feedback_mod
from hostrun.base import HostRunError, load_config
from question_queue.store import QueueStore, REVIEW_KIND, CORRECTION_KIND

INTENTS = ("daily", "feedback", "maintenance")
MAINTENANCE_REL = Path("library") / "maintenance"
RUNS_REL = Path("hostrun") / "runs.jsonl"

PIPELINE_BRIEF = """horizon 有界运行（{intent}）：请在**当前会话**中完成一篇论文推送，只处理一篇。

硬约束：
- 工作目录 {repo}；运行时区 {home}（沿用环境变量 HORIZON_CODE/HORIZON_ARCHIVE/HORIZON_RUNTIME，模块命令不要再传位置参数）；
- 只走「检索 → 排序 → 写作 → 渲染 → 推送」这一条正向链路；不另起 OpenCode 或脱离本宿主的 agent；
- 评分题与更新题由统一队列发送，不要手写审批卡；不要运行 horizon record / editor fit；
- 论文必须来自本次检索候选，不得编造；没有新候选就输出「今天没有新的」并结束；
- 完成后调用 `{finish}` 记录结果（ok 或 error），不要留常驻进程。

步骤：
1. python -m horizon.cli validate
2. python -m horizon.cli plan --date {date} --days 7 --out {tmp}/horizon-plan.json
3. python -m retriever.cli fetch --plan {tmp}/horizon-plan.json --out {tmp}/horizon-candidates.jsonl --source live
4. 逐条 python -m archive.cli add-paper --file <临时 json>（自动去重）
5. python -m editor.cli task --candidates {tmp}/horizon-candidates.jsonl --date {date} --out {tmp}/editor-task.json
6. 你亲自读 task、写 {tmp}/selection.json，并 python -m editor.cli check ...
7. 从 selection 里只选 1 篇；python -m writer.cli task ...；python -m writer.cli figures ...
8. 你亲自写 push（七段 HTML + assets）；python -m writer.cli check ...
9. python -m writer.cli render --push ... --out {tmp}/<push_id>.html
10. python -m archive.cli add-push --file ...
11. python -m library.cli ensure-source --paper <push 里的 paper_id>
12. python -m publisher.cli send --push ... --html ... --to {owner}
13. python -m hostrun.cli feedback sync（把新题持久化到飞书）
{extra}"""

FINISH_HINT = "python -m hostrun.cli run --intent {intent} --finish ok"


def _queue(home: Path) -> QueueStore:
    queue = QueueStore(home)
    queue.init()
    return queue


def _runlog(home: Path, row: dict) -> None:
    append_jsonl(Path(home) / RUNS_REL, {"schema_version": 1, "at": now(), **row})


def owner(home: Path) -> str:
    """The recipient the legacy callers resolve, kept out of the install report.

    ``install/recipient.json`` is the current source (written by the Console's
    ``/horizon-init`` transport test, #86).  The config fallback is read-only
    for pre-existing installs; it is never copied into a report. Public so the
    Console can route replies and apply the DM allow-list without a private
    bridge.

    This helper does **not** prove verification; the Console's DM allow-list
    uses :func:`verified_owner` (ADR 0009 §4), while existing callers keep the
    old behaviour.
    """
    recipient = read_json(Path(home) / "install" / "recipient.json") or {}
    owner_id = str(recipient.get("owner") or "")
    if owner_id:
        return owner_id
    config = load_config(home)
    return str((config.get("permissions") or {}).get("owner") or "")


#: Backwards-compatible private alias (existing callers and tests use it).
_owner = owner


def verified_owner(home: Path) -> str:
    """The owner recorded by a real verification, or ``""`` (ADR 0008 §3).

    Only ``install/recipient.json`` counts, and it must carry a positive
    ``schema_version``, a non-empty ``owner`` and a non-empty ``verified_at``
    (the shape the Console's transport test writes).  There is deliberately no
    fallback to ``config.permissions.owner``: an unverified recipient may not
    command the Console, and a missing record means log-and-ignore.
    """
    payload = read_json(Path(home) / "install" / "recipient.json") or {}
    if not isinstance(payload, dict):
        return ""
    try:
        schema_version = int(payload.get("schema_version"))
    except (TypeError, ValueError):
        return ""
    if schema_version < 1:
        return ""
    owner_id = str(payload.get("owner") or "").strip()
    verified_at = str(payload.get("verified_at") or "").strip()
    if not owner_id or not verified_at:
        return ""
    return owner_id


def _brief(
    home: Path,
    repo: Path,
    intent: str,
    checkpoint: str | None,
    extra_lines: list[str],
) -> str:
    from common import temp_dir

    extra = ""
    if checkpoint:
        extra += f"- 本次只处理知识卡片：{checkpoint}\n"
    if extra_lines:
        extra += "\n".join(f"- {line}" for line in extra_lines)
    return PIPELINE_BRIEF.format(
        intent=intent,
        repo=repo,
        home=home,
        date=today(),
        tmp=temp_dir(),
        owner=owner(home) or "<owner open_id>",
        extra=extra,
        finish=FINISH_HINT.format(intent=intent),
    ).strip()


# ------------------------------------------------------------- maintenance
def maintenance_due(home: Path, *, date: str | None = None) -> bool:
    day = date or today()
    record = read_json(Path(home) / MAINTENANCE_REL / f"{day}.json")
    return not (record and record.get("ok"))


def _offline_library(home: Path):
    from library.cli import _offline_library as build

    return build(home)[0]


def run_maintenance(home: Path, *, date: str | None = None, offline: bool = False, library=None) -> dict:
    """Once per calendar day: reconcile both libraries and check the quota.

    Never deletes anything; a failed day is not marked done, so the next run
    retries it safely.
    """
    home = Path(home)
    day = date or today()
    record_path = home / MAINTENANCE_REL / f"{day}.json"
    if library is None:
        try:
            library = _offline_library(home) if offline else None
        except Exception as exc:  # noqa: BLE001 - report, do not crash the run
            result = {"ok": False, "date": day, "error": str(exc), "reason": "library_unavailable"}
            ensure_dir(record_path.parent)
            write_json(record_path, {**result, "at": now()})
            return result
    if library is None:
        from library.service import Library

        library = Library(home)
    try:
        reconcile = library.reconcile(dry_run=False)
        usage = library.usage()
        alert = library.maybe_alert(reason="maintenance")
    except Exception as exc:  # noqa: BLE001 - keep the run bounded and report
        result = {"ok": False, "date": day, "error": str(exc), "reason": "maintenance_failed"}
        ensure_dir(record_path.parent)
        write_json(record_path, {**result, "at": now()})
        return result

    counts = reconcile.get("counts") or {}
    changed = any(counts.values())
    result = {
        "ok": bool(reconcile.get("ok")),
        "date": day,
        "at": now(),
        "counts": counts,
        "quota": {
            "used": usage.get("used"),
            "limit": usage.get("limit"),
            "percent": usage.get("percent"),
            "level": usage.get("level"),
        },
        "alert": bool(alert),
        "report_path": reconcile.get("report_path"),
        "changed": changed,
        "deleted": 0,
    }
    ensure_dir(record_path.parent)
    write_json(record_path, result)
    return result


# ------------------------------------------------------------------- resume
def resume_state(home: Path) -> dict:
    """What is unfinished, expressed as work the host agent must pick up."""
    queue = _queue(home)
    items = queue.status()["items"]
    active = queue.active()
    reviews = [
        r["id"]
        for r in items
        if r.get("kind") == REVIEW_KIND and r.get("status") in ("pending", "active")
        and (r.get("payload") or {}).get("ready") is False
    ]
    corrections = [
        r["id"]
        for r in items
        if r.get("kind") == CORRECTION_KIND and r.get("status") in ("pending", "active")
        and (r.get("payload") or {}).get("ready") is False
    ]
    failed = [r["id"] for r in items if r.get("status") == "failed"]
    needs_delivery = bool(active and not active.get("message_id"))
    return {
        "active": (active or {}).get("id"),
        "active_kind": (active or {}).get("kind"),
        "needs_delivery": needs_delivery,
        "reviews_to_write": reviews,
        "corrections_to_propose": corrections,
        "failed": failed,
        "unfinished": queue.has_unfinished(),
    }


# -------------------------------------------------------------------- run
def run(
    home: Path,
    intent: str,
    *,
    repo: str | Path | None = None,
    host: str | None = None,
    minutes: int = executor.DEFAULT_MINUTES,
    offline: bool = False,
    client=None,
    library=None,
    date: str | None = None,
    finish: str | None = None,
    lease: bool = True,
) -> dict:
    """Entry point of ``python -m hostrun.cli run --intent ...``.

    ``lease=False`` means the caller already holds the executor lease for the
    whole operation (a Console Agent run): this call neither acquires nor
    releases, so the preparation and the Agent child share one continuous
    lease (ADR 0009 §10).  The default keeps a standalone ``run`` owning its
    own lease exactly as before.
    """
    if intent not in INTENTS:
        raise HostRunError(f"unknown intent: {intent}")
    home = Path(home)
    config = load_config(home, repo=Path(repo) if repo else None)
    repo_path = Path(repo or config["paths"]["repo"])
    room = host or (config.get("host") or {}).get("product") or "host"

    if finish:
        return finish_run(home, intent, finish, host=room)

    owns_lease = bool(lease)
    if owns_lease:
        acquired = executor.acquire(home, host=room, minutes=minutes)
        if not acquired.get("ok"):
            row = {
                "intent": intent,
                "host": room,
                "ok": False,
                "reason": acquired["reason"],
                "quiet": True,
            }
            _runlog(home, row)
            return {**row, "executor": acquired}

    result = {
        "schema_version": 1,
        "intent": intent,
        "at": now(),
        "host": room,
        "ok": True,
        "actions": [],
        "quiet": True,
        "notify": None,
        "brief": None,
        "next": None,
    }
    extra_lines: list[str] = []
    try:
        # 1. feedback submitted while the agent was stopped
        try:
            fb_client = client
            if fb_client is None:
                from hostrun.base import base_client

                fb_client = base_client(home, offline=offline)
            processed = feedback_mod.process(home, client=fb_client)
            result["feedback"] = processed
            if processed["processed"] or processed["stale"]:
                result["actions"].append(f"feedback:{processed['processed']} processed")
                result["quiet"] = False
        except HostRunError as exc:
            result["feedback"] = {"ok": False, "reason": "not_configured", "note": str(exc)}
            result["actions"].append("feedback:not_configured")

        # 2. unfinished work
        resume = resume_state(home)
        result["resume"] = resume
        if resume["needs_delivery"]:
            extra_lines.append(f"当前活动题 {resume['active']} 已激活但未投递：先 python -m publisher.cli deliver")
        if resume["reviews_to_write"]:
            extra_lines.append("待写命题综述：" + "、".join(resume["reviews_to_write"]))
        if resume["corrections_to_propose"]:
            extra_lines.append("待生成更正提议：" + "、".join(resume["corrections_to_propose"]))
        if resume["failed"]:
            extra_lines.append("失败题需处理：" + "、".join(resume["failed"]) + "（retry 或 abandon 后才继续）")
            result["notify"] = "有失败的问题需要处理：" + "、".join(resume["failed"])
            result["quiet"] = False

        # 3. maintenance: the first hourly check of each day folds it in; the
        # daily push and a manual recovery run may also do it when still due.
        if intent in ("daily", "feedback", "maintenance") and maintenance_due(home, date=date):
            maintenance = run_maintenance(home, date=date, offline=offline, library=library)
            result["maintenance"] = maintenance
            result["actions"].append("maintenance:ok" if maintenance.get("ok") else "maintenance:failed")
            result["quiet"] = result["quiet"] and not maintenance.get("changed")
            if not maintenance.get("ok"):
                result["notify"] = "原文库/推送库维护失败，请查看 library/reports"
                result["quiet"] = False
            elif maintenance.get("changed") or maintenance.get("alert"):
                result["notify"] = "原文库/推送库对账有差异或配额提醒，详见维护记录"
                result["quiet"] = False

        # 4. retrieval gate — only the daily task may request/start a push.
        # The hourly task processes feedback and maintenance and then stops.
        # A daily push also requires formal knowledge to be ready.
        queue = _queue(home)
        if intent == "daily":
            from hostrun import console_gate

            if not console_gate.daily_allowed(home).get("ready"):
                result["retrieval"] = {
                    "requested": False,
                    "started": False,
                    "reason": "knowledge_not_ready",
                }
                result["actions"].append("retrieval:blocked(knowledge_not_ready)")
                result["next"] = "先完成正式知识初始化，再开始每日推送"
            else:
                queue.request_push("scheduled")
                result["actions"].append("request:daily")
                start = queue.try_start(minutes)
                if start and start.get("ok"):
                    result["retrieval"] = {"requested": True, "started": True}
                    checkpoint = start.get("card_id")
                    result["brief"] = _brief(home, repo_path, intent, checkpoint, extra_lines)
                    result["next"] = FINISH_HINT.format(intent=intent)
                    result["actions"].append("retrieval:started")
                    result["quiet"] = False
                else:
                    reason = (start or {}).get("reason", "no_request")
                    result["retrieval"] = {"requested": True, "started": False, "reason": reason}
                    if reason in ("queue_busy", "queue_blocked"):
                        result["actions"].append(f"retrieval:queued({reason})")
                        if extra_lines:
                            result["brief"] = _brief(home, repo_path, intent, None, extra_lines)
                            result["next"] = "先把队列里的题处理完；完成后 hostrun 会在下一次运行继续"
                    elif reason == "no_request":
                        result["actions"].append("retrieval:none")
        else:
            result["retrieval"] = {
                "requested": False,
                "started": False,
                "reason": "not_a_retrieval_intent",
            }
            result["actions"].append("retrieval:skipped")
    finally:
        if owns_lease:
            executor.release(home, host=room, ok=result["ok"], reason=result.get("notify") or "")

    _runlog(
        home,
        {
            "at": result["at"],
            "intent": intent,
            "host": room,
            "ok": result["ok"],
            "quiet": result["quiet"],
            "actions": result["actions"],
            "notify": result["notify"],
        },
    )
    return result


def finish_run(home: Path, intent: str, outcome: str, *, host: str = "host") -> dict:
    """Record the pipeline outcome so the request lease is released correctly."""
    home = Path(home)
    queue = _queue(home)
    ok = str(outcome).lower() in ("ok", "success", "0")
    result = queue.finish_start(ok, None if ok else str(outcome))
    executor.release(home, host=host, ok=ok, reason="" if ok else str(outcome))
    _runlog(home, {"intent": intent, "host": host, "kind": "finish", "ok": ok, "outcome": outcome})
    return {
        "schema_version": 1,
        "intent": intent,
        "ok": ok,
        "request": result,
        "quiet": False,
    }


def status(home: Path) -> dict:
    """One read-only snapshot: executor, queue, feedback, maintenance, install."""
    from hostrun import install

    queue = _queue(home)
    state = queue.status()
    record = read_json(Path(home) / MAINTENANCE_REL / f"{today()}.json")
    return {
        "schema_version": 1,
        "executor": executor.status(home),
        "queue": {
            "active": state.get("active_id"),
            "items": len(state["items"]),
            "unfinished": queue.has_unfinished(),
            "failed": queue.has_failed(),
            "request": state.get("request"),
        },
        "resume": resume_state(home),
        "maintenance_today": record,
        "install": install.summary(home),
        "at": now(),
    }
