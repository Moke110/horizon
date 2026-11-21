"""The read-only evidence supplied to a Voyager child session."""
from uuid import UUID

from hostrun.agent_service import CommandError
from hostrun.voyager_materials import MaterialsService


def _candidate(row):
    reason = row.get('selection_reason')
    return dict(source=row['source'], source_id=row['source_id'], title=row['title'][:200],
        doi=row.get('doi'), query_ids=row['query_ids'][:4],
        selection_reason=dict(kind=reason['kind'],
            matched_terms=[term[:50] for term in reason['matched_terms'][:8]],
            reposted=reason.get('reposted', False)) if reason else None)


def child_evidence(voyager, request_id, task_id, *, token):
    try:
        valid = isinstance(request_id, str) and str(UUID(request_id)) == request_id
    except ValueError:
        valid = False
    if not valid:
        raise CommandError('invalid_request', 'Voyager needs an explicit retrieval ID')
    request = voyager.get(request_id, token=token)
    if request.get('task_id') != task_id or request['scope']['kind'] != 'temporary_explicit':
        raise CommandError('invalid_task', 'Retrieval is not bound to this task')
    if request['status'] in ('planned', 'running', 'cancelled'):
        raise CommandError('invalid_transition', 'Finish the current retrieval segment first')

    candidates = [_candidate(row) for row in request['candidates'][:12]]
    materials = MaterialsService(voyager.home, token=token)
    snapshots = []
    seen = set()
    for work in reversed(request.get('fulltext_work', [])):
        reference = work.get('evidence_ref') or ''
        if work['status'] != 'succeeded' or not reference.startswith('material:') or reference in seen:
            continue
        seen.add(reference)
        if len(seen) > 8:
            break
        try:
            package = materials.get_package(reference.removeprefix('material:'), token=token)
        except CommandError as error:
            if error.code != 'not_found':
                raise
            continue
        snapshot = package['snapshot']
        locators = ((package.get('pdf') or {}).get('anchors') or
                    (package.get('web') or {}).get('anchors') or [])
        anchors = [{key: item[key] for key in ('page', 'id', 'start', 'end') if key in item}
                   for item in locators[:12]]
        snapshots.append(dict(material_ref=reference, source=work['candidate_source'],
            source_id=work['candidate_id'], snapshot_id=snapshot.get('snapshot_id'),
            content_hash=snapshot['content_hash'], extraction_version=snapshot['extraction_version'],
            anchors=anchors, anchor_count=len(locators)))

    return dict(schema_version=1, request_id=request_id, scope_kind=request['scope']['kind'],
        status=request['status'], stop_reason=request.get('stop_reason'), candidates=candidates,
        candidate_count=len(request['candidates']), snapshots=snapshots,
        gaps=[{key: gap[key] for key in ('source', 'reason', 'query_id', 'work_id') if key in gap}
              for gap in request['gaps'][:20]], gap_count=len(request['gaps']),
        limitations=['Only retrieved source metadata and captured snapshot locators are available.',
                     'The child has no original body, does not verify claims, and may not infer complete coverage.',
                     'Unlisted candidates, anchors and gaps are outside the bounded projection.'])
