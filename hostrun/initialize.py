"""Formal knowledge initialization from literature, requirements and a draft.

This module starts the research library.  It no longer waits for the retired
temporary keyword smoke loop: the Console's ``/horizon-init`` transport test and
``/horizon-update-kb`` review are the current gate (ADR 0009).

Input modes:

    PDF       extract pages with PyMuPDF; a missing extractor is reported, not
              replaced by invented text;
    text      read UTF-8; unreadable files become explicit gaps;
    index     JSON / JSONL / CSV / one-title-per-line metadata.  An index has
              no full text, so it can support cards but never fabricated quotes.

The host agent writes the candidate cards (it is the writer).  This module
validates them against the current card model, records the user's confirmation
with a draft hash, writes them through the authoritative path
(``Archive.put_card`` → ``knowledge.bitable.sync``), reads the Base back, and
records one standard retrieval plan.  Applying twice is idempotent; an
interrupted apply continues from the local archive + sync.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from archive.store import Archive
from common import ensure_dir, now, read_json, write_json
from hostrun import install as install_mod
from hostrun.base import HostRunError, base_client, base_config

INIT_REL = Path("install") / "initial"
LITERATURE_REL = INIT_REL / "literature.json"
DRAFT_REL = INIT_REL / "draft"
CONFIRM_REL = INIT_REL / "confirmation.json"
APPLY_REL = INIT_REL / "applied.json"
PLAN_REL = INIT_REL / "retrieval.json"
CARD_KEYS = ("id", "label", "ontology", "annotation")


# -------------------------------------------------------------------- inputs
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_index(path: Path) -> list[dict]:
    """Index lists are metadata only: JSON, JSONL, CSV or one title per line."""
    suffix = path.suffix.lower()
    rows: list[dict] = []
    if suffix == ".json":
        payload = json.loads(_read_text(path) or "{}")
        rows = payload if isinstance(payload, list) else (
            payload.get("items") or payload.get("papers") or payload.get("records") or []
        )
    elif suffix == ".jsonl":
        for line in _read_text(path).splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    elif suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = [dict(row) for row in csv.DictReader(handle)]
    else:
        rows = [
            {"title": line.strip()}
            for line in _read_text(path).splitlines()
            if line.strip()
        ]
    return [row for row in rows if isinstance(row, dict) and any(str(v).strip() for v in row.values())]


def _extract_pdf(path: Path) -> dict:
    try:
        from pdf_ingest.ingest import _extract_pages
    except Exception as exc:  # noqa: BLE001 - missing dependency is a result
        return {"readable": False, "reason": f"PDF 提取器不可用：{exc}", "pages": 0, "chars": 0}
    try:
        pages = _extract_pages(path)
    except Exception as exc:  # noqa: BLE001 - unreadable PDF is a result
        return {"readable": False, "reason": f"PDF 读取失败：{exc}", "pages": 0, "chars": 0}
    text = "\n".join(pages)
    return {"readable": True, "pages": len(pages), "chars": len(text), "text": text}


def intake(
    home: Path,
    *,
    pdfs: list[str] | None = None,
    texts: list[str] | None = None,
    indexes: list[str] | None = None,
    requirements: str = "",
) -> dict:
    """Record literature and the user's requirements; never invent content."""
    home = Path(home).expanduser().resolve()
    requirements = " ".join(str(requirements or "").split())
    if not requirements:
        raise HostRunError("需要用户直接提出的研究方向与要求")
    records: list[dict] = []
    gaps: list[dict] = []
    cache = ensure_dir(home / "cache" / "initial")
    seen: set[str] = set()

    def add(path_text: str, kind: str) -> None:
        path = Path(path_text).expanduser().resolve()
        if not path.is_file():
            gaps.append({"path": str(path), "kind": kind, "reason": "文件不存在"})
            return
        digest = _sha256(path)
        if digest in seen:
            return
        seen.add(digest)
        entry = {
            "path": str(path),
            "kind": kind,
            "sha256": digest,
            "title": path.stem,
            "fulltext": kind != "index",
            "readable": True,
            "pages": 0,
            "chars": 0,
        }
        if kind == "pdf":
            extracted = _extract_pdf(path)
            entry.update({k: v for k, v in extracted.items() if k != "text"})
            if extracted.get("readable"):
                target = cache / f"{digest[:16]}.txt"
                target.write_text(extracted["text"], encoding="utf-8")
                entry["text_path"] = str(target)
            else:
                gaps.append({"path": str(path), "kind": kind, "reason": extracted["reason"]})
        elif kind == "text":
            try:
                text = _read_text(path)
            except OSError as exc:
                entry["readable"] = False
                gaps.append({"path": str(path), "kind": kind, "reason": str(exc)})
            else:
                target = cache / f"{digest[:16]}.txt"
                target.write_text(text, encoding="utf-8")
                entry["chars"] = len(text)
                entry["text_path"] = str(target)
        elif kind == "index":
            try:
                rows = read_index(path)
            except Exception as exc:  # noqa: BLE001 - unreadable index is a gap
                entry["readable"] = False
                gaps.append({"path": str(path), "kind": kind, "reason": str(exc)})
            else:
                entry["entry_count"] = len(rows)
                entry["entries"] = rows[:200]
                if not rows:
                    gaps.append({"path": str(path), "kind": kind, "reason": "索引为空"})
        records.append(entry)

    for value in pdfs or []:
        add(value, "pdf")
    for value in texts or []:
        add(value, "text")
    for value in indexes or []:
        add(value, "index")

    if not records:
        raise HostRunError("至少需要一篇可读取的 PDF、文本或索引列表")
    payload = {
        "schema_version": 1,
        "collected_at": now(),
        "requirements": requirements,
        "records": records,
        "gaps": gaps,
        "readable": sum(1 for row in records if row.get("readable")),
        "fulltext": sum(1 for row in records if row.get("fulltext") and row.get("readable")),
    }
    write_json(home / LITERATURE_REL, payload)
    return {
        "ok": True,
        **payload,
        "note": (
            "索引只提供元数据，不能作为引文证据"
            if any(row["kind"] == "index" for row in records)
            else ""
        ),
    }


