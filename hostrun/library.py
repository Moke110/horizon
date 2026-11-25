"""Library dictionary storage boundary for the Librarian."""

class Library:
    def __init__(self, cards):
        self._cards = cards

    def authorize(self, token):
        self._cards._authorize(token)

    def connect(self):
        return self._cards._connect()

    def list(self, *, token):
        return self._cards.list(token=token)

    def query(self, *, token, **filters):
        return self._cards.query(token=token, **filters)

    def view(self, term_id, *, token):
        return self._cards.view(term_id, token=token)

    def read_in_transaction(self, db, term_id):
        return self._cards.read_in_transaction(db, term_id)

    def query_all_in_transaction(self, db):
        return self._cards.query_all_in_transaction(db)

    def term_fields(self, command):
        return self._cards._fields(command)

    def term_relations(self, db, term_id, relations):
        return self._cards._relations(db, term_id, relations)

    def apply(self, db, action, command, *, token, now=None, origin="manual"):
        return self._cards.apply_in_transaction(
            db, action, command, token=token, lock_clock=now, origin=origin)

    def notify_committed(self, term):
        self._cards.notify_committed(term)
