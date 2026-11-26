"""Transfer Hpedia records through reviewed Librarian operations."""
import json
import re
import uuid

from hostrun import hpedia
from hostrun.agent_service import CommandError


KINDS = {
    "term": ("t", "card_id", ("title", "content", "aliases", "relations", "sources", "validity", "tracked", "status")),
    "question": ("q", "question_id", ("prompt", "answer", "validity", "tracked")),
    "frontier": ("f", "frontier_id", ("question", "scope", "relations", "hard_constraints", "status")),
}
IDENTITY = re.compile(r"[tqf]-[0-9a-f]{32}")


def export(db, library):
    entries = []
    for kind, records in (("term", library.query_all_in_transaction(db)),
                          ("question", hpedia.list_questions(db)),
                          ("frontier", library.query_frontiers_in_transaction(db))):
        _, id_field, fields = KINDS[kind]
        for row in records:
            if row.get("status") == "deleted":
                continue
            content = {key: row[key] for key in fields if key in row}
            if kind == "term":
                content["sources"] = [{k: source[k] for k in ("material_id", "excerpt", "locator")}
                                      for source in row["sources"]]
            entries.append({"kind": kind, "id": row[id_field], "content": content})
    return {"schema_version": 1, "entries": entries}


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS hpedia_imports(
        id TEXT PRIMARY KEY, origin TEXT NOT NULL, package TEXT NOT NULL, card TEXT NOT NULL)""")


def invalid(message):
    return CommandError("invalid_import", message)


def review(db, librarian, package):
    current = {row["id"]: row for row in export(db, librarian.library)["entries"]}
    owners = {identity[2:]: identity[0] for identity in current}
    if db.execute("SELECT 1 FROM sqlite_schema WHERE name='hpedia_identities'").fetchone():
        owners.update(db.execute("SELECT body,prefix FROM hpedia_identities"))
    grouped = {}
    items = []
    for entry in package["entries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("content"), dict):
            raise invalid("百科导入记录无效")
        kind, identity = entry.get("kind"), entry.get("id")
        if kind not in KINDS or not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
            raise invalid("百科导入记录的类型或 ID 无效")
        local = current.get(identity)
        item = {**entry, "status": "skip" if local == entry else "create"}
        if local and item["status"] != "skip":
            item["status"] = "different"
            item["diff"] = {k: {"local": local["content"].get(k), "incoming": entry["content"].get(k)}
                            for k in sorted(local["content"].keys() | entry["content"].keys())
                            if local["content"].get(k) != entry["content"].get(k)}
        if identity[0] != KINDS[kind][0] or owners.get(identity[2:], identity[0]) != identity[0]:
            item.update(status="conflict", reason="声明类型与前缀不符或主体已被另一类型占用")
        elif set(entry["content"]) - set(KINDS[kind][2]):
            item.update(status="conflict", reason="记录包含未知字段")
        grouped.setdefault(identity[2:], []).append(item)
        items.append(item)
    for group in grouped.values():
        if len(group) > 1:
            for item in group:
                item.update(status="conflict", reason="导入文件中出现重复的 ID 主体")

    available = set(current) | {item["id"] for item in items}
    available.difference_update(item["id"] for item in items if item["status"] == "conflict")
    # Drop blocked dependents to a fixed point; valid cycles of term links remain importable.
    while True:
        before = len(available)
        for item in items:
            if item["status"] in ("conflict", "blocked"):
                continue
            if dependencies(item) - available:
                item.update(status="blocked", reason="依赖的条目有冲突或尚未导入")
                available.discard(item["id"])
        if len(available) == before:
            return items


def dependencies(item):
    data = item["content"]
    references = set()
    if isinstance(data.get("relations"), list):
        references.update(ref for ref in data["relations"] if isinstance(ref, str))
    for key in ("prompt", "answer", "content", "question"):
        if isinstance(data.get(key), str):
            references.update(hpedia.referenced_terms(data[key]))
    scope = data.get("scope")
    if isinstance(scope, dict) and isinstance(scope.get("text"), str):
        references.update(hpedia.referenced_terms(scope["text"]))
    return references


def request(db, librarian, request_id, package, origin, token):
    if not isinstance(request_id, str) or not request_id or not isinstance(origin, str) or not origin:
        raise invalid("导入需要请求标识和来源")
    if not isinstance(package, dict) or package.get("schema_version") != 1 or not isinstance(package.get("entries"), list):
        raise invalid("需要版本 1 的百科导出文件")
    initialize(db)
    encoded = json.dumps(package, sort_keys=True)
    row = db.execute("SELECT origin, package, card FROM hpedia_imports WHERE id=?", (request_id,)).fetchone()
    if row:
        if row[:2] != (origin, encoded):
            raise invalid("导入请求标识已使用")
        return json.loads(row[2])
    card = {"operation_id": request_id, "status": "pending", "items": review(db, librarian, package),
            "expires_at": librarian.clock() + librarian.confirmation_lifetime}
    db.execute("INSERT INTO hpedia_imports VALUES(?,?,?,?)", (request_id, origin, encoded, json.dumps(card)))
    return card


def write(db, librarian, item, token):
    library = librarian.library
    kind, identity = item["kind"], item["id"]
    content = item["content"].copy()
    archived = kind == "term" and content.pop("status", None) == "archived"
    command = {**content, "operation_id": str(uuid.uuid4()), "schema_version": 1}
    action = "update" if item["status"] == "different" else "create"
    kwargs = {"now": librarian.clock(), "import_id": identity if action == "create" else None}
    if action == "update":
        if kind == "term":
            current = library.read_in_transaction(db, identity)
        elif kind == "frontier":
            current = library.read_frontier_in_transaction(db, identity)
        else:
            current = hpedia.read(db, identity)
        command.update({KINDS[kind][1]: identity, "expected_version": current["version"]})
    if kind == "term":
        term, _ = library.apply(db, action, command, token=token, **kwargs)
        if archived:
            library.apply(db, "archive", {"schema_version": 1, "operation_id": str(uuid.uuid4()),
                          "card_id": identity, "expected_version": term["version"]}, token=token, now=kwargs["now"])
    elif kind == "frontier":
        library.apply_frontier(db, action, command, token=token, **kwargs)
    else:
        library.apply_question(db, action, command, **kwargs)


def decide(db, librarian, request_id, choices, origin, token):
    initialize(db)
    row = db.execute("SELECT card,package FROM hpedia_imports WHERE id=? AND origin=?", (request_id, origin)).fetchone()
    if not row:
        raise invalid("找不到此导入确认")
    card = json.loads(row[0])
    if card["status"] != "pending":
        return card
    if librarian.clock() >= card["expires_at"]:
        card["status"] = "expired"
    elif choices is None:
        card["status"] = "rejected"
    else:
        if not isinstance(choices, dict):
            raise invalid("导入确认选项无效")
        if card["items"] != review(db, librarian, json.loads(row[1])):
            raise invalid("本地条目已有变更，请重新导入并核对差异")
        selected = []
        for item in card["items"]:
            if item["status"] == "different" and choices.get(item["id"]) not in ("incoming", "local"):
                raise invalid("请选择保留本地或导入版本")
            if item["status"] == "create" or (item["status"] == "different" and choices[item["id"]] == "incoming"):
                selected.append(item)
        # Introduce all term identities before installing potentially cyclic links.
        deferred = []
        for item in selected:
            if item["status"] == "create" and item["kind"] == "term":
                content = item["content"]
                write(db, librarian, {**item, "content": {**content, "relations": [], "status": "active"}}, token)
                if content.get("relations") or content.get("status") == "archived":
                    deferred.append({**item, "status": "different"})
            else:
                deferred.append(item)
        for item in deferred:
            write(db, librarian, item, token)
        card["status"] = "confirmed"
    db.execute("UPDATE hpedia_imports SET card=? WHERE id=?", (json.dumps(card), request_id))
    return card
