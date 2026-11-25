"""Decision planning for one whole-group Keeper approval or rejection.

The Keeper candidate in ``keeper_proposals`` is a preview, never a command:
it stores the owner before/after snapshots, the Explorer's reason and the
verified material references. This module converts that plan into the exact
owner commands used on the caller's transaction. It also owns decision-shape
validation and the deterministic per-owner operation identities, so retrying a
proposal revision can never apply one owner change twice.

Nothing here writes. ``KeeperService.decide`` owns the single BEGIN IMMEDIATE,
the read-only revalidation and the post-commit notifications.
"""

from __future__ import annotations

import copy

from hostrun.agent_service import CommandError

_SCHEMA_VERSION = 1
_MAX_ID_BYTES = 128
_MAX_REASON_BYTES = 500
_MAX_EXPECTED_VERSIONS = 16

_DECISION_FIELDS = {"schema_version", "operation_id", "proposal_id", "revision",
                    "expected_versions", "decision", "reason"}
_REQUIRED_FIELDS = _DECISION_FIELDS - {"reason"}
_KINDS = ("card", "theme", "frontier")
_CARD_FIELDS = ("title", "content", "aliases", "relations",
                "questionable")
_THEME_FIELDS = ("judgement", "conditions", "related_cards", "evidence")
_FRONTIER_FIELDS = ("question", "scope", "hard_constraints", "relations",
                    "status")
_KIND_ORDER = {"theme": 0, "card": 1, "frontier": 2}
_ID_FIELDS = {"card": "card_id", "theme": "theme_id",
              "frontier": "frontier_id"}
_EVIDENCE_ERRORS = {"not_found", "version_conflict", "invalid_source",
                    "ambiguous_source", "invalid_material", "snapshot_corrupt",
                    "snapshot_changed"}


def _bounded_text(value, *, name, byte_limit, required=True):
    if not isinstance(value, str) or len(value.encode("utf-8")) > byte_limit:
        raise CommandError("invalid_command", f"{name} exceeds its bound")
    if required and not value.strip():
        raise CommandError("invalid_command", f"{name} must not be empty")
    return value


def validate_decision(command):
    """Normalize the fixed Explorer decision command before any database read."""
    if not isinstance(command, dict):
        raise CommandError("invalid_command", "Decision must be an object")
    if type(command.get("schema_version")) is not int \
            or command["schema_version"] != _SCHEMA_VERSION:
        raise CommandError("unsupported_schema", "Unsupported schema_version")
    if set(command) - _DECISION_FIELDS or not _REQUIRED_FIELDS <= set(command):
        raise CommandError("invalid_command", "Decision contains unsupported fields")
    operation_id = _bounded_text(command["operation_id"],
                                 name="operation_id",
                                 byte_limit=_MAX_ID_BYTES)
    proposal_id = _bounded_text(command["proposal_id"],
                                name="proposal_id",
                                byte_limit=_MAX_ID_BYTES)
    revision = command["revision"]
    if type(revision) is not int or revision < 1:
        raise CommandError("invalid_command", "revision must be a positive integer")
    expected_versions = command["expected_versions"]
    if not isinstance(expected_versions, list) \
            or not expected_versions \
            or len(expected_versions) > _MAX_EXPECTED_VERSIONS:
        raise CommandError("invalid_command",
                           "expected_versions must be a bounded nonempty list")
    for version in expected_versions:
        if version is None:
            continue
        if type(version) is not int or version < 1:
            raise CommandError(
                "invalid_command",
                "expected_versions entries must be positive integers or null")
    decision = command["decision"]
    if decision not in ("approve", "reject"):
        raise CommandError("invalid_command", "decision must be approve or reject")
    normalized = {
        "schema_version": _SCHEMA_VERSION,
        "operation_id": operation_id,
        "proposal_id": proposal_id,
        "revision": revision,
        "expected_versions": list(expected_versions),
        "decision": decision,
    }
    if command.get("reason") is not None:
        reason = command["reason"]
        if not isinstance(reason, str):
            raise CommandError("invalid_command", "reason must be text")
        if len(reason.encode("utf-8")) > _MAX_REASON_BYTES:
            raise CommandError("invalid_command", "reason exceeds its bound")
        if reason.strip():
            normalized["reason"] = reason
    return normalized


def check_expected_versions(command, changes):
    """The displayed card's ordered pins must identify this exact revision."""
    expected = command["expected_versions"]
    if len(expected) != len(changes):
        raise CommandError("version_conflict",
                           "expected_versions do not match the proposal")
    for change, version in zip(changes, expected):
        if change["action"] == "create":
            if version is not None:
                raise CommandError("version_conflict",
                                   "A creation has no expected version")
        elif version is None or version != change.get("expected_version"):
            raise CommandError("version_conflict",
                               "expected_versions do not match the proposal")


def owner_operation_id(proposal_id, revision, index):
    """A stable owner receipt identity for one change of one revision."""
    return f"keeper:{proposal_id}:{revision}:{index}"


def apply_order(changes):
    """Themes first, then Cards, then Frontiers; ties keep proposal order."""
    return sorted(enumerate(changes),
                  key=lambda item: (_KIND_ORDER[item[1]["kind"]], item[0]))


