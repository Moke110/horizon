"""Librarian review and durable term confirmations."""

import json
import time

from hostrun import frontier_progress, hpedia_identity, hpedia_migration, library_cleanup
from hostrun.agent_service import CommandError
from hostrun.library import Library


class Librarian:
    confirmation_lifetime = 72 * 60 * 60

    def __init__(self, cards, *, token, clock=time.time, frontiers=None):
        self.library = Library(cards, token=token, frontiers=frontiers, clock=clock)
        self.clock = clock
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.library.initialize_questions(db)
            hpedia_migration.migrate(db)
            hpedia_identity.migrate_deleted(db)

    def deleted_entries(self, *, token):
        self.library.authorize(token)
        with self.library.connect() as db:
            return {identity: "条目已删除" for identity, in db.execute("SELECT id FROM hpedia_deleted")}

    def migration_reviews(self, *, token):
        self.library.authorize(token)
        with self.library.connect() as db:
            return hpedia_migration.reviews(db)

    def decide_migration(self, review_id, target, *, token):
        self.library.authorize(token)
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            hpedia_migration.decide(db, review_id, target)

    def list(self, *, token):
        return self.library.list(token=token)

    def query(self, *, token, **filters):
        return self.library.query(token=token, **filters)

    def view(self, term_id, *, token):
        return self.library.view(term_id, token=token)

    def list_frontiers(self, *, token):
        return self.library.list_frontiers(token=token)

    def view_frontier(self, frontier_id, *, token):
        return self.library.view_frontier(frontier_id, token=token)

    def list_progress(self, frontier_id, *, token):
        return self.library.list_progress(frontier_id, token=token)

    @staticmethod
    def read_frontier_in_transaction(db, frontier_id):
        return Library.read_frontier_in_transaction(db, frontier_id)

    @staticmethod
    def query_frontiers_in_transaction(db):
        return Library.query_frontiers_in_transaction(db)

    def apply_approved_frontier(self, db, action, command, *, token):
        """Persist a Frontier change already approved as part of a Keeper group."""
        return self.library.apply_frontier(db, action, command, token=token, now=self.clock(), origin="keeper")

    def notify_frontier(self, frontier):
        self.library.notify_frontier(frontier)

    @staticmethod
    def resolve_frontier_in_transaction(db, frontier_id, *, expected_version=None):
        return Library.resolve_frontier_in_transaction(db, frontier_id, expected_version=expected_version)

    def request_frontier(self, action, command, *, token, origin):
        return self._request_tracking("frontier", action, command, token=token, origin=origin)

    def request_progress(self, action, command, *, token, origin):
        return self._request_tracking("progress", action, command, token=token, origin=origin)

    def _request_tracking(self, entity, action, command, *, token, origin):
        if not isinstance(command, dict):
            raise CommandError("invalid_command", "需要前沿或进展变更内容")
        return self.request_batch(command.get("operation_id"), [(action, command)],
                                  token=token, origin=origin, entity=entity)

    def list_sources(self, *, token):
        self.library.authorize(token)
        return self.library.archive.list()

    def view_source(self, source_id, *, token):
        self.library.authorize(token)
        return self.library.archive.view(source_id)

    def open_source(self, source_id, *, token, task_id=None):
        self.library.authorize(token)
        return self.library.archive.open(source_id, token=token, task_id=task_id)

    def source_original(self, source_id, *, token, task_id=None):
        self.library.authorize(token)
        return self.library.archive.original(source_id, token=token, task_id=task_id)

    def source_pdf(self, source_id, *, token):
        self.library.authorize(token)
        return self.library.archive.pdf(source_id, token=token)

    def cache_source(self, original, media_type, *, token):
        self.library.authorize(token)
        return self.library.archive.documents.cache_original(original, media_type)

    def download_source(self, url, *, token):
        self.library.authorize(token)
        if not isinstance(url, str):
            raise CommandError("invalid_document", "需要公开的源文档链接")
        return self.library.archive.documents.download(url)

    def request_source(self, action, command, *, token, origin):
        if action not in ("create", "register", "import", "restore", "update", "delete") or not isinstance(command, dict):
            raise CommandError("invalid_command", "需要有效的资料库来源变更")
        return self.request_batch(command.get("operation_id"), [(action, command)],
                                  token=token, origin=origin, entity="source")

    def stage_report(self, html, *, token, origin):
        self.library.authorize(token)
        if not isinstance(origin, str) or not origin.startswith("agent:") or not origin[6:].strip():
            raise CommandError("invalid_report", "报道稿件需要当前 Agent 会话")
        return self.library.reports.stage(html, origin)

    def request_report(self, action, command, *, token, origin):
        self.library.authorize(token)
        if action not in ("create", "import", "assess"):
            raise CommandError("immutable_report", "已登记报道不能修改")
        if not isinstance(command, dict):
            raise CommandError("invalid_report", "需要有效的报道登记请求")
        return self.request_batch(command.get("operation_id"), [(action, command)],
                                  token=token, origin=origin, entity="report")

    def list_reports(self, *, token):
        self.library.authorize(token)
        return self.library.reports.list()

    def view_report(self, report_id, *, token):
        self.library.authorize(token)
        return self.library.reports.view(report_id)

    def open_report(self, report_id, *, token):
        self.library.authorize(token)
        return self.library.reports.open(report_id)

    @property
    def HISTORY(self):
        return self.library.history_metadata

    def history_removed(self, db):
        return self.library.history_removed(db)

    def history_project(self, term, removed):
        return self.library.history_project(term, removed)

    def check_write_lock(self, db, term_id):
        self.library.check_write_lock(db, term_id)

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
        # Keeper's reviewed card removal archives the cited record for history and undo.
        return self.library.apply(db, "archive" if action == "delete" else action,
                                  command, token=token, origin=origin)

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

    def reserve_reviewed_terms(self, db, proposal):
        """Enroll an evidence-reviewed Editor proposal in the shared lock ledger."""
        changes = [change for change in proposal["changes"] if change["kind"] in ("card", "frontier")]
        if proposal["provenance"] != "editor" or not changes:
            return
        now = self.clock()
        self._expire(db, now)
        request_id = "keeper:" + proposal["proposal_id"]
        lock_keys = set()
        for change in changes:
            if change["action"] == "create":
                continue
            identity = change["id"]
            if change["kind"] == "frontier":
                self.library.check_frontier_lock(db, identity, now=now)
                lock_keys.add("frontier:" + identity)
            else:
                self.library.check_write_lock(db, identity, now=now)
                lock_keys.add(identity)
        proposal["term_expires_at"] = now + self.confirmation_lifetime
        card = {"operation_id": request_id, "status": "pending"}
        db.execute("INSERT INTO librarian_term_requests VALUES(?,?,?,?,?)",
                   (request_id, "keeper", "[]", proposal["term_expires_at"], json.dumps(card)))
        db.executemany("INSERT INTO librarian_term_locks VALUES(?,?)",
                       [(key, request_id) for key in sorted(lock_keys)])

    def release_reviewed_terms(self, db, proposal):
        request_id = "keeper:" + proposal["proposal_id"]
        db.execute("DELETE FROM librarian_term_locks WHERE request_id=?", (request_id,))
        db.execute("DELETE FROM librarian_term_requests WHERE id=?", (request_id,))

    def reviewed_terms_expired(self, proposal):
        expiry = proposal.get("term_expires_at")
        return proposal["status"] == "pending" and expiry is not None and expiry <= self.clock()

    def confirmations(self, *, token, origin):
        self.library.authorize(token)
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, self.clock())
            rows = db.execute("SELECT result FROM librarian_term_requests WHERE origin=?",
                              (origin,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def bulletin_links(self, *, token):
        return self.library.bulletin_links(token=token)

    def list_questions(self, *, token):
        return self.library.list_questions(token=token)

    def view_question(self, question_id, *, token):
        return self.library.view_question(question_id, token=token)

    def request_question(self, action, command, *, token, origin):
        if not isinstance(command, dict):
            raise CommandError("invalid_command", "需要百科问题变更内容")
        return self.request_batch(command.get("operation_id"), [(action, command)],
                                  token=token, origin=origin, entity="question")

    def request(self, action, command, *, token, origin):
        if not isinstance(command, dict):
            raise CommandError("invalid_command", "需要术语变更内容")
        return self.request_batch(command.get("operation_id"), [(action, command)],
                                  token=token, origin=origin)

    def preview_test_cleanup(self, *, token, selection=None):
        self.library.authorize(token)
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.library.archive.initialize(db)
            frontier_progress.initialize(db)
            return library_cleanup.plan(db, selection)

    def request_test_cleanup(self, request_id, *, token, origin, selection=None):
        self.library.authorize(token)
        if not isinstance(request_id, str) or not request_id.strip() or not isinstance(origin, str) or not origin:
            raise CommandError("invalid_command", "需要有效的清理确认标识")
        encoded = json.dumps({"entity": "test_cleanup", "selection": selection}, sort_keys=True)
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
            self.library.archive.initialize(db)
            frontier_progress.initialize(db)
            plan = library_cleanup.plan(db, selection)
            entries = [item for item in plan["entries"] if not item["blocked"]]
            if not entries:
                raise CommandError("nothing_to_clean", "没有可清理的测试条目")
            keys = library_cleanup.lock_keys(entries)
            for key in keys:
                if db.execute("SELECT 1 FROM librarian_term_locks WHERE term_id=?", (key,)).fetchone():
                    raise CommandError("test_cleanup_locked", "测试条目或相关前沿正在等待确认")
            card = {"operation_id": request_id, "status": "pending", "entity": "test_cleanup",
                    "expires_at": now + self.confirmation_lifetime, "changes": [], "cleanup": plan}
            db.execute("INSERT INTO librarian_term_requests VALUES(?,?,?,?,?)",
                       (request_id, origin, encoded, card["expires_at"], json.dumps(card)))
            db.executemany("INSERT INTO librarian_term_locks VALUES(?,?)",
                           [(key, request_id) for key in sorted(keys)])
        return card

    def _clean_test_records(self, db, card, *, token, now):
        original = card["cleanup"]["entries"]
        current = library_cleanup.plan(db, [[entry["kind"], entry["id"]] for entry in original])["entries"]
        if current != original:
            raise CommandError("test_cleanup_changed", "清理条目或受影响引用已变化，请重新审核")
        results = []
        for i, item in enumerate(entry for entry in current if not entry["blocked"]):
            kind = item["kind"]
            code = f'{card["operation_id"]}:cleanup:{i}'
            command = library_cleanup.removal_command(item, code)
            if kind == "question":
                result = self.library.apply_question(db, "delete", command, now=now)
            elif kind == "progress":
                result = self.library.apply_progress(db, "delete", command, now=now)
            elif kind == "source":
                result = self.library.archive.apply(db, "delete", command)
            elif kind == "frontier":
                result, _ = self.library.apply_frontier(db, "delete", command, token=token, now=now)
            else:
                result, _ = self.library.apply(db, "delete", command, token=token, now=now)
            results.append({"kind": kind, "record": result})
        return results

    def export_entries(self, *, token):
        from hostrun import hpedia_transfer
        self.library.authorize(token)
        with self.library.connect() as db:
            db.execute("BEGIN")
            return hpedia_transfer.export(db, self.library)

    def request_import(self, request_id, package, *, token, origin):
        from hostrun import hpedia_transfer
        self.library.authorize(token)
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return hpedia_transfer.request(db, self, request_id, package, origin, token)

    def pending_imports(self, *, token, origin):
        from hostrun import hpedia_transfer
        self.library.authorize(token)
        with self.library.connect() as db:
            hpedia_transfer.initialize(db)
            return [json.loads(card) for card, in db.execute(
                """SELECT card FROM hpedia_imports WHERE origin=? AND json_extract(card,'$.status')='pending'
                AND json_extract(card,'$.expires_at')>? ORDER BY rowid""", (origin, self.clock()))]

    def decide_import(self, request_id, choices, *, token, origin):
        from hostrun import hpedia_transfer
        self.library.authorize(token)
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return hpedia_transfer.decide(db, self, request_id, choices, origin, token)

    def request_batch(self, request_id, changes, *, token, origin, entity="term"):
        self.library.authorize(token)
        if not isinstance(origin, str) or not origin or not isinstance(request_id, str) \
                or not request_id.strip() or not isinstance(changes, (list, tuple)) \
                or not 1 <= len(changes) <= 100:
            raise CommandError("invalid_command", "确认请求需要来源、标识和 1 至 100 项变更")
        actions = {"term": ("create", "update", "archive", "delete"),
                   "question": ("create", "update", "delete"),
                   "source": ("create", "register", "import", "restore", "update", "delete"),
                   "report": ("create", "import", "assess"),
                   "frontier": ("create", "update", "delete"),
                   "progress": ("create", "update", "delete")}.get(entity, ())
        for change in changes:
            if not isinstance(change, (list, tuple)) or len(change) != 2 \
                    or change[0] not in actions \
                    or not isinstance(change[1], dict):
                raise CommandError("invalid_command", "无效的术语变更")
        for action, command in changes:
            if "test_data" in command and (
                type(command["test_data"]) is not bool or action not in ("create", "register", "import") or not origin.startswith("agent:")
            ):
                raise CommandError("invalid_command", "只有 Agent 新建的测试条目可以标记为测试")
        normalized = [list(change) for change in changes]
        encoded = json.dumps({"entity": entity, "changes": normalized} if entity != "term"
                             else normalized, sort_keys=True)
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
            if entity in ("source", "progress", "report"):
                self.library.archive.initialize(db)
            if entity in ("progress", "report"):
                self.library.reports.initialize(db)
            if entity == "progress":
                frontier_progress.initialize(db)
            term_ids = set()
            summaries = []
            db.execute("SAVEPOINT librarian_review")
            try:
                for action, command in changes:
                    if entity == "question":
                        proposed = self.library.apply_question(db, action, command, now=now)
                        if action != "create":
                            term_ids.add("question:" + proposed["question_id"])
                        if action != "delete":
                            for term_id in proposed["term_ids"]:
                                self.library.check_write_lock(db, term_id, now=now)
                                term_ids.add(term_id)
                    elif entity == "source":
                        proposed = self.library.archive.apply(db, action, command)
                        if action in ("register", "import", "restore"):
                            summaries.append(proposed["title"])
                        if action not in ("register", "create"):
                            term_ids.add("archive:" + proposed["source_id"])
                    elif entity == "report":
                        proposed = self.library.reports.apply(db, action, command, origin=origin, publish=False)
                        summaries.append(proposed["title"])
                        if action == "assess":
                            lock = "archive:" + proposed["report_id"]
                            self.library.check_write_lock(db, lock, now=now)
                            term_ids.add(lock)
                    elif entity == "frontier":
                        proposed, _ = self.library.apply_frontier(db, action, command, token=token, now=now)
                        summaries.append(proposed["question"])
                        if action != "create":
                            term_ids.add("frontier:" + proposed["frontier_id"])
                    elif entity == "progress":
                        proposed = self.library.apply_progress(db, action, command, now=now)
                        summaries.append(proposed["content"])
                        term_ids.add("frontier:" + proposed["frontier_id"])
                        if action != "delete":
                            term_ids.update("archive:" + source_id for source_id in proposed["source_ids"])
                    else:
                        term_id = command.get("card_id")
                        if action != "create" and isinstance(term_id, str):
                            term_ids.add(term_id)
                        self.library.apply(db, action, command, token=token, now=now)
            finally:
                db.execute("ROLLBACK TO librarian_review")
                db.execute("RELEASE librarian_review")
            card = {"operation_id": request_id, "status": "pending",
                    "expires_at": now + self.confirmation_lifetime,
                    "changes": normalized}
            if entity != "term":
                card["entity"] = entity
            if summaries:
                card["summaries"] = summaries
            db.execute("INSERT INTO librarian_term_requests VALUES(?,?,?,?,?)",
                       (request_id, origin, encoded, card["expires_at"], json.dumps(card)))
            db.executemany("INSERT INTO librarian_term_locks VALUES(?,?)",
                           [(term_id, request_id) for term_id in term_ids])
        return card

    def decide(self, request_id, choice, *, token, origin, expected_entity=None):
        self.library.authorize(token)
        if choice not in ("confirm", "reject"):
            raise CommandError("invalid_command", "无效的确认选项")
        notifications = []
        frontier_notifications = []
        with self.library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = self.clock()
            self._expire(db, now)
            row = db.execute("SELECT result FROM librarian_term_requests WHERE id=? AND origin=?",
                             (request_id, origin)).fetchone()
            if not row:
                raise CommandError("not_found", "本会话没有该确认请求")
            card = json.loads(row[0])
            if expected_entity is not None and card.get("entity", "term") != expected_entity:
                raise CommandError("not_found", "本会话没有该类型的确认请求")
            if card["status"] != "pending":
                return card
            db.execute("DELETE FROM librarian_term_locks WHERE request_id=?", (request_id,))
            results = []
            status = "rejected"
            if choice == "confirm":
                db.execute("SAVEPOINT librarian_apply")
                try:
                    if card.get("entity") == "test_cleanup":
                        results = self._clean_test_records(db, card, token=token, now=now)
                        notifications.extend(item["record"] for item in results if item["kind"] == "term")
                        frontier_notifications.extend(item["record"] for item in results if item["kind"] == "frontier")
                    for action, command in card["changes"]:
                        if card.get("entity") == "question":
                            results.append(self.library.apply_question(db, action, command, now=now))
                        elif card.get("entity") == "source":
                            results.append(self.library.archive.apply(db, action, command, publish=True))
                        elif card.get("entity") == "report":
                            results.append(self.library.reports.apply(db, action, command, origin=origin, publish=True))
                        elif card.get("entity") == "frontier":
                            frontier, changed = self.library.apply_frontier(db, action, command, token=token, now=now)
                            results.append(frontier)
                            if changed:
                                frontier_notifications.append(frontier)
                        elif card.get("entity") == "progress":
                            results.append(self.library.apply_progress(db, action, command, now=now))
                        else:
                            term, changed = self.library.apply(db, action, command, token=token, now=now)
                            results.append(term)
                            if changed:
                                notifications.append(term)
                except CommandError as error:
                    db.execute("ROLLBACK TO librarian_apply")
                    results.clear()
                    notifications.clear()
                    frontier_notifications.clear()
                    status = "returned"
                    card["error"] = {"code": error.code, "message": str(error)}
                    if isinstance(getattr(error, "details", None), dict):
                        card["error"]["details"] = error.details
                else:
                    status = "confirmed"
                finally:
                    db.execute("RELEASE librarian_apply")
            card.update(status=status, results=results)
            db.execute("UPDATE librarian_term_requests SET result=? WHERE id=?",
                       (json.dumps(card), request_id))
        for term in notifications:
            self.library.notify_committed(term)
        for frontier in frontier_notifications:
            self.library.notify_frontier(frontier)
        return card