# -------------------------------------------------------------------- draft
def _normalize_card(raw: dict) -> dict:
    card = {key: raw.get(key) for key in CARD_KEYS}
    if isinstance(card["ontology"], str):
        card["ontology"] = [
            part.strip()
            for part in card["ontology"].replace("，", ",").split(",")
            if part.strip()
        ]
    if not isinstance(card["ontology"], list):
        card["ontology"] = []
    return card


def _validate_initial_card(card: dict, seen: set[str]) -> list[str]:
    from initial_build.build import _validate_card as strict_card

    draft_ids: set[str] = set()
    errors = strict_card(card, draft_ids, seen)
    return errors


def _normalize_ontology(value) -> list[str]:
    if isinstance(value, str):
        # Text cells store the term list newline-separated; older rows may be
        # comma-separated. Split both so a read-back compares as a term set.
        text = value.replace("，", ",").replace("\r\n", "\n")
        parts = []
        for line in text.splitlines() or [text]:
            parts.extend(part.strip() for part in line.split(","))
        value = [part for part in parts if part]
    if not isinstance(value, list):
        return []
    return [str(part) for part in value if str(part).strip()]


def _intake_gate(home: Path) -> dict:
    """A draft needs a real intake: readable inputs, readable fulltext, requirements."""
    literature = read_json(home / LITERATURE_REL) or {}
    counts = {
        "readable": int(literature.get("readable") or 0),
        "fulltext": int(literature.get("fulltext") or 0),
    }
    gaps = literature.get("gaps") or []
    checks = (
        (
            not literature,
            "intake_missing",
            "没有文献/文本录入记录；先运行 init intake 提供可读输入与用户要求",
            [],
        ),
        (
            not str(literature.get("requirements") or "").strip(),
            "intake_requirements_missing",
            "录入记录缺少用户要求；重新运行 init intake 补充要求",
            gaps,
        ),
        (
            counts["readable"] <= 0,
            "intake_not_readable",
            "录入的文献/文本没有可读内容；先按 gaps 修复后重新 intake",
            gaps,
        ),
        (
            counts["fulltext"] <= 0,
            "intake_no_fulltext",
            "录入内容只有索引/元数据，没有可读全文；草案不能脱离全文编造",
            gaps,
        ),
    )
    for failed, reason, note, gap_rows in checks:
        if failed:
            return {
                "ok": False,
                "reason": reason,
                "note": note,
                "gaps": gap_rows,
                **counts,
            }
    return {
        "ok": True,
        "gaps": gaps,
        "requirements": str(literature.get("requirements")),
        **counts,
    }


