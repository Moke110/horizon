"""Librarian review and durable term confirmations."""

import json
import time

from hostrun.agent_service import CommandError
from hostrun.library import Library


class Librarian:
    confirmation_lifetime = 72 * 60 * 60

    def __init__(self, cards, *, token, clock=time.time):
        self.library = Library(cards)
        self.clock = clock
        self.library.authorize(token)

    def list(self, *, token):
        return self.library.list(token=token)

    def query(self, *, token, **filters):
        return self.library.query(token=token, **filters)

    def view(self, term_id, *, token):
        return self.library.view(term_id, token=token)

    def read_in_transaction(self, db, term_id):
        return self.library.read_in_transaction(db, term_id)

    def query_all_in_transaction(self, db):
        return self.library.query_all_in_transaction(db)

    def term_fields(self, command):
        return self.library.term_fields(command)

    def term_relations(self, db, term_id, relations):
        return self.library.term_relations(db, term_id, relations)

    def apply_in_transaction(self, db, action, command, *, token, origin):
        """Apply a term change already approved through the Keeper workflow."""
        if origin != "keeper":
            raise ValueError("A reviewed Keeper proposal is required")
        return self.library.apply(db, action, command, token=token, origin=origin)

    def notify_committed(self, term):
        self.library.notify_committed(term)

    @staticmethod
    def _expire(db, now):
        db.execute("""UPDATE librarian_term_requests
            SET result=json_set(result,'$.status','expired')
            WHERE expires_at<=? AND json_extract(result,'$.status')='pending'""", (now,))
        db.execute("""DELETE FROM librarian_term_locks WHERE request_id IN (
            SELECT id FROM librarian_term_requests
            WHERE json_extract(result,'$.status')='expired')""")

    def confirmations(self, *, token, origin):
        self.library.authorize(token)
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, self.clock())
            rows = db.execute("SELECT result FROM librarian_term_requests WHERE origin=?",
                              (origin,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def request(self, action, command, *, token, origin):
        if not isinstance(command, dict):
            raise CommandError("invalid_command", "需要术语变更内容")
        return self.request_batch(command.get("operation_id"), [(action, command)],
                                  token=token, origin=origin)

    def request_batch(self, request_id, changes, *, token, origin):
        self.library.authorize(token)
        if not isinstance(origin, str) or not origin or not isinstance(request_id, str) \
                or not request_id.strip() or not isinstance(changes, (list, tuple)) \
                or not 1 <= len(changes) <= 100:
            raise CommandError("invalid_command", "确认请求需要来源、标识和 1 至 100 项变更")
        for change in changes:
            if not isinstance(change, (list, tuple)) or len(change) != 2 \
                    or change[0] not in ("create", "update", "archive", "delete") \
                    or not isinstance(change[1], dict):
                raise CommandError("invalid_command", "无效的术语变更")
        encoded = json.dumps(changes, sort_keys=True)
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = self.clock()
            self._expire(db, now)
            row = db.execute("SELECT origin,request,result FROM librarian_term_requests WHERE id=?",
                             (request_id,)).fetchone()
            if row:
                if row[:2] != (origin, encoded):
                    raise CommandError("operation_conflict", "确认请求标识已使用")
                return json.loads(row[2])
            term_ids = set()
            db.execute("SAVEPOINT librarian_review")
            try:
                for action, command in changes:
                    term_id = command.get("card_id")
                    if action != "create" and isinstance(term_id, str):
                        term_ids.add(term_id)
                    self.library.apply(db, action, command, token=token, now=now)
            finally:
                db.execute("ROLLBACK TO librarian_review")
                db.execute("RELEASE librarian_review")
            card = {"operation_id": request_id, "status": "pending",
                    "expires_at": now + self.confirmation_lifetime,
                    "changes": json.loads(encoded)}
            db.execute("INSERT INTO librarian_term_requests VALUES(?,?,?,?,?)",
                       (request_id, origin, encoded, card["expires_at"], json.dumps(card)))
            db.executemany("INSERT INTO librarian_term_locks VALUES(?,?)",
                           [(term_id, request_id) for term_id in term_ids])
        return card

    def decide(self, request_id, choice, *, token, origin):
        self.library.authorize(token)
        if choice not in ("confirm", "reject"):
            raise CommandError("invalid_command", "无效的确认选项")
        notifications = []
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = self.clock()
            self._expire(db, now)
            row = db.execute("SELECT result FROM librarian_term_requests WHERE id=? AND origin=?",
                             (request_id, origin)).fetchone()
            if not row:
                raise CommandError("not_found", "本会话没有该确认请求")
            card = json.loads(row[0])
            if card["status"] != "pending":
                return card
            db.execute("DELETE FROM librarian_term_locks WHERE request_id=?", (request_id,))
            results = []
            if choice == "confirm":
                for action, command in card["changes"]:
                    term, changed = self.library.apply(db, action, command, token=token, now=now)
                    results.append(term)
                    if changed:
                        notifications.append(term)
            card.update(status="confirmed" if choice == "confirm" else "rejected", results=results)
            db.execute("UPDATE librarian_term_requests SET result=? WHERE id=?",
                       (json.dumps(card), request_id))
        for term in notifications:
            self.library.notify_committed(term)
        return card
