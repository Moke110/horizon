"""Build queued Keeper requests exclusively from committed Editor claims."""

import json


def editor_groups(report, proposals):
    if not isinstance(proposals, list):
        return []
    known = {(row["kind"], row["id"]): row["version"]
             for row in report["context_versions"].get("knowledge", [])}
    changes, identities = [], set()
    for proposed in proposals[:3]:
        if not isinstance(proposed, dict):
            continue
        claim_index, reason = proposed.get("claim_index"), proposed.get("reason")
        if (type(claim_index) is not int or not 0 <= claim_index < len(report["claims"])
                or not _bounded(reason, 420)):
            continue
        claim = report["claims"][claim_index]
        if claim["kind"] not in ("source", "inference"):
            continue
        sources = [{name: row[name] for name in ("material_id", "material_version", "locator", "excerpt")}
                   for row in claim["citations"] if row.get("status") != "abstract"][:3]
        if not sources:
            continue
        kind = proposed.get("kind")
        identity = proposed.get("id")
        fields = proposed.get("fields")
        if fields is None:
            fields = {key: proposed[key] for key in ("title", "content", "question") if key in proposed}
        if not isinstance(fields, dict):
            continue
        if kind == "card" and not identity:
            if set(fields) != {"title", "content"} or not _bounded(fields["title"], 150) or not _bounded(fields["content"], 500):
                continue
            key = ("create", fields["title"].casefold().strip())
            change = {"kind": kind, "action": "create"}
        elif kind in ("card", "frontier") and isinstance(identity, str) and (kind, identity) in known:
            if kind == "card":
                if set(fields) != {"content"} or not _bounded(fields["content"], 500):
                    continue
            elif (identity != report["context_versions"].get("writing_frontier_id")
                  or set(fields) != {"question"} or not _bounded(fields["question"], 350)):
                continue
            key = (kind, identity)
            change = {"kind": kind, "action": "update", "id": identity,
                      "expected_version": known[kind, identity]}
        else:
            continue
        if key not in identities:
            identities.add(key)
            changes.append({"changes": [{**change, "fields": fields, "reason": reason, "sources": sources}]})
    return changes


def _bounded(value, maximum):
    return isinstance(value, str) and bool(value.strip()) and len(value.encode()) <= maximum


def enqueue_editor_request(db, report, task_version, proposals):
    if type(task_version) is not int or task_version < 1:
        return
    groups = editor_groups(report, proposals)
    if not groups:
        return
    operation = f"editor-keeper:{report['report_id']}:{report['version']}"
    command = {"schema_version": 1, "operation_id": operation, "report_kind": "editor",
               "task_id": report["task_id"], "task_version": task_version,
               "report_id": report["report_id"], "report_version": report["version"], "groups": groups}
    db.execute("INSERT INTO editor_keeper_outbox VALUES(?,?,NULL)", (operation, json.dumps(command)))


def verify_editor_request(db, command):
    row = db.execute("SELECT request FROM editor_keeper_outbox WHERE operation_id=?",
                     (command["operation_id"],)).fetchone()
    if not row or json.loads(row[0]) != command:
        raise PermissionError("Keeper production intake requires a committed Editor request")
