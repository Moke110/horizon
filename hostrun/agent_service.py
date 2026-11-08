"""Agent-owned persistent commands and queries for authenticated desktop clients."""
import hmac
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class CommandError(ValueError):
    """Stable application error code; message is diagnostic text."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def desktop_home():
    """New-product data only; never discovers or imports a legacy workspace."""
    override = os.environ.get('HORIZON_DATA_HOME')
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise ValueError('HORIZON_DATA_HOME must be absolute')
        return path
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'Horizon' / 'data'


class AgentService:
    def __init__(self, home, *, token, transaction_observer=None):
        if not isinstance(token, str) or not token:
            raise ValueError('A launch token is required')
        self._token = token
        self._transaction_observer = transaction_observer or (lambda phase: None)
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self._database = self.home / 'horizon.sqlite3'
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    task_id TEXT PRIMARY KEY, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_children (
                    child_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_operations (
                    operation_id TEXT PRIMARY KEY, request TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id TEXT UNIQUE NOT NULL, event TEXT NOT NULL);
            ''')

            db.execute('BEGIN IMMEDIATE')
            for task_id, encoded in db.execute('SELECT task_id,result FROM agent_tasks').fetchall():
                task = json.loads(encoded)
                if 'session_id' not in task:
                    task['session_id'] = str(uuid.uuid4())
                    db.execute('UPDATE agent_tasks SET result=? WHERE task_id=?', (json.dumps(task), task_id))

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self._database)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _authorize(self, token):
        if not isinstance(token, str) or not hmac.compare_digest(token, self._token):
            raise PermissionError('Unauthorized desktop command')

    def create_task(self, command, *, token):
        self._authorize(token)
        if not isinstance(command, dict):
            raise CommandError('invalid_command', 'Command must be an object')
        if type(command.get('schema_version')) is not int or command['schema_version'] != 1:
            raise CommandError('unsupported_schema', 'Unsupported schema_version')
        operation = command.get('operation_id')
        title = command.get('title')
        if not isinstance(operation, str) or not operation.strip():
            raise CommandError('invalid_command', 'operation_id is required')
        if not isinstance(title, str) or not title.strip():
            raise CommandError('invalid_command', 'title is required')
        request = json.dumps(command, sort_keys=True)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT request, result FROM agent_operations WHERE operation_id=?', (operation,)).fetchone()
            if existing:
                if existing[0] != request:
                    raise CommandError('operation_conflict', 'operation_id conflicts with a previous command')
                return json.loads(existing[1])
            task_id = str(uuid.uuid4())
            result = {'schema_version': 1, 'operation_id': operation, 'task_id': task_id,
                      'entity_id': task_id, 'session_id': str(uuid.uuid4()), 'version': 1, 'title': title, 'messages': []}
            encoded = json.dumps(result)
            db.execute('INSERT INTO agent_tasks VALUES (?, ?)', (task_id, encoded))
            db.execute('INSERT INTO agent_operations VALUES (?, ?, ?)', (operation, request, encoded))
            event = {'schema_version': 1, 'type': 'agent.task_created', 'task_id': task_id, 'operation_id': operation}
            db.execute('INSERT INTO agent_events(operation_id,event) VALUES (?, ?)', (operation, json.dumps(event)))
            self._transaction_observer('before_commit')
        self._transaction_observer('after_commit')
        return result

    def list_tasks(self, *, token):
        self._authorize(token)
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT result FROM agent_tasks ORDER BY rowid DESC')]

    def events(self, *, token):
        self._authorize(token)
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT event FROM agent_events ORDER BY sequence')]

    def append_message(self, command, *, token):
        self._authorize(token)
        if not isinstance(command, dict):
            raise CommandError('invalid_command', 'Command must be an object')
        if type(command.get('schema_version')) is not int or command['schema_version'] != 1:
            raise CommandError('unsupported_schema', 'Unsupported schema_version')
        operation = command.get('operation_id')
        if not isinstance(operation, str) or not operation.strip():
            raise CommandError('invalid_command', 'operation_id is required')
        if not isinstance(command.get('task_id'), str) or not command['task_id'].strip():
            raise CommandError('invalid_command', 'task_id is required')
        if command.get('role') not in ('You', 'Horizon') or not isinstance(command.get('text'), str):
            raise CommandError('invalid_command', 'Invalid message')
        request = json.dumps(command, sort_keys=True)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT request,result FROM agent_operations WHERE operation_id=?', (operation,)).fetchone()
            if existing:
                if existing[0] != request:
                    raise CommandError('operation_conflict', 'operation_id conflicts with a previous command')
                return json.loads(existing[1])
            row = db.execute('SELECT result FROM agent_tasks WHERE task_id=?', (command.get('task_id'),)).fetchone()
            if row is None:
                raise CommandError('not_found', 'Unknown task_id')
            result = json.loads(row[0])
            expected = command.get('expected_version')
            if type(expected) is not int or expected != result['version']:
                raise CommandError('version_conflict', 'expected_version conflicts with current task version')
            if not result['messages'] and command['role'] == 'You':
                result['title'] = command['text'][:24]
            result['messages'].append({'role': command['role'], 'text': command['text']})
            result['version'] += 1
            result['operation_id'] = operation
            encoded = json.dumps(result)
            db.execute('UPDATE agent_tasks SET result=? WHERE task_id=?', (encoded, result['task_id']))
            db.execute('INSERT INTO agent_operations VALUES (?,?,?)', (operation, request, encoded))
            event = {'schema_version': 1, 'type': 'agent.message_added', 'task_id': result['task_id'], 'operation_id': operation}
            db.execute('INSERT INTO agent_events(operation_id,event) VALUES (?,?)', (operation, json.dumps(event)))
            self._transaction_observer('before_commit')
        self._transaction_observer('after_commit')
        return result

    def list_children(self, task_id, *, token):
        self._authorize(token)
        if not isinstance(task_id, str):
            raise CommandError('invalid_command', 'task_id is required')
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT result FROM agent_children WHERE task_id=? ORDER BY rowid', (task_id,))]

    def start_child(self, command, *, token):
        return self._child_command(command, token=token, finishing=False)

    def finish_child(self, command, *, token):
        return self._child_command(command, token=token, finishing=True)

    def _child_command(self, command, *, token, finishing):
        self._authorize(token)
        if not isinstance(command, dict):
            raise CommandError('invalid_command', 'Command must be an object')
        if type(command.get('schema_version')) is not int or command['schema_version'] != 1:
            raise CommandError('unsupported_schema', 'Unsupported schema_version')
        operation = command.get('operation_id')
        if not isinstance(operation, str) or not operation.strip():
            raise CommandError('invalid_command', 'operation_id is required')
        identity = command.get('child_id' if finishing else 'task_id')
        if not isinstance(identity, str) or not identity:
            raise CommandError('invalid_command', 'Entity identity is required')
        if finishing:
            if command.get('status') not in ('succeeded', 'failed'):
                raise CommandError('invalid_command', 'Invalid child status')
            for field in ('result', 'error'):
                value = command.get(field, '')
                if not isinstance(value, str) or len(value) > 100000:
                    raise CommandError('invalid_command', 'Child result exceeds bounded context')
        else:
            if command.get('role') not in ('development_reader', 'development_summarizer'):
                raise CommandError('invalid_command', 'Unsupported child role')
            text = command.get('input_text')
            if not isinstance(text, str) or not text.strip() or len(text) > 16000:
                raise CommandError('invalid_command', 'Child input must be bounded text')
        request = json.dumps({'action':'finish_child' if finishing else 'start_child','command':command},sort_keys=True)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT request,result FROM agent_operations WHERE operation_id=?',(operation,)).fetchone()
            if previous:
                if previous[0] != request:
                    raise CommandError('operation_conflict', 'operation_id conflicts with a previous command')
                return json.loads(previous[1])
            if finishing:
                row = db.execute('SELECT result FROM agent_children WHERE child_id=?',(identity,)).fetchone()
                if not row:
                    raise CommandError('not_found', 'Unknown child_id')
                result = json.loads(row[0])
                if type(command.get('expected_version')) is not int or command['expected_version'] != result['version']:
                    raise CommandError('version_conflict', 'expected_version conflicts with child version')
                if result['status'] != 'running':
                    raise CommandError('version_conflict', 'Child already completed')
                result.update(status=command['status'], result=command.get('result',''), error=command.get('error',''),version=2,operation_id=operation)
                event_type = 'agent.child_finished'
            else:
                row = db.execute('SELECT result FROM agent_tasks WHERE task_id=?',(identity,)).fetchone()
                if not row:
                    raise CommandError('not_found', 'Unknown task_id')
                parent = json.loads(row[0])
                if type(command.get('input_version')) is not int or command['input_version'] != parent['version']:
                    raise CommandError('version_conflict', 'input_version conflicts with current task version')
                child_id = str(uuid.uuid4())
                result = {'schema_version':1,'operation_id':operation,'entity_id':child_id,'child_id':child_id,
                          'task_id':identity,'session_id':str(uuid.uuid4()),'parent_session_id':parent['session_id'],
                          'role':command['role'],'input_text':command['input_text'],'input_version':command['input_version'],
                          'status':'running','result':'','error':'','artifact_ref':'agent-child:'+child_id,'version':1}
                event_type = 'agent.child_started'
            encoded = json.dumps(result)
            db.execute('INSERT INTO agent_children VALUES(?,?,?) ON CONFLICT(child_id) DO UPDATE SET result=excluded.result', (result['child_id'],result['task_id'],encoded))
            db.execute('INSERT INTO agent_operations VALUES(?,?,?)',(operation,request,encoded))
            event = {'schema_version':1,'type':event_type,'operation_id':operation,'task_id':result['task_id'],'child_id':result['child_id']}
            db.execute('INSERT INTO agent_events(operation_id,event) VALUES(?,?)',(operation,json.dumps(event)))
            self._transaction_observer('before_commit')
        self._transaction_observer('after_commit')
        return result
