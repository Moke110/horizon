"""``/horizon-update-kb`` review side: one key per candidate, then write (ADR 0009 §8).

The console consumes the candidate area written by the read side
(:mod:`hostrun.update_kb`) and never re-runs the read.  It shows one candidate at
a time with a ``第 N / M`` progress line and reads exactly one key:

- **Enter** accepts the candidate (it enters the knowledge base normally);
- **Backspace** discards it (it never enters the knowledge base but stays in the
  candidate area and in the decision journal);
- **Tab** keeps it but marks it ``Under review`` (#88's optional flag), so it is
  synced and counted but skipped by retrieval, ranking and writing;
- **q** / **Esc** aborts: every already-decided item takes effect immediately and
  the undecided ones are shown again on the next run.

Each decision is appended to ``<runtime>/update-kb/review/decisions.jsonl`` the
moment the key is read, so an interrupted review resumes with only the undecided
items.  When the review ends (completed or aborted) an item is written exactly
once through :class:`archive.store.Archive` — never with a review changelog — and
a batch confirmation (hash + decision summary) is written to
``<runtime>/update-kb/review/confirmation.json``.  A completed review then syncs
the Base, restores the service to its previous state and triggers one real
first-push drill; every failure is reported rather than hidden.

Contracts owned by this module:

- decisions: ``<runtime>/update-kb/review/decisions.jsonl`` (append-only)
- batch confirmation: ``<runtime>/update-kb/review/confirmation.json``

The key source, the Base sync, the service and the clock are injectable, so the
whole review is driven offline with fake keys; no real TTY is needed.  What
stays ``untested``: a real console TTY pressing the keys, a real Base sync and
a real first push through live credentials.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from archive.store import (
    CARD_ID_RE,
    PROP_ID_RE,
    Archive,
    ArchiveError,
    card_id_for,
    id_slug,
    prop_id_for,
)
from common import (
    append_jsonl,
    now,
    read_json,
    read_jsonl,
    stable_id,
    write_json,
)
from hostrun import update_kb

REVIEW_DIRNAME = "review"
DECISIONS_FILENAME = "decisions.jsonl"
CONFIRMATION_FILENAME = "confirmation.json"
SCHEMA_VERSION = 1

ACCEPTED = "accepted"
DISCARDED = "discarded"
KEPT = "kept"

#: Raw key / key name -> decision.  Abort is handled before a decision is written.
KEY_DECISIONS = {
    "\r": ACCEPTED,
    "\n": ACCEPTED,
    "enter": ACCEPTED,
    "\x08": DISCARDED,
    "\x7f": DISCARDED,
    "\b": DISCARDED,
    "backspace": DISCARDED,
    "\t": KEPT,
    "tab": KEPT,
}
ABORT_KEYS = {"q", "Q", "esc", "escape", "\x1b"}
KEY_HINT = "Enter 接受 · Backspace 丢弃 · Tab 保留（Under review） · q 中止"


# --------------------------------------------------------------------- paths
def review_dir(home: str | Path) -> Path:
    return update_kb.state_dir(home) / REVIEW_DIRNAME


def decisions_path(home: str | Path) -> Path:
    return review_dir(home) / DECISIONS_FILENAME


def confirmation_path(home: str | Path) -> Path:
    return review_dir(home) / CONFIRMATION_FILENAME


# ---------------------------------------------------------------- candidates
def read_candidates(home: str | Path) -> dict:
    """The candidate area written by the read side; ``{}`` when there is none."""
    payload = read_json(update_kb.candidates_path(home))
    return payload if isinstance(payload, dict) else {}


def _identity(prefix: str, record: dict) -> str:
    identifier = str(record.get("id") or "").strip()
    if identifier:
        return identifier
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    return stable_id(prefix, payload)


def _display_name(kind: str, record: dict) -> str:
    key = "label" if kind == "card" else "title"
    value = str(record.get(key) or record.get("id") or "").strip()
    return value or "（无标题候选）"


def build_items(payload: dict) -> list[dict]:
    """One review item per candidate, cards first, then propositions."""
    items: list[dict] = []
    for kind, prefix, list_name in (
        ("card", "card", "cards"),
        ("proposition", "prop", "propositions"),
    ):
        for record in payload.get(list_name) or []:
            if not isinstance(record, dict):
                continue
            items.append(
                {
                    "key": f"{prefix}:{_identity(prefix, record)}",
                    "kind": kind,
                    "display": _display_name(kind, record),
                    "record": record,
                }
            )
    return items


# ------------------------------------------------------------- normalization
def _ontology(value: object, label: str) -> list[str]:
    if isinstance(value, list):
        parts = [str(part).strip() for part in value if str(part).strip()]
    elif isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[,，、;；]", value) if part.strip()]
    else:
        parts = []
    if parts:
        return parts
    return [label] if label else []


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def normalize_card(source: dict, existing: dict[str, dict], *, at: str) -> tuple[dict, str]:
    """Turn one candidate card into a valid archive record, or explain why not."""
    label = str(source.get("label") or "").strip()
    annotation = str(source.get("annotation") or "").strip()
    if not label:
        return {}, "候选卡片缺少 label"
    if not annotation:
        return {}, "候选卡片缺少 annotation"
    identifier = str(source.get("id") or "").strip()
    if not CARD_ID_RE.match(identifier):
        if id_slug(label):
            identifier = card_id_for(label, existing)
        else:
            identifier = stable_id("card", label, annotation)
    return (
        {
            "id": identifier,
            "label": label,
            "ontology": _ontology(source.get("ontology"), label),
            "annotation": annotation,
        },
        "",
    )


def normalize_proposition(
    source: dict, existing: dict[str, dict], *, at: str
) -> tuple[dict, str]:
    """Turn one candidate proposition into a valid archive record, or explain."""
    title = str(source.get("title") or "").strip()
    review = str(source.get("review") or "").strip()
    if not title:
        return {}, "候选命题缺少 title"
    if not review:
        return {}, "候选命题缺少 review"
    related = _string_list(source.get("related_knowledge"))
    if len(related) < 2:
        return {}, "候选命题的 related_knowledge 少于 2 项"
    evidence = source.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return {}, "候选命题缺少 evidence"
    identifier = str(source.get("id") or "").strip()
    if not PROP_ID_RE.match(identifier):
        if id_slug(title):
            identifier = prop_id_for(title, existing)
        else:
            identifier = stable_id("prop", title, review)
    return (
        {
            "id": identifier,
            "title": title,
            "review": review,
            "related_knowledge": related,
            "evidence": evidence,
            "changelog": [{"at": at, "action": "created"}],
        },
        "",
    )


def normalize_record(
    kind: str,
    source: dict,
    *,
    existing_cards: dict[str, dict],
    existing_props: dict[str, dict],
    at: str,
) -> tuple[dict, str]:
    if kind == "card":
        return normalize_card(source, existing_cards, at=at)
    return normalize_proposition(source, existing_props, at=at)


# ------------------------------------------------------------------ decisions
def read_decisions(home: str | Path) -> list[dict]:
    rows: list[dict] = []
    for row in read_jsonl(decisions_path(home)):
        if isinstance(row, dict) and row.get("key") and row.get("decision") in {
            ACCEPTED,
            DISCARDED,
            KEPT,
        }:
            rows.append(row)
    return rows


def decision_map(home: str | Path) -> dict[str, str]:
    """The latest decision per item key (a resume only ever adds new keys)."""
    latest: dict[str, str] = {}
    for row in read_decisions(home):
        latest[str(row["key"])] = str(row["decision"])
    return latest


def _tally(items: list[dict], decided: dict[str, str]) -> dict:
    counts = {
        "total": len(items),
        ACCEPTED: 0,
        DISCARDED: 0,
        KEPT: 0,
        "remaining": 0,
    }
    for item in items:
        decision = decided.get(item["key"])
        if decision in (ACCEPTED, DISCARDED, KEPT):
            counts[decision] += 1
        else:
            counts["remaining"] += 1
    return counts


# --------------------------------------------------------------------- apply
def _same_card(existing: dict, record: dict) -> bool:
    return (
        str(existing.get("label") or "") == record["label"]
        and list(existing.get("ontology") or []) == list(record["ontology"])
        and str(existing.get("annotation") or "") == record["annotation"]
    )


def _same_prop(existing: dict, record: dict) -> bool:
    return (
        str(existing.get("title") or "") == record["title"]
        and str(existing.get("review") or "") == record["review"]
        and list(existing.get("related_knowledge") or []) == record["related_knowledge"]
        and list(existing.get("evidence") or []) == record["evidence"]
    )


def apply_decisions(
    home: str | Path,
    rows: list[dict] | None = None,
    *,
    archive: Archive | None = None,
) -> dict:
    """Write every accepted/kept record through the knowledge layer.

    A pre-existing record with the same id but different content is never
    overwritten (``pre_existing``); an identical record is an idempotent rerun
    (``unchanged``).  Discarded items are never written.  No review changelog is
    appended: the flag travels in the record exactly as #88 defines it.
    """
    home = Path(home)
    store = archive if archive is not None else Archive(home)
    rows = read_decisions(home) if rows is None else rows
    latest: dict[str, dict] = {}
    for row in rows:
        latest[str(row.get("key") or "")] = row
    written = {"cards": 0, "propositions": 0, "failed": [], "unchanged": 0}
    for row in latest.values():
        decision = row.get("decision")
        if decision not in (ACCEPTED, KEPT):
            continue
        record = row.get("record") or {}
        kind = row.get("kind")
        if not isinstance(record, dict) or not record:
            written["failed"].append(
                {
                    "key": row.get("key"),
                    "kind": kind,
                    "reason": str(row.get("record_error") or "候选记录无效"),
                }
            )
            continue
        flag = decision == KEPT
        target = dict(record)
        target["under_review"] = flag
        try:
            if kind == "card":
                existing = None
                try:
                    existing = store.get_card(target["id"])
                except ArchiveError:
                    existing = None
                if existing is not None:
                    if not _same_card(existing, target):
                        written["failed"].append(
                            {
                                "key": row.get("key"),
                                "kind": kind,
                                "id": target["id"],
                                "reason": "pre_existing",
                            }
                        )
                        continue
                    if bool(existing.get("under_review", False)) == flag:
                        written["unchanged"] += 1
                        continue
                store.put_card(target)
                written["cards"] += 1
            elif kind == "proposition":
                existing = None
                try:
                    existing = store.get_prop(target["id"])
                except ArchiveError:
                    existing = None
                if existing is not None:
                    if not _same_prop(existing, target):
                        written["failed"].append(
                            {
                                "key": row.get("key"),
                                "kind": kind,
                                "id": target["id"],
                                "reason": "pre_existing",
                            }
                        )
                        continue
                    if bool(existing.get("under_review", False)) == flag:
                        written["unchanged"] += 1
                        continue
                store.put_prop(target)
                written["propositions"] += 1
            else:
                written["failed"].append(
                    {"key": row.get("key"), "kind": kind, "reason": "未知候选类型"}
                )
        except (ArchiveError, ValueError, TypeError, OSError) as exc:
            written["failed"].append(
                {
                    "key": row.get("key"),
                    "kind": kind,
                    "id": target.get("id"),
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
    return written


# -------------------------------------------------------------- confirmation
def batch_hash(rows: list[dict]) -> str:
    """Deterministic hash of the decided batch (content + decision)."""
    canonical = json.dumps(
        [
            {
                "key": row.get("key"),
                "kind": row.get("kind"),
                "decision": row.get("decision"),
                "record": row.get("record"),
            }
            for row in sorted(rows, key=lambda item: str(item.get("key") or ""))
        ],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return stable_id("review", canonical)


def write_confirmation(
    home: str | Path,
    *,
    rows: list[dict],
    counts: dict,
    written: dict,
    aborted: bool,
    at: str,
) -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "confirmed_at": at,
        "aborted": bool(aborted),
        "batch_hash": batch_hash(rows),
        "counts": counts,
        "decisions": [
            {
                "key": row.get("key"),
                "kind": row.get("kind"),
                "decision": row.get("decision"),
                "display": row.get("display"),
            }
            for row in sorted(rows, key=lambda item: str(item.get("key") or ""))
        ],
        "written": written,
    }
    write_json(confirmation_path(home), payload)
    return payload


# ---------------------------------------------------------------- key source
def read_key() -> str:
    """Read one key without requiring Enter (live TTY only; tests inject)."""
    if os.name == "nt":
        import msvcrt

        return msvcrt.getwch()
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    try:
        tty.setraw(descriptor)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)


def classify_key(value: object) -> str:
    """``accepted|discarded|kept|abort|unknown`` for one raw key or key name."""
    text = "" if value is None else str(value)
    if text in ABORT_KEYS:
        return "abort"
    if text.lower() in ABORT_KEYS:
        return "abort"
    return KEY_DECISIONS.get(text, "unknown")


def _default_isatty() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


# ------------------------------------------------------------------ defaults
def _default_sync(home: Path) -> dict:
    from knowledge import bitable

    return bitable.sync(Path(home))


def _default_service(home: Path):
    from hostrun import console_service

    return console_service.Service(Path(home))


def _restore_service(home: Path, service) -> tuple[bool, str]:
    """Restore the service after the review, unless the owner had paused it.

    The chained ``/horizon-update-kb`` read leaves the service paused until
    this point; restoring here — not before the review — keeps the scheduler
    quiet for the whole human-paced review (US25).  The first-push drill is
    started by the caller after this returns.
    """
    try:
        from hostrun import console

        prior_paused = bool(console.read_state(home).get("paused"))
    except Exception:  # noqa: BLE001 - a missing console state is not "paused"
        prior_paused = False
    if prior_paused:
        return False, "服务此前已暂停，保持暂停（未自动启动）。"
    lifecycle = service or _default_service(home)
    try:
        start_result = lifecycle.start()
        return bool((start_result or {}).get("ok", True)), ""
    except Exception as exc:  # noqa: BLE001 - restore is reported, not raised
        return False, f"恢复服务失败：{type(exc).__name__}: {exc}"


def _review_refusal(home: Path, service, result: dict) -> dict:
    """A review that never entered the item loop still releases the pause."""
    restored, note = _restore_service(home, service)
    return {**result, "restored": restored, "service_restore": note}


# ----------------------------------------------------------------------- run
def run(
    home: str | Path,
    *,
    key_reader=None,
    out=None,
    isatty=None,
    sync=None,
    service=None,
    archive: Archive | None = None,
    clock=None,
    request_push=None,
) -> dict:
    """Review the pending candidates, write the decisions, then finish the flow.

    ``key_reader`` returns one raw key (or key name); ``isatty`` gates entry;
    ``sync`` receives the runtime area and returns the Base-sync result;
    ``service`` follows :class:`hostrun.console_service.Service`
    (``start``/``request_push``).  ``request_push`` overrides only the drill.
    """
    home = Path(home)
    output = sys.stdout if out is None else out
    stamp = clock or now
    tty = isatty or _default_isatty

    def emit(text: str) -> None:
        try:
            output.write(text + "\n")
            output.flush()
        except (OSError, ValueError, AttributeError):
            pass

    try:
        interactive = bool(tty())
    except Exception:  # noqa: BLE001 - an undetectable stdin is not a TTY
        interactive = False
    if not interactive:
        return _review_refusal(
            home,
            service,
            {
                "ok": False,
                "action": "review_kb",
                "reason": "not_a_tty",
                "text": "审阅需要交互式终端；请在控制台执行该指令（非 TTY 环境已拒绝）。",
                "counts": {"total": 0, ACCEPTED: 0, DISCARDED: 0, KEPT: 0, "remaining": 0},
            },
        )

    payload = read_candidates(home)
    items = build_items(payload)
    if not items:
        return _review_refusal(
            home,
            service,
            {
                "ok": False,
                "action": "review_kb",
                "reason": "no_candidates",
                "text": "没有待审阅的候选；先运行 /horizon-update-kb 读取资料。",
                "counts": {"total": 0, ACCEPTED: 0, DISCARDED: 0, KEPT: 0, "remaining": 0},
            },
        )

    store = archive if archive is not None else Archive(home)
    existing_cards = {card["id"]: card for card in store.list_cards()}
    existing_props = {prop["id"]: prop for prop in store.list_props()}
    decided = decision_map(home)
    rows = read_decisions(home)
    counts = _tally(items, decided)
    pending = [item for item in items if item["key"] not in decided]

    if not pending:
        return _review_refusal(
            home,
            service,
            {
                "ok": True,
                "action": "review_kb",
                "reason": "nothing_pending",
                "text": f"全部 {counts['total']} 条候选都已审阅，无需再次决定。",
                "counts": counts,
                "completed": True,
                "aborted": False,
                "decisions": rows,
            },
        )

    reader = key_reader or read_key
    emit(f"待审阅候选：{len(pending)} 条（全部 {counts['total']} 条）")
    emit("按 Enter 接受，Backspace 丢弃，Tab 保留并标记 Under review，q / Esc 中止。")
    aborted = False
    for index, item in enumerate(pending, start=1):
        kind_name = "知识卡片" if item["kind"] == "card" else "命题"
        emit(f"第 {index} / {len(pending)}  [{kind_name}] {item['display']}")
        emit(f"  {KEY_HINT}")
        while True:
            raw = reader()
            decision = classify_key(raw)
            if decision == "abort":
                aborted = True
                break
            if decision != "unknown":
                break
            emit("  无法识别的按键；请用 Enter / Backspace / Tab / q。")
        if aborted:
            break
        record, error = normalize_record(
            item["kind"],
            item["record"],
            existing_cards=existing_cards,
            existing_props=existing_props,
            at=stamp(),
        )
        row = {
            "schema_version": SCHEMA_VERSION,
            "at": stamp(),
            "key": item["key"],
            "kind": item["kind"],
            "decision": decision,
            "display": item["display"],
            "record": record,
            "record_error": error,
        }
        # The decision is durable before the next card is shown.
        append_jsonl(decisions_path(home), row)
        rows.append(row)
        decided[item["key"]] = decision
        if decision == ACCEPTED:
            emit(f"  → 已接受：{item['display']}")
        elif decision == DISCARDED:
            emit(f"  → 已丢弃（保留在候选区与决定记录）：{item['display']}")
        else:
            emit(f"  → 保留（Under review）：{item['display']}")

    counts = _tally(items, decided)
    if aborted:
        emit("审阅已中止：已决定项立即生效，未决定项留待下次。")

    written = apply_decisions(home, rows, archive=store)
    sync_result = _sync_home(home, sync)
    confirmation = write_confirmation(
        home,
        rows=rows,
        counts=counts,
        written=written,
        aborted=aborted,
        at=stamp(),
    )

    completed = not aborted and counts["remaining"] == 0
    # The review owns the pause bracket for the chained update-kb flow: the
    # service restores here (after every decision is durable) and only then may
    # the first-push drill run.  An abort restores too, without a drill.
    restored, restore_note = _restore_service(home, service)
    first_push: dict = {}
    if completed:
        lifecycle = service or _default_service(home)
        drill = request_push or getattr(lifecycle, "request_push", None)
        if drill is None:
            first_push = {"ok": False, "reason": "no_service"}
        else:
            try:
                first_push = dict(drill("first") or {})
            except Exception as exc:  # noqa: BLE001 - a failed drill is a result
                first_push = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    lines = [
        f"审阅结果：共 {counts['total']} 条，接受 {counts[ACCEPTED]}，"
        f"丢弃 {counts[DISCARDED]}，保留（Under review） {counts[KEPT]}，"
        f"未决定 {counts['remaining']}",
        f"写入：卡片 {written['cards']}，命题 {written['propositions']}，"
        f"未变更 {written['unchanged']}，失败 {len(written['failed'])}",
        f"Base 同步：{'成功' if sync_result.get('ok') else '失败：' + str(sync_result.get('reason') or '未知原因')}",
    ]
    for failure in written["failed"]:
        lines.append(
            f"  写入失败：{failure.get('key')}（{failure.get('reason')}）"
        )
    if aborted:
        lines.append("审阅未完成：未决定项下次继续；服务已恢复，未做首次推送演练。")
    else:
        if restore_note:
            lines.append(restore_note)
        elif restored:
            lines.append("服务已恢复。")
        if first_push.get("ok"):
            lines.append("首次推送演练：已触发。")
        else:
            lines.append(
                f"首次推送演练失败：{first_push.get('reason') or '未知原因'}"
            )
    lines.append(f"整批确认：{confirmation['batch_hash']}（{confirmation_path(home)}）")

    ok = not written["failed"]
    return {
        "ok": ok,
        "action": "review_kb",
        "reason": "completed" if completed else ("aborted" if aborted else "partial"),
        "text": "\n".join(lines),
        "counts": counts,
        "written": written,
        "base_sync": sync_result,
        "restored": restored,
        "first_push": first_push,
        "aborted": aborted,
        "completed": completed,
        "batch_hash": confirmation["batch_hash"],
        "confirmation_path": str(confirmation_path(home)),
        "candidates_path": str(update_kb.candidates_path(home)),
        "decisions": rows,
    }


def _sync_home(home: Path, sync) -> dict:
    """Run the Base sync; a failure is reported, never raised."""
    runner = sync or _default_sync
    try:
        outcome = runner(home)
    except Exception as exc:  # noqa: BLE001 - the local write stays regardless
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not isinstance(outcome, dict):
        return {"ok": False, "reason": "sync returned no result"}
    return outcome
