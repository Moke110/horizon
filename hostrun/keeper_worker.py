"""Deterministic, offline Keeper reviewer.

The trusted desktop host runs this exact file with its own interpreter, so no
import path or installed package is needed:

    python -B -X utf8 <code_root>/hostrun/keeper_worker.py

It reads one bounded UTF-8 JSON evidence document from private standard input
and writes one reviewer result line to standard output. It never reads files,
opens a database, uses tools, reaches the network or consumes credentials. Its
job is to reject evidence that is incomplete or contradictory before the public
Keeper service records a proposal for a human.
"""
import json
import re
import sys

SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 262144
MAX_GROUPS = 32
MAX_CHANGES = 128
ACTIONS = frozenset({'create', 'update', 'delete'})
_HASH = re.compile('[a-f0-9]{64}')


def _text(value, limit=512):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _returned(reason, *, request_id='', proposal_ids=None, counts=None):
    return {'schema_version': SCHEMA_VERSION, 'request_id': request_id,
            'status': 'returned', 'reason': str(reason)[:1000],
            'proposal_ids': list(proposal_ids or []), 'reviewer': 'deterministic-local',
            'checks': counts or {}}


def _source_ok(source):
    if not isinstance(source, dict):
        return False
    version = source.get('material_version')
    digest = source.get('content_hash')
    anchor = source.get('anchor')
    return (_text(source.get('material_id')) and type(version) is int and version > 0
            and isinstance(digest, str) and bool(_HASH.fullmatch(digest))
            and _text(source.get('locator'), 2000) and _text(source.get('excerpt'), 4000)
            and isinstance(anchor, dict) and type(anchor.get('start')) is int
            and type(anchor.get('end')) is int and 0 <= anchor['start'] < anchor['end'])


def _relation_ok(value):
    if not isinstance(value, list) or len(value) > 32:
        return False
    seen = set()
    for item in value:
        if isinstance(item, str):
            identity = item.strip()
        elif isinstance(item, dict):
            identity = str(item.get('target') or item.get('id') or item.get('card_id')
                           or item.get('theme_id') or item.get('frontier_id') or '').strip()
        else:
            return False
        if not identity or len(identity) > 512 or identity in seen:
            return False
        seen.add(identity)
    return True


def verify(document):
    """Return one deterministic reviewer result for a bounded evidence document."""
    if not isinstance(document, dict):
        return _returned('Evidence must be a JSON object')
    if document.get('schema_version') != SCHEMA_VERSION:
        return _returned('Unsupported evidence schema')
    request_id = document.get('request_id')
    if not _text(request_id):
        return _returned('request_id is required')
    for field in ('task_id', 'report_id'):
        if not _text(document.get(field)):
            return _returned(field + ' is required', request_id=request_id)
    for field in ('task_version', 'report_version'):
        value = document.get(field)
        if type(value) is not int or value < 1:
            return _returned(field + ' must be a positive integer', request_id=request_id)
    groups = document.get('groups')
    if not isinstance(groups, list) or not groups:
        return _returned('At least one reviewable group is required', request_id=request_id)
    if len(groups) > MAX_GROUPS:
        return _returned('Too many reviewable groups', request_id=request_id)
    proposal_ids, proposal_seen, change_seen = [], set(), set()
    changes = source_count = 0
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            return _returned('Group %d must be an object' % (index + 1), request_id=request_id)
        proposal_id = group.get('proposal_id') or group.get('id')
        if not _text(proposal_id):
            return _returned('Every group needs a proposal_id', request_id=request_id)
        if proposal_id in proposal_seen:
            return _returned('Duplicate proposal_id: ' + proposal_id, request_id=request_id)
        proposal_seen.add(proposal_id)
        proposal_ids.append(proposal_id)
        group_changes = group.get('changes')
        if not isinstance(group_changes, list) or not group_changes:
            return _returned('Proposal %s has no changes' % proposal_id, request_id=request_id)
        targets = set()
        for change in group_changes:
            changes += 1
            if changes > MAX_CHANGES:
                return _returned('Too many changes', request_id=request_id)
            if not isinstance(change, dict):
                return _returned('Proposal %s has an invalid change' % proposal_id,
                                 request_id=request_id)
            change_id = change.get('change_id') or change.get('id')
            if change_id is not None and not _text(change_id):
                return _returned('Invalid change identity in ' + proposal_id,
                                 request_id=request_id)
            if change_id is not None:
                if change_id in change_seen:
                    return _returned('Duplicate change_id: ' + change_id, request_id=request_id)
                change_seen.add(change_id)
            action = change.get('action') or change.get('operation')
            if action not in ACTIONS:
                return _returned('Proposal %s has an unsupported change action' % proposal_id,
                                 request_id=request_id)
            entity = change.get('entity') or change.get('target') or change.get('object')
            if isinstance(entity, dict):
                entity = entity.get('id') or entity.get('entity_id') or entity.get('name')
            if not _text(entity):
                return _returned('Proposal %s change has no target object' % proposal_id,
                                 request_id=request_id)
            target = (entity, action)
            if target in targets:
                return _returned('Proposal %s repeats target %s' % (proposal_id, entity),
                                 request_id=request_id)
            targets.add(target)
            sources = change.get('source')
            if not sources:
                return _returned('Proposal %s change for %s has no source' % (proposal_id, entity),
                                 request_id=request_id)
            if not isinstance(sources, list) or not all(_source_ok(source) for source in sources):
                return _returned('Proposal %s change for %s has an unverified source' % (proposal_id, entity),
                                 request_id=request_id)
            source_count += len(sources)
            if 'relations' in change and not _relation_ok(change['relations']):
                return _returned('Proposal %s has invalid or duplicate relations' % proposal_id,
                                 request_id=request_id)
    return {'schema_version': SCHEMA_VERSION, 'request_id': request_id,
            'status': 'reviewed', 'reason': '', 'proposal_ids': proposal_ids,
            'reviewer': 'deterministic-local',
            'checks': {'groups': len(groups), 'changes': changes,
                       'sources': source_count}}


def main():
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        result = _returned('Evidence exceeds the bounded input size')
    else:
        try:
            document = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            result = _returned('Evidence is not bounded UTF-8 JSON')
        else:
            result = verify(document)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n')
    sys.stdout.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
