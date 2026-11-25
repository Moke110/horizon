"""Library storage boundary for the Librarian."""

from functools import cached_property

from hostrun.archive_sources import ArchiveSources

from hostrun import hpedia
from hostrun.agent_service import CommandError


class Library:
    def __init__(self, cards):
        self._cards = cards

    @cached_property
    def archive(self):
        return ArchiveSources(self.connect, self._cards.materials)

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

    @property
    def history_metadata(self):
        return self._cards.HISTORY

    def history_removed(self, db):
        return self._cards.history_removed(db)

    def history_project(self, term, removed):
        return self._cards.history_project(term, removed)

    def check_write_lock(self, db, term_id, *, now=None):
        self._cards._check_term_lock(db, term_id, now)

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

    @staticmethod
    def initialize_questions(db):
        hpedia.initialize_database(db)

    def list_questions(self, *, token):
        self.authorize(token)
        with self.connect() as db:
            return hpedia.list_questions(db)

    def view_question(self, question_id, *, token):
        self.authorize(token)
        with self.connect() as db:
            return hpedia.read(db, question_id)

    def apply_question(self, db, action, command, *, now):
        def check_question_lock(question_id):
            locked = db.execute("""SELECT 1 FROM librarian_term_locks AS lock
                JOIN librarian_term_requests AS request ON request.id=lock.request_id
                WHERE lock.term_id=? AND request.expires_at>?""",
                                ("question:" + question_id, now)).fetchone()
            if locked:
                raise CommandError("question_locked", "百科问题正在等待确认")

        return hpedia.apply(db, action, command, read_term=lambda term_id: self.read_in_transaction(db, term_id),
                            check_question_lock=check_question_lock, now=now)

    def notify_committed(self, term):
        self._cards.notify_committed(term)
