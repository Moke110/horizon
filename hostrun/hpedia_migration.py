"""Migrate Hpedia identities and typed references together in one transaction."""
import json
import re
from collections import defaultdict

from hostrun.agent_service import CommandError
from hostrun.hpedia_identity import allocate

ENTRIES = {"t": ("knowledge_cards", "card_id"), "q": ("hpedia_questions", "question_id"),
           "f": ("frontiers", "frontier_id")}
FIELDS = {"card_id": "t", "term_id": "t", "question_id": "q", "frontier_id": "f",
          "term_ids": "t", "related_cards": "t", "relations": "t"}
LINK = re.compile(r"\[\[term:([^\[\]\s]+)\]\]")
UUID = re.compile(r"^(?:[tqf]-)?([0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$", re.I)
# Tables whose JSON documents carry Hpedia references, keyed by stable record identity.
DOCUMENTS = {table: (field, ("result",)) for table, field in ENTRIES.values()}
for owner in ("knowledge_card", "frontier", "voyager", "knowledge_theme"):
    DOCUMENTS[owner + "_operations"] = ("operation_id", ("request", "result"))
    DOCUMENTS[owner + "_events"] = ("operation_id", ("event",))
DOCUMENTS.update(frontier_progress=("progress_id", ("result",)), voyager_requests=("request_id", ("result",)),
                 knowledge_themes=("theme_id", ("result",)), librarian_term_requests=("id", ("request", "result")))


def _body(value):
    match = UUID.fullmatch(value or "")
    return match[1].replace("-", "").lower() if match else None


def _visit(value, resolve, field=None):
    if isinstance(value, dict):
        return {key: _visit(child, resolve, key) for key, child in value.items()}
    if isinstance(value, list):
        return [_visit(item, resolve, field) for item in value]
    if isinstance(value, str):
        if field in FIELDS:
            return resolve(FIELDS[field], value)
        if field in ("prompt", "answer", "question", "text", "content", "scope_text"):
            return LINK.sub(lambda m: "[[term:" + resolve("t", m[1]) + "]]", value)
    return value


def _related_indexes(db, table, key, doc):
    if table == "hpedia_questions":
        db.execute("DELETE FROM hpedia_term_references WHERE question_id=?", (key,))
        db.executemany("INSERT OR IGNORE INTO hpedia_term_references VALUES(?,?)",
                       [(key, term) for term in doc.get("term_ids", [])])
    if table == "frontier_progress":
        db.execute("UPDATE frontier_progress SET frontier_id=? WHERE progress_id=?", (doc["frontier_id"], key))


