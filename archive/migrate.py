"""One-shot v1 → v2 migration for the knowledge store.

v1 had three overlapping notions: checkpoints (``horizon.yaml``), knowledge-graph
nodes (``archive/kg/nodes``) and propositions keyed by node ids. v2 keeps two:

* knowledge cards — ``id / label / ontology / annotation``, no links;
* propositions — ``id / title / content / history / related_knowledge``.

The migration is additive in spirit but backs up every file it touches. Running
it twice is a no-op; ``rollback`` restores the backup byte for byte.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from archive.store import Archive, ArchiveError, card_id_for, id_slug, prop_id_for
from common import clip, ensure_dir, now, read_json, read_yaml, write_json


def _ts() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")


def _rel(home: Path, path: Path) -> str:
    return str(path.relative_to(home))


def _copy_into(home: Path, backup: Path, path: Path) -> str | None:
    if not path.exists():
        return None
    rel = _rel(home, path)
    target = backup / rel
    ensure_dir(target.parent)
    if path.is_dir():
        shutil.copytree(path, target, dirs_exist_ok=True)
    else:
        shutil.copy2(path, target)
    return rel


def _restore(backup: Path, home: Path, rel: str) -> None:
    src = backup / rel
    dst = home / rel
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        ensure_dir(dst.parent)
        shutil.copy2(src, dst)


def _unique(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def _migrate_cards(home: Path, store: Archive) -> tuple[dict[str, str], list[dict], list[str]]:
    vocab = read_yaml(home / "horizon.yaml") or {}
    checkpoints = vocab.get("checkpoints") or []
    nodes = [read_json(p) or {} for p in sorted((store.root / "kg" / "nodes").glob("*.json"))]
    nodes = [n for n in nodes if n.get("node_id")]

    mapping: dict[str, str] = {}
    cards: list[dict] = []
    used: dict[str, dict] = {}
    warnings: list[str] = []

    # Checkpoints become cards first; they carry the hand-written ontology and
    # annotation that used to drive retrieval.
    for cp in checkpoints:
        node_id = cp.get("kg_node_id")
        node = next((n for n in nodes if n["node_id"] == node_id), {})
        card_id = "card_" + id_slug(cp.get("id", ""))
        if card_id == "card_":
            raise ArchiveError(f"checkpoint {cp.get('id')!r} has no ASCII slug for a card id")
        label = node.get("label") or cp.get("label") or cp.get("id")
        annotation = (cp.get("annotation") or "").strip() or (node.get("summary") or "").strip()
        if not annotation:
            warnings.append(f"{cp.get('id')}: no annotation or summary; label used")
            annotation = label
        cards.append(
            {
                "id": card_id,
                "label": label,
                "ontology": _unique([*(cp.get("ontology") or []), *(node.get("aliases") or [])]),
                "annotation": annotation,
            }
        )
        used[card_id] = cards[-1]
        if node_id:
            mapping[node_id] = card_id

    # Remaining graph nodes become their own cards. They lose edges, type and
    # summary-as-card-summary; aliases become ontology terms.
    for node in nodes:
        if node["node_id"] in mapping:
            continue
        label = node.get("label") or node["node_id"]
        card_id = card_id_for(label, used)
        annotation = (node.get("summary") or "").strip() or label
        cards.append(
            {
                "id": card_id,
                "label": label,
                "ontology": _unique([label, *(node.get("aliases") or [])]),
                "annotation": annotation,
            }
        )
        used[card_id] = cards[-1]
        mapping[node["node_id"]] = card_id

    # Edges were part of v1; no card-level links exist in v2.
    edges = store.root / "kg" / "edges.jsonl"
    if edges.exists() and edges.read_text(encoding="utf-8").strip():
        warnings.append("v1 kg edges were dropped: v2 cards carry no links")

    return mapping, cards, warnings


def _prop_title(text: str, fallback_id: str) -> str:
    text = (text or "").strip().rstrip("。.")
    return clip(text, 60) or fallback_id


def _migrate_props(
    store: Archive, node_map: dict[str, str], cards: dict[str, dict]
) -> tuple[list[dict], dict[str, str]]:
    old_dir = store.props
    props: list[dict] = []
    prop_map: dict[str, str] = {}
    used: dict[str, dict] = {}
    for path in sorted(old_dir.glob("*.json")):
        old = read_json(path) or {}
        if not old:
            continue
        if old.get("related_knowledge"):
            # Already v2; do not touch.
            continue
        old_id = old.get("prop_id") or path.stem
        text = old.get("text") or old.get("content") or ""
        title = _prop_title(text, old_id)
        # Keep the old id when it is already a semantic slug; the one legacy
        # abbreviation (vc) gets spelled out.
        explicit = old_id if old_id.startswith("prop_") else None
        if explicit == "prop_vc_perturbation":
            explicit = "prop_virtual_cell_perturbation"
        new_id = prop_id_for(title, used, explicit=explicit)
        related = _unique([node_map.get(nid, "") for nid in old.get("node_ids") or []])
        related = [r for r in related if r]
        if len(related) < 2:
            # Keep every proposition valid: pad with the cards that existed for
            # the same checkpoint (or the first card as a last resort).
            for candidate in cards:
                if candidate not in related:
                    related.append(candidate)
                if len(related) >= 2:
                    break
        evidence = []
        for item in old.get("evidence") or []:
            evidence.append(
                {
                    "paper_id": item.get("paper_id"),
                    "push_id": item.get("push_id"),
                    "page": item.get("page"),
                    "quote": item.get("note", ""),
                }
            )
        props.append(
            {
                "id": new_id,
                "title": title,
                "content": text,
                "related_knowledge": related,
                "history": [
                    {
                        "at": now(),
                        "action": "created",
                        "source": {"kind": "migration", "id": f"v1:{old_id}"},
                        "before": old,
                        "after": {
                            "title": title,
                            "content": text,
                            "related_knowledge": related,
                        },
                        "rationale": (
                            "v1 → v2 迁移：文本字段改为 title/content，node_ids 改为 "
                            "related_knowledge，evidence 移入 history。"
                        ),
                        "evidence": evidence,
                    }
                ],
            }
        )
        used[new_id] = props[-1]
        prop_map[old_id] = new_id
    return props, prop_map


def _rewrite_push(
    push: dict,
    cp_map: dict[str, str],
    node_map: dict[str, str],
    prop_map: dict[str, str],
) -> dict:
    push = json.loads(json.dumps(push))
    old_cp = push.get("checkpoint_id")
    if old_cp in cp_map:
        push["checkpoint_id"] = cp_map[old_cp]
    push.pop("kg_node_id", None)
    ctx = push.get("context") or {}
    if "kg_node_id" in ctx:
        ctx.pop("kg_node_id", None)
    if isinstance(ctx.get("propositions"), list):
        ctx["propositions"] = [
            prop_map.get(prop_id, prop_id) for prop_id in ctx["propositions"]
        ]
        push["context"] = ctx
    if isinstance(push.get("proposals"), list):
        for proposal in push["proposals"]:
            proposal["status"] = "historical"
            proposal["historical"] = True
            proposal["migrated_from"] = "v1"
            target = proposal.get("target_id")
            if target in node_map:
                proposal["target_id"] = node_map[target]
            elif target in prop_map:
                proposal["target_id"] = prop_map[target]
            value = (proposal.get("diff") or {}).get("value")
            if isinstance(value, dict):
                if "node_ids" in value:
                    value["related_knowledge"] = _unique(
                        [node_map.get(n, "") for n in value.get("node_ids") or []]
                    )
                    value["related_knowledge"] = [
                        c for c in value["related_knowledge"] if c
                    ]
                if value.get("prop_id") in prop_map:
                    value["prop_id"] = prop_map[value["prop_id"]]
    return push


def migrate(home: Path, dry_run: bool = False) -> dict:
    home = Path(home).resolve()
    store = Archive(home)
    cards_dir = store.cards
    existing_cards = list(cards_dir.glob("*.json")) if cards_dir.exists() else []
    horizon = home / "horizon.yaml"

    if existing_cards and not horizon.exists():
        return {
            "ok": True,
            "status": "already-migrated",
            "home": str(home),
            "cards": len(existing_cards),
        }
    if existing_cards and horizon.exists():
        raise ArchiveError(
            "archive/cards is populated but horizon.yaml still exists; "
            "refusing to guess (roll back or clean up manually)"
        )
    if not horizon.exists():
        raise ArchiveError(f"nothing to migrate: no {horizon}")

    vocab = read_yaml(horizon) or {}
    cp_map = {}
    for cp in vocab.get("checkpoints") or []:
        cp_map[cp.get("id")] = "card_" + id_slug(cp.get("id", ""))
    node_map, cards, warnings = _migrate_cards(home, store)
    props, prop_map = _migrate_props(store, node_map, {c["id"]: c for c in cards})

    pushes = []
    for path in sorted(store.pushes.glob("*.json")):
        push = read_json(path) or {}
        if push:
            pushes.append(_rewrite_push(push, cp_map, node_map, prop_map))

    papers = []
    for path in sorted(store.raw.glob("*.json")):
        paper = read_json(path) or {}
        if paper:
            old_cp = paper.get("checkpoint_id")
            if old_cp in cp_map:
                paper["checkpoint_id"] = cp_map[old_cp]
            papers.append(paper)

    state_path = home / "state" / "checkpoints.yaml"
    state = read_yaml(state_path) or {}
    new_state = {"schema_version": 1, "checkpoints": {}}
    for old_key, value in (state.get("checkpoints") or {}).items():
        new_key = cp_map.get(old_key, old_key)
        new_state["checkpoints"][new_key] = value
    for card in cards:
        new_state["checkpoints"].setdefault(
            card["id"],
            {
                "weight": 5,
                "ema": 0.5,
                "enabled": True,
                "last_checked": None,
                "hit_rate": {
                    "checked": 0,
                    "pushed": 0,
                    "scored_total": 0,
                    "scored_positive": 0,
                },
                "proposals": [],
            },
        )

    ratings = []
    push_card = {p["push_id"]: p.get("checkpoint_id") for p in pushes}
    ratings_path = home / "ratings.jsonl"
    if ratings_path.exists():
        from common import read_jsonl

        for row in read_jsonl(ratings_path):
            row = dict(row)
            if row.get("push_id") in push_card and not row.get("card_id"):
                row["card_id"] = push_card[row["push_id"]]
            ratings.append(row)

    plan = {
        "ok": True,
        "status": "dry-run" if dry_run else "migrated",
        "home": str(home),
        "cards": cards,
        "propositions": props,
        "papers": len(papers),
        "pushes": len(pushes),
        "ratings": len(ratings),
        "checkpoint_map": cp_map,
        "node_map": node_map,
        "warnings": warnings,
    }
    if dry_run:
        plan["backup"] = None
        return plan

    # ------------------------------------------------------------ backup
    backup = store.legacy / f"v1-{_ts()}"
    ensure_dir(backup)
    backed: list[str] = []
    for path in (
        horizon,
        state_path,
        ratings_path,
        store.root / "kg",
        store.root / "index",
        store.root / "propositions",
        store.root / "raw",
        store.root / "pushes",
    ):
        rel = _copy_into(home, backup, path)
        if rel:
            backed.append(rel)
    write_json(
        backup / "manifest.json",
        {
            "schema_version": 1,
            "created_at": now(),
            "files": backed,
            "new_paths": ["archive/cards", "archive/legacy"],
            "removed_paths": ["archive/kg"],
        },
    )

    # ------------------------------------------------------------ write
    for card in cards:
        store.put_card(card)
    for prop in props:
        store.put_prop(prop)
    # A renamed proposition (prop_vc_perturbation -> prop_virtual_cell_perturbation)
    # leaves the v1 file behind; drop v1-shaped leftovers now that they are backed up.
    keep = {p["id"] for p in props}
    for path in store.props.glob("*.json"):
        old = read_json(path) or {}
        if path.stem not in keep and old.get("prop_id") and not old.get("id"):
            path.unlink()
    for paper in papers:
        write_json(store.raw / f"{paper['paper_id']}.json", paper)
    for push in pushes:
        write_json(store.pushes / f"{push['push_id']}.json", push)
    if ratings:
        from common import write_jsonl

        write_jsonl(ratings_path, ratings)
    from common import write_yaml

    write_yaml(state_path, new_state)

    # v1-only layers retire. horizon.yaml moves into the backup; kg/ is removed
    # (it is already copied under backup/archive/kg).
    horizon.unlink()
    if (store.root / "kg").exists():
        shutil.rmtree(store.root / "kg")
    store.reindex()
    legacy_index = store.index / "kg.jsonl"
    if legacy_index.exists():
        legacy_index.unlink()

    report = {
        **plan,
        "status": "migrated",
        "backup": str(backup),
        "migrated_at": now(),
    }
    write_json(backup / "report.json", report)
    return report


def rollback(home: Path, backup: str) -> dict:
    home = Path(home).resolve()
    backup_dir = Path(backup)
    if not backup_dir.is_absolute():
        backup_dir = home / "archive" / "legacy" / backup
    manifest = read_json(backup_dir / "manifest.json")
    if not manifest:
        raise ArchiveError(f"no manifest under {backup_dir}")

    # Remove v2-only output before restoring v1 files.
    cards = home / "archive" / "cards"
    if cards.exists():
        shutil.rmtree(cards)
    for rel in manifest.get("files") or []:
        src = backup_dir / rel
        dst = home / rel
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
        if src.exists():
            _restore(backup_dir, home, rel)
    Archive(home).reindex()
    return {
        "ok": True,
        "status": "rolled-back",
        "backup": str(backup_dir),
        "restored": manifest.get("files", []),
    }


def migrate_v3(home: Path, dry_run: bool = False) -> dict:
    """Convert v2 propositions (content + history) to v3 (review + evidence + changelog)."""
    from archive.store import v3_prop_from_v2

    home = Path(home).resolve()
    store = Archive(home)
    paths = sorted(store.props.glob("*.json"))
    if not paths:
        return {"ok": True, "status": "nothing-to-migrate", "home": str(home), "converted": 0}
    pending = []
    already = 0
    for path in paths:
        raw = read_json(path) or {}
        if raw.get("schema_version") == 3 and "review" in raw and "evidence" in raw:
            already += 1
            continue
        pending.append((path, raw))
    if not pending:
        return {
            "ok": True,
            "status": "already-migrated",
            "home": str(home),
            "converted": 0,
            "already": already,
        }
    plan = {
        "ok": True,
        "status": "dry-run" if dry_run else "migrated",
        "home": str(home),
        "converted": len(pending),
        "already": already,
        "propositions": [raw.get("id") or raw.get("prop_id") for _, raw in pending],
    }
    if dry_run:
        return plan

    backup = store.legacy / f"v3-{_ts()}"
    backed = _copy_into(home, backup, store.props)
    write_json(
        backup / "manifest.json",
        {
            "schema_version": 1,
            "created_at": now(),
            "files": [backed] if backed else [],
            "kind": "v2-to-v3-propositions",
        },
    )
    converted = []
    for path, raw in pending:
        prop = v3_prop_from_v2(raw)
        store.put_prop(prop)
        converted.append(prop["id"])
    store.reindex()
    report = {
        **plan,
        "status": "migrated",
        "converted_ids": converted,
        "backup": str(backup),
        "migrated_at": now(),
    }
    write_json(backup / "report.json", report)
    return report


def rollback_v3(home: Path, backup: str) -> dict:
    home = Path(home).resolve()
    backup_dir = Path(backup)
    if not backup_dir.is_absolute():
        backup_dir = home / "archive" / "legacy" / backup
    manifest = read_json(backup_dir / "manifest.json")
    if not manifest or manifest.get("kind") != "v2-to-v3-propositions":
        raise ArchiveError(f"no v3 manifest under {backup_dir}")
    store = Archive(home)
    for rel in manifest.get("files") or []:
        src = backup_dir / rel
        dst = home / rel
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
    store.reindex()
    return {
        "ok": True,
        "status": "rolled-back",
        "backup": str(backup_dir),
        "restored": manifest.get("files", []),
    }
