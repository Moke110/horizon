"""Allocate permanent Hpedia identities in the writing transaction."""
import uuid


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