def _existing_card_ids(home: Path, client=None) -> set[str]:
    """Card ids that already existed before the draft (Base + local archive).

    Fail closed: if either snapshot cannot be read the caller must refuse to
    propose, because an empty snapshot would disable the apply-time guard.
    """
    ids: set[str] = set()
    errors: list[str] = []
    config = read_json(home / "knowledge" / "base.json") or {}
    table = (config.get("tables") or {}).get("cards")
    try:
        if not table:
            raise HostRunError("知识卡片表尚未创建，无法建立既有卡片快照")
        remote = client or base_client(home)
        for row in _remote_cards(remote, table):
            card_id = str((row.get("fields") or {}).get("卡片 ID") or "").strip()
            if card_id:
                ids.add(card_id)
    except Exception as exc:  # noqa: BLE001 - snapshot failure must refuse
        errors.append(f"Base：{exc}")
    try:
        ids.update(card["id"] for card in Archive(home).list_cards())
    except Exception as exc:  # noqa: BLE001 - snapshot failure must refuse
        errors.append(f"本地档案：{exc}")
    if errors:
        raise HostRunError("无法读取草案前既有卡片快照：" + "；".join(errors))
    return ids


def _same_text(left, right) -> bool:
    return " ".join(str(left or "").split()) == " ".join(str(right or "").split())


def _same_ontology(left, right) -> bool:
    return {part.strip() for part in _normalize_ontology(left)} == {
        part.strip() for part in _normalize_ontology(right)
    }


def propose(
    home: Path,
    cards: list[dict],
    *,
    rationale: str = "",
    client=None,
) -> dict:
    """Validate the agent's candidate cards and write the reviewable draft.

    The draft needs a real intake first: missing or unreadable literature, or an
    index-only intake with no readable fulltext, is refused with the intake gaps
    instead of generating a draft from nothing.
    """
    home = Path(home).expanduser().resolve()
    gate = _intake_gate(home)
    if not gate["ok"]:
        return gate
    if not cards:
        raise HostRunError("候选知识卡片为空")
    seen: set[str] = set()
    normalized = [_normalize_card(card) for card in cards]
    errors: list[str] = []
    for card in normalized:
        errors.extend(_validate_initial_card(card, seen))
    if errors:
        return {"ok": False, "reason": "invalid_cards", "errors": errors, "cards": normalized}
    try:
        pre_existing = sorted(_existing_card_ids(home, client))
    except HostRunError as exc:
        return {
            "ok": False,
            "reason": "pre_existing_snapshot_failed",
            "note": str(exc),
        }
    draft_dir = ensure_dir(home / DRAFT_REL)
    with (draft_dir / "cards.jsonl").open("w", encoding="utf-8") as handle:
        for card in normalized:
            handle.write(json.dumps(card, ensure_ascii=False) + "\n")
    (draft_dir / "propositions.jsonl").write_text("", encoding="utf-8")
    (draft_dir / "sources.jsonl").write_text("", encoding="utf-8")
    hash_value = draft_hash(normalized)
    meta = {
        "schema_version": 1,
        "proposed_at": now(),
        "rationale": rationale,
        "draft_hash": hash_value,
        "cards": len(normalized),
        "literature": str(home / LITERATURE_REL),
        "intake": {
            "readable": gate["readable"],
            "fulltext": gate["fulltext"],
            "requirements": gate["requirements"],
            "gaps": len(gate["gaps"]),
        },
        "pre_existing_ids": pre_existing,
    }
    write_json(draft_dir / "draft.json", meta)
    (home / CONFIRM_REL).unlink(missing_ok=True)
    return {
        "ok": True,
        "draft": str(draft_dir),
        "draft_hash": hash_value,
        "cards": len(normalized),
        "pre_existing": len(pre_existing),
        "needs_user_confirmation": True,
    }