def migrate(db):
    db.execute("CREATE TABLE IF NOT EXISTS hpedia_migrations(name TEXT PRIMARY KEY)")
    if not db.execute("INSERT OR IGNORE INTO hpedia_migrations VALUES('typed_ids')").rowcount:
        return
    db.execute("""CREATE TABLE hpedia_reference_reviews(
        review_id INTEGER PRIMARY KEY, details TEXT NOT NULL, deferred INTEGER DEFAULT 0)""")
    db.execute("CREATE TABLE IF NOT EXISTS hpedia_identities(body TEXT PRIMARY KEY, prefix TEXT NOT NULL)")
    exact = defaultdict(set)
    aliases = defaultdict(set)
    titles = defaultdict(set)
    entries = []
    for prefix, (table, field) in ENTRIES.items():
        for row, key, encoded in db.execute(f"SELECT rowid,{field},result FROM {table}"):
            doc = json.loads(encoded)
            entries.append((prefix, table, field, row, key, doc))
    # Keep established canonical identities first if a legacy UUID collides.
    entries.sort(key=lambda item: not (item[4] and item[4] == item[0] + "-" + (_body(item[4]) or "")))
    used = set()
    for prefix, table, field, row, key, doc in entries:
        body = _body(key) or _body(doc.get(field))
        canonical = body and prefix + "-" + body
        if canonical != key or not body:
            if body and body not in used and db.execute(
                    "INSERT OR IGNORE INTO hpedia_identities VALUES(?,?)", (body,prefix)).rowcount:
                identity = canonical
            else:
                identity = allocate(db, prefix)
        else:
            identity = canonical
            db.execute("INSERT OR IGNORE INTO hpedia_identities VALUES(?,?)", (body,prefix))
            if body in used:
                identity = allocate(db, prefix)
        used.add(identity[2:])
        for alias in (key, doc.get(field), identity):
            if isinstance(alias, str) and alias:
                exact[prefix,alias].add(identity)
                aliases[prefix, _body(alias) or alias].add(identity)
        for title in (doc.get("title"), doc.get("prompt"), doc.get("question"), *doc.get("aliases", [])):
            if isinstance(title,str) and title:
                titles[prefix,title].add(identity)
        doc[field] = identity
        db.execute(f"UPDATE {table} SET {field}=?,result=? WHERE rowid=?", (identity,json.dumps(doc),row))

    db.execute("DELETE FROM hpedia_term_references")
    tables = {name for name, in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    for table, (primary, columns) in DOCUMENTS.items():
        if table not in tables:
            continue
        for column in columns:
            for key, encoded in db.execute(f"SELECT {primary},{column} FROM {table}").fetchall():
                doc = json.loads(encoded)
                pending = {}
                def resolve(prefix, raw):
                    choices = exact[prefix,raw] or aliases[prefix,_body(raw) or raw] or titles[prefix,raw]
                    # A newly assigned canonical ID always identifies its own entry.
                    if raw in choices:
                        return raw
                    if len(choices) == 1:
                        return next(iter(choices))
                    if len(choices) > 1:
                        pending[prefix,raw] = sorted(choices)
                    return raw
                updated = _visit(doc, resolve)
                if updated != doc:
                    db.execute(f"UPDATE {table} SET {column}=? WHERE {primary}=?",
                               (json.dumps(updated,sort_keys=column=="request"),key))
                if column == "result":
                    _related_indexes(db,table,key,updated)
                for (prefix,raw), candidates in pending.items():
                    details = dict(table=table,column=column,key=key,prefix=prefix,raw=raw,candidates=candidates)
                    db.execute("INSERT INTO hpedia_reference_reviews(details) VALUES(?)",(json.dumps(details),))
    if "librarian_term_locks" in tables:
        for key, in db.execute("SELECT term_id FROM librarian_term_locks").fetchall():
            kind, sep, rest = key.partition(":")
            prefix = {"question":"q","frontier":"f"}.get(kind,"t")
            raw = rest if sep else key
            targets = exact[prefix,raw] or aliases[prefix,_body(raw) or raw]
            if len(targets) == 1:
                db.execute("UPDATE librarian_term_locks SET term_id=? WHERE term_id=?",
                           ((kind+sep if sep else "")+next(iter(targets)),key))


def reviews(db):
    result = []
    for review_id, encoded, deferred in db.execute("SELECT * FROM hpedia_reference_reviews ORDER BY review_id"):
        item = json.loads(encoded)
        table, field = ENTRIES[item["prefix"]]
        candidates = []
        for identity in item["candidates"]:
            row = db.execute(f"SELECT result FROM {table} WHERE {field}=?", (identity,)).fetchone()
            if row:
                doc = json.loads(row[0])
                candidates.append(dict(id=identity,title=doc.get("title") or doc.get("question") or doc.get("prompt"),
                                       summary=doc.get("content") or doc.get("answer") or doc.get("scope",{}).get("text","")))
        result.append(dict(id=review_id,raw=item["raw"],owner=item["key"],candidates=candidates,deferred=bool(deferred)))
    return result


def decide(db, review_id, target):
    row = db.execute("SELECT details FROM hpedia_reference_reviews WHERE review_id=?", (review_id,)).fetchone()
    if not row:
        return
    item = json.loads(row[0])
    if target is None:
        db.execute("UPDATE hpedia_reference_reviews SET deferred=1 WHERE review_id=?", (review_id,))
        return
    if target not in item["candidates"]:
        raise CommandError("invalid_target", "请选择待确认引用的候选条目")
    table, column, key = item["table"], item["column"], item["key"]
    if table not in DOCUMENTS or column not in DOCUMENTS[table][1]:
        raise CommandError("invalid_target", "无效的历史引用位置")
    primary = DOCUMENTS[table][0]
    current = db.execute(f"SELECT {column} FROM {table} WHERE {primary}=?", (key,)).fetchone()
    if current:
        updated = _visit(json.loads(current[0]), lambda p,r: target if (p,r)==(item["prefix"],item["raw"]) else r)
        db.execute(f"UPDATE {table} SET {column}=? WHERE {primary}=?", (json.dumps(updated,sort_keys=column=="request"),key))
        if column == "result":
            _related_indexes(db,table,key,updated)
    db.execute("DELETE FROM hpedia_reference_reviews WHERE review_id=?", (review_id,))

