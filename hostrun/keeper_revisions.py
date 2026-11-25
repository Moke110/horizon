"""Revision planning for one Keeper proposal after real owner drift.

A pending proposal pins the owner versions it reviewed. When an Explorer
approves that exact revision but an owner changed afterwards, the Keeper does
not write knowledge and does not silently rebase the card. Instead the service
records a durable revision job. This module owns the pure pieces of that job:
the revise command shape, recovering the original raw changes for one displayed
proposal, carrying only fields the previous plan actually changed, preserving
unrelated manual edits, exposing same-field conflicts to the Explorer, and the
immutable fingerprint used to detect a snapshot change during a worker run.

Nothing here writes. ``KeeperService`` owns the SQLite transactions, the real
Keeper dispatch outside them and the post-commit audit trail.
"""

from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
import uuid

from hostrun.agent_service import CommandError

_SCHEMA_VERSION = 1
_MAX_ID_BYTES = 128
_MAX_FEEDBACK_BYTES = 500
_MAX_ADJUST_ITEMS = 16
_MAX_KEEP_FIELDS = 16
_REQUEST_NAMESPACE = uuid.UUID("1f0b4d2a-6b0f-5c47-9a51-3a8f7c1e6d24")

_REVISE_FIELDS = {"schema_version", "operation_id", "proposal_id", "revision",
                  "decision", "feedback", "omit_changes",
                  "keep_current_fields"}
_REVISE_REQUIRED = {"schema_version", "operation_id", "proposal_id", "revision",
                    "decision"}
_KIND_LABEL_FIELDS = {"card": "title", "theme": "judgement",
                      "frontier": "question"}
_EDITABLE_FIELDS = {
    "card": ("title", "content", "aliases", "relations",
             "questionable"),
    "theme": ("judgement", "conditions", "related_cards", "evidence"),
    "frontier": ("question", "scope", "hard_constraints", "relations",
                 "status"),
}
_NESTED_FIELDS = ("hard_constraints", "scope")
_NESTED_LEAVES = {
    "hard_constraints": ("must_include", "exclude"),
    "scope": ("text", "alternatives", "sources"),
}


def _bounded_text(value, *, name, byte_limit, required=True):
    if not isinstance(value, str) or len(value.encode("utf-8")) > byte_limit:
        raise CommandError("invalid_command", f"{name} exceeds its bound")
    if required and not value.strip():
        raise CommandError("invalid_command", f"{name} must not be empty")
    return value


def _index(value, *, name):
    if type(value) is not int or value < 0:
        raise CommandError("invalid_command", f"{name} must be a zero-based index")
    return value


def _omit_indexes(value):
    if not isinstance(value, list) or len(value) > _MAX_ADJUST_ITEMS:
        raise CommandError("invalid_command", "omit_changes must be a bounded list")
    normalized = []
    previous = -1
    for position, item in enumerate(value):
        item = _index(item, name=f"omit_changes[{position}]")
        if item <= previous:
            raise CommandError(
                "invalid_command",
                "omit_changes must be ordered unique zero-based indexes")
        previous = item
        normalized.append(item)
    return normalized


def _keep_current(value):
    if not isinstance(value, list) or len(value) > _MAX_ADJUST_ITEMS:
        raise CommandError("invalid_command",
                           "keep_current_fields must be a bounded list")
    normalized = []
    seen = set()
    for position, entry in enumerate(value):
        if not isinstance(entry, dict) \
                or set(entry) != {"change_index", "fields"}:
            raise CommandError(
                "invalid_command",
                "keep_current_fields entries require change_index and fields")
        index = _index(entry["change_index"],
                       name=f"keep_current_fields[{position}].change_index")
        if index in seen:
            raise CommandError(
                "invalid_command",
                "keep_current_fields must name each change once")
        seen.add(index)
        fields = entry["fields"]
        if not isinstance(fields, list) or not 1 <= len(fields) <= _MAX_KEEP_FIELDS:
            raise CommandError(
                "invalid_command",
                "keep_current_fields fields must be a bounded list")
        normalized_fields = []
        for field_position, name in enumerate(fields):
            name = _bounded_text(
                name, name=f"keep_current_fields[{position}].fields[{field_position}]",
                byte_limit=_MAX_ID_BYTES)
            if name in normalized_fields:
                raise CommandError(
                    "invalid_command",
                    "keep_current_fields fields must be unique")
            normalized_fields.append(name)
        normalized.append({"change_index": index,
                           "fields": normalized_fields})
    return normalized


