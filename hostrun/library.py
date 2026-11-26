"""Library storage boundary for the Librarian."""

from functools import cached_property

from hostrun.archive_sources import ArchiveSources

from hostrun import convergence, frontier_progress, hpedia
from hostrun.frontier import FrontierService
from hostrun.library_tags import visible_tags
from hostrun.agent_service import CommandError


class Library:
    def __init__(self, cards, *, token, frontiers, clock):
        self._cards = cards
        self.authorize(token)
        self._frontiers = frontiers if frontiers is not None else FrontierService(cards.home, token=token, clock=clock)

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

    def validate_import_sources(self, db, content):
        self._cards.validate_import_sources(db, content)

    def term_relations(self, db, term_id, relations):
        return self._cards._relations(db, term_id, relations)

    def apply(self, db, action, command, *, token, now=None, origin="manual", import_id=None):
        return self._cards.apply_in_transaction(
            db, "delete_entry" if action == "delete" else action, command, token=token, lock_clock=now, origin=origin, import_id=import_id)

    def list_frontiers(self, *, token):
        return self._frontiers.list(token=token)

    def view_frontier(self, frontier_id, *, token):
        return self._frontiers.view(frontier_id, token=token)

    @staticmethod
    def read_frontier_in_transaction(db, frontier_id):
        return FrontierService.read_in_transaction(db, frontier_id)

    @staticmethod
    def query_frontiers_in_transaction(db):
        return FrontierService.query_all_in_transaction(db)

    @staticmethod
    def resolve_frontier_in_transaction(db, frontier_id, *, expected_version=None):
        return FrontierService.resolve_in_transaction(db, frontier_id, expected_version=expected_version)

    def list_progress(self, frontier_id, *, token):
        self.authorize(token)
        with self.connect() as db:
            self._frontiers.read_in_transaction(db, frontier_id)
            frontier_progress.initialize(db)
            return frontier_progress.list_for_frontier(db, frontier_id)

    def check_frontier_lock(self, db, frontier_id, *, now=None):
        self._frontiers.check_write_lock(db, frontier_id, now=now)

    def apply_frontier(self, db, action, command, *, token, now, origin="manual", import_id=None):
        if action == "create":
            command = {"schema_version": 1, "hard_constraints": {},
                       "scope": {"text": command.get("question"), "alternatives": [], "sources": ["arxiv"]},
                       **command}
        return self._frontiers.apply_in_transaction(
            db, action, command, token=token, lock_clock=now, origin=origin, import_id=import_id)

    def apply_progress(self, db, action, command, *, now):
        frontier = self._frontiers.read_in_transaction(db, command.get("frontier_id"))
        self._frontiers.check_write_lock(db, frontier["frontier_id"], now=now)
        return frontier_progress.apply(
            db, action, command, frontier=frontier, now=now,
            view_source=lambda source_id: self.archive.read_in_transaction(db, source_id),
            check_lock=lambda source_id: self.archive.check_write_lock(db, source_id),
        )

    def notify_frontier(self, frontier):
        self._frontiers.notify_committed(frontier)

    @staticmethod
    def initialize_questions(db):
        hpedia.initialize_database(db)

    def bulletin_links(self, *, token):
        self.authorize(token)
        with self.connect() as db:
            db.execute("BEGIN")
            terms = self.query_all_in_transaction(db)
            questions = hpedia.list_questions(db)
            frontiers = self.query_frontiers_in_transaction(db)
        return [
            {"kind": kind, "id": row[id_field], "title": row[title_field],
             "convergence": convergence.current(row), "validity": row["validity"], "tracked": True,
             "summary": row["content"] if kind == "term" else row["answer"],
             "term_ids": ([row[id_field], *row.get("relations", [])] if kind == "term"
                          else row.get("term_ids", [])), **visible_tags(row)}
            for kind, records, id_field, title_field in (
                ("term", terms, "card_id", "title"),
                ("question", questions, "question_id", "prompt"))
            for row in records
            if row.get("status", "active") == "active" and row["tracked"]
        ] + [
            {"kind": "frontier", "id": row["frontier_id"], "title": row["question"],
             "summary": row["scope"]["text"], "term_ids": hpedia.referenced_terms(row["question"], row["scope"]["text"]),
             "tracked": True, **visible_tags(row)}
            for row in frontiers if row["status"] != "deleted" and row["tracked"]
        ]

    def list_questions(self, *, token):
        self.authorize(token)
        with self.connect() as db:
            return hpedia.list_questions(db)

    def view_question(self, question_id, *, token):
        self.authorize(token)
        with self.connect() as db:
            return hpedia.read(db, question_id)

    def apply_question(self, db, action, command, *, now, import_id=None):
        def check_question_lock(question_id):
            locked = db.execute("""SELECT 1 FROM librarian_term_locks AS lock
                JOIN librarian_term_requests AS request ON request.id=lock.request_id
                WHERE lock.term_id=? AND request.expires_at>?""",
                                ("question:" + question_id, now)).fetchone()
            if locked:
                raise CommandError("question_locked", "百科问题正在等待确认")

        return hpedia.apply(db, action, command, read_term=lambda term_id: self.read_in_transaction(db, term_id),
                            check_question_lock=check_question_lock, now=now, import_id=import_id)

    def notify_committed(self, term):
        self._cards.notify_committed(term)
