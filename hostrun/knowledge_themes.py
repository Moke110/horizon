"""Local Themes (命题) with applicability conditions and directional evidence.

A Theme is the Explorer's claim across sources, maintained beside Knowledge
Cards, Voyager, Frontier and AgentService in the stable data home's
``horizon.sqlite3``. It records a judgement, its applicability conditions,
related Knowledge Card versions and directional evidence. A single-sided or
empty evidence set is valid and stays readable; it is derived as
``single_sided`` or ``unsupported`` instead of being rejected, so the Explorer
can keep a disagreement visible while collecting more support or opposition.

Each evidence entry has a stance (``supports`` or ``opposes``), a reason and a
verified citation to a saved material snapshot. The citation pins the material
version, content hash, title and snapshot identity; the Explorer supplies the
locator and excerpt. ``open_evidence`` re-reads that pinned snapshot and
returns the actual page or paragraph so the Editor can open the source. A
``Report`` field is not a citation here.

Related cards are validated and versioned through ``KnowledgeCardService`` on
the same transaction. An invalid or stale relation aborts the whole mutation,
leaving no Theme and no citation binding behind. Adding newer evidence never
rewrites the judgement: only an explicit update with the expected version can.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from hostrun.agent_service import CommandError
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_history import set_path, validate_reason
from hostrun.managed_database import database_connection
from hostrun.voyager_materials import MaterialsService

_JUDGEMENT_BYTES = 4000
_CONDITION_BYTES = 2000
_REASON_BYTES = 4000
_SOURCE_LOCATOR_BYTES = 2000
_SOURCE_EXCERPT_BYTES = 4000
_THEME_ID_BYTES = 128
_MAX_CONDITIONS = 16
_MAX_RELATED_CARDS = 32
_MAX_EVIDENCE = 64
_MAX_QUERY_IDS = 100

_STANCES = ("supports", "opposes")
_INPUT_SOURCE_FIELDS = ("material_id", "excerpt", "locator")
_PINNED_SOURCE_FIELDS = _INPUT_SOURCE_FIELDS + (
    "material_version", "content_hash", "source_kind", "title",
    "snapshot_id", "source_label", "status", "anchor",
)


def _bounded_text(value, *, name, byte_limit, required=True):
    if not isinstance(value, str) or len(value.encode("utf-8")) > byte_limit:
        raise CommandError("invalid_theme", f"{name} exceeds its bound")
    if required and not value.strip():
        raise CommandError("invalid_theme", f"{name} must not be empty")
    return value


def _bounded_strings(value, *, name, byte_limit, max_items):
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > max_items:
        raise CommandError("invalid_theme", f"{name} must be a bounded list")
    cleaned = []
    for item in value:
        if not isinstance(item, str) or not item.strip() \
                or len(item.encode("utf-8")) > byte_limit:
            raise CommandError("invalid_theme",
                               f"{name} items must be bounded nonempty strings")
        cleaned.append(item)
    return list(dict.fromkeys(cleaned))


def _related_cards(value):
    """Validate the relation input shape before any knowledge lookup."""
    if not isinstance(value, list) or len(value) > _MAX_RELATED_CARDS:
        raise CommandError("invalid_theme", "关联卡片数量超出限制")
    cleaned = []
    seen = set()
    for item in value:
        if isinstance(item, str):
            card_id, version = item, None
        elif isinstance(item, dict) and set(item) == {"card_id", "version"}:
            card_id, version = item["card_id"], item["version"]
            if type(version) is not int or version < 1:
                raise CommandError("invalid_theme", "关联卡片版本必须是正整数")
        else:
            raise CommandError("invalid_theme",
                               "每项关联卡片需要卡片 ID 和可选版本")
        _bounded_text(card_id, name="关联卡片", byte_limit=_THEME_ID_BYTES)
        if card_id in seen:
            raise CommandError("invalid_theme", "关联卡片不能重复")
        seen.add(card_id)
        cleaned.append({"card_id": card_id, "version": version})
    return cleaned


def _source(value):
    if not isinstance(value, dict) or not value:
        raise CommandError("invalid_theme", "证据需要已保存资料、摘录和定位")
    if set(value) - set(_PINNED_SOURCE_FIELDS):
        raise CommandError("invalid_theme", "证据来源字段不受支持")
    citation = {}
    for name in _INPUT_SOURCE_FIELDS:
        if name not in value:
            raise CommandError("invalid_theme", "证据需要已保存资料、摘录和定位")
    citation["material_id"] = _bounded_text(
        value["material_id"], name="资料", byte_limit=_THEME_ID_BYTES)
    citation["excerpt"] = _bounded_text(
        value["excerpt"], name="摘录", byte_limit=_SOURCE_EXCERPT_BYTES)
    citation["locator"] = _bounded_text(
        value["locator"], name="定位", byte_limit=_SOURCE_LOCATOR_BYTES)
    if "material_version" in value:
        version = value["material_version"]
        if type(version) is not int or version < 1:
            raise CommandError("invalid_theme", "资料版本必须是正整数")
        citation["material_version"] = version
    return citation


def _evidence(value, *, allow_assigned_id):
    if not isinstance(value, list) or len(value) > _MAX_EVIDENCE:
        raise CommandError("invalid_theme", "证据数量超出限制")
    entries = []
    for entry in value:
        if not isinstance(entry, dict) \
                or set(entry) - {"evidence_id", "stance", "reason", "source"} \
                or not {"stance", "reason", "source"} <= set(entry):
            raise CommandError("invalid_theme",
                               "每项证据需要方向、理由和来源")
        if "evidence_id" in entry and not allow_assigned_id:
            raise CommandError("invalid_theme", "证据 ID 由服务分配")
        evidence_id = entry.get("evidence_id")
        if evidence_id is not None:
            _bounded_text(evidence_id, name="证据", byte_limit=_THEME_ID_BYTES)
        if entry["stance"] not in _STANCES:
            raise CommandError("invalid_theme", "证据方向必须为支持或反对")
        entries.append({
            "evidence_id": evidence_id,
            "stance": entry["stance"],
            "reason": _bounded_text(entry["reason"], name="理由",
                                    byte_limit=_REASON_BYTES),
            "source": _source(entry["source"]),
        })
    if len({entry["evidence_id"] for entry in entries
            if entry["evidence_id"] is not None}) != \
            sum(entry["evidence_id"] is not None for entry in entries):
        raise CommandError("invalid_theme", "证据不能重复")
    return entries


def _evidence_signature(entry):
    source = entry["source"]
    return (entry.get("evidence_id"), entry["stance"], entry["reason"],
            source["material_id"], source["locator"], source["excerpt"])


def _sufficiency(evidence):
    """Derive two-sidedness; never a caller claim and never a hard gate."""
    stances = {entry["stance"] for entry in evidence}
    if not stances:
        return "unsupported"
    return "balanced" if stances == set(_STANCES) else "single_sided"


class KnowledgeThemeService:
    """Authenticated public CRUD and reads for local Themes."""

    # Evidence and relation arrays stay atomic and conservative: a later edit to
    # either whole field is an overlap, not a merge.
    HISTORY = {
        "kind": "theme",
        "table": "knowledge_themes",
        "operations": "knowledge_theme_operations",
        "events": "knowledge_theme_events",
        "id_field": "theme_id",
        "event_type": "knowledge_theme.undo",
        "editable": (("judgement",), ("conditions",), ("related_cards",),
                     ("evidence",)),
    }

    def __init__(self, home, *, token, cards=None, materials=None,
                 clock=time.time, on_change=None):
        if not isinstance(token, str) or not token:
            raise ValueError("A launch token is required")
        self.home = Path(home)
        self._token = token
        self.cards = cards if cards is not None else KnowledgeCardService(
            home, token=token, materials=materials)
        self.materials = materials if materials is not None else self.cards.materials
        self.clock = clock
        self.on_change = on_change or (lambda event: None)
        with self._connect() as db:
            self.initialize_database(db)

    @staticmethod
    def initialize_database(db):
        # Keep executescript() out of the transaction path: Frontier/Voyager may
        # resolve knowledge inside their own BEGIN IMMEDIATE.
        db.execute("CREATE TABLE IF NOT EXISTS knowledge_themes(theme_id TEXT PRIMARY KEY,result TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS knowledge_theme_operations(operation_id TEXT PRIMARY KEY,request TEXT NOT NULL,result TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS knowledge_theme_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,operation_id TEXT UNIQUE NOT NULL,event TEXT NOT NULL)")

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
    def _replay(db, command, action, origin="manual"):
        request = {"action": action, "command": command}
        if origin == "keeper":
            # Trusted attribution is recorded beside, not inside, the command,
            # so a caller-supplied command field can never claim it.
            request["origin"] = "keeper"
        encoded = json.dumps(request, sort_keys=True)
        row = db.execute("SELECT request,result FROM knowledge_theme_operations WHERE operation_id=?",
                         (command["operation_id"],)).fetchone()
        if row:
            if row[0] != encoded:
                raise CommandError("operation_conflict", "operation_id conflicts")
            return encoded, json.loads(row[1])
        return encoded, None

    def _notify(self, theme):
        try:
            self.on_change({"schema_version": 1, "theme_id": theme["theme_id"],
                            "version": theme["version"], "status": theme["status"]})
        except Exception:
            pass  # A disconnected viewer cannot undo committed work.

    def notify_committed(self, theme):
        """Notify after a trusted caller's group transaction has committed."""
        self._notify(theme)

    @staticmethod
    def _event_json(event, origin):
        # Keeper attribution is trusted caller metadata, not command input.
        if origin == "keeper":
            event["origin"] = "keeper"
        return json.dumps(event)

    def history_removed(self, db):
        """Material ids whose pinned snapshots were permanently removed."""
        return self.materials.removed_references(db)

    def history_project(self, theme, removed):
        """Project a stored snapshot without reporting removed sources live."""
        return self._project_sources(theme, removed)

    @staticmethod
    def history_undoable(record):
        return record.get("status") == "active"

    def apply_history_undo(self, db, record, fields):
        """Apply trusted historical editable fields, reusing pinned evidence."""
        updated = json.loads(json.dumps(record))
        for path, value in fields.items():
            set_path(updated, path, value)
        if ("related_cards",) in fields:
            existing = {(entry["card_id"], entry["version"]) for entry in record["related_cards"]}
            introduced = [entry for entry in updated["related_cards"]
                          if (entry["card_id"], entry["version"]) not in existing]
            self._relations(db, introduced)
        updated["evidence_sufficiency"] = _sufficiency(updated["evidence"])
        updated["known"] = True
        return updated

    @staticmethod
    def _fields(command, *, allow_assigned_id):
        fields = {}
        if "judgement" in command:
            fields["judgement"] = _bounded_text(
                command["judgement"], name="judgement", byte_limit=_JUDGEMENT_BYTES)
        if "conditions" in command:
            fields["conditions"] = _bounded_strings(
                command["conditions"], name="conditions",
                byte_limit=_CONDITION_BYTES, max_items=_MAX_CONDITIONS)
        if "related_cards" in command:
            fields["related_cards"] = _related_cards(command["related_cards"])
        if "evidence" in command:
            fields["evidence"] = _evidence(
                command["evidence"], allow_assigned_id=allow_assigned_id)
        return fields

    def _relations(self, db, related):
        """Resolve related cards through the Knowledge Card service."""
        resolved = []
        for relation in related:
            card = self.cards.read_in_transaction(db, relation["card_id"])
            if relation["version"] is not None \
                    and relation["version"] != card["version"]:
                error = CommandError("version_conflict",
                                     "关联知识卡片已更新，请查看后重试")
                error.details = {
                    "kind": "related_card",
                    "card_id": relation["card_id"],
                    "expected_version": relation["version"],
                    "current_version": card["version"],
                    "current": card,
                }
                raise error
            if card["status"] != "active" or not card["known"]:
                raise CommandError("invalid_relation",
                                   "已归档的知识卡片不能关联")
            resolved.append({"card_id": card["card_id"],
                             "version": card["version"]})
        return resolved

    def _resolve_evidence(self, db, theme_id, entries, previous=()):
        existing = {entry["evidence_id"]: entry for entry in previous}
        resolved = []
        for entry in entries:
            evidence_id = entry["evidence_id"] or str(uuid.uuid4())
            pinned = existing.get(evidence_id)
            if entry["evidence_id"] is not None and pinned is None:
                raise CommandError("not_found", "Unknown evidence")
            source = entry["source"]
            unchanged = pinned and all(source[field] == pinned["source"][field]
                                       for field in _INPUT_SOURCE_FIELDS)
            if unchanged and source.get("material_version", pinned["source"]["material_version"]) == pinned["source"]["material_version"]:
                source = pinned["source"]
            else:
                reference = self.materials.theme_reference(
                    db, source, theme_id=theme_id, token=self._token)
                source = {**source, **reference}
            resolved.append({
                "evidence_id": evidence_id,
                "stance": entry["stance"],
                "reason": entry["reason"],
                "source": source,
            })
        return resolved

    @staticmethod
    def _project_sources(theme, removed_materials):
        if not any(entry["source"]["material_id"] in removed_materials
                   for entry in theme["evidence"]):
            return theme
        return {**theme, "evidence": [
            {**entry, "source": {**entry["source"], "status": "removed"}}
            if entry["source"]["material_id"] in removed_materials else entry
            for entry in theme["evidence"]
        ]}

    def create(self, command, *, token):
        self._authorize(token)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            theme, changed = self.apply_in_transaction(
                db, "create", command, token=token)
        if changed:
            self.notify_committed(theme)
        return theme

    def apply_in_transaction(self, db, action, command, *, token, origin="manual"):
        """Apply one Theme write on the caller's open transaction.

        ``delete`` and ``archive`` share the public archive semantics and the
        same operation receipt. The caller owns BEGIN, COMMIT and notification.
        """
        self._authorize(token)
        self._validate_command(command)
        validate_reason(command.get("reason"), code="invalid_theme")
        if origin not in ("manual", "keeper"):
            raise ValueError("Unknown Theme origin")
        if action == "delete":
            action = "archive"
        if action not in ("create", "update", "archive"):
            raise CommandError("invalid_command", "Unknown Theme action")
        encoded, previous = self._replay(db, command, action, origin)
        if previous is not None:
            return previous, False
        if action == "create":
            theme_id = str(uuid.uuid4())
            now = int(self.clock())
            fields = self._fields(command, allow_assigned_id=False)
            if "judgement" not in fields:
                raise CommandError("invalid_theme", "judgement is required")
            related = self._relations(db, fields.get("related_cards", []))
            evidence = self._resolve_evidence(
                db, theme_id, fields.get("evidence", []))
            theme = {
                "schema_version": 1,
                "operation_id": command["operation_id"],
                "theme_id": theme_id,
                "version": 1,
                "judgement": fields["judgement"],
                "conditions": fields.get("conditions", []),
                "related_cards": related,
                "evidence": evidence,
                "evidence_sufficiency": _sufficiency(evidence),
                "known": True,
                "origin": "keeper" if origin == "keeper" else "manual",
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
            db.execute("INSERT INTO knowledge_themes VALUES(?,?)",
                       (theme_id, json.dumps(theme)))
            db.execute("INSERT INTO knowledge_theme_operations VALUES(?,?,?)",
                       (command["operation_id"], encoded, json.dumps(theme)))
            db.execute("INSERT INTO knowledge_theme_events(operation_id,event) VALUES(?,?)",
                       (command["operation_id"], self._event_json({
                           "schema_version": 1, "type": "knowledge_theme.created",
                           "theme_id": theme_id,
                           "operation_id": command["operation_id"]}, origin)))
            return theme, True
        theme = self._read(db, command.get("theme_id"))
        if action == "update":
            fields = self._fields(command, allow_assigned_id=True)
            if type(command.get("expected_version")) is not int \
                    or command["expected_version"] != theme["version"]:
                self._conflict(command, theme, fields)
            if theme["status"] == "archived":
                raise CommandError("invalid_transition", "已归档的命题不能编辑")
            if "status" in command:
                raise CommandError("invalid_status", "请用归档操作改变命题状态")
            if "related_cards" in fields:
                fields["related_cards"] = self._relations(db, fields["related_cards"])
            if "evidence" in fields:
                fields["evidence"] = self._resolve_evidence(
                    db, theme["theme_id"], fields["evidence"], theme["evidence"])
            updated = dict(theme)
            updated["operation_id"] = command["operation_id"]
            updated.update(fields)
            updated["evidence_sufficiency"] = _sufficiency(updated["evidence"])
            updated["known"] = True
            event_type = "knowledge_theme.updated"
        else:
            if type(command.get("expected_version")) is not int \
                    or command["expected_version"] != theme["version"]:
                self._conflict(command, theme, {})
            if theme["status"] == "archived":
                raise CommandError("invalid_transition", "该命题已归档")
            updated = dict(theme)
            updated["operation_id"] = command["operation_id"]
            updated["status"] = "archived"
            updated["known"] = False
            event_type = "knowledge_theme.archived"
        updated["version"] = theme["version"] + 1
        updated["updated_at"] = int(self.clock())
        db.execute("UPDATE knowledge_themes SET result=? WHERE theme_id=?",
                   (json.dumps(updated), theme["theme_id"]))
        db.execute("INSERT INTO knowledge_theme_operations VALUES(?,?,?)",
                   (command["operation_id"], encoded, json.dumps(updated)))
        db.execute("INSERT INTO knowledge_theme_events(operation_id,event) VALUES(?,?)",
                   (command["operation_id"], self._event_json({
                       "schema_version": 1, "type": event_type,
                       "theme_id": theme["theme_id"],
                       "operation_id": command["operation_id"],
                       "version": updated["version"]}, origin)))
        return updated, True

    @classmethod
    def query_all_in_transaction(cls, db):
        """Read every committed Theme, newest row first, on the caller's transaction."""
        rows = db.execute("SELECT result FROM knowledge_themes ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def list(self, *, token):
        """List all committed Themes, including archived ones with status."""
        self._authorize(token)
        with self._connect() as db:
            db.execute("BEGIN")
            themes = self.query_all_in_transaction(db)
            removed = self.materials.removed_references(db)
        return [self._summary(self._project_sources(theme, removed))
                for theme in themes]

    def query(self, *, token, theme_ids=None, include_archived=False):
        """Read committed Themes for search and the Editor, carrying versions."""
        self._authorize(token)
        if type(include_archived) is not bool:
            raise CommandError("invalid_query", "include_archived must be true or false")
        if theme_ids is None:
            identifiers = None
        else:
            identifiers = _bounded_strings(theme_ids, name="theme_ids",
                                           byte_limit=_THEME_ID_BYTES,
                                           max_items=_MAX_QUERY_IDS)
            if not identifiers:
                raise CommandError("invalid_query", "theme_ids must not be empty")
        with self._connect() as db:
            db.execute("BEGIN")
            if identifiers is None:
                themes = self.query_all_in_transaction(db)
            else:
                themes = [self._read(db, theme_id) for theme_id in identifiers]
            removed = self.materials.removed_references(db)
        return [self._project_sources(theme, removed) for theme in themes
                if include_archived or (theme["status"] == "active" and theme["known"])]

    @staticmethod
    def _summary(theme):
        return {
            "theme_id": theme["theme_id"],
            "version": theme["version"],
            "judgement": theme["judgement"],
            "status": theme["status"],
            "evidence_sufficiency": theme["evidence_sufficiency"],
            "evidence_count": len(theme["evidence"]),
            "related_card_count": len(theme["related_cards"]),
            "updated_at": theme["updated_at"],
        }

    def view(self, theme_id, *, token):
        self._authorize(token)
        with self._connect() as db:
            db.execute("BEGIN")
            return self._project_sources(
                self._read(db, theme_id), self.materials.removed_references(db))

    @staticmethod
    def _read(db, theme_id):
        if not isinstance(theme_id, str) or not theme_id.strip():
            raise CommandError("not_found", "Unknown Theme")
        row = db.execute("SELECT result FROM knowledge_themes WHERE theme_id=?",
                         (theme_id,)).fetchone()
        if not row:
            raise CommandError("not_found", "Unknown Theme")
        return json.loads(row[0])

    @classmethod
    def read_in_transaction(cls, db, theme_id):
        """Read owned data on an authenticated caller's open transaction."""
        return cls._read(db, theme_id)

    def open_evidence(self, theme_id, evidence_id, *, token, expected_version=None):
        """Open the pinned snapshot page/paragraph behind one evidence entry."""
        self._authorize(token)
        with self._connect() as db:
            db.execute("BEGIN")
            theme = self._read(db, theme_id)
            if expected_version is not None and (type(expected_version) is not int or expected_version != theme["version"]):
                self._conflict({"expected_version": expected_version}, theme, {})
        evidence = next((entry for entry in theme["evidence"]
                         if entry["evidence_id"] == evidence_id), None)
        if evidence is None:
            raise CommandError("not_found", "Unknown evidence")
        source = evidence["source"]
        return self.materials.citation_view(
            source,
            f"theme:{theme_id}:{source['content_hash']}",
            token=self._token,
        )

    def _conflict(self, command, theme, fields):
        submitted = {}
        changed = []
        for name in ("judgement", "conditions", "related_cards", "evidence"):
            if name not in fields:
                continue
            submitted[name] = fields[name]
            current_value = theme[name]
            if name == "evidence":
                expected = [_evidence_signature(entry) for entry in current_value]
                actual = [_evidence_signature(entry) for entry in fields[name]]
                if expected != actual:
                    changed.append(name)
            elif fields[name] != current_value:
                changed.append(name)
        error = CommandError("version_conflict", "命题已被修改，请查看差异后重试")
        error.details = {
            "kind": "theme",
            "expected_version": command.get("expected_version"),
            "current_version": theme["version"],
            "current": dict(theme),
            "submitted": submitted,
            "changed": changed,
        }
        raise error

    def update(self, command, *, token):
        self._authorize(token)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            theme, changed = self.apply_in_transaction(
                db, "update", command, token=token)
        if changed:
            self.notify_committed(theme)
        return theme

    def archive(self, command, *, token):
        """Archive a Theme explicitly; the row and its history remain readable."""
        self._authorize(token)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            theme, changed = self.apply_in_transaction(
                db, "archive", command, token=token)
        if changed:
            self.notify_committed(theme)
        return theme
