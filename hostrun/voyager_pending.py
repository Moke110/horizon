"""Resolve academic material waiting for a full text."""
import base64
import copy
import hashlib
import json
import uuid

from hostrun.agent_service import CommandError
from hostrun.voyager_material_lifecycle import material_write_lock


SUMMARY_LIMIT = '依据来源摘要解读，尚未核对全文。此授权只适用于指定任务，不可作为正式报道的全文证据。'
PDF_LIMIT = 10 * 1024 * 1024


def resolve(materials, command, *, token):
    materials._authorize(token)
    action = command.get('decision')
    operation = command.get('operation_id')
    material_id = command.get('material_id')
    task_id = command.get('task_id')
    request_id = command.get('request_id')
    if (type(command.get('schema_version')) is not int or command['schema_version'] != 1 or
            not isinstance(operation, str) or not 0 < len(operation) <= 256 or
            not isinstance(material_id, str) or not 0 < len(material_id) <= 64 or
            action not in ('abandon', 'authorize_abstract', 'supplement')):
        raise CommandError('invalid_command', '资料处理操作无效')
    request = {k: v for k, v in command.items() if k != 'pdf_base64'}
    pdf = None
    if action == 'supplement':
        encoded_pdf = command.get('pdf_base64')
        if not isinstance(encoded_pdf, str) or len(encoded_pdf) > ((PDF_LIMIT + 2) // 3) * 4:
            raise CommandError('invalid_pdf', '请选择不超过 10 MiB 的 PDF')
        try:
            pdf = base64.b64decode(encoded_pdf, validate=True)
        except ValueError:
            raise CommandError('invalid_pdf', 'PDF 格式无效') from None
        if len(pdf) > PDF_LIMIT or not pdf.startswith(b'%PDF-'):
            raise CommandError('invalid_pdf', 'PDF 格式无效')
        request['pdf_hash'] = hashlib.sha256(pdf).hexdigest()
    request = json.dumps(request, sort_keys=True)

    def previous(db):
        row = db.execute('SELECT request,result FROM voyager_material_operations WHERE operation_id=?', (operation,)).fetchone()
        if row:
            if row[0] != request:
                raise CommandError('operation_conflict', '操作标识与已有决定冲突')
            return json.loads(row[1])

    with materials._connect() as db:
        prior = previous(db)
    if prior:
        return prior['material_id']

    extracted = materials.extract_academic_pdf(pdf) if pdf is not None else None
    if extracted:
        if not extracted['text'].strip():
            raise CommandError('invalid_pdf', 'PDF 没有可提取的正文，原资料仍待全文')
        text_bytes = extracted['text'].encode('utf8')
        text_hash = hashlib.sha256(text_bytes).hexdigest()
        original_hash = hashlib.sha256(pdf).hexdigest()
    with material_write_lock(materials.home), materials._connect() as db:
        if extracted:
            materials._publish(text_bytes, text_hash)
            materials._publish(pdf, original_hash)
        db.execute('BEGIN IMMEDIATE')
        prior = previous(db)
        if prior:
            return prior['material_id']
        row = db.execute('SELECT result FROM voyager_materials WHERE material_id=?', (material_id,)).fetchone()
        if row is None:
            raise CommandError('not_found', '找不到资料')
        result = json.loads(row[0])
        academic = result.get('academic')
        if result.get('archived') or not academic or academic['status'] != 'pending_fulltext':
            raise CommandError('invalid_transition', '此资料不再等待全文')
        if type(command.get('expected_version')) is not int or command['expected_version'] != result['version']:
            raise CommandError('version_conflict', '资料已更新，请重新打开')
        result['version'] += 1

        if action == 'authorize_abstract':
            if command.get('confirmed') is not True or not isinstance(task_id, str) or not isinstance(request_id, str):
                raise CommandError('confirmation_required', '请选择任务并确认摘要限制')
            abstract = abstract_for(db, academic, request_id, task_id)
            permissions = academic.setdefault('abstract_authorizations', [])
            if not any(item['task_id'] == task_id and item['request_id'] == request_id for item in permissions):
                permissions.append(dict(task_id=task_id, request_id=request_id, operation_id=operation,
                                        abstract=abstract, limitation=SUMMARY_LIMIT))
        elif action == 'abandon':
            academic['status'] = 'abandoned'
            academic.pop('abstract_authorizations', None)
        else:
            academic['status'] = 'supplemented'
            academic['supplied_material_id'] = str(uuid.uuid4())
            followup = copy.deepcopy(result)
            followup['material_id'] = academic['supplied_material_id']
            last_version, = db.execute('''SELECT MAX(json_extract(result,'$.version'))
                FROM voyager_materials WHERE json_extract(result,'$.entity_id')=?''',
                (result['entity_id'],)).fetchone()
            removed = db.execute('SELECT version FROM voyager_removed_academic_versions WHERE identity=?',
                                 (academic['identity'],)).fetchone()
            followup['version'] = max(result['version'], last_version or 0, removed[0] if removed else 0) + 1
            followup['snapshot'] = dict(snapshot_id=str(uuid.uuid4()), hash_algorithm='sha256',
                content_hash=text_hash, original_hash=original_hash, byte_length=len(text_bytes),
                extraction_version='pypdf.7.1.1')
            followup['pdf'] = extracted['pdf']
            followup['academic'].update(status='available', format='pdf', supplemented_from=material_id)
            followup['academic'].pop('abstract_authorizations', None)
            followup['academic'].pop('supplied_material_id', None)
            db.execute('INSERT INTO voyager_materials VALUES(?,?)', (followup['material_id'], json.dumps(followup)))
            from hostrun.voyager_late_fulltext import enqueue
            enqueue(db, followup)

        result_id = academic.get('supplied_material_id', material_id)
        db.execute('UPDATE voyager_materials SET result=? WHERE material_id=?', (json.dumps(result), material_id))
        db.execute('INSERT INTO voyager_material_operations VALUES(?,?,?)',
                   (operation, request, json.dumps({'material_id': result_id})))
        db.execute('INSERT INTO voyager_material_events(event) VALUES(?)', (json.dumps(dict(
            schema_version=1, type='voyager.pending_material_resolved', material_id=material_id,
            operation_id=operation, decision=action)),))
    return result_id


def abstract_for(db, academic, request_id, task_id):
    row = db.execute('SELECT result FROM voyager_requests WHERE request_id=?', (request_id,)).fetchone()
    if row is None or not db.execute('SELECT 1 FROM agent_tasks WHERE task_id=?', (task_id,)).fetchone():
        raise CommandError('not_found', '找不到原检索或任务')
    request = json.loads(row[0])
    if request.get('task_id') != task_id or request['status'] == 'cancelled':
        raise CommandError('invalid_task', '摘要授权须绑定资料所属的原任务')
    identities = {(entry['source'], entry['source_id']) for entry in academic['provenance']
                  if entry['request_id'] == request_id}
    for candidate in request['candidates']:
        if (candidate['source'], candidate['source_id']) in identities and candidate.get('abstract', '').strip():
            return candidate['abstract']
    raise CommandError('abstract_unavailable', '来源未提供可用摘要')
