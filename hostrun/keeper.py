"""Persistent Keeper requests and read-only, reviewable knowledge proposals.

The Keeper turns an explicit test report owned by a completed Agent turn into
bounded proposals. It never writes Knowledge Cards, Themes or Frontiers: a
proposal previews the candidate before/after values using the owning service's
normal validators, and every source is verified read-only against the saved
material snapshot. Pending proposals therefore leave known and search state
unchanged until the later approval slice.

Only a trusted server-side principal may submit. The command carries no role;
the principal is supplied beside it and a Voyager or untrusted role is refused.
The Agent report is a committed completed turn: its ``turn_id`` and ``version``
are pinned with the task and its current version. The report body and task
messages stay private; callers see only bounded pins and proposal projections.

Review is a bounded call out to an injected dispatcher, then one final atomic
recheck of every pin before pending proposals and the terminal outcome commit.
The dispatcher returns only a status, an optional reason and an optional child
identifier; it cannot invent edits.
"""

from __future__ import annotations

import copy
import hmac
import json
import re
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from hostrun.agent_service import AgentService, CommandError
from hostrun.frontier import (
    FrontierService, _validate_constraints, _validate_question,
    _validate_relations, _validate_scope, _validate_status,
)
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_cards import _provenance as _card_provenance
from hostrun.knowledge_themes import KnowledgeThemeService, _sufficiency
from hostrun.managed_database import database_connection

_SCHEMA_VERSION = 1
_MAX_COMMAND_BYTES = 128 * 1024
_MAX_GROUPS = 8
_MAX_CHANGES_PER_GROUP = 8
_MAX_TOTAL_CHANGES = 16
_MAX_SOURCES = 64
_MAX_ID_BYTES = 128
_MAX_REASON_BYTES = 500
_MAX_LOCATOR_BYTES = 2000
_MAX_EXCERPT_BYTES = 4000
_MAX_DISPATCH_REASON_BYTES = 1000
_MAX_LABEL_BYTES = 200

_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]+")
_KINDS = ("card", "theme", "frontier")
_ACTIONS = ("create", "update", "delete")
_CARD_FIELDS = {"title", "content", "aliases", "recall_terms", "relations",
                "questionable"}
_THEME_FIELDS = {"judgement", "conditions", "related_cards", "evidence"}
_FRONTIER_FIELDS = {"question", "scope", "hard_constraints", "relations",
                    "status"}
_TOP_LEVEL_FIELDS = {"schema_version", "operation_id", "task_id",
                     "task_version", "report_id", "report_version", "groups"}
_SOURCE_FIELDS = {"material_id", "material_version", "locator", "excerpt"}
_EVIDENCE_ERRORS = {"not_found", "version_conflict", "invalid_source",
                    "ambiguous_source", "invalid_material", "snapshot_corrupt",
                    "snapshot_changed"}


@dataclass(frozen=True)
class KeeperPrincipal:
    """Trusted server-side provenance supplied beside, never inside, a command."""

    role: str
    explicit_test: bool


class _Returned(Exception):
    """A fair, persistent sent-back outcome instead of a hard refusal."""

    def __init__(self, reason, evidence=None):
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence or []


def _bounded_text(value, *, name, byte_limit, required=True):
    if not isinstance(value, str) or len(value.encode("utf-8")) > byte_limit:
        raise CommandError("invalid_command", f"{name} exceeds its bound")
    if required and not value.strip():
        raise CommandError("invalid_command", f"{name} must not be empty")
    return value


def _bounded_identifier(value, *, name):
    value = _bounded_text(value, name=name, byte_limit=_MAX_ID_BYTES)
    if not _ID_PATTERN.fullmatch(value):
        raise CommandError("invalid_command", f"{name} is invalid")
    return value


def _bounded_list(value, *, name, maximum, minimum=0):
    if not isinstance(value, list) or len(value) < minimum or len(value) > maximum:
        raise CommandError("invalid_command", f"{name} must be a bounded list")
    return value


