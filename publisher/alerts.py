"""Deliver library alerts as copyable owner DMs.

``library`` never talks to Feishu: it writes JSON files under
``<运行时区>/library/alerts/``. This module is the only sender, so alert wording
and delivery live with the rest of the outbound messages.
"""

from __future__ import annotations

from pathlib import Path

from common import ensure_dir, now, read_json
from publisher import feishu
from publisher.store import PublisherStore

GB = 1024**3
MB = 1024**2


def alert_paths(home: Path) -> list[Path]:
    root = Path(home) / "library" / "alerts"
    if not root.exists():
        return []
    return sorted(p for p in root.glob("*.json") if p.is_file())


def _size(value) -> str:
    value = float(value or 0)
    if value >= GB:
        return f"{value / GB:.2f} GB"
    return f"{value / MB:.1f} MB"


def format_alert(alert: dict) -> str:
    level = alert.get("level")
    if level == "full":
        head = "【horizon 库已满】"
    elif level == "warn":
        head = "【horizon 库容量提醒】"
    else:
        head = "【horizon 库状态】"
    candidates = alert.get("candidates") or {}
    lines = [
        f"{head}{alert.get('percent')}% "
        f"({_size(alert.get('used_bytes'))} / {_size(alert.get('limit_bytes'))})",
    ]
    if level == "full":
        lines.append("新原文已暂停入库；推送件不受影响。")
    elif level == "warn":
        lines.append("接近上限，建议先做一次离线备份。")
    if candidates:
        lines.append(
            f"可离线归档候选：{candidates.get('count')} 篇，"
            f"约 {_size(candidates.get('bytes'))}。"
        )
    lines.append("")
    lines.append(str(alert.get("instruction") or "").strip())
    return "\n".join(line for line in lines if line is not None).strip()


def deliver(
    home: Path,
    target: str,
    *,
    dry_run: bool = False,
    identity: str = "bot",
    max_alerts: int = 5,
) -> dict:
    home = Path(home)
    paths = alert_paths(home)[:max_alerts]
    store = PublisherStore(home)
    store.init()
    sent, failed = [], []
    for path in paths:
        alert = read_json(path) or {}
        text = format_alert(alert)
        if dry_run:
            out = store.save_outbox(
                f"alert_{path.stem}",
                {"kind": "alert", "target": target, "text": text,
                 "alert": str(path), "created_at": now()},
            )
            sent.append({"alert": str(path), "outbox": str(out)})
            continue
        try:
            result = feishu.send_text(target, text, identity)
        except feishu.FeishuError as exc:
            failed.append({"alert": str(path), "error": str(exc)})
            continue
        dest = ensure_dir(path.parent / "sent") / path.name
        path.replace(dest)
        sent.append(
            {"alert": str(path), "sent_to": str(dest),
             "message_id": result.get("message_id")}
        )
    return {
        "ok": not failed,
        "dry_run": dry_run,
        "count": len(sent),
        "sent": sent,
        "failed": failed,
        "pending": len(alert_paths(home)),
    }