def validate_revise(command):
    """Normalize an Explorer revision adjustment or cancellation."""
    if not isinstance(command, dict):
        raise CommandError("invalid_command", "Revision must be an object")
    if type(command.get("schema_version")) is not int \
            or command["schema_version"] != _SCHEMA_VERSION:
        raise CommandError("unsupported_schema", "Unsupported schema_version")
    if set(command) - _REVISE_FIELDS or not _REVISE_REQUIRED <= set(command):
        raise CommandError("invalid_command", "Revision contains unsupported fields")
    operation_id = _bounded_text(command["operation_id"], name="operation_id",
                                 byte_limit=_MAX_ID_BYTES)
    proposal_id = _bounded_text(command["proposal_id"], name="proposal_id",
                                byte_limit=_MAX_ID_BYTES)
    revision = command["revision"]
    if type(revision) is not int or revision < 1:
        raise CommandError("invalid_command", "revision must be a positive integer")
    decision = command["decision"]
    if decision not in ("adjust", "cancel"):
        raise CommandError("invalid_command", "decision must be adjust or cancel")
    omit = command.get("omit_changes")
    keep = command.get("keep_current_fields")
    if decision == "cancel" and (omit is not None or keep is not None):
        raise CommandError("invalid_command",
                           "Cancel does not accept adjustment choices")
    normalized = {
        "schema_version": _SCHEMA_VERSION,
        "operation_id": operation_id,
        "proposal_id": proposal_id,
        "revision": revision,
        "decision": decision,
    }
    feedback = command.get("feedback")
    if feedback is not None:
        feedback = _bounded_text(feedback, name="feedback",
                                 byte_limit=_MAX_FEEDBACK_BYTES,
                                 required=False)
        if not feedback.strip():
            feedback = None
    if decision == "adjust":
        if omit is not None:
            omit_indexes = _omit_indexes(omit)
            if omit_indexes:
                normalized["omit_changes"] = omit_indexes
        if keep is not None:
            keep_fields = _keep_current(keep)
            if keep_fields:
                normalized["keep_current_fields"] = keep_fields
        if feedback is None:
            raise CommandError("invalid_command", "Adjustment requires bounded feedback")
    if feedback is not None:
        normalized["feedback"] = feedback
    return normalized


def _field_changed(change, name):
    before = change.get("before") or {}
    after = change.get("after") or {}
    if name in _NESTED_FIELDS:
        return bool(_changed_leaves(name, before, after))
    return before.get(name) != after.get(name)


def validate_adjustment(proposal, command):
    """Bind adjustment indexes and fields to the displayed proposal revision."""
    changes = proposal.get("changes") or []
    omit = command.get("omit_changes") or []
    keep = command.get("keep_current_fields") or []
    for index in omit:
        if index >= len(changes):
            raise CommandError("invalid_revision",
                               "omit_changes no longer describes this proposal")
    if omit and len(omit) >= len(changes):
        raise CommandError("invalid_revision",
                           "An adjustment cannot omit every change")
    for entry in keep:
        index = entry["change_index"]
        if index >= len(changes):
            raise CommandError(
                "invalid_revision",
                "keep_current_fields no longer describes this proposal")
        change = changes[index]
        if change.get("action") != "update":
            raise CommandError(
                "invalid_revision",
                "keep_current_fields only applies to proposed updates")
        if change.get("merge_from"):
            raise CommandError("invalid_revision",
                               "The merge derives its survivor from both pinned Cards")
        editable = _EDITABLE_FIELDS.get(change.get("kind"), ())
        for name in entry["fields"]:
            if name not in editable or not _field_changed(change, name):
                raise CommandError(
                    "invalid_revision",
                    "keep_current_fields may only retain proposed editable fields")
    return command


def apply_adjustment(raw_changes, command):
    """Delete explicitly retained raw fields, then drop omitted changes.

    Deleting the selected raw field makes the owner's latest value authoritative
    for it while every unselected field and every unsuperseded unrelated update
    keeps its original proposal semantics. A change whose every field vanished
    contributes no effect and is not published as a hollow update.
    """
    omit = set(command.get("omit_changes") or [])
    for entry in command.get("keep_current_fields") or []:
        raw = raw_changes[entry["change_index"]]
        fields = raw.get("fields")
        if isinstance(fields, dict):
            for name in entry["fields"]:
                fields.pop(name, None)
    selected = []
    for index, raw in enumerate(raw_changes):
        if index in omit:
            continue
        if raw.get("action") == "update" and not raw.get("fields"):
            continue
        selected.append((index, raw))
    return selected


