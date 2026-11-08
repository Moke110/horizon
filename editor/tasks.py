"""Agent-facing editor tasks: build a ranking packet, validate the answer.

The agent does the semantic ranking. This module never calls a model.
"""

from __future__ import annotations

from pathlib import Path

from common import now, read_json, read_yaml
from archive.store import Archive, is_under_review
from horizon.model import is_temporary_card

INSTRUCTIONS = """你是检索排序器。根据每张知识卡片的 ontology / annotation 与关联命题，
从候选论文中挑出最值得今天推送的条目。排序时综合考虑：
1) 与知识卡片的语义相关性（ontology 命中但要以 annotation 排除误召回）；
2) 时效性；
3) 历史偏好（preferences.checkpoints / sources，冷启动时都是 0.5）；
4) 知识卡片权重。
输出 selection.json，字段见 contracts/README.md §4。每条必须写一句 rationale。
不得编造 candidates 之外的论文，不得超过 limits。"""


def _cards(home: Path) -> list[dict]:
    card_dir = Path(home) / "archive" / "cards"
    rows = [read_json(p) for p in sorted(card_dir.glob("*.json"))] if card_dir.exists() else []
    # Temporary acceptance cards live in the same archive but are not knowledge:
    # ranking context must never see them (#55).  Under review cards are kept
    # but equally excluded from formal ranking (#88).
    return [
        r
        for r in rows
        if r and not is_temporary_card(r) and not is_under_review(r)
    ]


def _card_context(home: Path, card: dict, weight: int) -> dict:
    root = Path(home) / "archive"
    card_id = card.get("id")
    props = []
    prop_dir = root / "propositions"
    if prop_dir.exists():
        for path in sorted(prop_dir.glob("*.json")):
            prop = read_json(path) or {}
            if is_under_review(prop):
                continue
            if card_id and card_id in prop.get("related_knowledge", []):
                props.append(
                    {
                        "prop_id": prop.get("id"),
                        "title": prop.get("title"),
                        "text": prop.get("content"),
                        "evidence": [
                            e.get("paper_id")
                            for entry in prop.get("history", [])
                            for e in entry.get("evidence", [])
                        ],
                    }
                )
    archived = 0
    raw_dir = Archive(Path(home)).raw
    if raw_dir.exists():
        for path in raw_dir.glob("*.json"):
            rec = read_json(path) or {}
            if (rec.get("card_id") or rec.get("checkpoint_id")) == card_id:
                archived += 1
    return {
        "label": card.get("label"),
        "annotation": card.get("annotation", ""),
        "ontology": card.get("ontology", []),
        "weight": weight,
        "card": {
            "id": card.get("id"),
            "label": card.get("label"),
            "annotation": card.get("annotation", ""),
            "ontology": card.get("ontology", []),
        },
        "propositions": props,
        "archived_count": archived,
    }


def _pushed_paper_ids(home: Path) -> set[str]:
    push_dir = Archive(Path(home)).pushes
    pushed: set[str] = set()
    if push_dir.exists():
        for path in sorted(push_dir.glob("*.json")):
            row = read_json(path) or {}
            pushed.update(row.get("paper_ids") or [])
    return pushed


def build_task(
    home: Path,
    candidates: list[dict],
    date: str,
    limit: int = 5,
    per_checkpoint: int = 2,
) -> dict:
    home = Path(home)
    state = read_yaml(home / "state" / "checkpoints.yaml") or {}
    model = read_yaml(home / "state" / "editor.yaml") or {}
    checkpoints = {}
    for card in _cards(home):
        st = state.get("checkpoints", {}).get(card["id"], {})
        checkpoints[card["id"]] = _card_context(home, card, st.get("weight", 5))

    pushed = _pushed_paper_ids(home)
    papers = []
    seen = set()
    for cand in candidates:
        pid = cand.get("paper_id")
        if not pid or pid in seen or pid in pushed:
            continue
        seen.add(pid)
        papers.append(
            {
                "paper_id": pid,
                "checkpoint_id": cand.get("checkpoint_id")
                or cand.get("retrieval", {}).get("checkpoint_id"),
                "source": cand.get("source"),
                "source_id": cand.get("source_id"),
                "title": cand.get("title"),
                "abstract": cand.get("abstract"),
                "authors": cand.get("authors", []),
                "journal": cand.get("journal"),
                "published": cand.get("published"),
                "is_preprint": cand.get("is_preprint", False),
                "url": cand.get("url"),
                "pmcid": cand.get("pmcid"),
                "doi": cand.get("doi"),
                "retrieval": cand.get("retrieval", {}),
            }
        )

    return {
        "schema_version": 1,
        "kind": "editor-task",
        "date": date,
        "generated_at": now(),
        "instructions": INSTRUCTIONS,
        "limits": {"limit": limit, "per_checkpoint": per_checkpoint},
        "preferences": {
            "checkpoints": model.get("checkpoints", {}),
            "sources": model.get("sources", {}),
        },
        "checkpoints": checkpoints,
        "candidates": papers,
    }


def validate(task: dict, selection: dict) -> dict:
    errors: list[str] = []
    candidates = {c["paper_id"]: c for c in task.get("candidates", [])}
    limits = task.get("limits", {})
    rows = selection.get("selected")
    if not isinstance(rows, list):
        return {"ok": False, "errors": ["selection.selected must be a list"]}
    if len(rows) > int(limits.get("limit", 5)):
        errors.append(f"selected {len(rows)} > limit {limits.get('limit')}")

    counts: dict[str, int] = {}
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        where = f"selected[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{where}: not an object")
            continue
        cp = row.get("checkpoint_id")
        paper = row.get("paper") or {}
        pid = paper.get("paper_id")
        if not pid:
            errors.append(f"{where}: missing paper.paper_id")
        elif pid not in candidates:
            errors.append(f"{where}: paper {pid} is not in candidates")
        elif candidates[pid].get("checkpoint_id") != cp:
            errors.append(
                f"{where}: paper {pid} belongs to {candidates[pid].get('checkpoint_id')}, not {cp}"
            )
        if pid in seen:
            errors.append(f"{where}: duplicate paper {pid}")
        seen.add(pid)
        score = row.get("score")
        if not isinstance(score, (int, float)) or not 0 <= float(score) <= 1:
            errors.append(f"{where}: score must be a number in 0..1")
        if not isinstance(row.get("rationale"), str) or not row["rationale"].strip():
            errors.append(f"{where}: rationale must be a non-empty string")
        counts[cp] = counts.get(cp, 0) + 1

    per_cp = int(limits.get("per_checkpoint", 2))
    for cp, count in counts.items():
        if count > per_cp:
            errors.append(f"checkpoint {cp}: {count} selected > per_checkpoint {per_cp}")

    return {
        "ok": not errors,
        "errors": errors,
        "selected": len(rows),
        "by_checkpoint": counts,
    }
