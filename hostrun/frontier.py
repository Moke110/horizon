"""Local Frontier (关注问题/方向) knowledge, independent of Feishu.

A Frontier is the Explorer's focus question and retrieval scope with hard
constraints and a following/paused/resolved/deleted status. It lives in the
stable data home (the same ``horizon.sqlite3`` as Voyager and AgentService), not
in any cloud Base. K1 keeps relations empty; later slices add cards and themes.

``FrontierService`` is the public application seam the desktop GUI drives. Its
scope resolution is the *real knowledge provider* that replaces the temporary
explicit scope for Voyager: a following Frontier resolves to a durable
``frontier:`` scope identity, and pause/resolve/delete stop future retrieval
without destroying the Frontier or any already-produced materials. Manual edits
commit directly through this service; Keeper approval is a later slice.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from hostrun.agent_service import CommandError
from hostrun.managed_database import database_connection

FRONTIER_STATUSES = ("following", "paused", "resolved", "deleted")
ACTIVE_STATUS = "following"

# Byte budgets mirror Voyager's explicit-scope bounds so a Frontier's scope can
# always be handed to Voyager without re-validation drift.
_QUESTION_BYTES = 1000
_TEXT_BYTES = 4096
_ALTERNATIVE_BYTES = 512
_TERM_BYTES = 256
_SOURCE_BYTES = 80
_CONSTRAINT_BYTES = 4096
_MAX_ITEMS = 16
_SOURCE_RE = re.compile(r"[A-Za-z0-9_.-]+")


def _bounded_list(value, *, name, byte_limit, allow_empty):
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > _MAX_ITEMS:
        raise CommandError("invalid_scope", f"{name} must be a bounded list")
    cleaned = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.encode("utf-8")) > byte_limit:
            raise CommandError("invalid_scope", f"{name} items must be bounded nonempty strings")
        cleaned.append(item)
    if not allow_empty and not cleaned:
        raise CommandError("invalid_scope", f"{name} must not be empty")
    return list(dict.fromkeys(cleaned))


def _validate_question(question):
    if not isinstance(question, str) or not question.strip() or len(question.encode("utf-8")) > _QUESTION_BYTES:
        raise CommandError("invalid_scope", "A bounded Frontier question is required")
    return question


def _validate_scope(scope):
    if not isinstance(scope, dict):
        raise CommandError("invalid_scope", "Frontier scope must be an object")
    text = scope.get("text")
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > _TEXT_BYTES:
        raise CommandError("invalid_scope", "A bounded explicit scope is required")
    result = {
        "text": text,
        "alternatives": _bounded_list(scope.get("alternatives"), name="alternatives",
                                       byte_limit=_ALTERNATIVE_BYTES, allow_empty=True),
        "sources": _bounded_list(scope.get("sources"), name="sources",
                                  byte_limit=_SOURCE_BYTES, allow_empty=False),
    }
    for source in result["sources"]:
        if not _SOURCE_RE.fullmatch(source):
            raise CommandError("invalid_scope", "Source identifiers are invalid")
    return result


def _validate_constraints(constraints):
    if not isinstance(constraints, dict):
        raise CommandError("invalid_scope", "Hard constraints must be an object")
    must_include = _bounded_list(constraints.get("must_include"), name="must_include",
                                  byte_limit=_TERM_BYTES, allow_empty=True)
    exclude = _bounded_list(constraints.get("exclude"), name="exclude",
                             byte_limit=_TERM_BYTES, allow_empty=True)
    if sum(len(term.encode("utf-8")) for term in (*must_include, *exclude)) > _CONSTRAINT_BYTES:
        raise CommandError("invalid_scope", "Hard constraints exceed their byte budget")
    return {"must_include": must_include, "exclude": exclude}


def _validate_status(status):
    if status not in FRONTIER_STATUSES[:-1]:
        raise CommandError("invalid_status", "请选择关注中、已暂停或已解决")
    return status


def _validate_relations(relations):
    if relations != []:
        raise CommandError("invalid_scope", "此版本的 Frontier 关联须为空列表")
    return []


def resolve_frontier_scope(frontier, *, expected_version=None):
    """Map a persisted Frontier to Voyager's explicit-scope shape.

    This is the single mapping the "real knowledge provider" and
    ``FrontierService.resolve_scope`` share, so an edited scope changes actual
    queries while a pause/resolve/delete stops them without destroying the
    Frontier or any already-produced materials.
    """
    if frontier.get("status") != ACTIVE_STATUS:
        raise CommandError("frontier_inactive", "Frontier 已暂停/已解决/已删除，不能发起新检索")
    if expected_version is not None:
        if type(expected_version) is not int or expected_version != frontier["version"]:
            raise CommandError("version_conflict", "Frontier 已被修改，请刷新后重试")
    return {
        "kind": "frontier",
        "frontier_id": frontier["frontier_id"],
        "frontier_version": frontier["version"],
        "text": frontier["scope"]["text"],
        "alternatives": list(frontier["scope"]["alternatives"]),
        "must_include": list(frontier["hard_constraints"]["must_include"]),
        "exclude": list(frontier["hard_constraints"]["exclude"]),
        "sources": list(frontier["scope"]["sources"]),
    }


class FrontierService:
    """Authenticated public CRUD for local Frontiers in the stable data home."""

    def __init__(self, home, *, token, clock=time.time, on_change=None):
        if not isinstance(token, str) or not token:
            raise ValueError("A launch token is required")
        self.home = Path(home)
        self._token = token
        self.clock = clock
        self.on_change = on_change or (lambda event: None)
        with self._connect() as db:
            self.initialize_database(db)

    @staticmethod
    def initialize_database(db):
        # Execute statements one at a time: executescript() implicitly commits
        # the caller's transaction, which would break Frontier/Voyager atomicity
        # when resolution runs inside a BEGIN IMMEDIATE.
        db.execute("CREATE TABLE IF NOT EXISTS frontiers(frontier_id TEXT PRIMARY KEY,result TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS frontier_operations(operation_id TEXT PRIMARY KEY,request TEXT NOT NULL,result TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS frontier_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,operation_id TEXT UNIQUE NOT NULL,event TEXT NOT NULL)")

    @contextmanager
    def _connect(self):
        with database_connection(self.home) as db:
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _authorize(self, token):
        import hmac

        if not isinstance(token, str) or not hmac.compare_digest(token, self._token):
            raise PermissionError("Unauthorized desktop command")

    @staticmethod
    def _validate_command(command):
        if not isinstance(command, dict) or type(command.get("schema_version")) is not int \
                or command["schema_version"] != 1 \
                or not isinstance(command.get("operation_id"), str) or not command["operation_id"].strip():
            raise CommandError("invalid_command", "schema_version and operation_id required")

    @staticmethod
    def _replay(db, command, action):
        encoded = json.dumps({"action": action, "command": command}, sort_keys=True)
        row = db.execute("SELECT request,result FROM frontier_operations WHERE operation_id=?",
                         (command["operation_id"],)).fetchone()
        if row:
            if row[0] != encoded:
                raise CommandError("operation_conflict", "operation_id conflicts")
            return encoded, json.loads(row[1])
        return encoded, None

    def _notify(self, frontier):
        try:
            self.on_change({"schema_version": 1, "frontier_id": frontier["frontier_id"],
                            "version": frontier["version"], "status": frontier["status"]})
        except Exception:
            pass  # A disconnected viewer cannot undo committed work.

    def create(self, command, *, token):
        self._authorize(token)
        self._validate_command(command)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            encoded, previous = self._replay(db, command, "create")
            if previous is not None:
                return previous
            frontier_id = str(uuid.uuid4())
            now = int(self.clock())
            frontier = {
                "schema_version": 1,
                "operation_id": command["operation_id"],
                "frontier_id": frontier_id,
                "version": 1,
                "question": _validate_question(command.get("question")),
                "status": _validate_status(command.get("status", ACTIVE_STATUS)),
                "scope": _validate_scope(command.get("scope")),
                "hard_constraints": _validate_constraints(command.get("hard_constraints")),
                "relations": _validate_relations(command.get("relations", [])),
                "created_at": now,
                "updated_at": now,
            }
            db.execute("INSERT INTO frontiers VALUES(?,?)", (frontier_id, json.dumps(frontier)))
            db.execute("INSERT INTO frontier_operations VALUES(?,?,?)",
                       (command["operation_id"], encoded, json.dumps(frontier)))
            db.execute("INSERT INTO frontier_events(operation_id,event) VALUES(?,?)",
                       (command["operation_id"], json.dumps({"schema_version": 1, "type": "frontier.created",
                                                             "frontier_id": frontier_id,
                                                             "operation_id": command["operation_id"]})))
        self._notify(frontier)
        return frontier

    def list(self, *, token):
        self._authorize(token)
        with self._connect() as db:
            rows = db.execute("SELECT result FROM frontiers ORDER BY rowid DESC").fetchall()
        return [self._summary(json.loads(row[0])) for row in rows]

    @staticmethod
    def _summary(frontier):
        return {"frontier_id": frontier["frontier_id"], "version": frontier["version"],
                "question": frontier["question"], "status": frontier["status"],
                "scope_text": frontier["scope"]["text"][:256], "updated_at": frontier["updated_at"]}

    def view(self, frontier_id, *, token):
        self._authorize(token)
        with self._connect() as db:
            return self._read(db, frontier_id)

    @staticmethod
    def _read(db, frontier_id):
        if not isinstance(frontier_id, str) or not frontier_id.strip():
            raise CommandError("not_found", "Unknown Frontier")
        row = db.execute("SELECT result FROM frontiers WHERE frontier_id=?", (frontier_id,)).fetchone()
        if not row:
            raise CommandError("not_found", "Unknown Frontier")
        return json.loads(row[0])

    @classmethod
    def resolve_in_transaction(cls, db, frontier_id, *, expected_version=None):
        """Read owned data on the authenticated caller's open transaction."""
        return resolve_frontier_scope(cls._read(db, frontier_id), expected_version=expected_version)

    def resolve_scope(self, frontier_id, *, token, expected_version=None):
        self._authorize(token)
        with self._connect() as db:
            return self.resolve_in_transaction(db, frontier_id, expected_version=expected_version)

    def update(self, command, *, token):
        self._authorize(token)
        self._validate_command(command)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            encoded, previous = self._replay(db, command, "update")
            if previous is not None:
                return previous
            frontier_id = command.get("frontier_id")
            frontier = self._read(db, frontier_id)
            if type(command.get("expected_version")) is not int or command["expected_version"] != frontier["version"]:
                raise CommandError("version_conflict", "Frontier 已被修改，请刷新后重试")
            if frontier["status"] == "deleted":
                raise CommandError("invalid_transition", "已删除的 Frontier 不能编辑")
            updated = dict(frontier)
            updated["operation_id"] = command["operation_id"]
            if "question" in command:
                updated["question"] = _validate_question(command["question"])
            if "scope" in command:
                updated["scope"] = _validate_scope(command["scope"])
            if "hard_constraints" in command:
                updated["hard_constraints"] = _validate_constraints(command["hard_constraints"])
            if "status" in command:
                updated["status"] = _validate_status(command["status"])
            if "relations" in command:
                updated["relations"] = _validate_relations(command["relations"])
            updated["version"] = frontier["version"] + 1
            updated["updated_at"] = int(self.clock())
            db.execute("UPDATE frontiers SET result=? WHERE frontier_id=?",
                       (json.dumps(updated), frontier_id))
            db.execute("INSERT INTO frontier_operations VALUES(?,?,?)",
                       (command["operation_id"], encoded, json.dumps(updated)))
            db.execute("INSERT INTO frontier_events(operation_id,event) VALUES(?,?)",
                       (command["operation_id"], json.dumps({"schema_version": 1, "type": "frontier.updated",
                                                             "frontier_id": frontier_id,
                                                             "operation_id": command["operation_id"],
                                                             "version": updated["version"]})))
        self._notify(updated)
        return updated

    def delete(self, command, *, token):
        """Delete a Frontier by marking it terminal; history and materials stay."""
        self._authorize(token)
        self._validate_command(command)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            encoded, previous = self._replay(db, command, "delete")
            if previous is not None:
                return previous
            frontier_id = command.get("frontier_id")
            frontier = self._read(db, frontier_id)
            if type(command.get("expected_version")) is not int or command["expected_version"] != frontier["version"]:
                raise CommandError("version_conflict", "Frontier 已被修改，请刷新后重试")
            if frontier["status"] == "deleted":
                raise CommandError("invalid_transition", "该 Frontier 已删除")
            updated = dict(frontier)
            updated["operation_id"] = command["operation_id"]
            updated["status"] = "deleted"
            updated["version"] = frontier["version"] + 1
            updated["updated_at"] = int(self.clock())
            db.execute("UPDATE frontiers SET result=? WHERE frontier_id=?",
                       (json.dumps(updated), frontier_id))
            db.execute("INSERT INTO frontier_operations VALUES(?,?,?)",
                       (command["operation_id"], encoded, json.dumps(updated)))
            db.execute("INSERT INTO frontier_events(operation_id,event) VALUES(?,?)",
                       (command["operation_id"], json.dumps({"schema_version": 1, "type": "frontier.deleted",
                                                             "frontier_id": frontier_id,
                                                             "operation_id": command["operation_id"],
                                                             "version": updated["version"]})))
        self._notify(updated)
        return updated
