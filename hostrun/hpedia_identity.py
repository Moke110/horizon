"""Allocate permanent Hpedia identities in the writing transaction."""
import uuid


def allocate(db, prefix):
    db.execute("CREATE TABLE IF NOT EXISTS hpedia_identities(body TEXT PRIMARY KEY, prefix TEXT NOT NULL)")
    while True:
        body = uuid.uuid4().hex
        if db.execute("INSERT OR IGNORE INTO hpedia_identities VALUES(?,?)", (body, prefix)).rowcount:
            return f"{prefix}-{body}"
