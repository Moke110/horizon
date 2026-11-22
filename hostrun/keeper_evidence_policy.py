"""K7 rejection and duplicate policy for Keeper proposals.

``review_policy`` is a pure read helper beside the Keeper review seam.  Given
the normalized K6 entries a request would turn into proposals, it reads the
proposals already stored in ``keeper_proposals`` on the caller's transaction and
returns either ``None`` or a stable ``{"reason": ...}`` outcome suitable for
Keeper's ``_Returned`` path.  It never creates schema, writes state or changes a
proposal.

Three decisions are made, in this order:

- A change already approved and executed in the same monitored scope (the same
  entity and ``expected_version``, or the same creation content) is suppressed
  as ``already_applied``.  An owner update that changes no editable field is
  suppressed as ``no_effect`` (or ``already_applied`` when an approved no-op
  already covered that scope).
- A change previously rejected is suppressed as ``duplicate_change`` even when
  its operation ID, versions, evidence order, casing or whitespace changed, or
  when a larger incoming grouping re-presents a rejected piece.  Only actual
  editable-field changes form the compared intent.
- A rejected change may be reconsidered with ``supersedes_proposal_id`` and a
  nonempty ``new_evidence_reason``.  The named proposal must be the rejected
  proposal that contains the matching change(s), and the incoming entry must
  carry a genuinely distinct normalized quote, not merely a recaptured copy of
  the same support from the same selected anchor and digest.  Every affected
  rejected entry is compared, including entries hidden inside a larger group.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata

DUPLICATE_CHANGE = "duplicate_change"
INVALID_SUPERSEDES = "invalid_supersedes"
ALREADY_APPLIED = "already_applied"
NO_EFFECT = "no_effect"

# Only these fields are requested knowledge edits.  Owner metadata, operation
# IDs, versions, timestamps, generated evidence IDs and display labels are not
# part of an intent.
_EDITABLE_FIELDS = {
    "card": {"title", "content", "aliases", "recall_terms", "relations",
             "questionable"},
    "theme": {"judgement", "conditions", "related_cards", "evidence"},
    "frontier": {"question", "scope", "hard_constraints", "relations",
                 "status"},
}


def _text(value):
    """Case, compatibility and whitespace normalization for comparison only."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _canonical(value):
    """A hashable, order-insensitive form that keeps scalars distinct by type."""
    if isinstance(value, str):
        return ("s", _text(value))
    if isinstance(value, bool):
        return ("b", value)
    if isinstance(value, (int, float)):
        return ("n", value)
    if value is None:
        return ("z",)
    if isinstance(value, (list, tuple)):
        return ("l", tuple(sorted((_canonical(item) for item in value),
                                  key=repr)))
    if isinstance(value, dict):
        return ("d", tuple(sorted(
            ((str(key), _canonical(item)) for key, item in value.items()),
            key=repr)))
    return ("r", repr(value))


def _project(kind, field, value):
    """Drop metadata that must not make two equal edits look different."""
    if kind == "theme" and field == "evidence":
        if not isinstance(value, list):
            return _canonical(value)
        projected = []
        for entry in value:
            if not isinstance(entry, dict):
                projected.append(_canonical(entry))
                continue
            source = entry.get("source")
            source = source if isinstance(source, dict) else {}
            projected.append((
                _text(str(entry.get("stance", ""))),
                _text(str(entry.get("reason", ""))),
                _text(str(source.get("locator", ""))),
                _text(str(source.get("excerpt", ""))),
            ))
        return ("l", tuple(sorted(projected, key=repr)))
    if kind == "theme" and field == "related_cards":
        if not isinstance(value, list):
            return _canonical(value)
        identities = []
        for entry in value:
            if isinstance(entry, dict):
                identities.append(_text(str(entry.get("card_id", ""))))
            else:
                identities.append(_canonical(entry))
        return ("l", tuple(sorted(identities, key=repr)))
    return _canonical(value)


def _diff(path, before, after, tokens):
    """Collect added, removed and changed pieces at field path granularity."""
    if before == after:
        return
    if before[0] == after[0] == "d":
        old = dict(before[1])
        new = dict(after[1])
        for key in sorted(set(old) | set(new)):
            _diff(f"{path}.{key}", old.get(key, ("z",)), new.get(key, ("z",)), tokens)
        return
    if before[0] == after[0] == "l":
        old = set(before[1])
        new = set(after[1])
        for item in sorted(old - new, key=repr):
            tokens.append(("removed", path, item))
        for item in sorted(new - old, key=repr):
            tokens.append(("added", path, item))
        return
    tokens.append(("changed", path, after))


def _create_delta(kind, after):
    after = after if isinstance(after, dict) else {}
    return tuple(
        ("create", field, _project(kind, field, after.get(field)))
        for field in sorted(_EDITABLE_FIELDS.get(kind, ()))
    )


