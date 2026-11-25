"""Pure K9 helpers for merging one Knowledge Card into another.

``KeeperService`` owns all database reads, pin validation and transactions.
This module only turns already-pinned card snapshots into the survivor's next
values and describes the redirects required of active inbound relations.  No
function here writes state or reads the database, so preview construction and
approval revalidation share exactly the same deterministic rules.
"""

from __future__ import annotations

import copy

from hostrun.agent_service import CommandError

MERGE_FROM = "merge_from"
ADD_SOURCES = "add_sources"
_MAX_ID_BYTES = 128


def parse_merge_from(value):
    """Normalize the explicit ``fields.merge_from`` marker."""
    if not isinstance(value, dict) or set(value) != {"card_id", "version"}:
        raise CommandError(
            "invalid_command",
            "merge_from requires exactly card_id and version")
    card_id = value["card_id"]
    if not isinstance(card_id, str) or not card_id.strip() \
            or len(card_id.encode("utf-8")) > _MAX_ID_BYTES:
        raise CommandError("invalid_command", "merge_from.card_id is invalid")
    version = value["version"]
    if type(version) is not int or version < 1:
        raise CommandError(
            "invalid_command", "merge_from.version must be a positive integer")
    return {"card_id": card_id, "version": version}


def parse_add_sources(value):
    if not isinstance(value, bool):
        raise CommandError("invalid_command", "add_sources must be true or false")
    return value


def source_key(source):
    """A card citation identity: the same source, locator and exact excerpt."""
    return (source.get("material_id"), source.get("locator"),
            source.get("excerpt"))


def union_sources(*groups):
    """Preserve the first pin for each exact citation; never drop or truncate."""
    seen = {}
    for group in groups:
        for source in group or []:
            key = source_key(source)
            if key not in seen:
                seen[key] = copy.deepcopy(source)
    return list(seen.values())


def merge_source_union(*groups):
    """Union citations, refusing two different pins for the same citation."""
    seen = {}
    for group in groups:
        for source in group or []:
            key = source_key(source)
            pinned = seen.get(key)
            if pinned is None:
                seen[key] = copy.deepcopy(source)
                continue
            if any(pinned.get(field) != source.get(field)
                   for field in ("material_version", "content_hash",
                                 "snapshot_id")):
                raise CommandError(
                    "version_conflict",
                    "The cards pin different snapshots for the same citation")
    return list(seen.values())


def union_strings(*groups):
    seen = []
    for group in groups:
        for value in group or []:
            if value not in seen:
                seen.append(value)
    return seen


def combined_content(survivor, donor):
    """Keep both complete bodies, skipping only a literally repeated donor body."""
    body = survivor.get("content") or ""
    addition = donor.get("content") or ""
    if not addition or addition == body or addition in body:
        return body
    if not body:
        return addition
    return body + "\n\n" + addition


def merge_relations(survivor, donor):
    """Union outgoing references while omitting the survivor and donor selves."""
    survivor_id = survivor.get("card_id")
    donor_id = donor.get("card_id")
    values = []
    for relation in list(survivor.get("relations") or []) \
            + list(donor.get("relations") or []):
        if relation in (survivor_id, donor_id):
            continue
        if relation not in values:
            values.append(relation)
    return values


def survivor_after(survivor, donor, sources):
    """Build the survivor's merged values. The survivor title is authoritative."""
    updated = copy.deepcopy(survivor)
    updated.update({
        "title": survivor["title"],
        "content": combined_content(survivor, donor),
        "aliases": union_strings(survivor.get("aliases") or [],
                                  donor.get("aliases") or [],
                                  [donor.get("title")] if donor.get("title") else []),
        "sources": copy.deepcopy(list(sources or [])),
        "relations": merge_relations(survivor, donor),
        "questionable": bool(survivor.get("questionable"))
        or bool(donor.get("questionable")),
        "known": True,
        "version": survivor["version"] + 1,
    })
    return updated


def redirect_relations(record, donor_id, survivor_id):
    """Replace the donor with the survivor, preserving the other outgoing edges."""
    values = []
    for relation in record.get("relations") or []:
        if relation == donor_id:
            relation = survivor_id
        if relation not in values:
            values.append(relation)
    if survivor_id not in values and donor_id in (record.get("relations") or []):
        values.append(survivor_id)
    return values


def redirect_related_cards(record, donor_id, survivor_id, survivor_version):
    """Replace the donor relation in a Theme and deduplicate by Card identity."""
    values = []
    seen = set()
    for relation in record.get("related_cards") or []:
        card_id = relation.get("card_id")
        item = copy.deepcopy(relation)
        if card_id == donor_id:
            item = {"card_id": survivor_id, "version": survivor_version}
            card_id = survivor_id
        if card_id in seen:
            continue
        seen.add(card_id)
        values.append(item)
    if survivor_id not in seen \
            and any(relation.get("card_id") == donor_id
                    for relation in record.get("related_cards") or []):
        values.append({"card_id": survivor_id, "version": survivor_version})
    return values
