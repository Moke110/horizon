"""The knowledge-base gate for the daily push (ADR 0009 §6, #87).

The Console keeps running when the knowledge base is not initialized, but the
daily push must not silently pretend it can run: the push is *explicitly*
blocked and both the terminal and ``/horizon-status`` say "待初始化知识库" with
the next step.  The gate is derived, never duplicated:

- a **completed** ``/horizon-update-kb`` review confirmation (the record #90
  writes into ``<runtime>/update-kb/review/confirmation.json``) releases the
  gate automatically, so finishing the knowledge base needs no extra bookkeeping;
- ``console.state.knowledge_ready`` is the explicit decision recorded by
  ``/horizon-init``: ``False`` for "暂不初始化知识库" (the gate stays closed
  until #90's review really completes), ``True`` for a confirmed knowledge base;
- with no explicit decision and no confirmation the gate stays open, so an
  install that was already pushing before the Console is not blocked merely by
  upgrading; the legacy ``intents`` knowledge gate still applies to the actual
  daily run.

Every refusal carries the same ``knowledge_not_ready`` reason string the legacy
daily gate uses, so the console, the status snapshot and #90's receipt speak one
language.
"""

from __future__ import annotations

from pathlib import Path

from common import read_json

#: The reason string shared with ``intents.request``'s legacy daily gate.
REASON = "knowledge_not_ready"
BLOCKED_NOTE = (
    "待初始化知识库：每日推送已被知识库门禁挡住；"
    "下一步运行 /horizon-update-kb 完成知识库初始化（完成后自动放行）。"
)
READY_NOTE = "知识库已就绪，每日推送不受门禁限制。"


def confirmation_path(home: Path) -> Path:
    """The record #90 writes when a review finishes (never re-implemented here)."""
    from hostrun import update_kb_review

    return update_kb_review.confirmation_path(home)


def review_completed(home: Path) -> bool:
    """True only for a finished review: aborted or partly undecided does not count."""
    try:
        payload = read_json(confirmation_path(home)) or {}
    except OSError:
        return False
    if not isinstance(payload, dict) or not payload:
        return False
    if payload.get("aborted"):
        return False
    counts = payload.get("counts") or {}
    try:
        remaining = int(counts.get("remaining") or 0)
    except (TypeError, ValueError):
        remaining = 0
    return remaining == 0


def set_knowledge_ready(home: Path, ready: bool, *, source: str = "") -> dict:
    """Persist the explicit ``/horizon-init`` decision on the gate."""
    from hostrun import console

    state = console.write_state(
        home,
        knowledge_ready=bool(ready),
        knowledge_gate_source=str(source or ""),
    )
    return {
        "ok": True,
        "ready": bool(state.get("knowledge_ready")),
        "source": str(state.get("knowledge_gate_source") or ""),
    }


def knowledge_gate(home: Path) -> dict:
    """The one answer every push path asks: is the daily push allowed?"""
    home = Path(home)
    if review_completed(home):
        return {
            "ok": True,
            "ready": True,
            "reason": "update_kb_completed",
            "source": "update_kb_review",
            "note": READY_NOTE + "（/horizon-update-kb 已完成）",
        }
    from hostrun import console

    state = console.read_state(home)
    explicit = state.get("knowledge_ready")
    if explicit is True:
        return {
            "ok": True,
            "ready": True,
            "reason": "ok",
            "source": str(state.get("knowledge_gate_source") or "init"),
            "note": READY_NOTE,
        }
    if explicit is False:
        return {
            "ok": False,
            "ready": False,
            "reason": REASON,
            "source": str(state.get("knowledge_gate_source") or "init"),
            "note": BLOCKED_NOTE,
        }
    return {
        "ok": True,
        "ready": True,
        "reason": "not_set",
        "source": "",
        "note": "知识库门禁未设置（既未选择暂不初始化，也无完成的 /horizon-update-kb）。",
    }


def daily_allowed(home: Path) -> dict:
    """The gate as the *daily run* sees it.

    An unset console decision falls back to the legacy installation record, so a
    migrated install behaves exactly as before and a fresh install stays blocked
    until its knowledge base exists.  An explicit ``/horizon-init`` decision and
    a completed ``/horizon-update-kb`` review always win, which is what "完成后
    自动放行" means.
    """
    gate = knowledge_gate(home)
    if not gate.get("ready"):
        return gate
    if gate.get("reason") in ("update_kb_completed", "ok"):
        return gate
    try:
        from hostrun.install import step_status

        legacy_ok = step_status(Path(home)).get("knowledge") == "ok"
    except Exception:  # noqa: BLE001 - an unreadable install state is not "ready"
        legacy_ok = False
    if legacy_ok:
        return {**gate, "reason": "install_knowledge_ok"}
    return {
        "ok": False,
        "ready": False,
        "reason": REASON,
        "source": "install",
        "note": BLOCKED_NOTE,
    }