def has_effect(change):
    """Whether one rebuilt proposal change still changes an editable field."""
    if change.get("action") != "update":
        return True
    before = change.get("before") or {}
    after = change.get("after") or {}
    if any(before.get(name) != after.get(name)
           for name in _EDITABLE_FIELDS.get(change.get("kind"), ())):
        return True
    if change.get("merge_from") is not None:
        return True
    return change.get("add_sources") is True \
        and before.get("sources") != after.get("sources")


def snapshot(proposal):
    """An immutable copy of one displayed proposal revision.

    Prior revision notes are stripped so nesting history never grows without
    bound; the historical revision itself remains in ``keeper_revision_history``
    and the audit trail.
    """
    value = copy.deepcopy(proposal)
    for name in ("previous_revision", "revision_notes", "conflicts"):
        value.pop(name, None)
    return value


def revision_notes(previous, *, reason, review_id, feedback=None,
                   child_id=None, conflicts=None):
    """The bounded desktop-facing explanation of the revision being shown."""
    prior = snapshot(previous)
    notes = {
        "previous_revision": prior.get("revision"),
        "previous_changes": prior.get("changes") or [],
        "reason": reason,
        "review_id": review_id,
        "child_id": child_id,
        "conflicts": copy.deepcopy(list(conflicts or [])),
    }
    if feedback is not None and feedback.strip():
        notes["feedback"] = feedback
    return notes


def request_id(review_id, digest):
    """A child identity that is stable per job and proposal fingerprint."""
    return str(uuid.uuid5(_REQUEST_NAMESPACE, f"{review_id}:{digest}"))


def fingerprint(proposal):
    """A stable digest of a rebuilt proposal before it is dispatched."""
    changes = copy.deepcopy(proposal["changes"])
    for change in changes:
        if change["kind"] != "theme" or change["action"] == "delete":
            continue
        existing = {entry["evidence_id"]
                    for entry in (change["before"] or {}).get("evidence", [])}
        for entry in change["after"].get("evidence", []):
            # The preview uses provisional IDs. The Theme owner assigns final IDs
            # when approval commits, and existing evidence IDs remain meaningful.
            if entry["evidence_id"] not in existing:
                del entry["evidence_id"]
    stable = {
        "schema_version": _SCHEMA_VERSION,
        "proposal_id": proposal.get("proposal_id"),
        "revision": proposal.get("revision"),
        "request_id": proposal.get("request_id"),
        "task_id": proposal.get("task_id"),
        "task_version": proposal.get("task_version"),
        "report_id": proposal.get("report_id"),
        "report_version": proposal.get("report_version"),
        "feedback": proposal.get("feedback"),
        "changes": changes,
    }
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalized_label(value):
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _creation_label(change):
    after = change.get("after") or {}
    field = _KIND_LABEL_FIELDS[change["kind"]]
    return _normalized_label(after.get(field))


def _matches(displayed, raw):
    supplemented = displayed.get("supplemented_title")
    if supplemented and raw.get("kind") == "card" and raw.get("action") == "create":
        return _normalized_label(raw["fields"].get("title")) == supplemented
    if displayed["action"] != raw["action"] \
            or displayed["kind"] != raw["kind"]:
        return False
    if displayed["action"] == "create":
        return _creation_label(displayed) == _creation_label(
            {"kind": raw["kind"], "after": raw.get("fields") or {}})
    return displayed.get("id") == raw.get("id")


def original_changes(command, proposal_changes):
    """Recover only the raw request changes that produced this displayed group.

    A request can be split into independent proposal groups. Selection uses the
    stable target identity, and the creation label for a new object, so a
    revision never rebuilds a sibling proposal that the Explorer did not open.
    """
    remaining = []
    for group in command.get("groups", []):
        remaining.extend(copy.deepcopy(group.get("changes", [])))
    selected = []
    for displayed in proposal_changes:
        index = next((position for position, raw in enumerate(remaining)
                      if _matches(displayed, raw)), None)
        if index is None:
            raise CommandError("invalid_revision",
                               "The original request no longer describes this proposal")
        raw = remaining.pop(index)
        if displayed.get("supplemented_title") and raw["action"] == "create":
            raw.update(action="update", id=displayed["id"],
                       expected_version=displayed["expected_version"],
                       fields={"add_sources": True},
                       supplemented_title=displayed["supplemented_title"])
        selected.append(raw)
    if not selected:
        raise CommandError("invalid_revision", "A revision requires at least one change")
    return selected


