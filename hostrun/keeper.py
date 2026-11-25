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
import time
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from hostrun import keeper_merge, keeper_revisions
from hostrun.editor_keeper import verify_editor_request
from hostrun.editor_reports import ReportService
from hostrun.agent_service import AgentService, CommandError
from hostrun.frontier import (
    _validate_constraints, _validate_question,
    _validate_relations, _validate_scope, _validate_status,
)
from hostrun.keeper_decisions import (
    applied_result, apply_order, check_expected_versions, owner_command,
    owner_operation_id, validate_decision, validate_material_references,
    validate_relations_against_deletions,
)
from hostrun.keeper_evidence_policy import review_policy
from hostrun.librarian import Librarian
from hostrun.knowledge_cards import _provenance as _card_provenance
from hostrun.knowledge_themes import KnowledgeThemeService, _MAX_EVIDENCE, _sufficiency
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
_MAX_REVISION_ATTEMPTS = 2
_REVISION_TERMINAL = ("approved", "rejected", "cancelled")

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
_RECONSIDERATION_FIELDS = {"supersedes_proposal_id", "new_evidence_reason"}
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
    if "merge_from" in fields:
        if kind != "card" or action != "update":
            raise CommandError("invalid_command", "Only a Card update can merge another Card")
        keeper_merge.parse_merge_from(fields["merge_from"])
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
    production = command.get("report_kind") == "editor"
    fields = _TOP_LEVEL_FIELDS | ({"report_kind"} if production else set())
    if set(command) not in (fields, fields | _RECONSIDERATION_FIELDS):
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
    reconsideration = {}
    if "supersedes_proposal_id" in command:
        reconsideration = {
            "supersedes_proposal_id": _bounded_identifier(
                command["supersedes_proposal_id"], name="supersedes_proposal_id"),
            "new_evidence_reason": _bounded_text(
                command["new_evidence_reason"], name="new_evidence_reason",
                byte_limit=_MAX_REASON_BYTES),
        }
    return {
        "schema_version": _SCHEMA_VERSION,
        "operation_id": operation,
        "task_id": task_id,
        "task_version": command["task_version"],
        "report_id": report_id,
        "report_version": command["report_version"],
        "groups": normalized_groups, **reconsideration,
        **({"report_kind": "editor"} if production else {}),
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

    def __init__(self, agent, materials, cards, themes, frontiers, *, token, clock=time.time):
        if not isinstance(token, str) or not token:
            raise ValueError("A launch token is required")
        self.agent = agent
        self.materials = materials
        self.cards = Librarian(cards, token=token, clock=clock, frontiers=frontiers)
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
        db.execute("CREATE TABLE IF NOT EXISTS keeper_audit("
                   "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                   "proposal_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, "
                   "event TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS keeper_revision_jobs("
                   "review_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, "
                   "base_revision INTEGER NOT NULL, status TEXT NOT NULL, "
                   "request TEXT NOT NULL, result TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS keeper_revision_jobs_proposal "
                   "ON keeper_revision_jobs(proposal_id)")
        db.execute("CREATE TABLE IF NOT EXISTS keeper_revision_history("
                   "proposal_id TEXT NOT NULL, revision INTEGER NOT NULL, "
                   "operation_id TEXT NOT NULL, result TEXT NOT NULL, "
                   "created_at INTEGER NOT NULL, "
                   "PRIMARY KEY(proposal_id, revision))")

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
    def _check_principal(principal, *, production=False):
        if not isinstance(principal, KeeperPrincipal):
            raise PermissionError("A trusted Keeper principal is required")
        expected_test = not production
        if principal.role != "editor" or principal.explicit_test is not expected_test:
            raise PermissionError("This principal cannot submit Keeper requests")

    @staticmethod
    def _check_explorer(principal):
        if not isinstance(principal, KeeperPrincipal):
            raise PermissionError("A trusted Explorer principal is required")
        if principal.role != "explorer" or principal.explicit_test is not False:
            raise PermissionError("Only a trusted Explorer can decide Keeper proposals")

    @staticmethod
    def _check_pins(db, command):
        task = AgentService.read_task_in_transaction(db, command["task_id"])
        if command.get("report_kind") == "editor":
            if task["version"] < command["task_version"]:
                raise CommandError("version_conflict", "Task version moved backwards")
            verify_editor_request(db, command)
            ReportService.read_version_in_transaction(db, command["report_id"], command["task_id"],
                                                      command["report_version"])
            return
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
        production = isinstance(command, dict) and command.get("report_kind") == "editor"
        self._check_principal(principal, production=production)
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
                outcome = self._dispatch_failure(error, production=command.get("report_kind") == "editor")
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
                    if command.get("report_kind") == "editor":
                        receipt["review_reason"] = outcome.get("reason", "")
                    proposals = [self._proposal(receipt, command, group) for group in plan]
                    db.execute("SAVEPOINT reserve_reviewed_terms")
                    try:
                        for proposal in proposals:
                            self.cards.reserve_reviewed_terms(db, proposal)
                    except CommandError:
                        db.execute("ROLLBACK TO reserve_reviewed_terms")
                        raise
                    finally:
                        db.execute("RELEASE reserve_reviewed_terms")
                except (CommandError, _Returned) as error:
                    outcome = self._returned_outcome(error)
            if outcome["status"] == "reviewed":
                proposal_ids = []
                for proposal in proposals:
                    proposal_ids.append(proposal["proposal_id"])
                    db.execute("INSERT INTO keeper_proposals VALUES(?,?,?)",
                               (proposal["proposal_id"], request_id, json.dumps(proposal)))
                receipt.update(status="pending", proposal_ids=proposal_ids)
            else:
                receipt.update(status=outcome["status"], reason=outcome["reason"],
                               evidence=outcome.get("evidence", []))
            self._persist_request(db, receipt, command)
        return _receipt_result(receipt)

    def decide(self, command, *, token, principal=None):
        """Approve or reject one pending proposal in one atomic transaction."""
        self._authorize(token)
        self._check_explorer(principal)
        command = validate_decision(command)
        encoded = json.dumps({"action": "decide", "command": command},
                             sort_keys=True, ensure_ascii=False)
        notifications = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT request,result FROM keeper_operations WHERE operation_id=?",
                (command["operation_id"],)).fetchone()
            if previous is not None:
                if previous[0] != encoded:
                    raise CommandError(
                        "operation_conflict",
                        "operation_id conflicts with a previous decision")
                return json.loads(previous[1])
            row = db.execute("SELECT result FROM keeper_proposals WHERE proposal_id=?",
                             (command["proposal_id"],)).fetchone()
            if row is None:
                raise CommandError("not_found", "Unknown Keeper proposal")
            proposal = json.loads(row[0])
            if proposal.get("revision") != command["revision"]:
                if command["decision"] == "approve" \
                        and proposal.get("status") == "revising":
                    result = self._stale_approval(proposal, command)
                    db.execute("INSERT INTO keeper_operations VALUES(?,?,?)",
                               (command["operation_id"], encoded,
                                json.dumps(result)))
                    self._audit_stale_approval(db, proposal, result)
                    return result
                raise CommandError("version_conflict",
                                   "Keeper proposal revision changed")
            changes = proposal["changes"]
            check_expected_versions(command, changes)
            if "term_expires_at" in proposal and "decision" in proposal:
                return proposal["decision"]
            if self.cards.reviewed_terms_expired(proposal):
                self.cards.release_reviewed_terms(db, proposal)
                result = {"operation_id": command["operation_id"],
                          "proposal_id": proposal["proposal_id"], "status": "expired",
                          "revision": proposal["revision"], "decision": command["decision"], "applied": []}
                proposal.update(status="expired", decision=result)
                db.execute("UPDATE keeper_proposals SET result=? WHERE proposal_id=?",
                           (json.dumps(proposal), proposal["proposal_id"]))
                db.execute("INSERT INTO keeper_operations VALUES(?,?,?)",
                           (command["operation_id"], encoded, json.dumps(result)))
                return result
            if proposal.get("status") == "revising" \
                    and command["decision"] == "approve":
                # A concurrent window approved the same displayed revision while
                # Keeper is already rebuilding it. Return an explicit revising
                # receipt instead of reporting a successful knowledge write.
                result = self._stale_approval(proposal, command)
                db.execute("INSERT INTO keeper_operations VALUES(?,?,?)",
                           (command["operation_id"], encoded,
                            json.dumps(result)))
                self._audit_stale_approval(db, proposal, result)
                return result
            if proposal.get("status") != "pending":
                raise CommandError("already_decided",
                                   "Keeper proposal has already been decided")
            self.cards.release_reviewed_terms(db, proposal)
            applied = []
            failure = None
            if command["decision"] == "approve":
                db.execute("SAVEPOINT keeper_apply")
                try:
                    request, _ = self._load_request(db, proposal["request_id"])
                    drift = []
                    for change in changes:
                        if change["action"] == "create":
                            continue
                        record = self._read_owner(db, change["kind"], change["id"])
                        if record["version"] != change["expected_version"]:
                            drift.append({
                                "kind": change["kind"], "id": change["id"],
                                "expected_version": change["expected_version"],
                                "current_version": record["version"],
                            })
                    if drift:
                        # The displayed revision still pins the old owner snapshot.
                        # Record a durable revision job before any owner write; the
                        # Explorer's approval never silently rebases knowledge.
                        result, event = self._begin_revision(
                            db, proposal, command, drift=drift)
                        db.execute("UPDATE keeper_proposals SET result=? "
                                   "WHERE proposal_id=?",
                                   (json.dumps(proposal), command["proposal_id"]))
                        db.execute("INSERT INTO keeper_operations VALUES(?,?,?)",
                                   (command["operation_id"], encoded,
                                    json.dumps(result)))
                        self._record_audit(db, proposal["proposal_id"], event)
                        return result
                    self._check_approval_policy(db, changes, request)
                    self._validate_merge_component(
                        db,
                        [{"kind": change["kind"], "id": change["id"],
                          "before": change.get("before"),
                          "proposal": change,
                          "after": change.get("after")} for change in changes],
                        at_approval=True)
                    validate_relations_against_deletions(changes)
                    validate_material_references(self.materials, db, changes)
                    for change in changes:
                        if change["action"] == "create" and self._existing_overlap(
                                db, {"kind": change["kind"], "proposal": change}):
                            raise CommandError("duplicate_entity", "An equivalent record already exists")
                    results = {}
                    for index, change in apply_order(changes):
                        action, prepared = owner_command(
                            change["kind"], change,
                            owner_operation_id(command["proposal_id"],
                                               command["revision"], index))
                        record, changed = self._apply_owner(
                            db, change["kind"], action, prepared)
                        results[index] = applied_result(change["kind"], record)
                        if changed:
                            notifications.append((change["kind"], record))
                    applied = [results[index] for index in range(len(changes))]
                except CommandError as error:
                    if "term_expires_at" not in proposal:
                        raise
                    db.execute("ROLLBACK TO keeper_apply")
                    proposal = json.loads(row[0])
                    notifications.clear()
                    failure = {"code": error.code, "message": str(error)}
                finally:
                    db.execute("RELEASE keeper_apply")
            status = "returned" if failure else ("approved" if command["decision"] == "approve" else "rejected")
            decided_at = int(time.time())
            result = {
                "operation_id": command["operation_id"],
                "decision": command["decision"],
                "proposal_id": command["proposal_id"],
                "revision": command["revision"],
                "status": status,
                "applied": applied,
                "decided_at": decided_at,
            }
            if failure:
                result["error"] = failure
            if "reason" in command:
                result["reason"] = command["reason"]
            proposal.update(status=status, decision=result)
            db.execute("UPDATE keeper_proposals SET result=? WHERE proposal_id=?",
                       (json.dumps(proposal), command["proposal_id"]))
            db.execute("INSERT INTO keeper_operations VALUES(?,?,?)",
                       (command["operation_id"], encoded, json.dumps(result)))
            event = {
                "schema_version": _SCHEMA_VERSION,
                "type": "keeper.decision",
                "operation_id": command["operation_id"],
                "proposal_id": command["proposal_id"],
                "revision": command["revision"],
                "decision": command["decision"],
                "status": status,
                "actor": "explorer",
                "expected_versions": command["expected_versions"],
                "applied": applied,
                "decided_at": decided_at,
            }
            if failure:
                event["error"] = failure
            if "reason" in command:
                event["reason"] = command["reason"]
            db.execute("INSERT INTO keeper_audit(proposal_id,operation_id,event) "
                       "VALUES(?,?,?)",
                       (command["proposal_id"], command["operation_id"],
                        json.dumps(event)))
        for kind, record in notifications:
            self._notify_owner(kind, record)
        return result

    # ------------------------------------------------------------------
    # Revision jobs: a drifted owner pin becomes a durable, reviewable job.
    # ------------------------------------------------------------------

    def revise(self, command, *, token, principal=None):
        """Adjust or cancel one active proposal revision without owner writes."""
        self._authorize(token)
        self._check_explorer(principal)
        command = keeper_revisions.validate_revise(command)
        encoded = json.dumps({"action": "revise", "command": command},
                             sort_keys=True, ensure_ascii=False)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT request,result FROM keeper_operations WHERE operation_id=?",
                (command["operation_id"],)).fetchone()
            if previous is not None:
                if previous[0] != encoded:
                    raise CommandError(
                        "operation_conflict",
                        "operation_id conflicts with a previous revision command")
                return json.loads(previous[1])
            proposal = self._load_proposal(db, command["proposal_id"])
            if proposal.get("revision") != command["revision"]:
                raise CommandError("version_conflict",
                                   "Keeper proposal revision changed")
            status = proposal.get("status")
            if status in _REVISION_TERMINAL \
                    or status not in ("pending", "revising", "returned"):
                raise CommandError("already_decided",
                                   "Keeper proposal has already been decided")
            if self.cards.reviewed_terms_expired(proposal):
                raise CommandError("already_decided", "确认已过期")
            self.cards.release_reviewed_terms(db, proposal)
            if command["decision"] == "cancel":
                result = self._cancel_revision(db, proposal, command)
            else:
                if status == "revising":
                    raise CommandError(
                        "revision_in_progress",
                        "A Keeper revision worker already owns this proposal")
                keeper_revisions.validate_adjustment(proposal, command)
                result = self._adjust_revision(db, proposal, command)
            db.execute("INSERT INTO keeper_operations VALUES(?,?,?)",
                       (command["operation_id"], encoded, json.dumps(result)))
        return result

    def queued_revisions(self, *, token, production_only=False):
        """Identify jobs that still need a Keeper child, including startup recovery."""
        self._authorize(token)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT j.review_id,j.status,j.request,j.result FROM keeper_revision_jobs j "
                "JOIN keeper_proposals p ON p.proposal_id=j.proposal_id "
                "WHERE (?=0 OR json_extract(p.result, '$.provenance')='editor') ORDER BY j.rowid",
                (int(production_only),)).fetchall()
            queued = []
            for review_id, status, request, _result in rows:
                if status == "running":
                    job = self._load_job(db, review_id)
                    self._save_job(
                        db, job, "queued",
                        self._revision_receipt(job["request"], "queued",
                                               reason="resumed"))
                    queued.append(review_id)
                elif status == "queued":
                    queued.append(review_id)
            return queued

    def review_revision(self, review_id, *, token, dispatch):
        """Run the real Keeper child outside the transaction, then commit safely."""
        self._authorize(token)
        if not isinstance(review_id, str) or not review_id:
            raise CommandError("not_found", "Unknown Keeper revision job")
        if not callable(dispatch):
            raise CommandError("invalid_dispatch", "A trusted dispatcher is required")
        digest = None
        claim = None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = self._load_job(db, review_id)
            if job["status"] != "queued":
                return job["result"]
            proposal = self._load_proposal(db, job["proposal_id"])
            if proposal.get("status") != "revising" \
                    or proposal.get("revision") != job["base_revision"] \
                    or proposal.get("revision_job") != review_id:
                status = "cancelled" if proposal.get("status") == "cancelled" \
                    else "returned"
                result = self._revision_receipt(job["request"], status,
                                                reason="revision_inactive")
                self._save_job(db, job, status, result)
                if status == "returned":
                    self._persist_returned(db, proposal, job, result)
                return result
            try:
                keeper_revisions.validate_adjustment(proposal, job["request"])
                command, _ = self._load_request(db, proposal["request_id"])
                self._check_revision_pins(db, proposal)
                revised = self._rebuild_revision(
                    db, proposal, command, job["request"], review_id)
            except (CommandError, _Returned) as error:
                result = self._revision_failure(job["request"], error)
                self._save_job(db, job, "returned", result)
                self._persist_returned(db, proposal, job, result)
                self._audit_revision(db, proposal.get("proposal_id", job["proposal_id"]),
                                     "keeper.revision_returned", result,
                                     **self._revision_audit_details(job, result))
                return result
            digest = keeper_revisions.fingerprint(revised)
            claim = str(uuid.uuid4())
            job["request"]["attempts"] = int(job["request"].get("attempts", 0)) + 1
            job["request"]["fingerprint"] = digest
            job["request"]["claim"] = claim
            self._save_job(db, job, "running",
                           self._revision_receipt(job["request"], "running"))
            context = self._revision_context(revised, job["request"], review_id)
        try:
            outcome = self._validate_dispatch(dispatch(context))
        except Exception as error:
            outcome = self._dispatch_failure(error, production=proposal.get("provenance") == "editor")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = self._load_job(db, review_id)
            if job["status"] == "cancelled":
                return job["result"]
            if job["status"] != "running" \
                    or job["request"].get("claim") != claim \
                    or job["request"].get("fingerprint") != digest:
                # Another process resumed this job, cancelled it, or the same
                # review was reissued against a different snapshot. Never let
                # this older completion publish over the newer generation.
                return job["result"]
            proposal = self._load_proposal(db, job["proposal_id"])
            if proposal.get("status") == "cancelled":
                result = self._revision_receipt(
                    job["request"], "cancelled", reason="revision_inactive",
                    child_id=outcome.get("child_id"))
                self._save_job(db, job, "cancelled", result)
                return result
            if proposal.get("status") != "revising" \
                    or proposal.get("revision") != job["base_revision"] \
                    or proposal.get("revision_job") != review_id:
                result = self._revision_receipt(job["request"], "returned",
                                                reason="revision_inactive",
                                                child_id=outcome.get("child_id"))
                self._save_job(db, job, "returned", result)
                self._persist_returned(db, proposal, job, result)
                return result
            if outcome["status"] != "reviewed":
                result = self._revision_receipt(
                    job["request"], outcome["status"],
                    reason=outcome.get("reason"),
                    child_id=outcome.get("child_id"))
                self._save_job(db, job, outcome["status"], result)
                if outcome["status"] != "retry_required":
                    self._persist_returned(db, proposal, job, result)
                self._audit_revision(db, proposal["proposal_id"],
                                     "keeper.revision_" + outcome["status"],
                                     result,
                                     **self._revision_audit_details(job, result))
                return result
            try:
                command, _ = self._load_request(db, proposal["request_id"])
                self._check_revision_pins(db, proposal)
                revised = self._rebuild_revision(
                    db, proposal, command, job["request"], review_id)
            except (CommandError, _Returned) as error:
                result = self._revision_failure(job["request"], error)
                self._save_job(db, job, "returned", result)
                self._persist_returned(db, proposal, job, result)
                self._audit_revision(db, proposal["proposal_id"],
                                     "keeper.revision_returned", result,
                                     **self._revision_audit_details(job, result))
                return result
            if keeper_revisions.fingerprint(revised) != digest:
                # The owners moved while the child was running. Never issue a
                # stale revision; retry within a bound or return to the
                # Explorer with the current proposal still intact.
                if job["request"]["attempts"] < _MAX_REVISION_ATTEMPTS:
                    result = self._revision_receipt(
                        job["request"], "queued", reason="snapshot_changed")
                    self._save_job(db, job, "queued", result)
                    self._audit_revision(db, proposal["proposal_id"],
                                         "keeper.revision_retry", result,
                                         **self._revision_audit_details(job, result))
                    return result
                result = self._revision_receipt(
                    job["request"], "returned", reason="snapshot_changed")
                self._save_job(db, job, "returned", result)
                self._persist_returned(db, proposal, job, result)
                self._audit_revision(db, proposal["proposal_id"],
                                     "keeper.revision_returned", result,
                                     **self._revision_audit_details(job, result))
                return result
            try:
                self.cards.reserve_reviewed_terms(db, revised)
            except CommandError as error:
                result = self._revision_failure(job["request"], error)
                self._save_job(db, job, "returned", result)
                self._persist_returned(db, proposal, job, result)
                self._audit_revision(db, proposal["proposal_id"],
                                     "keeper.revision_returned", result,
                                     **self._revision_audit_details(job, result))
                return result
            revised["child_id"] = outcome.get("child_id") \
                or proposal.get("child_id")
            revised["review_id"] = review_id
            revised["revision_notes"] = keeper_revisions.revision_notes(
                proposal,
                reason=job["request"].get("reason") or "revision",
                review_id=review_id,
                feedback=job["request"].get("feedback"),
                child_id=revised["child_id"],
                conflicts=revised.get("conflicts") or [])
            previous_snapshot = keeper_revisions.snapshot(proposal)
            db.execute(
                "INSERT OR IGNORE INTO keeper_revision_history "
                "(proposal_id,revision,operation_id,result,created_at) "
                "VALUES(?,?,?,?,?)",
                (proposal["proposal_id"], proposal["revision"],
                 job["request"].get("operation_id", review_id),
                 json.dumps(previous_snapshot), int(time.time())))
            db.execute("UPDATE keeper_proposals SET result=? WHERE proposal_id=?",
                       (json.dumps(revised), proposal["proposal_id"]))
            result = self._revision_receipt(
                job["request"], "completed", child_id=revised["child_id"])
            self._save_job(db, job, "completed", result)
            self._audit_revision(db, proposal["proposal_id"],
                                 "keeper.revision_completed", result,
                                 **self._revision_audit_details(job, result))
        return result

    def _new_job(self, db, proposal, *, operation_id, reason, feedback=None,
                 omit=None, keep=None, drift=None):
        review_id = str(uuid.uuid4())
        request = {
            "schema_version": _SCHEMA_VERSION,
            "review_id": review_id,
            "proposal_id": proposal["proposal_id"],
            "base_revision": proposal["revision"],
            "operation_id": operation_id,
            "feedback": feedback,
            "reason": reason,
            "omit_changes": list(omit or []),
            "keep_current_fields": copy.deepcopy(list(keep or [])),
            "attempts": 0,
            "drift": list(drift or []),
        }
        db.execute("INSERT INTO keeper_revision_jobs VALUES(?,?,?,?,?,?)",
                   (review_id, proposal["proposal_id"], proposal["revision"],
                    "queued", json.dumps(request),
                    json.dumps(self._revision_receipt(request, "queued"))))
        proposal["status"] = "revising"
        proposal["revision_job"] = review_id
        proposal["review_id"] = review_id
        proposal["revision_notes"] = keeper_revisions.revision_notes(
            proposal, reason=reason, review_id=review_id, feedback=feedback)
        return review_id

    def _begin_revision(self, db, proposal, command, *, drift):
        review_id = self._new_job(
            db, proposal, operation_id=command["operation_id"],
            reason="owner_drift", drift=drift)
        decided_at = int(time.time())
        result = {
            "operation_id": command["operation_id"],
            "decision": command["decision"],
            "proposal_id": proposal["proposal_id"],
            "revision": proposal["revision"],
            "status": "revising",
            "applied": [],
            "review_id": review_id,
            "code": "version_conflict",
            "decided_at": decided_at,
        }
        if "reason" in command:
            result["reason"] = command["reason"]
        proposal["decision"] = result
        event = {
            "schema_version": _SCHEMA_VERSION,
            "type": "keeper.revision_requested",
            "operation_id": command["operation_id"],
            "proposal_id": proposal["proposal_id"],
            "revision": proposal["revision"],
            "decision": command["decision"],
            "status": "revising",
            "actor": "explorer",
            "expected_versions": command.get("expected_versions", []),
            "applied": [],
            "review_id": review_id,
            "drift": list(drift or []),
            "decided_at": decided_at,
        }
        if "reason" in command:
            event["reason"] = command["reason"]
        return result, event

    def _adjust_revision(self, db, proposal, command):
        # Adjusting is an explicit pre-dispatch request or a retry after a
        # returned job. A revising proposal already has a worker generation and
        # cannot be rewritten underneath it.
        if proposal.get("status") not in ("pending", "returned"):
            raise CommandError("revision_in_progress",
                               "A Keeper revision worker already owns this proposal")
        feedback = command.get("feedback")
        review_id = self._new_job(
            db, proposal, operation_id=command["operation_id"],
            reason="explorer_adjustment", feedback=feedback,
            omit=command.get("omit_changes"),
            keep=command.get("keep_current_fields"))
        result = {
            "operation_id": command["operation_id"],
            "decision": "adjust",
            "proposal_id": proposal["proposal_id"],
            "revision": proposal["revision"],
            "status": "revising",
            "applied": [],
            "review_id": review_id,
            "decided_at": int(time.time()),
        }
        if feedback is not None:
            result["feedback"] = feedback
        proposal["decision"] = result
        db.execute("UPDATE keeper_proposals SET result=? WHERE proposal_id=?",
                   (json.dumps(proposal), proposal["proposal_id"]))
        self._audit_revision(db, proposal["proposal_id"],
                             "keeper.revision_feedback", result,
                             review_id=review_id, feedback=feedback,
                             operation_id=command["operation_id"])
        return result

    def _cancel_revision(self, db, proposal, command):
        now = int(time.time())
        job = self._active_job(db, proposal["proposal_id"])
        review_id = job["review_id"] if job is not None else None
        result = {
            "operation_id": command["operation_id"],
            "decision": "cancel",
            "proposal_id": proposal["proposal_id"],
            "revision": proposal["revision"],
            "status": "cancelled",
            "applied": [],
            "decided_at": now,
        }
        if review_id is not None:
            result["review_id"] = review_id
        if "feedback" in command:
            result["feedback"] = command["feedback"]
        if job is not None and job["status"] in ("queued", "running",
                                                 "returned", "failed"):
            self._save_job(db, job, "cancelled", result)
        proposal["status"] = "cancelled"
        proposal["decision"] = result
        if review_id is not None:
            proposal["revision_job"] = review_id
            proposal["review_id"] = review_id
        db.execute("UPDATE keeper_proposals SET result=? WHERE proposal_id=?",
                   (json.dumps(proposal), proposal["proposal_id"]))
        self._audit_revision(db, proposal["proposal_id"],
                             "keeper.revision_cancelled", result,
                             review_id=review_id,
                             operation_id=command["operation_id"])
        return result

    def _persist_returned(self, db, proposal, job, result):
        """Persist an explicit returned proposal state, never a silent revising."""
        if proposal.get("status") != "revising" \
                or proposal.get("revision") != job["base_revision"] \
                or proposal.get("revision_job") != job["review_id"]:
            return
        proposal["status"] = "returned"
        proposal.pop("decision", None)
        proposal["review_id"] = job["review_id"]
        proposal["revision_job"] = job["review_id"]
        proposal["feedback"] = job["request"].get("feedback") or ""
        reason = result.get("reason")
        if reason is not None:
            proposal["reason"] = reason
        proposal["revision_notes"] = keeper_revisions.revision_notes(
            proposal, reason=reason or "returned", review_id=job["review_id"],
            feedback=job["request"].get("feedback"),
            child_id=result.get("child_id"))
        db.execute("UPDATE keeper_proposals SET result=? WHERE proposal_id=?",
                   (json.dumps(proposal), proposal["proposal_id"]))

    def _rebuild_revision(self, db, proposal, command, request, review_id):
        previous_changes = proposal["changes"]
        raw_changes = keeper_revisions.original_changes(command, previous_changes)
        for raw, previous in zip(raw_changes, previous_changes):
            raw["fields"] = keeper_revisions.revision_fields(
                previous, raw.get("fields") or {})
        selected = keeper_revisions.apply_adjustment(raw_changes, request)
        if not selected:
            raise _Returned("no_effect")
        merge_pins = {change["merge_from"]["card_id"]:
                      change["merge_from"]["version"]
                      for change in previous_changes
                      if change.get("merge_from") is not None}
        if merge_pins and request.get("omit_changes"):
            # Merging is an atomic confirmation: its donor archive and every
            # redirect cannot be partially omitted before publication.
            raise _Returned("incomplete_merge")
        survivor_ids = {change["id"] for change in previous_changes
                        if change.get("merge_from") is not None}
        survivor_versions = {
            survivor_id: self._read_owner(db, "card", survivor_id)["version"]
            for survivor_id in survivor_ids}
        for index, raw in selected:
            previous = previous_changes[index]
            if survivor_versions and previous.get("kind") == "theme" \
                    and isinstance(raw.get("fields", {}).get("related_cards"), list):
                # A merge survivor may have drifted; its redirect is recalculated
                # against the fresh version below, so validate that fresh pin.
                previous = copy.deepcopy(previous)
                for relation in (previous.get("after") or {}).get(
                        "related_cards", []):
                    if relation.get("card_id") in survivor_versions:
                        relation["version"] = survivor_versions[
                            relation["card_id"]]
            self._validate_retained_change(db, previous, raw)
            if raw["action"] == "create":
                continue
            record = self._read_owner(db, raw["kind"], raw["id"])
            if raw["action"] == "delete" and raw["id"] in merge_pins:
                # The explicit donor pin stays stable; a moved donor fails
                # safely instead of being silently rebound to its latest form.
                raw["expected_version"] = merge_pins[raw["id"]]
            else:
                raw["expected_version"] = record["version"]
            if raw["id"] in survivor_ids:
                survivor_versions[raw["id"]] = record["version"]
            if raw["action"] == "update":
                raw["fields"] = keeper_revisions.merge_latest_nested(
                    raw["fields"], record)
        for _, raw in selected:
            if raw["kind"] != "theme" or raw["action"] != "update":
                continue
            related = raw["fields"].get("related_cards")
            if not isinstance(related, list):
                continue
            for relation in related:
                survivor_version = survivor_versions.get(relation.get("card_id"))
                if survivor_version is not None:
                    relation["version"] = survivor_version
        scoped = dict(command)
        scoped["groups"] = [{"changes": [raw for _, raw in selected]}]
        plan = self._plan(db, scoped, omit_ineffective=True)
        if not plan:
            raise _Returned("no_effect")
        if len(plan) != 1:
            raise _Returned("group_changed")
        changes = [copy.deepcopy(entry["proposal"]) for entry in plan[0]]
        revised = copy.deepcopy(proposal)
        for name in ("decision", "revision_job", "review_id", "reviewed_at",
                     "previous_revision", "revision_notes", "conflicts",
                     "feedback", "reason"):
            revised.pop(name, None)
        found = keeper_revisions.conflicts(previous_changes, changes)
        revised.update({
            "revision": proposal["revision"] + 1,
            "status": "pending",
            "changes": changes,
            "conflicts": found,
            "review_id": review_id,
            "reviewed_at": int(time.time()),
            "feedback": request.get("feedback") or "",
            "revision_notes": keeper_revisions.revision_notes(
                proposal, reason=request.get("reason") or "revision",
                review_id=review_id, feedback=request.get("feedback"),
                child_id=proposal.get("child_id"), conflicts=found),
        })
        return revised

    def _validate_retained_change(self, db, previous, raw):
        """Check the captured pins that still support this revised change."""
        before = previous["before"] or {}
        after = previous["after"] or {}
        kind, action = raw["kind"], raw["action"]
        fields = raw["fields"]
        if kind == "theme" and action == "update" and "evidence" not in fields:
            previous = {**previous, "after": {**after, "evidence": before.get("evidence", [])}}
        validate_material_references(self.materials, db, [previous])
        if kind == "theme" and (action == "create" or "related_cards" in fields):
            self.themes._relations(db, after.get("related_cards", []))

    @staticmethod
    def _check_revision_pins(db, proposal):
        task = AgentService.read_task_in_transaction(db, proposal["task_id"])
        if task["version"] < proposal["task_version"]:
            raise CommandError("version_conflict", "Task version moved backwards")
        if proposal.get("provenance") == "editor":
            ReportService.read_version_in_transaction(db, proposal["report_id"], proposal["task_id"],
                                                      proposal["report_version"])
            return
        report = AgentService.read_turn_in_transaction(db, proposal["report_id"])
        if report.get("task_id") != proposal["task_id"]:
            raise CommandError("invalid_report", "Report belongs to another task")
        if report.get("status") != "completed":
            raise CommandError("invalid_report", "Report is not a completed turn")
        if not isinstance(report.get("result"), str) or not report["result"].strip():
            raise CommandError("invalid_report", "Report is empty")
        if report["version"] != proposal["report_version"]:
            raise CommandError("version_conflict", "Report version changed")

    @staticmethod
    def _revision_context(revised, request, review_id):
        provenance = revised.get("provenance", "editor_test")
        if not isinstance(provenance, dict):
            provenance = {"source": provenance}
        provenance = {
            **provenance,
            "proposal_id": revised["proposal_id"],
            "revision": revised["revision"],
            "review_id": review_id,
        }
        return {
            "schema_version": _SCHEMA_VERSION,
            "request_id": keeper_revisions.request_id(
                review_id, request["fingerprint"]),
            "review_id": review_id,
            "proposal_id": revised["proposal_id"],
            "revision": revised["revision"],
            "task_id": revised["task_id"],
            "task_version": revised["task_version"],
            "report_id": revised["report_id"],
            "report_version": revised["report_version"],
            "provenance": provenance,
            "feedback": request.get("feedback") or "",
            "fingerprint": request.get("fingerprint"),
            "execution_attempt": request.get("execution_attempt", 0),
            "groups": [{"proposal_id": revised["proposal_id"],
                        "changes": copy.deepcopy(revised["changes"])}],
        }

    @staticmethod
    def _revision_receipt(request, status, *, reason=None, child_id=None):
        result = {
            "schema_version": _SCHEMA_VERSION,
            "review_id": request["review_id"],
            "proposal_id": request["proposal_id"],
            "revision": request["base_revision"],
            "status": status,
        }
        if status == "completed":
            result["proposal_status"] = "pending"
            result["proposal_revision"] = request["base_revision"] + 1
        elif status in ("returned", "failed"):
            result["proposal_status"] = "returned"
        if reason is not None:
            result["reason"] = reason
        if child_id is not None:
            result["child_id"] = child_id
        return result

    @staticmethod
    def _revision_failure(request, error):
        if isinstance(error, _Returned):
            reason = error.reason
            evidence = error.evidence
        else:
            reason = error.code
            evidence = getattr(error, "details", None)
        result = KeeperService._revision_receipt(
            request, "returned", reason=reason)
        if isinstance(evidence, dict):
            # Dict details are an atomic diagnostic object; never slice them.
            result["evidence"] = [copy.deepcopy(evidence)]
        elif isinstance(evidence, list):
            result["evidence"] = copy.deepcopy(
                evidence[:_MAX_TOTAL_CHANGES * _MAX_SOURCES])
        elif evidence is not None:
            result["evidence"] = [copy.deepcopy(evidence)]
        return result

    @staticmethod
    def _stale_approval(proposal, command):
        result = {
            "operation_id": command["operation_id"],
            "decision": command["decision"],
            "proposal_id": proposal["proposal_id"],
            "revision": command["revision"],
            "status": "revising",
            "reason": "version_conflict",
            "applied": [],
            "decided_at": int(time.time()),
        }
        if proposal.get("review_id") is not None:
            result["review_id"] = proposal["review_id"]
        return result

    def _audit_stale_approval(self, db, proposal, result):
        self._audit_revision(
            db, proposal["proposal_id"], "keeper.decision_stale", result,
            review_id=result.get("review_id") or proposal.get("review_id"),
            operation_id=result["operation_id"],
            reason=result["reason"])

    @staticmethod
    def _revision_audit_details(job, result):
        return {
            "review_id": job["review_id"],
            "operation_id": f"revision:{job['review_id']}:{uuid.uuid4()}",
            "attempts": job["request"].get("attempts", 0),
            "result_status": result.get("status"),
            "proposal_revision": result.get("proposal_revision"),
        }

    @staticmethod
    def _record_audit(db, proposal_id, event):
        db.execute("INSERT INTO keeper_audit(proposal_id,operation_id,event) "
                   "VALUES(?,?,?)",
                   (proposal_id, event["operation_id"], json.dumps(event)))

    def _audit_revision(self, db, proposal_id, event_type, result, **details):
        event = {
            "schema_version": _SCHEMA_VERSION,
            "type": event_type,
            "proposal_id": proposal_id,
            "status": result.get("status"),
            "actor": ("explorer" if event_type in ("keeper.revision_feedback",
                                                    "keeper.revision_cancelled")
                      else "keeper"),
            "review_id": details.get("review_id", result.get("review_id")),
            "revision": result.get("revision"),
            "applied": [],
        }
        for name in ("operation_id", "feedback", "attempts", "reason",
                     "result_status", "proposal_revision"):
            if details.get(name) is not None:
                event[name] = details[name]
        if result.get("reason") is not None:
            event.setdefault("reason", result["reason"])
        if result.get("child_id") is not None:
            event["child_id"] = result["child_id"]
        if "operation_id" not in event:
            event["operation_id"] = f"revision:{event['review_id']}:{uuid.uuid4()}"
        self._record_audit(db, proposal_id, event)

    @staticmethod
    def _load_proposal(db, proposal_id):
        row = db.execute("SELECT result FROM keeper_proposals WHERE proposal_id=?",
                         (proposal_id,)).fetchone()
        if row is None:
            raise CommandError("not_found", "Unknown Keeper proposal")
        return json.loads(row[0])

    @staticmethod
    def _load_job(db, review_id):
        row = db.execute(
            "SELECT proposal_id,base_revision,status,request,result "
            "FROM keeper_revision_jobs WHERE review_id=?",
            (review_id,)).fetchone()
        if row is None:
            raise CommandError("not_found", "Unknown Keeper revision job")
        return {"review_id": review_id, "proposal_id": row[0],
                "base_revision": row[1], "status": row[2],
                "request": json.loads(row[3]), "result": json.loads(row[4])}

    @staticmethod
    def _save_job(db, job, status, result):
        job["status"] = status
        job["result"] = result
        db.execute("UPDATE keeper_revision_jobs SET status=?,request=?,result=? "
                   "WHERE review_id=?",
                   (status, json.dumps(job["request"]), json.dumps(result),
                    job["review_id"]))

    @staticmethod
    def _active_job(db, proposal_id):
        row = db.execute(
            "SELECT review_id FROM keeper_revision_jobs WHERE proposal_id=? "
            "ORDER BY rowid DESC LIMIT 1", (proposal_id,)).fetchone()
        return KeeperService._load_job(db, row[0]) if row is not None else None

    @staticmethod
    def _check_approval_policy(db, changes, request):
        blocked = review_policy(db, changes, request, require_matching_supersedes=False)
        if blocked is not None:
            error = CommandError(blocked["reason"], "Keeper proposal now conflicts with an earlier decision")
            error.details = blocked
            raise error

    def _apply_owner(self, db, kind, action, command):
        if kind == "frontier":
            return self.cards.apply_approved_frontier(db, action, command, token=self._token)
        service = {"card": self.cards, "theme": self.themes}[kind]
        return service.apply_in_transaction(
            db, action, command, token=self._token, origin="keeper")

    def _notify_owner(self, kind, record):
        if kind == "frontier":
            self.cards.notify_frontier(record)
            return
        service = {"card": self.cards, "theme": self.themes}[kind]
        service.notify_committed(record)

    def audit(self, proposal_id, *, token):
        """Read the persisted decision audit events for one proposal."""
        self._authorize(token)
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise CommandError("not_found", "Unknown Keeper proposal")
        with self._connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT 1 FROM keeper_proposals WHERE proposal_id=?",
                             (proposal_id,)).fetchone()
            if row is None:
                raise CommandError("not_found", "Unknown Keeper proposal")
            rows = db.execute("SELECT event FROM keeper_audit WHERE proposal_id=? "
                              "ORDER BY sequence", (proposal_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

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

    def queued(self, *, token, production_only=False):
        """Identify only recorded requests interrupted before a review outcome."""
        self._authorize(token)
        with self._connect() as db:
            return [row[0] for row in db.execute(
                "SELECT request_id FROM keeper_requests "
                "WHERE json_extract(result, '$.status')='queued' "
                "AND (?=0 OR json_extract(request, '$.report_kind')='editor') ORDER BY rowid",
                (int(production_only),))]

    @staticmethod
    def _dispatch_failure(error, *, production):
        from hostrun.keeper_runtime import KeeperRuntimeError
        if production and isinstance(error, KeeperRuntimeError) and error.retryable:
            return {"status": "retry_required", "reason": "keeper_retry_required", "child_id": error.child_id}
        return {"status": "failed", "child_id": getattr(error, "child_id", None),
                "reason": "invalid_dispatch" if isinstance(error, CommandError) else "keeper_execution_failed"}

    def retryable_reviews(self, *, token):
        self._authorize(token)
        with self._connect() as db:
            requests = [json.loads(row[0]) for row in db.execute(
                "SELECT result FROM keeper_requests WHERE json_extract(result, '$.status')='retry_required'")]
            revisions = [json.loads(row[0]) for row in db.execute(
                "SELECT result FROM keeper_revision_jobs WHERE status='retry_required'")]
        return requests + revisions

    def retry_review(self, command, *, token, principal):
        self._authorize(token)
        self._check_explorer(principal)
        if (type(command.get("schema_version")) is not int or command["schema_version"] != 1
                or set(command) not in ({"schema_version", "operation_id", "request_id"},
                                        {"schema_version", "operation_id", "review_id"})):
            raise CommandError("invalid_command", "请选择一个可重试的 Keeper 审核")
        operation = _bounded_identifier(command["operation_id"], name="operation_id")
        encoded = json.dumps({"action": "retry_review", "command": command}, sort_keys=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT request,result FROM keeper_operations WHERE operation_id=?", (operation,)).fetchone()
            if prior:
                if encoded != prior[0]:
                    raise CommandError("operation_conflict", "审核重试操作标识冲突")
                return json.loads(prior[1])
            if "request_id" in command:
                request, receipt = self._load_request(db, command["request_id"])
                if receipt["status"] != "retry_required" or request.get("report_kind") != "editor":
                    raise CommandError("invalid_transition", "该审核已完成或不可重试")
                receipt.update(status="queued", execution_attempt=receipt.get("execution_attempt", 0) + 1)
                receipt.pop("reason", None)
                self._persist_request(db, receipt, request)
                result = {"status": "queued", "request_id": command["request_id"]}
            else:
                job = self._load_job(db, command["review_id"])
                proposal = self._load_proposal(db, job["proposal_id"])
                if (job["status"] != "retry_required" or proposal.get("provenance") != "editor"
                        or proposal["status"] != "revising" or proposal.get("revision_job") != command["review_id"]):
                    raise CommandError("invalid_transition", "该复审已完成或不可重试")
                job["request"]["execution_attempt"] = job["request"].get("execution_attempt", 0) + 1
                result = self._revision_receipt(job["request"], "queued")
                self._save_job(db, job, "queued", result)
            db.execute("INSERT INTO keeper_operations VALUES(?,?,?)", (operation, encoded, json.dumps(result)))
        return result

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
        for group in self._merge_review_groups(db, command["groups"]):
            changes = []
            for change in group["changes"]:
                sources = [self._theme_source(source, self._pin_source(db, source))
                           for source in change["sources"]]
                if not sources and change["kind"] == "theme" and "evidence" in change["fields"]:
                    sources = self._build_change(db, change)["proposal"]["sources"]
                changes.append({
                    "kind": change["kind"], "action": change["action"],
                    "id": change.get("id"),
                    "expected_version": change.get("expected_version"),
                    "fields": change["fields"], "reason": change["reason"],
                    "sources": sources,
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
            **({"provenance": {"source": "editor"}, "execution_attempt": receipt.get("execution_attempt", 0)} if command.get("report_kind") == "editor" else {}),
        }

    def _read_owner(self, db, kind, identity):
        if kind == "card":
            return self.cards.read_in_transaction(db, identity)
        if kind == "theme":
            return KnowledgeThemeService.read_in_transaction(db, identity)
        return self.cards.read_frontier_in_transaction(db, identity)

    @staticmethod
    def _owner_label(kind, record):
        value = {"card": record.get("title"),
                 "theme": record.get("judgement"),
                 "frontier": record.get("question")}[kind]
        if not isinstance(value, str):
            value = ""
        encoded = value.encode("utf-8")[:_MAX_LABEL_BYTES]
        return encoded.decode("utf-8", "ignore")

    def _plan(self, db, command, *, omit_ineffective=False):
        """Build and validate every candidate without any business write."""
        entries = []
        seen_creations = {}
        targets = set()
        for group in self._merge_review_groups(db, command["groups"]):
            for change in group["changes"]:
                entry = self._build_change(db, change)
                if omit_ineffective and not keeper_revisions.has_effect(entry["proposal"]):
                    continue
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
                    supplemented = self._supplement_duplicate_card(db, entry)
                    if supplemented is not None:
                        entry = supplemented
                    else:
                        overlap = self._existing_overlap(db, entry)
                        if overlap:
                            raise _Returned("duplicate_entity", overlap)
                if entry["id"] is not None:
                    target = entry["kind"], entry["id"]
                    if target in targets:
                        raise CommandError("duplicate_target",
                                           "A reviewed target appears more than once")
                    targets.add(target)
                entries.append(entry)
        blocked = review_policy(db, entries, command,
                                require_matching_supersedes=not omit_ineffective)
        if blocked is not None:
            raise _Returned(blocked["reason"], [{key: value for key, value in blocked.items()
                                                if key != "reason"}])
        components = list(self._components(entries))
        for component in components:
            self._validate_merge_component(db, component)
        return components

    def _merge_review_groups(self, db, groups):
        """Use the same verified merge evidence for plan and worker dispatch."""
        cards = {change.get("id"): change for group in groups for change in group["changes"]
                 if change["kind"] == "card" and change.get("id")}
        related = {}
        for identity, change in cards.items():
            if keeper_merge.MERGE_FROM in change["fields"]:
                donor = keeper_merge.parse_merge_from(change["fields"][keeper_merge.MERGE_FROM])["card_id"]
                related[identity] = donor
                related[donor] = identity
        return [{"changes": [
            self._merge_missing_evidence(db, change, cards.get(related[change["id"]]))
            if change["kind"] == "card" and not change["sources"]
            and change.get("id") in related else change
            for change in group["changes"]]} for group in groups]

    def _merge_missing_evidence(self, db, change, other):
        record = self._read_owner(db, "card", change["id"])
        for saved in record.get("sources", []):
            source = {field: saved.get(field) for field in _SOURCE_FIELDS}
            if type(source["material_version"]) is not int:
                continue
            try:
                self._pin_source(db, source)
            except _Returned:
                continue
            return {**change, "sources": [source]}
        if other is not None and other["sources"]:
            return {**change, "sources": other["sources"][:1]}
        return change

    def _supplement_duplicate_card(self, db, entry):
        """An exact-title creation with genuinely new citations supplements existing.

        The existing Card keeps every field and its content. Only new exact
        citations are added, through an ordinary owner update. An unchanged
        same-evidence creation is left to the existing duplicate reply, and a
        near name never reaches this path.
        """
        if entry["kind"] != "card" \
                or entry["proposal"]["action"] != "create":
            return None
        label = _normalized_label(self._creation_label(entry))
        matches = [record for record in self.cards.query_all_in_transaction(db)
                   if record.get("status") == "active" and record.get("known")
                   and _normalized_label(record.get("title") or "") == label]
        if len(matches) != 1:
            return None
        record = matches[0]
        existing_keys = {keeper_merge.source_key(source)
                         for source in record.get("sources") or []}
        additions = [source for source in entry["proposal"]["sources"]
                     if keeper_merge.source_key(source) not in existing_keys]
        if not additions:
            return None
        sources = keeper_merge.union_sources(
            record.get("sources"), [self._card_source(source, source)
                                    for source in additions])
        if len(sources) > 16:
            raise CommandError("invalid_card", "来源数量超出限制")
        updated = copy.deepcopy(record)
        updated["sources"] = sources
        updated["provenance"], updated["source_pending"] = _card_provenance(sources)
        updated["known"] = True
        updated["version"] = record["version"] + 1
        reason = {"reason": entry["proposal"]["reason"]}
        proposal = self._proposal_change(
            "card", "update", record["card_id"], record["version"],
            record, updated, reason, additions)
        proposal[keeper_merge.ADD_SOURCES] = True
        proposal["supplemented_title"] = label
        return {"kind": "card", "id": record["card_id"],
                "before": record, "after": updated, "proposal": proposal}

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
            records = self.cards.query_all_in_transaction(db)
            records = [record for record in records
                       if record.get("status") == "active" and record.get("known")]
        elif kind == "theme":
            records = KnowledgeThemeService.query_all_in_transaction(db)
            records = [record for record in records
                       if record.get("status") == "active" and record.get("known")]
        else:
            records = self.cards.query_frontiers_in_transaction(db)
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
        proposal["sources"] = [self._theme_source(source, reference)
                               for source, reference in citations]
        if not proposal["sources"] and kind == "theme" and action != "delete" \
                and "evidence" in change["fields"]:
            existing = {item["evidence_id"] for item in
                        (proposal["before"] or {}).get("evidence", [])}
            proposal["sources"] = [item["source"] for item in
                                   proposal["after"]["evidence"]
                                   if item["evidence_id"] not in existing]
        if not proposal["sources"]:
            raise _Returned("missing_evidence")
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
        merge_value = fields_in.get(keeper_merge.MERGE_FROM)
        add_value = fields_in.get(keeper_merge.ADD_SOURCES)
        editable = {name: value for name, value in fields_in.items()
                    if name not in (keeper_merge.MERGE_FROM,
                                    keeper_merge.ADD_SOURCES)}
        if set(editable) - _CARD_FIELDS:
            raise CommandError("invalid_command", "Unsupported card fields")
        merge_from = keeper_merge.parse_merge_from(merge_value) \
            if merge_value is not None else None
        add_sources = keeper_merge.parse_add_sources(add_value) \
            if add_value is not None else None
        if merge_from is not None and add_sources is not None:
            raise CommandError(
                "invalid_command", "A merge and an explicit source addition differ")
        if merge_from is not None and editable:
            raise CommandError(
                "invalid_command",
                "A merge derives every editable field from both pinned cards")
        if action == "delete":
            if merge_from is not None or add_sources is not None:
                raise CommandError(
                    "invalid_command", "A delete carries no merge or source fields")
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
        fields = self.cards.term_fields(editable)
        if action == "create":
            if merge_from is not None or add_sources is not None:
                raise CommandError(
                    "invalid_command", "A creation cannot merge or add sources")
            if "title" not in fields or "content" not in fields:
                raise CommandError("invalid_card", "title and content are required")
            if not citations:
                raise _Returned("missing_evidence")
            relations = self.cards.term_relations(db, None, fields.get("relations", []))
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
        if "sources" in editable:
            raise CommandError("invalid_command", "Sources belong on the change")
        relations = card["relations"]
        if "relations" in fields:
            relations = self.cards.term_relations(db, card["card_id"], fields["relations"])
        requested_sources = [self._card_source(source, reference)
                             for source, reference in citations]
        if merge_from is not None:
            if merge_from["card_id"] == card["card_id"]:
                raise CommandError("invalid_command", "A card cannot merge into itself")
            donor = self._require_owner(db, "card", {
                "id": merge_from["card_id"],
                "expected_version": merge_from["version"],
            })
            if donor["status"] != "active" or not donor["known"]:
                raise CommandError(
                    "invalid_transition", "A merge donor must be an active Card")
            sources = keeper_merge.merge_source_union(
                card.get("sources"),
                self._pin_merge_card_sources(db, donor),
                requested_sources)
            if len(sources) > 16:
                raise CommandError("invalid_card", "来源数量超出限制")
            updated = keeper_merge.survivor_after(card, donor, sources)
            editable_merge = {key: updated[key] for key in _CARD_FIELDS}
            self.cards.term_fields(editable_merge)
            self.cards.term_relations(db, card["card_id"], updated["relations"])
            updated["provenance"], updated["source_pending"] = _card_provenance(sources)
            proposal = self._proposal_change(
                "card", "update", card["card_id"], change["expected_version"],
                card, updated, change, sources)
            proposal["merge_from"] = {**merge_from, "title": donor["title"]}
            return proposal
        if not citations and not card["sources"]:
            raise _Returned("missing_evidence")
        updated = copy.deepcopy(card)
        updated.update(fields)
        updated["relations"] = relations
        if add_sources:
            sources = keeper_merge.union_sources(
                card.get("sources"), requested_sources)
            if len(sources) > 16:
                raise CommandError("invalid_card", "来源数量超出限制")
            updated["sources"] = sources
            updated["provenance"], updated["source_pending"] = _card_provenance(sources)
        updated["known"] = True
        updated["version"] = card["version"] + 1
        proposal = self._proposal_change(
            "card", "update", card["card_id"], change["expected_version"],
            card, updated, change, updated["sources"] if add_sources
            else change["sources"])
        if add_sources:
            proposal[keeper_merge.ADD_SOURCES] = True
            if change.get("supplemented_title"):
                proposal["supplemented_title"] = change["supplemented_title"]
        return proposal

    def _pin_merge_card_sources(self, db, card):
        """Re-verify every historical card pin before it joins another card.

        The current owner must still expose the exact material version and
        digest this card pinned. A removed or metadata-sensitive historical pin
        is a conflict, never a silent rebind to the latest material.
        """
        sources = card.get("sources") or []
        for source in sources:
            try:
                self.materials.validate_knowledge_pin(db, source)
            except CommandError as error:
                raise CommandError(
                    "version_conflict",
                    "A historical card citation no longer matches") from error
        return sources

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
            evidence = self._theme_evidence(
                db, fields["evidence"], theme["evidence"], merge=True)
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

    def _theme_evidence(self, db, entries, current, *, merge=False):
        existing = {entry["evidence_id"]: entry for entry in current}
        if merge:
            return self._merge_theme_evidence(db, entries, current, existing)
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

    def _merge_theme_evidence(self, db, entries, current, existing):
        """Append new evidence and preserve existing citations and directions."""
        resolved = [copy.deepcopy(entry) for entry in current]
        by_id = {entry["evidence_id"]: entry for entry in resolved}
        seen = {self._evidence_key(entry) for entry in current}
        for entry in entries:
            evidence_id = entry.get("evidence_id")
            source = entry["source"]
            if evidence_id is not None:
                if evidence_id not in existing:
                    raise CommandError("not_found", "Unknown evidence")
                previous = by_id[evidence_id]
                pinned = previous["source"]
                if entry["stance"] != previous["stance"] or any(
                        source.get(name, pinned.get(name)) != pinned.get(name)
                        for name in ("material_id", "locator", "excerpt", "material_version")):
                    raise CommandError("invalid_evidence",
                                       "A saved evidence ID keeps its citation and direction")
                previous["reason"] = entry["reason"]
                continue
            if "material_version" not in source:
                raise CommandError(
                    "invalid_command", "New evidence requires a material version")
            reference = self._pin_source(db, source)
            pinned_source = self._theme_source(source, reference)
            item = {"evidence_id": str(uuid.uuid4()),
                    "stance": entry["stance"], "reason": entry["reason"],
                    "source": pinned_source}
            key = self._evidence_key(item)
            if key not in seen:
                resolved.append(item)
                seen.add(key)
        if len(resolved) > _MAX_EVIDENCE:
            raise CommandError("invalid_theme", "Evidence exceeds the Theme limit")
        return resolved

    @staticmethod
    def _evidence_key(entry):
        source = entry["source"]
        anchor = source.get("anchor") or {}
        return (entry["stance"], source.get("material_id"),
                source.get("material_version"), source.get("content_hash"),
                anchor.get("id") or source.get("locator"), source.get("excerpt"))

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

    @staticmethod
    def _merge_marker(entry):
        if not isinstance(entry, dict):
            return None
        return (entry.get("proposal") or {}).get(keeper_merge.MERGE_FROM)

    def _inbound_redirects(self, db, donor_id, survivor_id, survivor_version):
        """Active Cards and Themes that must explicitly replace the donor."""
        expected = {}
        for record in self.cards.query_all_in_transaction(db):
            if record.get("status") != "active" or not record.get("known") \
                    or record.get("card_id") in (donor_id, survivor_id):
                continue
            if donor_id in (record.get("relations") or []):
                expected[("card", record["card_id"])] = (
                    "relations",
                    keeper_merge.redirect_relations(
                        record, donor_id, survivor_id))
        for record in KnowledgeThemeService.query_all_in_transaction(db):
            if record.get("status") != "active" or not record.get("known"):
                continue
            if any(relation.get("card_id") == donor_id
                   for relation in record.get("related_cards") or []):
                expected[("theme", record["theme_id"])] = (
                    "related_cards",
                    keeper_merge.redirect_related_cards(
                        record, donor_id, survivor_id, survivor_version))
        return expected

    def _validate_merge_component(self, db, component, *, at_approval=False):
        """One complete, connected merge: survivor, donor archive and redirects."""
        merges = [entry for entry in component
                  if entry.get("kind") == "card"
                  and self._merge_marker(entry) is not None]
        if not merges:
            return

        def fail(detail):
            if at_approval:
                raise CommandError("incomplete_merge", detail)
            raise _Returned("incomplete_merge", [{
                "problem": "incomplete_merge", "detail": detail}])

        if len(merges) != 1:
            fail("A component may contain one merge only")
        merge = merges[0]
        marker = self._merge_marker(merge)
        donor_id = marker["card_id"]
        survivor_id = merge["id"]
        donor = next((entry for entry in component
                      if entry.get("kind") == "card"
                      and entry.get("id") == donor_id
                      and (entry.get("proposal") or {}).get("action") == "delete"),
                     None)
        if donor is None:
            fail("A merge requires the donor Card archive in the same component")
        if donor["proposal"].get("expected_version") != marker["version"]:
            fail("The donor archive must pin merge_from.version")
        if donor["proposal"].get("id") != donor_id:
            fail("The donor archive targets another Card")
        introduced = set(merge["proposal"]["after"]["relations"]) - set(merge["before"]["relations"])
        for identity in sorted(introduced):
            related = self._read_owner(db, "card", identity)
            if related.get("status") != "active" or not related.get("known"):
                fail("A newly carried relationship needs an active Card")
        if at_approval:
            survivor_current = self._read_owner(db, "card", survivor_id)
            donor_current = self._read_owner(db, "card", donor_id)
            if survivor_current["version"] \
                    != merge["proposal"].get("expected_version") \
                    or donor_current["version"] != marker["version"]:
                raise CommandError(
                    "version_conflict", "A merge pin changed before approval")
            if donor_current.get("status") != "active" \
                    or not donor_current.get("known"):
                fail("The donor is no longer an active Card")
            # Re-verify both historical pin sets from this transaction snapshot;
            # an unverifiable pin is a conflict, never a silent rebind.
            self._pin_merge_card_sources(db, donor_current)
        expected = self._inbound_redirects(
            db, donor_id, survivor_id, merge["before"]["version"])
        redirects = {(entry.get("kind"), entry.get("id")): entry
                     for entry in component
                     if entry is not merge and entry is not donor}
        for key, (field, value) in expected.items():
            entry = redirects.get(key)
            if entry is None \
                    or (entry.get("proposal") or {}).get("action") != "update":
                fail("Every active inbound relationship needs an explicit redirect")
            actual = (entry["proposal"].get("after") or {}).get(field)
            if not self._redirect_matches(field, actual, value):
                fail("A redirect must replace the donor and preserve every other edge")
        for entry in redirects.values():
            after = (entry.get("proposal") or {}).get("after") or {}
            if after.get("status") != "active":
                continue
            if entry.get("kind") == "card" \
                    and donor_id in (after.get("relations") or []):
                fail("A redirect still references the archived donor")
            if entry.get("kind") == "theme" \
                    and any(relation.get("card_id") == donor_id
                            for relation in after.get("related_cards") or []):
                fail("A redirect still references the archived donor")

    @staticmethod
    def _redirect_matches(field, actual, expected):
        """Order-insensitive, duplicate-free redirect comparison."""
        if not isinstance(actual, list):
            return False
        if field == "relations":
            return len(actual) == len(set(actual)) \
                and set(actual) == set(expected)
        values = {}
        for item in actual:
            if not isinstance(item, dict) or "card_id" not in item \
                    or item["card_id"] in values:
                return False
            values[item["card_id"]] = item.get("version")
        return values == {item["card_id"]: item.get("version")
                          for item in expected}

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
            for source, target in ((first, second), (second, first)):
                marker = self._merge_marker(source)
                if marker is not None and marker["card_id"] == target["id"]:
                    return True
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
            "provenance": "editor" if command.get("report_kind") == "editor" else "editor_test",
            "child_id": receipt.get("child_id"),
            **({"review_reason": receipt["review_reason"]} if receipt.get("review_reason") else {}),
            "changes": [copy.deepcopy(entry["proposal"]) for entry in entries],
            **{field: command[field] for field in _RECONSIDERATION_FIELDS if field in command},
        }

    def session_term_proposals(self, task_id, *, token):
        self._authorize(token)
        with self._connect() as db:
            rows = db.execute("""
                SELECT result FROM keeper_proposals WHERE json_extract(result,'$.task_id')=?
                    AND json_extract(result,'$.term_expires_at') IS NOT NULL
                ORDER BY rowid
            """, (task_id,)).fetchall()
        proposals = [json.loads(row[0]) for row in rows]
        for proposal in proposals:
            if self.cards.reviewed_terms_expired(proposal):
                proposal["status"] = "expired"
        return proposals

    def list(self, *, token, status="pending"):
        """List persisted proposals with a stable review status."""
        self._authorize(token)
        if status not in ("pending", "approved", "rejected", "returned",
                          "revising", "cancelled"):
            raise CommandError("invalid_command", "Unknown proposal status")
        with self._connect() as db:
            db.execute("BEGIN")
            rows = db.execute("SELECT result FROM keeper_proposals ORDER BY rowid").fetchall()
        proposals = [json.loads(row[0]) for row in rows]
        return [proposal for proposal in proposals if proposal["status"] == status
                and not self.cards.reviewed_terms_expired(proposal)]

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
        proposal = json.loads(row[0])
        if self.cards.reviewed_terms_expired(proposal):
            proposal["status"] = "expired"
        return proposal

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
