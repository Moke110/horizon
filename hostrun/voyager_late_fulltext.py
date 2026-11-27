"""Coverage-neutral follow-up work for a scheduled task's late full text."""
import json
import uuid

from hostrun.agent_service import CommandError


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS voyager_late_fulltext (
        operation_id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, task_id TEXT NOT NULL,
        request_id TEXT NOT NULL, material_id TEXT NOT NULL, state TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS voyager_late_schedule ON voyager_late_fulltext(schedule_id,state)')


def enqueue(db, material):
    initialize(db)
    for origin in material['academic']['provenance']:
        request_id, task_id = origin['request_id'], origin.get('task_id')
        if not task_id:
            continue
        request_row = db.execute('SELECT result FROM voyager_requests WHERE request_id=?', (request_id,)).fetchone()
        if not request_row:
            continue
        request = json.loads(request_row[0])
        if (request.get('task_id') != task_id or request['status'] == 'cancelled' or
                request['scope'].get('kind') != 'temporary_explicit' or request['scope'].get('frontier_id')):
            continue
        binding = db.execute("""SELECT json_extract(event,'$.schedule_id') FROM agent_events
            WHERE json_extract(event,'$.type')='agent.schedule_run_bound'
            AND json_extract(event,'$.task_id')=? ORDER BY sequence DESC LIMIT 1""", (task_id,)).fetchone()
        if binding and db.execute('SELECT 1 FROM agent_tasks WHERE task_id=?', (task_id,)).fetchone():
            operation = str(uuid.uuid5(uuid.UUID(material['material_id']), task_id))
            db.execute('INSERT OR IGNORE INTO voyager_late_fulltext VALUES(?,?,?,?,?,?)',
                (operation, binding[0], task_id, request_id, material['material_id'], 'queued'))


def claim(service, materials, *, token, occupied):
    """Bind free per-plan slots to turns, retaining paused or unresolved work."""
    service._authorize(token)
    from hostrun.agent_scheduling import get_limits

    limit = get_limits(service, token=token)['main']
    with service._connect() as db:
        initialize(db)
        if not db.execute("SELECT 1 FROM voyager_late_fulltext WHERE state IN ('queued','claimed') LIMIT 1").fetchone():
            return []
    with service._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        regular = {pid: json.loads(encoded) for pid, encoded in db.execute('SELECT schedule_id,result FROM agent_schedules')}
        turns = {turn_id: json.loads(encoded) for turn_id, encoded in db.execute('SELECT turn_id,result FROM agent_turns')}
        slots = occupied + sum(bool(run := plan['active_run']) and
            turns.get(run.get('turn_id'), {'status':'running'})['status'] == 'running' for plan in regular.values())
        rows = db.execute("""SELECT operation_id,schedule_id,task_id,request_id,material_id,state FROM voyager_late_fulltext
                              WHERE state IN ('queued','claimed') ORDER BY rowid""").fetchall()
        reserved, ready = set(), []
        for operation, schedule_id, task_id, request_id, material_id, state in rows:
            if state != 'claimed':
                continue
            turn = turns.get(operation)
            if turn and turn['status'] in ('completed','cancelled'):
                db.execute('UPDATE voyager_late_fulltext SET state=? WHERE operation_id=?', (turn['status'], operation))
                continue
            reserved.add(schedule_id)
            if turn and turn['status'] == 'running':
                slots += 1
                if regular.get(schedule_id, {}).get('status') == 'active':
                    ready.append((operation, turn))
        for operation, schedule_id, task_id, request_id, material_id, state in rows:
            plan = regular.get(schedule_id)
            if state != 'queued' or slots >= limit or schedule_id in reserved:
                continue
            if not plan or plan['status'] != 'active' or plan['active_run']:
                continue
            task_row = db.execute('SELECT result FROM agent_tasks WHERE task_id=?', (task_id,)).fetchone()
            request_row = db.execute('SELECT result FROM voyager_requests WHERE request_id=?', (request_id,)).fetchone()
            material_row = db.execute('SELECT result FROM voyager_materials WHERE material_id=?', (material_id,)).fetchone()
            if not task_row or not request_row or not material_row:
                db.execute("UPDATE voyager_late_fulltext SET state='cancelled' WHERE operation_id=?", (operation,))
                continue
            request = json.loads(request_row[0])
            material = json.loads(material_row[0])
            if request.get('task_id') != task_id or request['status'] == 'cancelled' or material.get('archived'):
                db.execute("UPDATE voyager_late_fulltext SET state='cancelled' WHERE operation_id=?", (operation,))
                continue
            task = json.loads(task_row[0])
            if any(turn['task_id'] == task_id and turn['status'] not in ('completed','cancelled') for turn in turns.values()):
                continue
            try:
                original_text = materials._read_blob(material['snapshot']['content_hash']).decode('utf8')
            except (CommandError, OSError, UnicodeError):
                db.execute("UPDATE voyager_late_fulltext SET state='cancelled' WHERE operation_id=?", (operation,))
                continue
            if not original_text.strip():
                db.execute("UPDATE voyager_late_fulltext SET state='cancelled' WHERE operation_id=?", (operation,))
                continue
            text = (f"处理迟到全文资料 {material_id}（原检索 {request_id}）。调用 delegate 工具，"
                    f"将下列 input 原样交给 editor 子会话生成并保存报道。不得重跑检索、"
                    f"更改覆盖区间或自动投递报道。\ninput: {material_id}")
            turn = service._begin_turn(db, {'task_id':task_id, 'expected_version':task['version'], 'text':text}, operation)
            turn['late_editor_input'] = material_id
            service._persist_turn(db, turn, operation, json.dumps({'late_fulltext':material_id}), action='begin')
            db.execute("UPDATE voyager_late_fulltext SET state='claimed' WHERE operation_id=?", (operation,))
            reserved.add(schedule_id)
            slots += 1
            ready.append((operation, turn))
    return ready