def _update_delta(kind, before, after):
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    tokens = []
    for field in sorted(_EDITABLE_FIELDS.get(kind, ())):
        _diff(field,
              _project(kind, field, before.get(field)),
              _project(kind, field, after.get(field)),
              tokens)
    return tuple(tokens)


def change_intent(change):
    """The operation-independent intent of one K6 proposal change.

    ``delta`` is a stable tuple of actual editable-field changes.  For updates
    it decomposes collections into added and removed members, so a larger
    grouping can still contain a previously rejected piece without collapsing a
    different intended value.  For creations it is the normalized editable
    payload; for deletions it is the terminal action on the entity.
    """
    kind = change.get("kind")
    action = change.get("action")
    if action == "create":
        delta = _create_delta(kind, change.get("after"))
        identity = None
    elif action == "delete":
        delta = (("delete",),)
        identity = change.get("id")
    else:
        delta = _update_delta(kind, change.get("before"), change.get("after"))
        identity = change.get("id")
    ordered = tuple(sorted(delta + _source_bearing_tokens(change), key=repr))
    return {"kind": kind, "action": action, "id": identity, "delta": ordered}


def _source_bearing_tokens(change):
    """K9 merges and supplements are distinguished by their exact citations.

    Ordinary K6/K7 intents keep their historical editable-field identity. A
    merge or explicit source addition is a different request when its source
    set or donor pin differs, so a rejection cannot swallow a genuinely new
    citation set or a later K8 revision of the same survivor.
    """
    if change.get("kind") != "card" \
            or not (change.get("merge_from") is not None
                    or change.get("add_sources") is True):
        return ()
    marker = change.get("merge_from")
    tokens = []
    if marker is not None:
        tokens.append(("merge_from", _canonical({
            "card_id": marker.get("card_id"),
            "version": marker.get("version")})))
    for source in _sources(change):
        tokens.append(("source", _canonical({
            field: source.get(field)
            for field in ("material_id", "material_version", "locator",
                          "excerpt", "content_hash")
        })))
    return tuple(tokens)


def intent(entry):
    """Intent for a normalized K6 entry (or a bare proposal change)."""
    change = entry.get("proposal") if isinstance(entry, dict) else None
    if not isinstance(change, dict):
        change = entry
    return change_intent(change)


def _covers(rejected, incoming):
    """Whether a rejected intent is the same edit or a piece of the incoming one."""
    if rejected["kind"] != incoming["kind"]:
        return False
    if rejected["action"] != incoming["action"]:
        return False
    if rejected["action"] == "create":
        return rejected["delta"] == incoming["delta"]
    if rejected["id"] != incoming["id"]:
        return False
    if rejected["action"] == "delete":
        return True
    if not rejected["delta"]:
        return False
    return set(rejected["delta"]) <= set(incoming["delta"])