def _normalized_label(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _validate_source(value, *, index):
    if not isinstance(value, dict) or set(value) != _SOURCE_FIELDS:
        raise CommandError("invalid_command", f"sources[{index}] has invalid fields")
    material_version = value["material_version"]
    if type(material_version) is not int or material_version < 1:
        raise CommandError("invalid_command", "material_version must be a positive integer")
    return {
        "material_id": _bounded_identifier(value["material_id"], name="material_id"),
        "material_version": material_version,
        "locator": _bounded_text(value["locator"], name="locator",
                                 byte_limit=_MAX_LOCATOR_BYTES),
        "excerpt": _bounded_text(value["excerpt"], name="excerpt",
                                 byte_limit=_MAX_EXCERPT_BYTES),
    }


def _validate_change(value, *, index):
    if not isinstance(value, dict):
        raise CommandError("invalid_command", f"changes[{index}] must be an object")
    kind = value.get("kind")
    action = value.get("action")
    if kind not in _KINDS or action not in _ACTIONS:
        raise CommandError("invalid_command", "Unknown change kind or action")
    allowed = {"kind", "action", "fields", "reason", "sources"}
    if action != "create":
        allowed |= {"id", "expected_version"}
    if set(value) - allowed:
        raise CommandError("invalid_command", "Change contains unsupported fields")
    if "reason" not in value or "fields" not in value or "sources" not in value:
        raise CommandError("invalid_command", "Change requires fields, reason and sources")
    fields = value["fields"]
    if not isinstance(fields, dict):
        raise CommandError("invalid_command", "fields must be an object")
    reason = _bounded_text(value["reason"], name="reason",
                           byte_limit=_MAX_REASON_BYTES)
    source_values = _bounded_list(value["sources"], name="sources",
                                  maximum=_MAX_SOURCES)
    sources = [_validate_source(source, index=position)
               for position, source in enumerate(source_values)]
    change = {"kind": kind, "action": action, "fields": dict(fields),
              "reason": reason, "sources": sources}
    if action == "create":
        if "id" in value or "expected_version" in value:
            raise CommandError("invalid_command", "Creation has no existing version")
    else:
        change["id"] = _bounded_identifier(value.get("id"), name="id")
        expected = value.get("expected_version")
        if type(expected) is not int or expected < 1:
            raise CommandError("invalid_command", "expected_version must be a positive integer")
        change["expected_version"] = expected
        if action == "delete" and fields:
            raise CommandError("invalid_command", "Delete previews a terminal action only")
    return change


def _validate_command(command):
    if not isinstance(command, dict):
        raise CommandError("invalid_command", "Command must be an object")
    if type(command.get("schema_version")) is not int \
            or command["schema_version"] != _SCHEMA_VERSION:
        raise CommandError("unsupported_schema", "Unsupported schema_version")
    if set(command) != _TOP_LEVEL_FIELDS:
        raise CommandError("invalid_command", "Command contains unsupported fields")
    try:
        encoded = json.dumps(command, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise CommandError("invalid_command", "Command is not serializable") from error
    if len(encoded.encode("utf-8")) > _MAX_COMMAND_BYTES:
        raise CommandError("invalid_command", "Command exceeds its byte budget")
    operation = _bounded_text(command.get("operation_id"), name="operation_id",
                              byte_limit=_MAX_ID_BYTES)
    task_id = _bounded_text(command.get("task_id"), name="task_id",
                            byte_limit=_MAX_ID_BYTES)
    report_id = _bounded_text(command.get("report_id"), name="report_id",
                              byte_limit=_MAX_ID_BYTES)
    for name in ("task_version", "report_version"):
        if type(command.get(name)) is not int or command[name] < 1:
            raise CommandError("invalid_command", f"{name} must be a positive integer")
    groups = _bounded_list(command.get("groups"), name="groups",
                           maximum=_MAX_GROUPS, minimum=1)
    normalized_groups = []
    targets = set()
    total = 0
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict) or set(group) != {"changes"}:
            raise CommandError("invalid_command", "Each group requires changes only")
        changes = _bounded_list(group["changes"], name="changes",
                                maximum=_MAX_CHANGES_PER_GROUP, minimum=1)
        total += len(changes)
        if total > _MAX_TOTAL_CHANGES:
            raise CommandError("invalid_command", "Too many changes in one request")
        normalized = [_validate_change(change, index=group_index)
                      for change in changes]
        for change in normalized:
            if change["action"] == "create":
                continue
            target = (change["kind"], change["id"])
            if target in targets:
                raise CommandError("duplicate_target",
                                   "A target appears more than once in the request")
            targets.add(target)
        normalized_groups.append({"changes": normalized})
    return {
        "schema_version": _SCHEMA_VERSION,
        "operation_id": operation,
        "task_id": task_id,
        "task_version": command["task_version"],
        "report_id": report_id,
        "report_version": command["report_version"],
        "groups": normalized_groups,
    }


def _receipt_result(receipt):
    result = {"request_id": receipt["request_id"], "status": receipt["status"],
              "proposal_ids": list(receipt.get("proposal_ids", []))}
    for field in ("reason", "child_id"):
        if receipt.get(field) is not None:
            result[field] = receipt[field]
    return result


class KeeperService:
    """Persistent, authenticated Keeper requests, review and proposal reads."""

    def __init__(self, agent, materials, cards, themes, frontiers, *, token):
        if not isinstance(token, str) or not token:
            raise ValueError("A launch token is required")
        self.agent = agent
        self.materials = materials
        self.cards = cards
        self.themes = themes
        self.frontiers = frontiers
        self.home = Path(agent.home)
        self._token = token
        with self._connect() as db:
            self.initialize_database(db)

    @staticmethod
    def initialize_database(db):
        db.execute("CREATE TABLE IF NOT EXISTS keeper_requests("
                   "request_id TEXT PRIMARY KEY, request TEXT NOT NULL, result TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS keeper_operations("
                   "operation_id TEXT PRIMARY KEY, request TEXT NOT NULL, result TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS keeper_proposals("
                   "proposal_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, result TEXT NOT NULL)")

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
        if not isinstance(token, str) or not hmac.compare_digest(token, self._token):
            raise PermissionError("Unauthorized Keeper command")

    @staticmethod
    def _check_principal(principal):
        if not isinstance(principal, KeeperPrincipal):
            raise PermissionError("A trusted Keeper principal is required")
        if principal.role != "editor" or principal.explicit_test is not True:
            raise PermissionError("This principal cannot submit Keeper requests")

    @staticmethod
    def _check_pins(db, command):
        task = AgentService.read_task_in_transaction(db, command["task_id"])
        if task["version"] != command["task_version"]:
            raise CommandError("version_conflict", "Task version changed")
        report = AgentService.read_turn_in_transaction(db, command["report_id"])
        if report.get("task_id") != command["task_id"]:
            raise CommandError("invalid_report", "Report belongs to another task")
        if report.get("status") != "completed":
            raise CommandError("invalid_report", "Report is not a completed turn")
        if not isinstance(report.get("result"), str) or not report["result"].strip():
            raise CommandError("invalid_report", "Report is empty")
        if report["version"] != command["report_version"]:
            raise CommandError("version_conflict", "Report version changed")

    def _new_receipt(self, command, *, status, reason=None, evidence=None):
        receipt = {
            "schema_version": _SCHEMA_VERSION,
            "request_id": str(uuid.uuid4()),
            "operation_id": command["operation_id"],
            "status": status,
            "task_id": command["task_id"],
            "task_version": command["task_version"],
            "report_id": command["report_id"],
            "report_version": command["report_version"],
            "proposal_ids": [],
        }
        if reason is not None:
            receipt["reason"] = reason
        if evidence:
            receipt["evidence"] = evidence[:_MAX_TOTAL_CHANGES * _MAX_SOURCES]
        return receipt

    def request(self, command, *, token, principal):
        """Persist one queued request or a fair, persistent returned outcome."""
        self._authorize(token)
        self._check_principal(principal)
        command = _validate_command(command)
        encoded = json.dumps(command, sort_keys=True, ensure_ascii=False)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT request,result FROM keeper_operations WHERE operation_id=?",
                (command["operation_id"],)).fetchone()
            if previous is not None:
                if previous[0] != encoded:
                    raise CommandError("operation_conflict",
                                       "operation_id conflicts with a previous request")
                return json.loads(previous[1])
            try:
                self._check_pins(db, command)
                self._plan(db, command)
            except _Returned as returned:
                if returned.reason == "missing_evidence":
                    receipt = self._new_receipt(command, status="queued")
                else:
                    receipt = self._new_receipt(command, status="returned",
                                                reason=returned.reason,
                                                evidence=returned.evidence)
            else:
                receipt = self._new_receipt(command, status="queued")
            self._persist_request(db, receipt, command)
            db.execute("INSERT INTO keeper_operations VALUES(?,?,?)",
                       (command["operation_id"], encoded, json.dumps(receipt)))
        return receipt

    def _persist_request(self, db, receipt, command):
        db.execute("INSERT INTO keeper_requests VALUES(?,?,?) ON CONFLICT(request_id) "
                   "DO UPDATE SET result=excluded.result",
                   (receipt["request_id"],
                    json.dumps(command, sort_keys=True, ensure_ascii=False),
                    json.dumps(receipt)))

    def review(self, request_id, *, token, dispatch):
        """Review one queued request through an injected bounded dispatcher."""
        self._authorize(token)
        if not isinstance(request_id, str) or not request_id:
            raise CommandError("not_found", "Unknown Keeper request")
        if not callable(dispatch):
            raise CommandError("invalid_dispatch", "A trusted dispatcher is required")
        with self._connect() as db:
            db.execute("BEGIN")
            command, receipt = self._load_request(db, request_id)
            if receipt["status"] != "queued":
                return _receipt_result(receipt)
            try:
                self._check_pins(db, command)
                context = self._context(db, receipt, command)
            except (CommandError, _Returned) as error:
                context = None
                outcome = self._returned_outcome(error)
        if context is not None:
            try:
                outcome = self._validate_dispatch(dispatch(context))
            except Exception as error:
                outcome = {"status": "failed", "child_id": getattr(error, "child_id", None),
                           "reason": "invalid_dispatch" if isinstance(error, CommandError)
                           else "keeper_execution_failed"}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            command, receipt = self._load_request(db, request_id)
            if receipt["status"] != "queued":
                return _receipt_result(receipt)
            if outcome.get("child_id") is not None:
                receipt["child_id"] = outcome["child_id"]
            if outcome["status"] == "reviewed":
                try:
                    self._check_pins(db, command)
                    plan = self._plan(db, command)
                except (CommandError, _Returned) as error:
                    outcome = self._returned_outcome(error)
            if outcome["status"] == "reviewed":
                proposal_ids = []
                for group in plan:
                    proposal = self._proposal(receipt, command, group)
                    proposal_ids.append(proposal["proposal_id"])
                    db.execute("INSERT INTO keeper_proposals VALUES(?,?,?)",
                               (proposal["proposal_id"], request_id, json.dumps(proposal)))
                receipt.update(status="pending", proposal_ids=proposal_ids)
            else:
                receipt.update(status=outcome["status"], reason=outcome["reason"],
                               evidence=outcome.get("evidence", []))
            self._persist_request(db, receipt, command)
        return _receipt_result(receipt)

    @staticmethod
    def _load_request(db, request_id):
        row = db.execute("SELECT request,result FROM keeper_requests WHERE request_id=?",
                         (request_id,)).fetchone()
        if row is None:
            raise CommandError("not_found", "Unknown Keeper request")
        return json.loads(row[0]), json.loads(row[1])

    @staticmethod
    def _returned_outcome(error):
        if isinstance(error, _Returned):
            return {"status": "returned", "reason": error.reason, "evidence": error.evidence}
        return {"status": "returned", "reason": error.code}

    def queued(self, *, token):
        """Identify only recorded requests interrupted before a review outcome."""
        self._authorize(token)
        with self._connect() as db:
            return [row[0] for row in db.execute(
                "SELECT request_id FROM keeper_requests "
                "WHERE json_extract(result, '$.status')='queued' ORDER BY rowid")]

    @staticmethod
    def _validate_dispatch(result):
        if not isinstance(result, dict) or set(result) - {"status", "reason", "child_id"}:
            raise CommandError("invalid_dispatch", "Dispatcher returned an invalid outcome")
        status = result.get("status")
        if status not in ("reviewed", "returned"):
            raise CommandError("invalid_dispatch", "Dispatcher status is invalid")
        reason = result.get("reason", "")
        if reason is None:
            reason = ""
        if not isinstance(reason, str) or len(reason.encode("utf-8")) > _MAX_DISPATCH_REASON_BYTES:
            raise CommandError("invalid_dispatch", "Dispatcher reason is invalid")
        if status == "returned" and not reason.strip():
            raise CommandError("invalid_dispatch", "A returned outcome requires a reason")
        child_id = result.get("child_id")
        if child_id is not None:
            _bounded_text(child_id, name="child_id", byte_limit=_MAX_ID_BYTES)
        return {"status": status, "reason": reason, "child_id": child_id}

    def _context(self, db, receipt, command):
        """A bounded projection: no report body, task messages or raw material text."""
        owners = []
        groups = []
        for group in command["groups"]:
            changes = []
            for change in group["changes"]:
                changes.append({
                    "kind": change["kind"], "action": change["action"],
                    "id": change.get("id"),
                    "expected_version": change.get("expected_version"),
                    "fields": change["fields"], "reason": change["reason"],
                    "sources": [self._theme_source(source, self._pin_source(db, source))
                                for source in change["sources"]],
                })
                if change["action"] != "create":
                    record = self._read_owner(db, change["kind"], change["id"])
                    owners.append({
                        "kind": change["kind"], "id": change["id"],
                        "version": record["version"],
                        "status": record.get("status"),
                        "label": self._owner_label(change["kind"], record),
                    })
            groups.append({"changes": changes})
        return {
            "schema_version": _SCHEMA_VERSION,
            "request_id": receipt["request_id"],
            "task_id": command["task_id"],
            "task_version": command["task_version"],
            "report_id": command["report_id"],
            "report_version": command["report_version"],
            "groups": groups,
            "owners": owners,
        }

    @staticmethod
    def _read_owner(db, kind, identity):
        if kind == "card":
            return KnowledgeCardService.read_in_transaction(db, identity)
        if kind == "theme":
            return KnowledgeThemeService.read_in_transaction(db, identity)
        return FrontierService.read_in_transaction(db, identity)

    @staticmethod
    def _owner_label(kind, record):
        value = {"card": record.get("title"),
                 "theme": record.get("judgement"),
                 "frontier": record.get("question")}[kind]
        if not isinstance(value, str):
            value = ""
        encoded = value.encode("utf-8")[:_MAX_LABEL_BYTES]
        return encoded.decode("utf-8", "ignore")

    def _plan(self, db, command):
        """Build and validate every candidate without any business write."""
        entries = []
        seen_creations = {}
        for group in command["groups"]:
            for change in group["changes"]:
                entry = self._build_change(db, change)
                if entry["proposal"]["action"] == "create":
                    key = (entry["kind"],
                           _normalized_label(self._creation_label(entry)))
                    if key in seen_creations:
                        raise _Returned("duplicate_entity", [{
                            "kind": entry["kind"],
                            "label": self._creation_label(entry),
                            "request_duplicate": True,
                        }])
                    seen_creations[key] = entry
                    overlap = self._existing_overlap(db, entry)
                    if overlap:
                        raise _Returned("duplicate_entity", overlap)
                entries.append(entry)
        return list(self._components(entries))

    @staticmethod
    def _creation_label(entry):
        kind = entry["kind"]
        values = entry["proposal"]["after"]
        return {"card": values.get("title"), "theme": values.get("judgement"),
                "frontier": values.get("question")}[kind]

    @staticmethod
    def _owner_key(kind, record):
        return (kind, record["card_id"] if kind == "card"
                else record["theme_id"] if kind == "theme"
                else record["frontier_id"])

    def _existing_overlap(self, db, entry):
        """Return evidence only for identical normalized creations, never near names."""
        kind = entry["kind"]
        label = _normalized_label(self._creation_label(entry))
        if kind == "card":
            records = KnowledgeCardService.query_all_in_transaction(db)
            records = [record for record in records
                       if record.get("status") == "active" and record.get("known")]
        elif kind == "theme":
            records = KnowledgeThemeService.query_all_in_transaction(db)
            records = [record for record in records
                       if record.get("status") == "active" and record.get("known")]
        else:
            records = FrontierService.query_all_in_transaction(db)
            records = [record for record in records
                       if record.get("status") != "deleted"]
        evidence = []
        for record in records:
            if _normalized_label(self._owner_label(kind, record)) == label:
                identity = {"card": record.get("card_id"), "theme": record.get("theme_id"),
                            "frontier": record.get("frontier_id")}[kind]
                evidence.append({"kind": kind, "id": identity,
                                 "version": record.get("version"),
                                 "label": self._owner_label(kind, record)})
        return evidence

    def _build_change(self, db, change):
        kind, action = change["kind"], change["action"]
        citations = []
        for source in change["sources"]:
            citations.append((source, self._pin_source(db, source)))
        if kind == "card":
            proposal = self._card_change(db, change, citations)
        elif kind == "theme":
            proposal = self._theme_change(db, change, citations)
        else:
            proposal = self._frontier_change(db, change, citations)
        if not citations:
            raise _Returned("missing_evidence")
        proposal["sources"] = [self._theme_source(source, reference)
                               for source, reference in citations]
        return {"kind": kind, "id": proposal["id"],
                "before": proposal["before"], "after": proposal["after"],
                "proposal": proposal}

    def _pin_source(self, db, source):
        try:
            return self.materials.validate_citation(db, source)
        except CommandError as error:
            if error.code in _EVIDENCE_ERRORS:
                raise _Returned("invalid_evidence", [{
                    "material_id": source.get("material_id"),
                    "material_version": source.get("material_version"),
                    "problem": error.code,
                }])
            raise

    @staticmethod
    def _card_source(source, reference):
        pinned = {
            "material_id": reference["material_id"],
            "excerpt": source["excerpt"],
            "locator": source["locator"],
            "material_version": reference["material_version"],
            "content_hash": reference["content_hash"],
            "source_kind": reference["source_kind"],
            "title": reference["title"],
        }
        if "snapshot_id" in reference:
            pinned["snapshot_id"] = reference["snapshot_id"]
        return pinned

    @staticmethod
    def _theme_source(source, reference):
        pinned = dict(source)
        pinned.update({
            "material_version": reference["material_version"],
            "content_hash": reference["content_hash"],
            "source_kind": reference["source_kind"],
            "title": reference["title"],
            "source_label": reference.get("source_label"),
            "anchor": reference["anchor"],
        })
        if "snapshot_id" in reference:
            pinned["snapshot_id"] = reference["snapshot_id"]
        return pinned

    def _card_change(self, db, change, citations):
        action = change["action"]
        fields_in = change["fields"]
        if set(fields_in) - _CARD_FIELDS:
            raise CommandError("invalid_command", "Unsupported card fields")
        if action == "delete":
            card = self._require_owner(db, "card", change)
            if card["status"] == "archived":
                raise CommandError("invalid_transition", "Knowledge Card is already archived")
            after = copy.deepcopy(card)
            after.update(status="archived", known=False, version=card["version"] + 1)
            return self._proposal_change("card", "delete", card["card_id"],
                                         change["expected_version"], card, after,
                                         change, change["sources"])
        if len(change["sources"]) > 16:
            raise CommandError("invalid_card", "来源数量超出限制")
        fields = KnowledgeCardService._fields(fields_in)
        if action == "create":
            if "title" not in fields or "content" not in fields:
                raise CommandError("invalid_card", "title and content are required")
            if not citations:
                raise _Returned("missing_evidence")
            relations = self.cards._relations(db, None, fields.get("relations", []))
            sources = self._dedupe_card_sources(citations)
            provenance, source_pending = _card_provenance(sources)
            after = {
                "schema_version": _SCHEMA_VERSION,
                "card_id": None,
                "version": 1,
                "title": fields["title"],
                "content": fields["content"],
                "aliases": fields.get("aliases", []),
                "recall_terms": fields.get("recall_terms", []),
                "sources": sources,
                "relations": relations,
                "provenance": provenance,
                "source_pending": source_pending,
                "questionable": fields.get("questionable", False),
                "known": True,
                "origin": "keeper",
                "status": "active",
            }
            return self._proposal_change("card", "create", None, None, None, after,
                                         change, change["sources"])
        card = self._require_owner(db, "card", change)
        if card["status"] == "archived":
            raise CommandError("invalid_transition", "Archived Knowledge Cards are read-only")
        if "sources" in fields_in:
            raise CommandError("invalid_command", "Sources belong on the change")
        relations = card["relations"]
        if "relations" in fields:
            relations = self.cards._relations(db, card["card_id"], fields["relations"])
        if not citations and not card["sources"]:
            raise _Returned("missing_evidence")
        updated = copy.deepcopy(card)
        updated.update(fields)
        updated["relations"] = relations
        updated["known"] = True
        updated["version"] = card["version"] + 1
        return self._proposal_change("card", "update", card["card_id"],
                                     change["expected_version"], card, updated,
                                     change, change["sources"])

    @staticmethod
    def _dedupe_card_sources(citations):
        seen = {}
        for source, reference in citations:
            pinned = KeeperService._card_source(source, reference)
            seen[(pinned["material_id"], pinned["locator"], pinned["excerpt"])] = pinned
        return list(seen.values())

    def _theme_change(self, db, change, citations):
        action = change["action"]
        fields_in = change["fields"]
        if set(fields_in) - _THEME_FIELDS:
            raise CommandError("invalid_command", "Unsupported theme fields")
        if action == "delete":
            theme = self._require_owner(db, "theme", change)
            if theme["status"] == "archived":
                raise CommandError("invalid_transition", "Theme is already archived")
            after = copy.deepcopy(theme)
            after.update(status="archived", known=False, version=theme["version"] + 1)
            return self._proposal_change("theme", "delete", theme["theme_id"],
                                         change["expected_version"], theme, after,
                                         change, change["sources"])
        fields = KnowledgeThemeService._fields(
            fields_in, allow_assigned_id=(action != "create"))
        if action == "create":
            if "judgement" not in fields:
                raise CommandError("invalid_theme", "judgement is required")
            related = self.themes._relations(db, fields.get("related_cards", []))
            evidence = self._theme_evidence(db, fields.get("evidence", []), ())
            if not evidence and not citations:
                raise _Returned("missing_evidence")
            after = {
                "schema_version": _SCHEMA_VERSION,
                "theme_id": None,
                "version": 1,
                "judgement": fields["judgement"],
                "conditions": fields.get("conditions", []),
                "related_cards": related,
                "evidence": evidence,
                "evidence_sufficiency": _sufficiency(evidence),
                "known": True,
                "origin": "keeper",
                "status": "active",
            }
            return self._proposal_change("theme", "create", None, None, None, after,
                                         change, change["sources"])
        theme = self._require_owner(db, "theme", change)
        if theme["status"] == "archived":
            raise CommandError("invalid_transition", "Archived Themes are read-only")
        related = theme["related_cards"]
        if "related_cards" in fields:
            related = self.themes._relations(db, fields["related_cards"])
        if "evidence" in fields:
            evidence = self._theme_evidence(db, fields["evidence"], theme["evidence"])
        else:
            evidence = copy.deepcopy(theme["evidence"])
        if not evidence and not theme["evidence"] and not citations:
            raise _Returned("missing_evidence")
        updated = copy.deepcopy(theme)
        updated.update(fields)
        updated["related_cards"] = related
        updated["evidence"] = evidence
        updated["evidence_sufficiency"] = _sufficiency(evidence)
        updated["known"] = True
        updated["version"] = theme["version"] + 1
        return self._proposal_change("theme", "update", theme["theme_id"],
                                     change["expected_version"], theme, updated,
                                     change, change["sources"])

    def _theme_evidence(self, db, entries, current):
        existing = {entry["evidence_id"]: entry for entry in current}
        resolved = []
        for entry in entries:
            evidence_id = entry.get("evidence_id")
            if evidence_id is not None:
                if evidence_id not in existing:
                    raise CommandError("not_found", "Unknown evidence")
                pinned = existing[evidence_id]
                source = entry["source"]
                unchanged = all(source.get(field) == pinned["source"].get(field)
                                for field in ("material_id", "locator", "excerpt"))
                if unchanged and source.get("material_version", pinned["source"]["material_version"]) == pinned["source"]["material_version"]:
                    pinned_source = copy.deepcopy(pinned["source"])
                else:
                    if "material_version" not in source:
                        raise CommandError("invalid_command", "New evidence requires a material version")
                    reference = self._pin_source(db, source)
                    pinned_source = self._theme_source(source, reference)
            else:
                if "material_version" not in entry["source"]:
                    raise CommandError("invalid_command", "New evidence requires a material version")
                reference = self._pin_source(db, entry["source"])
                evidence_id = str(uuid.uuid4())
                pinned_source = self._theme_source(entry["source"], reference)
            resolved.append({"evidence_id": evidence_id, "stance": entry["stance"],
                             "reason": entry["reason"], "source": pinned_source})
        return resolved

    def _frontier_change(self, db, change, citations):
        action = change["action"]
        fields_in = change["fields"]
        if set(fields_in) - _FRONTIER_FIELDS:
            raise CommandError("invalid_command", "Unsupported frontier fields")
        if action == "delete":
            frontier = self._require_owner(db, "frontier", change)
            if frontier["status"] == "deleted":
                raise CommandError("invalid_transition", "Frontier is already deleted")
            after = copy.deepcopy(frontier)
            after.update(status="deleted", version=frontier["version"] + 1)
            return self._proposal_change("frontier", "delete", frontier["frontier_id"],
                                         change["expected_version"], frontier, after,
                                         change, change["sources"])
        validated = {}
        if "question" in fields_in:
            validated["question"] = _validate_question(fields_in["question"])
        if "scope" in fields_in:
            validated["scope"] = _validate_scope(fields_in["scope"])
        if "hard_constraints" in fields_in:
            validated["hard_constraints"] = _validate_constraints(fields_in["hard_constraints"])
        if "status" in fields_in:
            validated["status"] = _validate_status(fields_in["status"])
        if "relations" in fields_in:
            validated["relations"] = _validate_relations(fields_in["relations"])
        if action == "create":
            if not {"question", "scope", "hard_constraints"} <= set(validated):
                raise CommandError("invalid_scope",
                                   "question, scope and hard_constraints are required")
            after = {
                "schema_version": _SCHEMA_VERSION,
                "frontier_id": None,
                "version": 1,
                "question": validated["question"],
                "status": validated.get("status", "following"),
                "scope": validated["scope"],
                "hard_constraints": validated["hard_constraints"],
                "relations": validated.get("relations", []),
            }
            return self._proposal_change("frontier", "create", None, None, None, after,
                                         change, change["sources"])
        frontier = self._require_owner(db, "frontier", change)
        if frontier["status"] == "deleted":
            raise CommandError("invalid_transition", "Deleted Frontiers are read-only")
        updated = copy.deepcopy(frontier)
        updated.update(validated)
        updated["version"] = frontier["version"] + 1
        return self._proposal_change("frontier", "update", frontier["frontier_id"],
                                     change["expected_version"], frontier, updated,
                                     change, change["sources"])

    def _require_owner(self, db, kind, change):
        record = self._read_owner(db, kind, change["id"])
        if record["version"] != change["expected_version"]:
            error = CommandError("version_conflict", "Owner version changed")
            error.details = {"kind": kind, "id": change["id"],
                             "expected_version": change["expected_version"],
                             "current_version": record["version"]}
            raise error
        return record

    @staticmethod
    def _proposal_change(kind, action, identity, expected_version, before, after,
                         change, sources):
        return {
            "kind": kind, "action": action, "id": identity,
            "expected_version": expected_version, "before": before,
            "after": after, "reason": change["reason"],
            "sources": [dict(source) for source in sources],
        }

    def _components(self, entries):
        remaining = set(range(len(entries)))
        while remaining:
            root = min(remaining)
            remaining.remove(root)
            group = [root]
            for origin in group:
                linked = [index for index in sorted(remaining)
                          if self._linked(entries[origin], entries[index])]
                group.extend(linked)
                remaining.difference_update(linked)
            yield [entries[index] for index in sorted(group)]

    @staticmethod
    def _card_relation_ids(entry):
        identities = set()
        for record in (entry["before"], entry["after"]):
            if isinstance(record, dict):
                for relation in record.get("relations", []) or []:
                    if isinstance(relation, str):
                        identities.add(relation)
        return identities

    @staticmethod
    def _theme_card_ids(entry):
        identities = set()
        for record in (entry["before"], entry["after"]):
            if isinstance(record, dict):
                for relation in record.get("related_cards", []) or []:
                    if isinstance(relation, dict) and isinstance(relation.get("card_id"), str):
                        identities.add(relation["card_id"])
        return identities

    def _linked(self, first, second):
        if first["kind"] == second["kind"] == "card":
            return (second["id"] in self._card_relation_ids(first)
                    or first["id"] in self._card_relation_ids(second))
        if {first["kind"], second["kind"]} == {"theme", "card"}:
            theme, card = ((first, second) if first["kind"] == "theme"
                           else (second, first))
            return card["id"] in self._theme_card_ids(theme)
        return False

    def _proposal(self, receipt, command, entries):
        return {
            "proposal_id": str(uuid.uuid4()),
            "revision": 1,
            "status": "pending",
            "request_id": receipt["request_id"],
            "task_id": command["task_id"],
            "task_version": command["task_version"],
            "report_id": command["report_id"],
            "report_version": command["report_version"],
            "provenance": "editor_test",
            "child_id": receipt.get("child_id"),
            "changes": [copy.deepcopy(entry["proposal"]) for entry in entries],
        }

    def list(self, *, token, status="pending"):
        """List persisted proposals with a stable review status."""
        self._authorize(token)
        if status not in ("pending", "approved", "rejected", "returned"):
            raise CommandError("invalid_command", "Unknown proposal status")
        with self._connect() as db:
            db.execute("BEGIN")
            rows = db.execute("SELECT result FROM keeper_proposals ORDER BY rowid").fetchall()
        proposals = [json.loads(row[0]) for row in rows]
        return [proposal for proposal in proposals if proposal["status"] == status]

    def view(self, proposal_id, *, token):
        """Read one persisted proposal with its pinned changes."""
        self._authorize(token)
        if not isinstance(proposal_id, str) or not proposal_id:
            raise CommandError("not_found", "Unknown Keeper proposal")
        with self._connect() as db:
            row = db.execute("SELECT result FROM keeper_proposals WHERE proposal_id=?",
                             (proposal_id,)).fetchone()
        if row is None:
            raise CommandError("not_found", "Unknown Keeper proposal")
        return json.loads(row[0])

    def receipt(self, request_id, *, token):
        """Read the current persistent request receipt."""
        self._authorize(token)
        if not isinstance(request_id, str) or not request_id:
            raise CommandError("not_found", "Unknown Keeper request")
        with self._connect() as db:
            row = db.execute("SELECT result FROM keeper_requests WHERE request_id=?",
                             (request_id,)).fetchone()
        if row is None:
            raise CommandError("not_found", "Unknown Keeper request")
        return json.loads(row[0])

    request_receipt = receipt
