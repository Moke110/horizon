"""Local Knowledge Cards (知识卡片), independent of Feishu.

A Knowledge Card is the Explorer's manually maintained unit of concept, method
or knowledge point. It lives in the stable data home (the same
``horizon.sqlite3`` as Voyager, Frontier and AgentService). K2 supports manual
creation, editing and archiving with an ``expected_version`` guard.

Known state is not inferred from reading, retrieval or ratings. Only a card
created or updated through this authenticated service is known while active.
Sources pin a saved material version and verified excerpt. Only captured
external materials yield ``sourced`` provenance. A card without one is
stored with ``provenance: user_statement`` and ``source_pending: true``; callers
cannot relabel it as externally supported. ``questionable`` is a separate
explicit flag and never turns an unsourced statement into an external fact.

``query`` is the read-only public seam for Voyager and the Editor: it returns
full committed cards with their versions so a caller can pin what it read.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from hostrun.agent_service import CommandError
from hostrun.knowledge_history import set_path, validate_reason
from hostrun.managed_database import database_connection
from hostrun.voyager_materials import MaterialsService

# Bounded budgets keep a card editable in the desktop form and printable in a
# list without turning the knowledge base into a document store.
_TITLE_BYTES = 500
_CONTENT_BYTES = 20000
_ALIAS_BYTES = 256
_SOURCE_LOCATOR_BYTES = 2000
_SOURCE_EXCERPT_BYTES = 4000
_CARD_ID_BYTES = 128
_MAX_ALIASES = 32
_MAX_SOURCES = 16
_MAX_RELATIONS = 32
_MAX_QUERY_IDS = 100

_SOURCE_FIELDS = ("material_id", "excerpt", "locator")


def _bounded_text(value, *, name, byte_limit, required=True):
    if not isinstance(value, str) or len(value.encode("utf-8")) > byte_limit:
        raise CommandError("invalid_card", f"{name} exceeds its bound")
    if required and not value.strip():
        raise CommandError("invalid_card", f"{name} must not be empty")
    return value


def _bounded_strings(value, *, name, byte_limit, max_items):
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > max_items:
        raise CommandError("invalid_card", f"{name} must be a bounded list")
    cleaned = []
    for item in value:
        if not isinstance(item, str) or not item.strip() \
                or len(item.encode("utf-8")) > byte_limit:
            raise CommandError("invalid_card",
                               f"{name} items must be bounded nonempty strings")
        cleaned.append(item)
    return list(dict.fromkeys(cleaned))


def _validate_questionable(value):
    if not isinstance(value, bool):
        raise CommandError("invalid_card", "questionable must be true or false")
    return value


def _bounded_sources(value):
    if not isinstance(value, list) or len(value) > _MAX_SOURCES:
        raise CommandError("invalid_card", "来源数量超出限制")
    citations = {}
    for source in value:
        if not isinstance(source, dict) or source.keys() != set(_SOURCE_FIELDS):
            raise CommandError("invalid_card", "每项来源需要已保存的资料、摘录和定位")
        citation = {
            "material_id": _bounded_text(source["material_id"], name="资料", byte_limit=_CARD_ID_BYTES),
            "excerpt": _bounded_text(source["excerpt"], name="摘录", byte_limit=_SOURCE_EXCERPT_BYTES),
            "locator": _bounded_text(source["locator"], name="定位", byte_limit=_SOURCE_LOCATOR_BYTES),
        }
        citations[_source_key(citation)] = citation
    return list(citations.values())


def _source_key(source):
    return tuple(source[field] for field in _SOURCE_FIELDS)


def _provenance(sources):
    """Derive provenance from recorded citations, never from caller claims."""
    if any(source["source_kind"] in ("public_web", "academic") for source in sources):
        return "sourced", False
    return "user_statement", True


class KnowledgeCardService:
    """Authenticated public CRUD for local Knowledge Cards."""

    # Metadata the cross-owner history helper reads. Every mutable field is an
    # atomic inverse-patch unit; only an ordinary update is undoable.
    HISTORY = {
        "kind": "card",
        "table": "knowledge_cards",
        "operations": "knowledge_card_operations",
        "events": "knowledge_card_events",
        "id_field": "card_id",
        "event_type": "knowledge_card.undo",
        "editable": (("title",), ("content",), ("aliases",),
                     ("recall_terms",), ("sources",), ("relations",),
                     ("questionable",)),
    }

    def __init__(self, home, *, token, clock=time.time, on_change=None, materials=None):
        if not isinstance(token, str) or not token:
            raise ValueError("A launch token is required")
        self.home = Path(home)
        self._token = token
        self.materials = materials if materials is not None else MaterialsService(home, token=token)
        self.clock = clock
        self.on_change = on_change or (lambda event: None)
        with self._connect() as db:
            self.initialize_database(db)

    @staticmethod
    def initialize_database(db):
        # Keep executescript() out of the transaction path: Frontier/Voyager may
        # resolve knowledge inside their own BEGIN IMMEDIATE.
        db.execute("CREATE TABLE IF NOT EXISTS knowledge_cards(card_id TEXT PRIMARY KEY,result TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS knowledge_card_operations(operation_id TEXT PRIMARY KEY,request TEXT NOT NULL,result TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS knowledge_card_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,operation_id TEXT UNIQUE NOT NULL,event TEXT NOT NULL)")

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
        row = db.execute("SELECT request,result FROM knowledge_card_operations WHERE operation_id=?",
                         (command["operation_id"],)).fetchone()
        if row:
            if row[0] != encoded:
                raise CommandError("operation_conflict", "operation_id conflicts")
            return encoded, json.loads(row[1])
        return encoded, None

    def _notify(self, card):
        try:
            self.on_change({"schema_version": 1, "card_id": card["card_id"],
                            "version": card["version"], "status": card["status"]})
        except Exception:
            pass  # A disconnected viewer cannot undo committed work.

    def history_removed(self, db):
        """Material ids whose pinned snapshots were permanently removed."""
        return self.materials.removed_references(db)

    def history_project(self, card, removed):
        """Project a stored snapshot without reporting removed sources live."""
        return self._project_sources(card, removed)

    @staticmethod
    def history_undoable(record):
        return record.get("status") == "active"

    def apply_history_undo(self, db, record, fields):
        """Apply trusted historical editable fields, reusing pinned citations."""
        updated = json.loads(json.dumps(record))
        for path, value in fields.items():
            set_path(updated, path, value)
        if ("relations",) in fields:
            self._relations(db, record["card_id"], updated["relations"])
        updated["provenance"], updated["source_pending"] = _provenance(
            updated["sources"])
        updated["known"] = True
        return updated

    @staticmethod
    def _fields(command):
        fields = {}
        if "title" in command:
            fields["title"] = _bounded_text(command["title"], name="title",
                                            byte_limit=_TITLE_BYTES)
        if "content" in command:
            fields["content"] = _bounded_text(command["content"], name="content",
                                              byte_limit=_CONTENT_BYTES)
        for name in ("aliases", "recall_terms"):
            if name in command:
                fields[name] = _bounded_strings(command[name], name=name,
                                                byte_limit=_ALIAS_BYTES, max_items=_MAX_ALIASES)
        if "sources" in command:
            fields["sources"] = _bounded_sources(command["sources"])
        if "relations" in command:
            fields["relations"] = _bounded_strings(command["relations"], name="relations",
                                                   byte_limit=_CARD_ID_BYTES,
                                                   max_items=_MAX_RELATIONS)
        if "questionable" in command:
            fields["questionable"] = _validate_questionable(command["questionable"])
        return fields

    def _resolve_sources(self, db, card_id, sources, previous=()):
        existing = {_source_key(source): source for source in previous}
        resolved = []
        for source in sources:
            pinned = existing.get(_source_key(source))
            if pinned is None:
                reference = self.materials.knowledge_reference(
                    db, source["material_id"], source["excerpt"], card_id=card_id, token=self._token)
                pinned = {**source, **reference}
            resolved.append(pinned)
        return resolved

    @staticmethod
    def _project_sources(card, removed_materials):
        removed = any(source["material_id"] in removed_materials for source in card["sources"])
        if not removed:
            return card
        return {**card, "sources": [
            {**source, "status": "removed"} if source["material_id"] in removed_materials else source
            for source in card["sources"]
        ]}

    def _relations(self, db, card_id, relations):
        for relation in relations:
            if relation == card_id:
                raise CommandError("invalid_card", "知识卡片不能关联自身")
            self._read(db, relation)
        return relations

    def create(self, command, *, token):
        self._authorize(token)
        self._validate_command(command)
        validate_reason(command.get("reason"), code="invalid_card")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            encoded, previous = self._replay(db, command, "create")
            if previous is not None:
                return previous
            card_id = str(uuid.uuid4())
            now = int(self.clock())
            fields = self._fields(command)
            if "title" not in fields or "content" not in fields:
                raise CommandError("invalid_card", "title and content are required")
            sources = self._resolve_sources(db, card_id, fields.get("sources", []))
            provenance, source_pending = _provenance(sources)
            card = {
                "schema_version": 1,
                "operation_id": command["operation_id"],
                "card_id": card_id,
                "version": 1,
                "title": fields["title"],
                "content": fields["content"],
                "aliases": fields.get("aliases", []),
                "recall_terms": fields.get("recall_terms", []),
                "sources": sources,
                "relations": self._relations(db, card_id, fields.get("relations", [])),
                "provenance": provenance,
                "source_pending": source_pending,
                "questionable": fields.get("questionable", False),
                "known": True,
                "origin": "manual",
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
            db.execute("INSERT INTO knowledge_cards VALUES(?,?)",
                       (card_id, json.dumps(card)))
            db.execute("INSERT INTO knowledge_card_operations VALUES(?,?,?)",
                       (command["operation_id"], encoded, json.dumps(card)))
            db.execute("INSERT INTO knowledge_card_events(operation_id,event) VALUES(?,?)",
                       (command["operation_id"], json.dumps({
                           "schema_version": 1, "type": "knowledge_card.created",
                           "card_id": card_id, "operation_id": command["operation_id"]})))
        self._notify(card)
        return card

    @classmethod
    def query_all_in_transaction(cls, db):
        """Read every committed card, newest row first, on the caller's transaction."""
        rows = db.execute("SELECT result FROM knowledge_cards ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def list(self, *, token):
        """List all committed cards, including archived ones with their status."""
        self._authorize(token)
        with self._connect() as db:
            db.execute("BEGIN")
            cards = self.query_all_in_transaction(db)
            removed = self.materials.removed_references(db)
        return [self._summary(self._project_sources(card, removed)) for card in cards]

    def query(self, *, token, card_ids=None, include_archived=False):
        """Read committed cards for Voyager and the Editor, carrying versions.

        ``card_ids`` pins an exact set of cards (unknown ids raise ``not_found``);
        omitting it returns every committed card. Archived cards are mixed in
        only when ``include_archived`` is explicitly true.
        """
        self._authorize(token)
        if type(include_archived) is not bool:
            raise CommandError("invalid_query", "include_archived must be true or false")
        if card_ids is None:
            identifiers = None
        else:
            identifiers = _bounded_strings(card_ids, name="card_ids",
                                           byte_limit=_CARD_ID_BYTES,
                                           max_items=_MAX_QUERY_IDS)
            if not identifiers:
                raise CommandError("invalid_query", "card_ids must not be empty")
        with self._connect() as db:
            db.execute("BEGIN")
            if identifiers is None:
                cards = self.query_all_in_transaction(db)
            else:
                cards = [self._read(db, card_id) for card_id in identifiers]
            removed = self.materials.removed_references(db)
        return [self._project_sources(card, removed) for card in cards
                if include_archived or (card["status"] == "active" and card["known"])]

    @staticmethod
    def _summary(card):
        return {
            "card_id": card["card_id"],
            "version": card["version"],
            "title": card["title"],
            "status": card["status"],
            "provenance": card["provenance"],
            "source_pending": card["source_pending"],
            "source_removed": any(source.get("status") == "removed" for source in card["sources"]),
            "questionable": card["questionable"],
            "updated_at": card["updated_at"],
        }

    def view(self, card_id, *, token):
        self._authorize(token)
        with self._connect() as db:
            db.execute("BEGIN")
            return self._project_sources(self._read(db, card_id), self.materials.removed_references(db))

    @staticmethod
    def _read(db, card_id):
        if not isinstance(card_id, str) or not card_id.strip():
            raise CommandError("not_found", "Unknown Knowledge Card")
        row = db.execute("SELECT result FROM knowledge_cards WHERE card_id=?",
                         (card_id,)).fetchone()
        if not row:
            raise CommandError("not_found", "Unknown Knowledge Card")
        return json.loads(row[0])

    @classmethod
    def read_in_transaction(cls, db, card_id):
        """Read owned data on an authenticated caller's open transaction."""
        return cls._read(db, card_id)

    def _conflict(self, command, card, fields):
        submitted = {}
        changed = []
        for name in ("title", "content", "aliases", "recall_terms", "sources",
                     "relations", "questionable"):
            if name not in fields:
                continue
            submitted[name] = fields[name]
            current_value = card[name]
            if name == "sources":
                current_value = [{key: source[key] for key in _SOURCE_FIELDS} for source in current_value]
            if fields[name] != current_value:
                changed.append(name)
        error = CommandError("version_conflict", "知识卡片已被修改，请查看差异后重试")
        error.details = {
            "expected_version": command.get("expected_version"),
            "current_version": card["version"],
            "current": dict(card),
            "submitted": submitted,
            "changed": changed,
        }
        raise error

    def update(self, command, *, token):
        self._authorize(token)
        self._validate_command(command)
        validate_reason(command.get("reason"), code="invalid_card")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            encoded, previous = self._replay(db, command, "update")
            if previous is not None:
                return previous
            card = self._read(db, command.get("card_id"))
            fields = self._fields(command)
            if type(command.get("expected_version")) is not int \
                    or command["expected_version"] != card["version"]:
                self._conflict(command, card, fields)
            if card["status"] == "archived":
                raise CommandError("invalid_transition", "已归档的知识卡片不能编辑")
            if "status" in command:
                raise CommandError("invalid_status", "请用归档操作改变知识卡片状态")
            if "sources" in fields:
                fields["sources"] = self._resolve_sources(db, card["card_id"], fields["sources"], card["sources"])
            if "relations" in fields:
                self._relations(db, card["card_id"], fields["relations"])
            updated = dict(card)
            updated["operation_id"] = command["operation_id"]
            updated.update(fields)
            updated["provenance"], updated["source_pending"] = _provenance(
                updated["sources"])
            updated["known"] = True
            updated["version"] = card["version"] + 1
            updated["updated_at"] = int(self.clock())
            db.execute("UPDATE knowledge_cards SET result=? WHERE card_id=?",
                       (json.dumps(updated), card["card_id"]))
            db.execute("INSERT INTO knowledge_card_operations VALUES(?,?,?)",
                       (command["operation_id"], encoded, json.dumps(updated)))
            db.execute("INSERT INTO knowledge_card_events(operation_id,event) VALUES(?,?)",
                       (command["operation_id"], json.dumps({
                           "schema_version": 1, "type": "knowledge_card.updated",
                           "card_id": card["card_id"],
                           "operation_id": command["operation_id"],
                           "version": updated["version"]})))
        self._notify(updated)
        return updated

    def archive(self, command, *, token):
        """Archive a card explicitly; the row and its history remain readable."""
        self._authorize(token)
        self._validate_command(command)
        validate_reason(command.get("reason"), code="invalid_card")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            encoded, previous = self._replay(db, command, "archive")
            if previous is not None:
                return previous
            card = self._read(db, command.get("card_id"))
            if type(command.get("expected_version")) is not int \
                    or command["expected_version"] != card["version"]:
                self._conflict(command, card, {})
            if card["status"] == "archived":
                raise CommandError("invalid_transition", "该知识卡片已归档")
            updated = dict(card)
            updated["operation_id"] = command["operation_id"]
            updated["status"] = "archived"
            updated["known"] = False
            updated["version"] = card["version"] + 1
            updated["updated_at"] = int(self.clock())
            db.execute("UPDATE knowledge_cards SET result=? WHERE card_id=?",
                       (json.dumps(updated), card["card_id"]))
            db.execute("INSERT INTO knowledge_card_operations VALUES(?,?,?)",
                       (command["operation_id"], encoded, json.dumps(updated)))
            db.execute("INSERT INTO knowledge_card_events(operation_id,event) VALUES(?,?)",
                       (command["operation_id"], json.dumps({
                           "schema_version": 1, "type": "knowledge_card.archived",
                           "card_id": card["card_id"],
                           "operation_id": command["operation_id"],
                           "version": updated["version"]})))
        self._notify(updated)
        return updated