def _stored_proposals(db):
    """Read proposals already stored on the caller's transaction."""
    try:
        rows = db.execute(
            "SELECT proposal_id, result FROM keeper_proposals ORDER BY rowid"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    stored = []
    for row in rows:
        proposal_id, raw = row[0], row[1]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            proposal = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(proposal, dict):
            continue
        if proposal.get("status") not in ("approved", "rejected"):
            continue
        changes = proposal.get("changes")
        if not isinstance(changes, list):
            continue
        stored.append({
            "proposal_id": proposal_id or proposal.get("proposal_id"),
            "status": proposal["status"],
            "changes": [change for change in changes
                        if isinstance(change, dict)],
        })
    return stored


def _same_scope(change, item):
    if item["intent"]["action"] == "create":
        return True
    return change.get("expected_version") == item["change"].get("expected_version")


def _already_applied(proposals, item):
    for proposal in proposals:
        for change in proposal["changes"]:
            other = change_intent(change)
            if other["kind"] != item["intent"]["kind"]:
                continue
            if other["action"] != item["intent"]["action"]:
                continue
            if other["action"] != "create" \
                    and other["id"] != item["intent"]["id"]:
                continue
            if not _same_scope(change, item):
                continue
            if other["delta"] == item["intent"]["delta"]:
                return proposal["proposal_id"]
    return None


def _sources(change):
    sources = change.get("sources")
    if not isinstance(sources, list):
        return []
    return [source for source in sources if isinstance(source, dict)]


def _new_evidence(sources, previous):
    """A genuinely distinct quote outside the previously selected support."""
    previous_quotes = {_text(source["excerpt"])
                       for source in previous
                       if isinstance(source.get("excerpt"), str)}
    previous_anchors = {
        (_canonical(source.get("content_hash")),
         _canonical(source.get("anchor")))
        for source in previous
    }
    for source in sources:
        excerpt = source.get("excerpt")
        if not isinstance(excerpt, str):
            continue
        quote = _text(excerpt)
        if not quote or quote in previous_quotes:
            continue
        anchor = (_canonical(source.get("content_hash")),
                  _canonical(source.get("anchor")))
        if anchor in previous_anchors:
            continue
        return True
    return False


def _result(reason, **details):
    return {"reason": reason, **details}


def _has_effect(change):
    if change.get("action") != "update":
        return True
    before, after = change.get("before") or {}, change.get("after") or {}
    if any(before.get(field) != after.get(field)
           for field in _EDITABLE_FIELDS[change["kind"]]):
        return True
    if change.get("merge_from") is not None:
        return True
    return change.get("add_sources") is True \
        and before.get("sources") != after.get("sources")


def review_policy(db, entries, command=None, *, require_matching_supersedes=True):
    """Return ``None`` or a stable rejection/duplicate reason for these entries.

    ``db`` is the already-open Keeper transaction, ``entries`` are the
    normalized K6 entries about to become proposals, and ``command`` is the
    validated request whose optional paired ``supersedes_proposal_id`` and
    ``new_evidence_reason`` describe new evidence for a prior rejection.  The
    helper only reads ``keeper_proposals``; it has no side effects.
    """
    incoming = []
    for entry in entries or []:
        change = entry.get("proposal") if isinstance(entry, dict) else None
        if not isinstance(change, dict):
            change = entry
        if not isinstance(change, dict):
            continue
        incoming.append({
            "position": len(incoming),
            "change": change,
            "intent": change_intent(change),
        })
    if not incoming:
        return None

    stored = _stored_proposals(db)
    approved = [proposal for proposal in stored
                if proposal["status"] == "approved"]
    rejected = [proposal for proposal in stored
                if proposal["status"] == "rejected"]

    for item in incoming:
        no_effect = not _has_effect(item["change"])
        applied = _already_applied(approved, item)
        if applied is not None:
            return _result(ALREADY_APPLIED, proposal_id=applied,
                           kind=item["intent"]["kind"],
                           id=item["intent"]["id"])
        if no_effect:
            return _result(NO_EFFECT, kind=item["intent"]["kind"],
                           id=item["intent"]["id"])

    affected = []
    for proposal in rejected:
        for change in proposal["changes"]:
            rejected_intent = change_intent(change)
            for item in incoming:
                if _covers(rejected_intent, item["intent"]):
                    affected.append({
                        "proposal": proposal,
                        "change": change,
                        "item": item,
                    })
    supersedes = (command or {}).get("supersedes_proposal_id")
    explanation = (command or {}).get("new_evidence_reason")
    if not affected:
        if supersedes is not None and require_matching_supersedes:
            return _result(INVALID_SUPERSEDES, proposal_id=supersedes,
                           detail="not_a_matching_rejection")
        return None

    if supersedes is None:
        first = affected[0]
        return _result(DUPLICATE_CHANGE,
                       proposal_id=first["proposal"]["proposal_id"],
                       proposal_ids=sorted({item["proposal"]["proposal_id"]
                                            for item in affected}),
                       kind=first["item"]["intent"]["kind"],
                       id=first["item"]["intent"]["id"])

    if not isinstance(explanation, str) or not _text(explanation):
        return _result(INVALID_SUPERSEDES, proposal_id=supersedes,
                       detail="missing_explanation")

    named = None
    for proposal in rejected:
        if proposal["proposal_id"] == supersedes:
            named = proposal
            break
    if named is None:
        return _result(INVALID_SUPERSEDES, proposal_id=supersedes,
                       detail="not_a_rejected_proposal")

    positions = {item["item"]["position"] for item in affected}
    explained = {}
    for item in affected:
        if item["proposal"]["proposal_id"] == supersedes:
            explained.setdefault(item["item"]["position"], []).append(change_intent(item["change"]))
    if any(not any(_covers(change_intent(item["change"]), prior)
                   for prior in explained.get(item["item"]["position"], [])) for item in affected):
        return _result(INVALID_SUPERSEDES, proposal_id=supersedes,
                       detail="other_rejected_proposals_affected",
                       proposal_ids=sorted({item["proposal"]["proposal_id"] for item in affected}))

    for position in sorted(positions):
        item = incoming[position]
        previous = [source for match in affected if match["item"]["position"] == position
                    for source in _sources(match["change"])]
        sources = _sources(item["change"])
        if not _new_evidence(sources, previous):
            return _result(DUPLICATE_CHANGE, proposal_id=supersedes,
                           detail="new_evidence_required",
                           kind=item["intent"]["kind"], id=item["intent"]["id"])
    return None