def validate_relations_against_deletions(changes):
    """A group must not introduce new Theme links to Cards it also archives."""
    archived_cards = {change["id"] for change in changes
                      if change["kind"] == "card" and change["action"] == "delete"}
    for change in changes:
        if change["kind"] != "theme" or change["action"] == "delete":
            continue
        earlier = {(relation["card_id"], relation["version"])
                   for relation in (change["before"] or {}).get("related_cards", [])}
        for relation in change["after"].get("related_cards", []):
            pair = (relation["card_id"], relation["version"])
            if pair not in earlier and relation["card_id"] in archived_cards:
                raise CommandError("invalid_relation", "The group archives a Card in a new Theme relationship")


def _reviewed_sources(change):
    yield from change.get("sources", [])
    if change["kind"] != "theme" or change["action"] == "delete":
        return
    old = {entry["evidence_id"]: entry["source"]
           for entry in (change["before"] or {}).get("evidence", [])}
    for evidence in change["after"].get("evidence", []):
        source = evidence["source"]
        if source != old.get(evidence.get("evidence_id")):
            yield source


def validate_material_references(materials, db, changes):
    """Re-read every stored citation; the exact version, digest and anchor hold."""
    for change in changes:
        for source in _reviewed_sources(change):
            citation = {
                "material_id": source.get("material_id"),
                "material_version": source.get("material_version"),
                "locator": source.get("locator"),
                "excerpt": source.get("excerpt"),
            }
            try:
                reference = materials.validate_citation(db, citation)
            except CommandError as error:
                if error.code in _EVIDENCE_ERRORS:
                    raise CommandError(
                        "invalid_evidence",
                        "A saved material reference no longer matches") from error
                raise
            if (reference.get("material_id") != source.get("material_id")
                    or reference.get("material_version")
                    != source.get("material_version")
                    or reference.get("content_hash") != source.get("content_hash")
                    or reference.get("anchor") != source.get("anchor")
                    or reference.get("snapshot_id") != source.get("snapshot_id")):
                raise CommandError(
                    "invalid_evidence",
                    "A saved material reference no longer matches")


def applied_result(kind, record):
    return {"kind": kind, "id": record[_ID_FIELDS[kind]],
            "version": record["version"]}


def owner_command(kind, change, operation_id):
    """Build the exact command the owner service applies on our transaction."""
    action = change["action"]
    owner = {"schema_version": _SCHEMA_VERSION,
             "operation_id": operation_id,
             "reason": change["reason"]}
    if kind == "card":
        _card_command(change, owner, action)
    elif kind == "theme":
        _theme_command(change, owner, action)
    elif kind == "frontier":
        _frontier_command(change, owner, action)
    else:
        raise CommandError("invalid_command", "Unknown change kind")
    return action, owner


def _card_command(change, command, action):
    after = change["after"]
    if action == "create":
        for name in _CARD_FIELDS:
            if name in after:
                command[name] = copy.deepcopy(after[name])
        # The owner re-verifies and pins each citation; a candidate carries
        # derived display fields that are not part of the K2 input shape.
        command["sources"] = [
            {name: copy.deepcopy(source[name])
             for name in ("material_id", "locator", "excerpt")}
            for source in after["sources"]]
        return
    command["card_id"] = change["id"]
    command["expected_version"] = change["expected_version"]
    if action == "update":
        # Only editable values that really changed; omitting sources preserves
        # the committed card's original citations.
        before = change.get("before") or {}
        for name in _CARD_FIELDS:
            if before.get(name) != after.get(name):
                command[name] = copy.deepcopy(after.get(name))
        if before.get("sources") != after.get("sources"):
            # Merge and explicit supplementation add only owner-validated pins.
            command["sources"] = [
                {name: copy.deepcopy(source[name])
                 for name in ("material_id", "locator", "excerpt")}
                for source in after.get("sources", [])]


def _theme_command(change, command, action):
    after = change["after"]
    if action == "create":
        for name in _THEME_FIELDS:
            if name == "evidence":
                command[name] = _theme_evidence(after.get(name, []), ())
            elif name in after:
                command[name] = copy.deepcopy(after[name])
        return
    command["theme_id"] = change["id"]
    command["expected_version"] = change["expected_version"]
    if action == "update":
        before = change.get("before") or {}
        existing = {entry.get("evidence_id")
                    for entry in before.get("evidence", [])
                    if entry.get("evidence_id") is not None}
        for name in _THEME_FIELDS:
            if name == "evidence":
                if before.get("evidence") != after.get("evidence"):
                    command[name] = _theme_evidence(after.get("evidence", []),
                                                    existing)
            elif before.get(name) != after.get(name):
                command[name] = copy.deepcopy(after.get(name))


def _theme_evidence(entries, existing_ids):
    """Strip preview-only source fields and never assign a new evidence ID."""
    resolved = []
    for entry in entries:
        source = entry["source"]
        citation = {name: copy.deepcopy(source[name])
                    for name in ("material_id", "locator", "excerpt")
                    if name in source}
        if "material_version" in source:
            citation["material_version"] = source["material_version"]
        item = {"stance": entry["stance"], "reason": entry["reason"],
                "source": citation}
        evidence_id = entry.get("evidence_id")
        if evidence_id in existing_ids:
            item["evidence_id"] = evidence_id
        resolved.append(item)
    return resolved


def _frontier_command(change, command, action):
    after = change["after"]
    if action == "create":
        for name in _FRONTIER_FIELDS:
            if name in after:
                command[name] = copy.deepcopy(after[name])
        return
    command["frontier_id"] = change["id"]
    command["expected_version"] = change["expected_version"]
    if action == "update":
        before = change.get("before") or {}
        for name in _FRONTIER_FIELDS:
            if before.get(name) != after.get(name):
                command[name] = copy.deepcopy(after.get(name))
