"""Plan removal of marked development records without changing genuine records."""

from collections import defaultdict
import json

from hostrun import convergence
from hostrun.agent_service import CommandError
from hostrun.archive_reports import ArchiveReports, REPORT_ID
from hostrun.library_tags import TEST_TAG


_RECORDS = {
    "question": ("hpedia_questions", "result", "question_id", "prompt"),
    "progress": ("frontier_progress", "result", "progress_id", "content"),
    "source": ("library_archive_sources", "record", "source_id", "title"),
    "term": ("knowledge_cards", "result", "card_id", "title"),
    "frontier": ("frontiers", "result", "frontier_id", "question"),
    "report": ("library_archive_reports", "record", "report_id", "title"),
}
_LOCK_PREFIX = {"term": "", "question": "question:", "source": "archive:", "frontier": "frontier:"}


def plan(db, selection=None):
    ArchiveReports.initialize(db)
    records = {}
    for kind, (table, payload, id_field, _) in _RECORDS.items():
        for (raw,) in db.execute(f"SELECT {payload} FROM {table}"):
            item = json.loads(raw)
            if item.get("status") not in ("archived", "deleted"):
                records[kind, item[id_field]] = item
    marked = {key for key, item in records.items() if key[0] != "report" and TEST_TAG in item.get("tags", [])}
    if selection is not None:
        if (not isinstance(selection, list) or any(
            not isinstance(row, (list, tuple)) or len(row) != 2
            or any(not isinstance(value, str) for value in row) for row in selection
        )):
            raise CommandError("invalid_command", "需要有效的测试条目标识")
        selected = {tuple(row) for row in selection}
        if selected - marked:
            raise CommandError("test_cleanup_changed", "清理范围已变化或包含非测试条目")
        marked = selected
    if not marked:
        return {"entries": []}

    references = defaultdict(list)
    incoming = defaultdict(set)

    def describe(key, effect):
        item = records.get(key)
        return {"kind": key[0], "id": key[1], "effect": effect,
                "title": item[_RECORDS[key[0]][3]] if item else key[1]}

    def relate(owner, target):
        if owner in marked:
            references[owner].append(describe(target, "retained"))
        if target in marked:
            incoming[target].add(owner)

    for key, item in records.items():
        kind, identity = key
        if key in marked and kind in ("term", "question") and convergence.current(item) != "resolved":
            references[key].append({"kind": "bulletin", "id": identity, "title": "白板链接", "effect": "removed"})
        if kind == "question":
            for target in item["term_ids"]:
                relate(key, ("term", target))
        elif kind == "term":
            for target in item.get("relations", []):
                relate(key, ("term", target))
            if key in marked:
                for source in item.get("sources", []):
                    references[key].append({"kind": "original", "id": source["material_id"],
                                            "title": source.get("title", "原文文件"), "effect": "retained"})
        elif kind == "progress":
            relate(key, ("frontier", item["frontier_id"]))
            for target in item["source_ids"]:
                relate(key, ("report" if REPORT_ID.fullmatch(target) else "source", target))
        elif kind == "report":
            for target in item["covered_docs"]:
                relate(key, ("source", target))
        elif kind == "source" and key in marked and item.get("material_id"):
            references[key].append({"kind": "original", "id": item["material_id"],
                                    "title": "原文文件", "effect": "retained"})

    if db.execute("SELECT 1 FROM sqlite_master WHERE name='voyager_requests'").fetchone():
        for identity, frontier_id in db.execute("""
            SELECT request_id, json_extract(result,'$.scope.frontier_id') FROM voyager_requests
            WHERE json_extract(result,'$.scope.kind')='frontier'
        """):
            key = ("frontier", frontier_id)
            if key in marked:
                references[key].append({"kind": "retrieval", "id": identity, "title": "Voyager 检索记录",
                                        "effect": "retained"})

    removable = set(marked)
    while True:
        blocked = {key for key in removable if incoming[key] - removable}
        if not blocked:
            break
        removable -= blocked
    entries = []
    for key in sorted(marked, key=lambda key: (list(_RECORDS).index(key[0]), key[1])):
        item = records[key]
        refs = references[key] + [
            describe(owner, "removed" if owner in removable else "blocked")
            for owner in sorted(incoming[key])
        ]
        for ref in refs:
            if (ref["kind"], ref["id"]) in removable:
                ref["effect"] = "removed"
        entries.append({"kind": key[0], "id": key[1], "version": item["version"],
                        "title": item[_RECORDS[key[0]][3]], "blocked": key not in removable,
                        "references": sorted(refs, key=lambda ref: (ref["kind"], ref["id"], ref["effect"])),
                        **({"frontier_id": item["frontier_id"]} if key[0] == "progress" else {})})
    return {"entries": entries}


def lock_keys(entries):
    keys = set()
    for item in entries:
        kind = item["kind"]
        if kind == "progress":
            keys.add("frontier:" + item["frontier_id"])
        else:
            keys.add(_LOCK_PREFIX[kind] + item["id"])
    return keys


def removal_command(item, operation_id):
    kind = item["kind"]
    _, _, field, _ = _RECORDS[kind]
    return {"schema_version": 1, "operation_id": operation_id,
            "expected_version": item["version"], field: item["id"],
            **({"frontier_id": item["frontier_id"]} if kind == "progress" else {})}