def revision_fields(previous, fields):
    """Carry only fields the previous plan actually changed.

    Nested Frontier constraints and scope retain their leaves separately, so a
    manual edit to an unrelated leaf is not overwritten by a whole-object copy.
    """
    if previous.get("action") != "update":
        return copy.deepcopy(fields)
    before = previous.get("before") or {}
    after = previous.get("after") or {}
    carried = {}
    for name, value in fields.items():
        if name in ("merge_from", "add_sources"):
            # A merge marker is an explicit request fact, not a before/after
            # value; a K8 rebuild must keep it and recalculate the survivor.
            carried[name] = copy.deepcopy(value)
            continue
        if name in _NESTED_FIELDS and isinstance(value, dict):
            old = before.get(name) if isinstance(before.get(name), dict) else {}
            new = after.get(name) if isinstance(after.get(name), dict) else {}
            leaves = {
                leaf: copy.deepcopy(value[leaf])
                for leaf in _NESTED_LEAVES[name]
                if leaf in value and old.get(leaf) != new.get(leaf)
            }
            if leaves:
                carried[name] = leaves
            continue
        if before.get(name) != after.get(name):
            carried[name] = copy.deepcopy(value)
    return carried


def merge_latest_nested(fields, latest):
    """Merge carried nested leaves onto the latest owner snapshot."""
    merged = copy.deepcopy(fields)
    for name in _NESTED_FIELDS:
        if name not in merged or not isinstance(merged[name], dict):
            continue
        base = latest.get(name) if isinstance(latest.get(name), dict) else {}
        value = copy.deepcopy(base)
        value.update(copy.deepcopy(merged[name]))
        merged[name] = value
    return merged


def _changed_leaves(name, before, after):
    old = before.get(name) if isinstance(before.get(name), dict) else {}
    new = after.get(name) if isinstance(after.get(name), dict) else {}
    return [leaf for leaf in _NESTED_LEAVES[name] if old.get(leaf) != new.get(leaf)]


def conflicts(previous_changes, rebuilt_changes):
    """Expose latest versus proposed values where a manual edit touched a field.

    The rebuilt proposal stays pending: the Explorer sees both values and must
    confirm the new revision. Nothing is applied on the strength of a manual
    edit that happened to match the earlier proposal.
    """
    found = []
    latest_changes = {(change["kind"], change["id"]): change
                      for change in rebuilt_changes if change["action"] == "update"}
    for previous in previous_changes:
        if previous.get("action") != "update":
            continue
        rebuilt = latest_changes.get((previous["kind"], previous["id"]))
        if rebuilt is None:
            continue
        before = previous.get("before") or {}
        after = previous.get("after") or {}
        latest = rebuilt.get("before") or {}
        proposed = rebuilt.get("after") or {}
        for name in _EDITABLE_FIELDS.get(previous.get("kind"), ()):
            if name not in after and name not in before:
                continue
            if name in _NESTED_FIELDS:
                for leaf in _changed_leaves(name, before, after):
                    old = before.get(name) if isinstance(before.get(name), dict) else {}
                    new = latest.get(name) if isinstance(latest.get(name), dict) else {}
                    if old.get(leaf) == new.get(leaf):
                        continue
                    proposed_value = (proposed.get(name) or {}).get(leaf) \
                        if isinstance(proposed.get(name), dict) else None
                    entry = {
                        "kind": previous["kind"], "id": previous["id"],
                        "field": f"{name}.{leaf}",
                        "latest": copy.deepcopy(new.get(leaf)),
                        "proposed": copy.deepcopy(proposed_value),
                    }
                    if entry["latest"] == entry["proposed"]:
                        entry["already_matches"] = True
                    found.append(entry)
                continue
            if before.get(name) == after.get(name):
                continue
            if before.get(name) == latest.get(name):
                continue
            entry = {
                "kind": previous["kind"], "id": previous["id"], "field": name,
                "latest": copy.deepcopy(latest.get(name)),
                "proposed": copy.deepcopy(proposed.get(name)),
            }
            if entry["latest"] == entry["proposed"]:
                entry["already_matches"] = True
            found.append(entry)
    return found
