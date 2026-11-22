"""Whole-group undo of one approved Keeper proposal (K9 #213).

An approval receipt is immutable and remains the only source of truth. The
deterministic per-owner operation identity recorded by :mod:`keeper_decisions`
locates each owner's committed snapshot and its true prior version, so undo
never trusts the proposal preview fields or a caller-supplied snapshot.

Preview and commit are conservative: every approved target version must still
equal the owner's current version. Any later owner edit is shown as a per-target
approved/current/inverse difference and blocks the whole group. ``commit``
re-derives that plan under one ``BEGIN IMMEDIATE`` before writing, then gives
every target a new owner version through the shared history writer. The
original approval is never mutated; the persisted proposal gains a separate
``undo`` receipt while its terminal ``approved`` status stays intact.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import secrets
import time

from hostrun.agent_service import CommandError
from hostrun.keeper_decisions import owner_operation_id
from hostrun.knowledge_history import KnowledgeHistoryService, get_path, validate_reason

_SCHEMA_VERSION = 1
_MAX_ID_BYTES = 128
_MAX_EXPECTED_VERSIONS = 16
_KINDS = ("card", "theme", "frontier")
_PREVIEW_NAMESPACE = "horizon.keeper.group_undo.v1"
# Process-only secret: a preview token can never be minted by the renderer and
# a stale token dies with this process.
_PREVIEW_KEY = secrets.token_bytes(32)
_ARCHIVED_STATUS = {"card": "archived", "theme": "archived", "frontier": "deleted"}
_ACTIVE_STATUSES = {"card": ("active",), "theme": ("active",),
                    "frontier": ("following", "paused", "resolved")}
_RECEIPT_ACTIONS = ("create", "update", "archive", "delete")
_COMMIT_FIELDS = {"schema_version", "operation_id", "proposal_id", "revision",
                  "expected_versions", "preview_token", "reason"}
_PREVIEW_FIELDS = {"schema_version", "operation_id", "proposal_id", "revision"}


def _bounded_text(value, *, name):
    if not isinstance(value, str) or not value.strip() \
            or len(value.encode("utf-8")) > _MAX_ID_BYTES:
        raise CommandError("invalid_command", f"{name} must be a bounded identifier")
    return value


def _stable(value):
    """Drop volatile identity fields so a token binds content, not time."""
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    stable = copy.deepcopy(value)
    stable.pop("operation_id", None)
    stable.pop("updated_at", None)
    return stable


class KeeperGroupUndo:
    """Preview and commit one atomic reversal of an approved Keeper group."""

    def __init__(self, keeper):
        self.keeper = keeper
        self.services = {"card": keeper.cards, "theme": keeper.themes,
                         "frontier": keeper.frontiers}
        self.history = KnowledgeHistoryService(self.services)

    # ------------------------------------------------------------------
    # Command and approval validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate(command, *, commit):
        if not isinstance(command, dict):
            raise CommandError("invalid_command", "Undo command must be an object")
        allowed = _COMMIT_FIELDS if commit else _PREVIEW_FIELDS
        if set(command) - allowed:
            raise CommandError("invalid_command", "Undo contains unsupported fields")
        if type(command.get("schema_version")) is not int \
                or command["schema_version"] != _SCHEMA_VERSION:
            raise CommandError("unsupported_schema", "Unsupported schema_version")
        operation_id = _bounded_text(command.get("operation_id"),
                                     name="operation_id")
        proposal_id = _bounded_text(command.get("proposal_id"),
                                    name="proposal_id")
        revision = command.get("revision")
        if type(revision) is not int or revision < 1:
            raise CommandError("invalid_command", "revision must be a positive integer")
        normalized = {"schema_version": _SCHEMA_VERSION,
                      "operation_id": operation_id,
                      "proposal_id": proposal_id, "revision": revision}
        if commit:
            expected = command.get("expected_versions")
            if not isinstance(expected, list) or not expected \
                    or len(expected) > _MAX_EXPECTED_VERSIONS:
                raise CommandError(
                    "invalid_command",
                    "expected_versions must be a bounded nonempty list")
            for version in expected:
                if type(version) is not int or version < 1:
                    raise CommandError(
                        "invalid_command",
                        "expected_versions entries must be positive integers")
            token = command.get("preview_token")
            if not isinstance(token, str) or not token or not token.isascii():
                raise CommandError("invalid_command", "preview_token required")
            normalized["expected_versions"] = list(expected)
            normalized["preview_token"] = token
            reason = validate_reason(command.get("reason"), code="invalid_command")
            if reason is not None:
                normalized["reason"] = reason
        return normalized

    @staticmethod
    def _approval(proposal, command):
        if proposal.get("status") != "approved":
            raise CommandError("invalid_transition",
                               "Only an approved Keeper group can be undone")
        decision = proposal.get("decision")
        if not isinstance(decision, dict) or decision.get("decision") != "approve" \
                or decision.get("status") != "approved":
            raise CommandError("invalid_transition",
                               "The Keeper group has no committed approval")
        if type(decision.get("revision")) is not int \
                or decision["revision"] != command["revision"] \
                or proposal.get("revision") != command["revision"]:
            raise CommandError("version_conflict",
                               "Keeper proposal revision changed")
        applied = decision.get("applied")
        changes = proposal.get("changes")
        if not isinstance(applied, list) or not applied \
                or not isinstance(changes, list) or len(changes) != len(applied):
            raise CommandError("snapshot_conflict",
                               "The approval receipt is incomplete")
        for index, entry in enumerate(applied):
            if not isinstance(entry, dict) or entry.get("kind") not in _KINDS \
                    or not isinstance(entry.get("id"), str) \
                    or type(entry.get("version")) is not int:
                raise CommandError("snapshot_conflict",
                                   "The approval receipt is corrupt")
            change = changes[index]
            if not isinstance(change, dict) \
                    or change.get("kind") != entry["kind"] \
                    or (change.get("id") is not None
                        and change.get("id") != entry["id"]):
                raise CommandError("snapshot_conflict",
                                   "The approval receipt is corrupt")
        return decision

    # ------------------------------------------------------------------
    # Owner receipt and history derivation
    # ------------------------------------------------------------------

    @staticmethod
    def _owner_receipt(db, meta, operation_id):
        row = db.execute(
            f"SELECT request,result FROM {meta['operations']}"
            " WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            raise CommandError("snapshot_conflict",
                               "An approved owner receipt is missing")
        try:
            request = json.loads(row[0])
            snapshot = json.loads(row[1])
        except (TypeError, ValueError) as error:
            raise CommandError("snapshot_conflict",
                               "An approved owner receipt is corrupt") from error
        if not isinstance(request, dict) or not isinstance(snapshot, dict):
            raise CommandError("snapshot_conflict",
                               "An approved owner receipt is corrupt")
        if request.get("origin") != "keeper" \
                or request.get("action") not in _RECEIPT_ACTIONS:
            raise CommandError("snapshot_conflict",
                               "The owner receipt is not a Keeper approval")
        return request["action"], snapshot

    @staticmethod
    def _history(db, meta, identifier):
        rows = db.execute(
            f"SELECT result FROM {meta['operations']} WHERE json_extract(result, ?)=?"
            " ORDER BY rowid", (f"$.{meta['id_field']}", identifier)).fetchall()
        by_version = {}
        for (result,) in rows:
            try:
                snapshot = json.loads(result)
            except (TypeError, ValueError):
                continue
            if not isinstance(snapshot, dict) \
                    or snapshot.get(meta["id_field"]) != identifier:
                continue
            version = snapshot.get("version")
            if type(version) is int and version not in by_version:
                by_version[version] = snapshot
        return by_version

    @staticmethod
    def _is_active(kind, snapshot):
        return snapshot.get("status") in _ACTIVE_STATUSES[kind]

    def _read_target(self, db, proposal, decision, changes, index, entry,
                     command):
        kind, identifier = entry["kind"], entry["id"]
        service = self.services[kind]
        meta = service.HISTORY
        operation_id = owner_operation_id(
            proposal["proposal_id"], decision["revision"], index)
        action, approved = self._owner_receipt(db, meta, operation_id)
        if approved.get(meta["id_field"]) != identifier \
                or approved.get("version") != entry["version"] \
                or approved.get("operation_id") != operation_id:
            raise CommandError("snapshot_conflict",
                               "The owner receipt does not bind its target")
        if action in ("create", "update") \
                and not self._is_active(kind, approved):
            raise CommandError("snapshot_conflict",
                               "The approved owner snapshot has an unexpected status")
        if action in ("archive", "delete") \
                and approved.get("status") != _ARCHIVED_STATUS[kind]:
            raise CommandError("snapshot_conflict",
                               "The approved owner snapshot has an unexpected status")

        current = service._read(db, identifier)
        if current.get(meta["id_field"]) != identifier:
            raise CommandError("snapshot_conflict",
                               "The owner record identity changed")
        if current.get("version") == entry["version"] \
                and current.get("operation_id") != operation_id:
            # Same version but another receipt produced it: the ledger itself
            # is inconsistent. A later version is ordinary drift, reported by
            # the conservative per-target plan instead of hiding the diff.
            raise CommandError("snapshot_conflict",
                               "The owner record was not produced by its receipt")
        history = self._history(db, meta, identifier)
        prior = None
        if action == "create":
            undo_action = "archive"
            projected = copy.deepcopy(current)
            projected["status"] = _ARCHIVED_STATUS[kind]
            if kind in ("card", "theme"):
                projected["known"] = False
        else:
            undo_action = "restore"
            prior = history.get(entry["version"] - 1)
            if prior is None:
                raise CommandError("snapshot_conflict",
                                   "The historical owner snapshot is missing")
            if prior.get(meta["id_field"]) != identifier \
                    or not self._is_active(kind, prior):
                raise CommandError("snapshot_conflict",
                                   "The historical owner snapshot is invalid")
            projected = copy.deepcopy(prior)
            if kind == "theme":
                projected["evidence_sufficiency"] = prior.get(
                    "evidence_sufficiency")
            if kind in ("card", "theme"):
                projected["known"] = True
        projected["version"] = current["version"] + 1
        return {
            "kind": kind,
            "id": identifier,
            "action": undo_action,
            "approval_action": action,
            "approved_version": entry["version"],
            "current_version": current["version"],
            "approved": approved,
            "current": current,
            "prior": prior,
            "projected": projected,
            "can_undo": True,
            "code": None,
        }

    # ------------------------------------------------------------------
    # Cross-owner link safety
    # ------------------------------------------------------------------

    def _pin_relations(self, db, targets):
        by_card = {target["id"]: target for target in targets
                   if target["kind"] == "card"}
        for target in targets:
            if target["kind"] == "theme" and target["action"] == "restore":
                pinned = []
                current = target["current"]
                existing = {(item["card_id"], item["version"])
                            for item in current.get("related_cards", [])} \
                    if self._is_active("theme", current) else set()
                for relation in target["projected"].get("related_cards", []) or []:
                    card_id = relation.get("card_id")
                    card_target = by_card.get(card_id)
                    unchanged = (card_id, relation["version"]) in existing
                    if card_target is not None:
                        if card_target["projected"].get("status") != "active":
                            raise CommandError(
                                "invalid_relation",
                                "A restored Theme would reference an archived Card")
                        version = (relation["version"]
                                   if unchanged and self._is_active("card", card_target["current"])
                                   else card_target["projected"]["version"])
                    elif unchanged:
                        version = relation["version"]
                    else:
                        card = self.services["card"]._read(db, card_id)
                        if card.get("status") != "active" \
                                or not card.get("known") \
                                or card.get("version") != relation.get("version"):
                            raise CommandError(
                                "invalid_relation",
                                "A related Card changed since the Theme snapshot")
                        version = relation["version"]
                    pinned.append({"card_id": card_id, "version": version})
                target["projected"]["related_cards"] = pinned
            elif target["kind"] == "card" and target["action"] == "restore":
                current = target["current"]
                existing = set(current.get("relations", [])) \
                    if self._is_active("card", current) else set()
                for card_id in target["projected"].get("relations", []) or []:
                    card_target = by_card.get(card_id)
                    if card_target is not None:
                        related = card_target["projected"]
                    elif card_id in existing:
                        continue
                    else:
                        related = self.services["card"]._read(db, card_id)
                    if related.get("status") != "active" or not related.get("known"):
                        raise CommandError(
                            "invalid_relation",
                            "A restored Card would reference an archived Card")

    @staticmethod
    def _incoming_links(db, cards, themes, archived, card_targets, theme_targets):
        """No external active record may still point at a newly archived Card."""
        for card in cards.query_all_in_transaction(db):
            if card.get("status") != "active" or card["card_id"] in card_targets:
                continue
            if archived & set(card.get("relations") or []):
                raise CommandError(
                    "invalid_relation",
                    "An external Card still links to a Card this undo archives")
        for theme in themes.query_all_in_transaction(db):
            if theme.get("status") != "active" or theme["theme_id"] in theme_targets:
                continue
            referenced = {relation.get("card_id")
                          for relation in theme.get("related_cards") or []
                          if isinstance(relation, dict)}
            if archived & referenced:
                raise CommandError(
                    "invalid_relation",
                    "An external Theme still links to a Card this undo archives")

    def _validate_links(self, db, targets):
        self._pin_relations(db, targets)
        archived = {target["id"] for target in targets
                    if target["kind"] == "card"
                    and target["projected"].get("status") != "active"}
        if archived:
            self._incoming_links(
                db, self.services["card"], self.services["theme"], archived,
                {target["id"] for target in targets if target["kind"] == "card"},
                {target["id"] for target in targets if target["kind"] == "theme"})

    # ------------------------------------------------------------------
    # Plan, preview token and display
    # ------------------------------------------------------------------

    def _plan(self, db, proposal, command):
        result = {
            "schema_version": _SCHEMA_VERSION,
            "operation_id": command["operation_id"],
            "proposal_id": command["proposal_id"],
            "revision": command["revision"],
            "can_undo": False,
            "code": None,
            "expected_versions": [],
            "preview_token": None,
            "targets": [],
        }
        if proposal.get("undo") is not None:
            result["code"] = "already_undone"
            return result
        try:
            decision = self._approval(proposal, command)
        except CommandError as error:
            result["code"] = error.code
            result["message"] = str(error)
            return result
        result["expected_versions"] = [entry["version"]
                                       for entry in decision["applied"]]
        try:
            for index, entry in enumerate(decision["applied"]):
                result["targets"].append(self._read_target(
                    db, proposal, decision, proposal["changes"], index, entry,
                    command))
        except CommandError as error:
            result["targets"] = []
            result["can_undo"] = False
            result["code"] = error.code
            result["message"] = str(error)
            return result

        removed = {kind: self.services[kind].history_removed(db)
                   for kind in _KINDS}
        if any(target["current_version"] != target["approved_version"]
               for target in result["targets"]):
            result["code"] = "version_conflict"
            result["message"] = "This Keeper group no longer matches the owners"
        else:
            try:
                self._validate_links(db, result["targets"])
            except CommandError as error:
                result["code"] = error.code
                result["message"] = str(error)
            else:
                result["can_undo"] = True
        for target in result["targets"]:
            if target["current_version"] != target["approved_version"]:
                target["code"] = "version_conflict"
            target["can_undo"] = result["can_undo"]

        self._display(result["targets"], removed)
        if result["can_undo"]:
            result["preview_token"] = self._preview_token(
                proposal, decision, result["targets"])
        return result

    @staticmethod
    def _changes(target, editable, removed, project):
        approved = project(target["approved"], removed)
        current = project(target["current"], removed)
        inverse = project(target["projected"], removed)
        paths = list(editable)
        if ("status",) not in paths:
            paths.append(("status",))
        changes = []
        for path in paths:
            approved_value = get_path(approved, path)
            current_value = get_path(current, path)
            inverse_value = get_path(inverse, path)
            if approved_value == current_value == inverse_value:
                continue
            changes.append({
                "field": ".".join(path),
                "approved": approved_value,
                "current": current_value,
                "inverse": inverse_value,
            })
        return changes

    def _display(self, targets, removed):
        for target in targets:
            service = self.services[target["kind"]]
            project = service.history_project
            target["changes"] = self._changes(
                target, service.HISTORY["editable"],
                removed[target["kind"]], project)
            # The display honours removed material snapshots; the raw projected
            # snapshot keeps the historical citation that commit restores.
            target["display"] = project(target["projected"],
                                        removed[target["kind"]])

    @staticmethod
    def _public_target(target):
        return {
            "kind": target["kind"],
            "id": target["id"],
            "action": target["action"],
            "approval_action": target["approval_action"],
            "approved_version": target["approved_version"],
            "current_version": target["current_version"],
            "projected_version": target["projected"]["version"],
            "can_undo": target["can_undo"],
            "code": target["code"],
            "projected": target["display"],
            "changes": target["changes"],
        }

    @staticmethod
    def _preview_token(proposal, decision, targets):
        payload = {
            "namespace": _PREVIEW_NAMESPACE,
            "proposal_id": proposal["proposal_id"],
            "revision": decision["revision"],
            "decision_operation_id": decision["operation_id"],
            "targets": [{
                "kind": target["kind"],
                "id": target["id"],
                "action": target["action"],
                "approved_version": target["approved_version"],
                "current_version": target["current_version"],
                "projected_version": target["projected"]["version"],
                "projected": _stable(target["display"]),
                "changes": target["changes"],
            } for target in targets],
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        return hmac.new(_PREVIEW_KEY, encoded, hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------
    # Public seams
    # ------------------------------------------------------------------

    def preview(self, command, *, token, principal):
        self.keeper._authorize(token)
        self.keeper._check_explorer(principal)
        command = self._validate(command, commit=False)
        with self.keeper._connect() as db:
            db.execute("BEGIN")
            proposal = self.keeper._load_proposal(db, command["proposal_id"])
            plan = self._plan(db, proposal, command)
        plan["targets"] = [self._public_target(target)
                           for target in plan["targets"]]
        return plan

    def commit(self, command, *, token, principal):
        # Authentication precedes even the replay lookup: an unauthenticated
        # caller can never learn whether an operation id was committed.
        self.keeper._authorize(token)
        self.keeper._check_explorer(principal)
        command = self._validate(command, commit=True)
        encoded = json.dumps({"action": "group_undo", "command": command},
                             sort_keys=True, ensure_ascii=False)
        notifications = []
        with self.keeper._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT request,result FROM keeper_operations WHERE operation_id=?",
                (command["operation_id"],)).fetchone()
            if row is not None:
                if row[0] != encoded:
                    raise CommandError("operation_conflict",
                                       "operation_id conflicts with a previous undo")
                return json.loads(row[1])

            proposal = self.keeper._load_proposal(db, command["proposal_id"])
            if proposal.get("undo") is not None:
                raise CommandError("already_undone",
                                   "This Keeper group has already been undone")
            plan = self._plan(db, proposal, command)
            if not plan["can_undo"]:
                error = CommandError(
                    plan["code"] or "invalid_transition",
                    plan.get("message", "This Keeper group cannot be undone"))
                error.details = {**plan, "targets": [
                    self._public_target(target) for target in plan["targets"]]}
                raise error
            if list(command["expected_versions"]) != plan["expected_versions"]:
                raise CommandError("version_conflict",
                                   "expected_versions do not match the approval")
            if not hmac.compare_digest(command["preview_token"],
                                       plan["preview_token"]):
                raise CommandError("preview_conflict",
                                   "The undo preview changed; review it again")

            decision = proposal["decision"]
            now = int(time.time())
            applied = {}
            # Cards are restored before Themes so a projected Theme relation
            # can be pinned to the Card version created in this transaction.
            order = {"card": 0, "theme": 1, "frontier": 2}
            for index, entry in sorted(
                    enumerate(decision["applied"]),
                    key=lambda item: (order[item[1]["kind"]], item[0])):
                target = plan["targets"][index]
                record = copy.deepcopy(target["projected"])
                owner_operation_id_value = (
                    f"group-undo:{command['operation_id']}:{index}")
                record["operation_id"] = owner_operation_id_value
                record["updated_at"] = now
                service = self.services[target["kind"]]
                meta = service.HISTORY
                history_command = {"proposal_id": proposal["proposal_id"],
                                   "revision": decision["revision"],
                                   "target_version": target["approved_version"]}
                if "reason" in command:
                    history_command["reason"] = command["reason"]
                self.history.write_version(
                    db, meta, record,
                    operation_id=owner_operation_id_value, action="group_undo",
                    command=history_command,
                    event_type=meta["event_type"].rsplit(".", 1)[0] + ".group_undo",
                    origin="keeper")
                applied[index] = {"kind": target["kind"], "id": target["id"],
                                  "version": record["version"],
                                  "status": record["status"]}
                notifications.append((target["kind"], record))

            receipt = {
                "schema_version": _SCHEMA_VERSION,
                "operation_id": command["operation_id"],
                "proposal_id": proposal["proposal_id"],
                "revision": decision["revision"],
                "status": "undone",
                "undo_of": decision["operation_id"],
                "applied": [applied[index] for index in range(len(applied))],
                "undone_at": now,
            }
            if "reason" in command:
                receipt["reason"] = command["reason"]
            proposal = copy.deepcopy(proposal)
            proposal["undo"] = receipt
            db.execute("UPDATE keeper_proposals SET result=? WHERE proposal_id=?",
                       (json.dumps(proposal), proposal["proposal_id"]))
            db.execute("INSERT INTO keeper_operations VALUES(?,?,?)",
                       (command["operation_id"], encoded, json.dumps(receipt)))
            event = {
                "schema_version": _SCHEMA_VERSION,
                "type": "keeper.group_undo",
                "operation_id": command["operation_id"],
                "proposal_id": proposal["proposal_id"],
                "revision": decision["revision"],
                "status": "undone",
                "actor": "explorer",
                "undo_of": decision["operation_id"],
                "applied": receipt["applied"],
                "undone_at": now,
            }
            if "reason" in receipt:
                event["reason"] = receipt["reason"]
            db.execute("INSERT INTO keeper_audit(proposal_id,operation_id,event) "
                       "VALUES(?,?,?)",
                       (proposal["proposal_id"], command["operation_id"],
                        json.dumps(event)))
        for kind, record in notifications:
            self.keeper._notify_owner(kind, record)
        return receipt
