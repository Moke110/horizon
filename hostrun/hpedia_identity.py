"""Permanent identities and durable deletion markers for Hpedia entries."""
import uuid


def initialize_deleted(db):
    db.execute("CREATE TABLE IF NOT EXISTS hpedia_deleted(id TEXT PRIMARY KEY, version INTEGER NOT NULL)")


def mark_deleted(db, identity, version):
    initialize_deleted(db)
    db.execute("INSERT OR REPLACE INTO hpedia_deleted VALUES(?,?)", (identity, version))


def import_version(db, import_id):
    if import_id is None:
        return 1
    initialize_deleted(db)
    row = db.execute("SELECT version FROM hpedia_deleted WHERE id=?", (import_id,)).fetchone()
    return row[0] + 1 if row else 1


def migrate_deleted(db):
    initialize_deleted(db)
    if not db.execute("INSERT OR IGNORE INTO hpedia_migrations VALUES('deleted_ids')").rowcount:
        return
    db.execute("""INSERT OR IGNORE INTO hpedia_deleted
        SELECT frontier_id,json_extract(result,'$.version') FROM frontiers
        WHERE json_extract(result,'$.status')='deleted'""")
    db.execute("""INSERT OR IGNORE INTO hpedia_deleted SELECT 'q-'||body,1 FROM hpedia_identities
        WHERE prefix='q' AND NOT EXISTS (SELECT 1 FROM hpedia_questions WHERE question_id='q-'||body)""")


def allocate(db, prefix, import_id=None):
    db.execute("CREATE TABLE IF NOT EXISTS hpedia_identities(body TEXT PRIMARY KEY, prefix TEXT NOT NULL)")
    if import_id is not None:
        body = import_id.removeprefix(prefix + "-")
        if len(body) != 32 or uuid.UUID(hex=body).hex != body or import_id != f"{prefix}-{body}":
            raise ValueError("invalid imported Hpedia ID")
        db.execute("INSERT INTO hpedia_identities VALUES(?,?)", (body, prefix))
        return import_id
    while True:
        body = uuid.uuid4().hex
        if db.execute("INSERT OR IGNORE INTO hpedia_identities VALUES(?,?)", (body, prefix)).rowcount:
            return f"{prefix}-{body}"