def draft_hash(cards: list[dict]) -> str:
    canonical = json.dumps(cards, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_draft(home: Path) -> list[dict]:
    path = Path(home) / DRAFT_REL / "cards.jsonl"
    if not path.is_file():
        raise HostRunError("还没有候选知识卡片；先运行 init propose")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def review(home: Path) -> dict:
    home = Path(home).expanduser().resolve()
    cards = load_draft(home)
    meta = read_json(home / DRAFT_REL / "draft.json") or {}
    confirmation = read_json(home / CONFIRM_REL) or {}
    current = draft_hash(cards)
    return {
        "ok": True,
        "cards": cards,
        "draft_hash": current,
        "confirmed": bool(confirmation)
        and confirmation.get("draft_hash") == current,
        "confirmed_at": confirmation.get("confirmed_at"),
        "literature_gaps": (read_json(home / LITERATURE_REL) or {}).get("gaps") or [],
        "needs_user_confirmation": True,
        "rationale": meta.get("rationale", ""),
    }


def confirm(home: Path, *, confirmed_by_user: bool = False, note: str = "") -> dict:
    home = Path(home).expanduser().resolve()
    if not confirmed_by_user:
        return {"ok": False, "reason": "needs_user_confirmation", "review": review(home)}
    cards = load_draft(home)
    hash_value = draft_hash(cards)
    payload = {
        "schema_version": 1,
        "confirmed_at": now(),
        "draft_hash": hash_value,
        "cards": len(cards),
        "note": note,
    }
    write_json(home / CONFIRM_REL, payload)
    return {"ok": True, **payload}


# -------------------------------------------------------------------- apply
def _remote_cards(client, table: str) -> list[dict]:
    return client.list(table) or []


def import_remote_cards(home: Path, *, client) -> dict:
    """Existing Base → local cards, so an attached library keeps working."""
    home = Path(home).expanduser().resolve()
    config = base_config(home)
    table = (config.get("tables") or {}).get("cards")
    if not table:
        raise HostRunError("知识卡片表尚未创建")
    store = Archive(home)
    local = {card["id"] for card in store.list_cards()}
    imported, gaps = [], []
    for row in _remote_cards(client, table):
        fields = row.get("fields") or {}
        card_id = str(fields.get("卡片 ID") or "")
        if not card_id or card_id in local:
            continue
        ontology = fields.get("ontology")
        if isinstance(ontology, str):
            ontology = [
                part.strip()
                for part in ontology.replace("，", ",").split(",")
                if part.strip()
            ]
        annotation = str(fields.get("annotation") or "").strip()
        if not annotation:
            gaps.append({"id": card_id, "reason": "远端卡片缺 annotation，未导入"})
            continue
        card = {
            "id": card_id,
            "label": str(fields.get("label") or card_id),
            "ontology": ontology or [str(fields.get("label") or card_id)],
            "annotation": annotation,
        }
        try:
            store.put_card(card)
        except Exception as exc:  # noqa: BLE001 - report, keep other cards
            gaps.append({"id": card_id, "reason": str(exc)})
        else:
            imported.append(card_id)
    return {"ok": not gaps, "imported": imported, "gaps": gaps, "total": len(imported)}


def standard_retrieval(home: Path, *, days: int = 7) -> dict:
    """One standard card-driven plan."""
    home = Path(home).expanduser().resolve()
    store = Archive(home)
    cards = store.list_cards()
    if not cards:
        raise HostRunError("正式知识库还没有知识卡片")
    from horizon.model import plan as build_plan

    result = build_plan(home, days=days)
    if not result.get("queries"):
        raise HostRunError("标准检索没有生成任何查询；检查知识卡片内容")
    target = home / PLAN_REL
    write_json(target, result)
    return {
        "ok": True,
        "plan": str(target),
        "cards": len(cards),
        "queries": len(result["queries"]),
        "card_ids": [card["id"] for card in cards],
    }


def apply_draft(home: Path, *, client=None) -> dict:
    """Write confirmed cards locally, sync to the Base, then read them back."""
    home = Path(home).expanduser().resolve()
    cards = load_draft(home)
    confirmation = read_json(home / CONFIRM_REL) or {}
    hash_value = draft_hash(cards)
    if not confirmation or confirmation.get("draft_hash") != hash_value:
        return {
            "ok": False,
            "reason": "needs_user_confirmation",
            "note": "卡片在确认后有改动，需重新确认当前草稿",
            "review": review(home),
        }
    seen: set[str] = set()
    errors = []
    for card in cards:
        errors.extend(_validate_initial_card(card, seen))
    if errors:
        return {"ok": False, "reason": "invalid_cards", "errors": errors}

    # A draft must never overwrite a card that already existed before it, and
    # there is no override: an id collision is always refused.
    meta = read_json(home / DRAFT_REL / "draft.json") or {}
    pre_existing = {str(value) for value in (meta.get("pre_existing_ids") or []) if value}
    conflicts = sorted(card["id"] for card in cards if card["id"] in pre_existing)
    if conflicts:
        return {
            "ok": False,
            "reason": "pre_existing_card",
            "conflicts": conflicts,
            "note": (
                "这些卡片 ID 在草案生成前已存在于正式库；不会覆盖既有研究内容，"
                "请为新卡片使用新的 ID"
            ),
            "counts": {"created": 0, "updated": 0, "skipped": 0},
        }

    store = Archive(home)
    counts = {"created": 0, "updated": 0, "skipped": 0}
    for card in cards:
        try:
            previous = store.get_card(card["id"])
        except Exception:
            previous = None
        if previous and all(previous.get(key) == card.get(key) for key in CARD_KEYS):
            counts["skipped"] += 1
            continue
        try:
            store.put_card(dict(card))
        except Exception as exc:  # noqa: BLE001 - no partial claim
            return {"ok": False, "reason": "local_write_failed", "error": str(exc), "counts": counts}
        counts["updated" if previous else "created"] += 1

    from knowledge import bitable

    try:
        client = client or base_client(home)
        synced = bitable.sync(home, client=client)
    except Exception as exc:  # noqa: BLE001 - local write stays; rerun sync
        return {
            "ok": False,
            "reason": "bitable_sync_failed",
            "error": str(exc),
            "counts": counts,
            "note": "本地知识已写入；修复后重跑 init apply，会幂等补同步",
        }
    config = base_config(home)
    cards_table = (config.get("tables") or {}).get("cards")
    remote = {
        str((row.get("fields") or {}).get("卡片 ID") or ""): row.get("fields") or {}
        for row in _remote_cards(client, cards_table)
    }
    missing = [card["id"] for card in cards if card["id"] not in remote]
    if missing:
        return {
            "ok": False,
            "reason": "base_readback_missing",
            "missing": missing,
            "counts": counts,
            "sync": synced,
            "note": "飞书读回缺卡片；本地已写入，修复后重跑",
        }
    mismatched: list[str] = []
    for card in cards:
        fields = remote.get(card["id"]) or {}
        same = (
            _same_text(fields.get("label"), card.get("label"))
            and _same_ontology(fields.get("ontology"), card.get("ontology"))
            and _same_text(fields.get("annotation"), card.get("annotation"))
        )
        if not same:
            mismatched.append(card["id"])
    if mismatched:
        return {
            "ok": False,
            "reason": "base_readback_mismatch",
            "mismatched": mismatched,
            "counts": counts,
            "sync": synced,
            "note": (
                "飞书读回的卡片字段与确认草案不一致；未声称写入成功，"
                "修复同步问题后重跑 init apply"
            ),
        }
    try:
        retrieval = standard_retrieval(home)
    except HostRunError as exc:
        retrieval = {"ok": False, "error": str(exc)}
    payload = {
        "schema_version": 1,
        "applied_at": now(),
        "counts": counts,
        "cards": len(cards),
        "base_token": config.get("base_token"),
        "readback": {"cards": len(cards), "verified": True},
        "retrieval": retrieval,
    }
    write_json(home / APPLY_REL, payload)
    ok = bool(retrieval.get("ok"))
    install_mod.record(
        home,
        "knowledge",
        "ok" if ok else "blocked",
        note=(
            f"正式知识已写入飞书并完成标准检索（{len(cards)} 张卡片）"
            if ok
            else "卡片已写入，但标准检索未完成：" + str(retrieval.get("error"))
        ),
        evidence={
            "cards": len(cards),
            "counts": counts,
            "base_token": config.get("base_token"),
            "retrieval_plan": retrieval.get("plan"),
        },
        recheck=True,
    )
    return {"ok": ok, **payload, "sync": synced}


def adopt_existing(home: Path, *, client=None, confirmed: bool = False) -> dict:
    """Reuse an existing Base: import its cards, never rebuild or clear it.

    This is the *existing library* path, not new-knowledge initialization: the
    user explicitly keeps their current library, so no card is written from a
    test draft and no initialization gate applies.
    """
    home = Path(home).expanduser().resolve()
    client = client or base_client(home)
    if not confirmed:
        config = base_config(home)
        cards_id = (config.get("tables") or {}).get("cards")
        remote = _remote_cards(client, cards_id) if cards_id else []
        return {
            "ok": False,
            "reason": "needs_user_confirmation",
            "existing_cards": len(remote),
            "note": "确认沿用已有知识库；不会清空、重建或重新初始化现有卡片",
        }
    imported = import_remote_cards(home, client=client)
    store = Archive(home)
    cards = store.list_cards()
    if not cards:
        return {
            "ok": False,
            "reason": "existing_base_has_no_usable_cards",
            "gaps": imported.get("gaps") or [],
        }
    retrieval = standard_retrieval(home)
    install_mod.record(
        home,
        "knowledge",
        "ok",
        note=f"沿用已有知识库：{len(cards)} 张卡片已就绪",
        evidence={
            "imported": len(imported.get("imported") or []),
            "cards": len(cards),
            "retrieval_plan": retrieval.get("plan"),
        },
        recheck=True,
    )
    return {
        "ok": True,
        "reused": True,
        "cards": len(cards),
        "imported": imported,
        "retrieval": retrieval,
    }


def status(home: Path) -> dict:
    home = Path(home).expanduser().resolve()
    literature = read_json(home / LITERATURE_REL) or {}
    draft = read_json(home / DRAFT_REL / "draft.json") or {}
    confirmation = read_json(home / CONFIRM_REL) or {}
    applied = read_json(home / APPLY_REL) or {}
    try:
        cards = len(load_draft(home))
    except HostRunError:
        cards = 0
    return {
        "ok": bool(applied.get("applied_at")),
        "requirements": literature.get("requirements", ""),
        "literature": {
            "records": len(literature.get("records") or []),
            "readable": literature.get("readable", 0),
            "gaps": literature.get("gaps") or [],
        },
        "draft": {
            "cards": cards,
            "hash": draft.get("draft_hash"),
            "confirmed": bool(confirmation)
            and confirmation.get("draft_hash") == draft.get("draft_hash"),
        },
        "applied": applied,
        "next": (
            "init apply"
            if cards and confirmation
            else "init confirm"
            if cards
            else "init propose"
        ),
    }
