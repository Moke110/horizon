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
                CREATE TABLE IF NOT EXISTS agent_turns (turn_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, result TEXT NOT NULL);
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

    def attempt_child(self, command, *, token):
        return self._child_command(command, token=token, finishing=True, attempting=True)

    def finish_child(self, command, *, token):
        return self._child_command(command, token=token, finishing=True)

    def _child_command(self, command, *, token, finishing, attempting=False):
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
        if attempting:
            if not isinstance(command.get('snapshots'), dict):
                raise CommandError('invalid_command', 'snapshots required')
        elif finishing:
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
        request = json.dumps({'action':'attempt_child' if attempting else 'finish_child' if finishing else 'start_child','command':command},sort_keys=True)
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
                if attempting:
                    result.setdefault('attempts', []).append({'operation_id':operation,'snapshots':command['snapshots']})
                    result['snapshots'] = command['snapshots']
                    result['operation_id'] = operation
                    event_type = 'agent.child_attempted'
                else:
                    result.update(status=command['status'], result=command.get('result',''), error=command.get('error',''),version=result['version']+1,operation_id=operation)
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
                          'start_operation_id':operation,'role':command['role'],'input_text':command['input_text'],'input_version':command['input_version'],'snapshots':command.get('snapshots',{}),
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


    def list_turns(self, task_id=None, *, token):
        self._authorize(token)
        with self._connect() as db:
            rows = db.execute('SELECT result FROM agent_turns ORDER BY rowid') if task_id is None else db.execute('SELECT result FROM agent_turns WHERE task_id=? ORDER BY rowid',(task_id,))
            return [json.loads(row[0]) for row in rows]

    def recoverable_turns(self, *, token):
        return [turn for turn in self.list_turns(token=token) if turn['status'] == 'running']

    def begin_turn(self, command, *, token):
        return self._turn_command(command, token=token, action='begin')

    def commit_turn(self, command, *, token):
        return self._turn_command(command, token=token, action='commit')

    def attempt_turn(self, command, *, token):
        return self._turn_command(command, token=token, action='attempt')

    def _turn_command(self, command, *, token, action):
        self._authorize(token)
        if not isinstance(command, dict) or type(command.get('schema_version')) is not int or command['schema_version'] != 1:
            raise CommandError('invalid_command', 'Invalid command schema')
        operation = command.get('operation_id')
        if not isinstance(operation, str) or not operation.strip():
            raise CommandError('invalid_command', 'operation_id is required')
        request = json.dumps({'action':action,'turn_command':command}, sort_keys=True)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT request,result FROM agent_operations WHERE operation_id=?',(operation,)).fetchone()
            if previous:
                if previous[0] != request: raise CommandError('operation_conflict','operation_id conflicts')
                return json.loads(previous[1])
            if action == 'begin':
                text = command.get('text')
                if not isinstance(text,str) or not text.strip(): raise CommandError('invalid_command','text is required')
                row = db.execute('SELECT result FROM agent_tasks WHERE task_id=?',(command.get('task_id'),)).fetchone()
                if not row: raise CommandError('not_found','Unknown task')
                task = json.loads(row[0])
                if type(command.get('expected_version')) is not int or command['expected_version'] != task['version']: raise CommandError('version_conflict','Task version changed')
                active = [json.loads(r[0]) for r in db.execute('SELECT result FROM agent_turns WHERE task_id=?',(task['task_id'],))]
                if any(t['status']=='running' for t in active): raise CommandError('version_conflict','Task has an unfinished turn')
                if not task['messages']: task['title'] = text[:24]
                task['messages'].append({'role':'You','text':text}); task['version'] += 1
                turn = {'schema_version':1,'turn_id':operation,'operation_id':operation,'entity_id':operation,'task_id':task['task_id'],'session_id':task['session_id'],'input_version':task['version'],'version':1,'status':'running','text':text,'result':'','error':'','attempts':[]}
                db.execute('UPDATE agent_tasks SET result=? WHERE task_id=?',(json.dumps(task),task['task_id']))
            else:
                row = db.execute('SELECT result FROM agent_turns WHERE turn_id=?',(command.get('turn_id'),)).fetchone()
                if not row: raise CommandError('not_found','Unknown turn')
                turn = json.loads(row[0])
                if turn['status'] != 'running': raise CommandError('version_conflict','Turn already finished')
                if action == 'attempt':
                    snapshots = command.get('snapshots')
                    if not isinstance(snapshots,dict): raise CommandError('invalid_command','snapshots required')
                    turn['attempts'].append({'operation_id':operation,'snapshots':snapshots})
                else:
                    if type(command.get('expected_version')) is not int or command['expected_version'] != turn['version']: raise CommandError('version_conflict','Turn version changed')
                    status = command.get('status')
                    if status not in ('completed','failed','recovery_required','cancelled'): raise CommandError('invalid_command','Invalid turn status')
                    result,error = command.get('result',''),command.get('error','')
                    if not isinstance(result,str) or not isinstance(error,str): raise CommandError('invalid_command','Invalid turn result')
                    turn.update(status=status,result=result,error=error,version=turn['version']+1)
                    if status == 'completed':
                        task = json.loads(db.execute('SELECT result FROM agent_tasks WHERE task_id=?',(turn['task_id'],)).fetchone()[0])
                        task['messages'].append({'role':'Horizon','text':result}); task['version'] += 1
                        db.execute('UPDATE agent_tasks SET result=? WHERE task_id=?',(json.dumps(task),task['task_id']))
            turn['operation_id'] = operation
            turn['entity_id'] = turn['turn_id']
            encoded = json.dumps(turn)
            db.execute('INSERT INTO agent_turns VALUES(?,?,?) ON CONFLICT(turn_id) DO UPDATE SET result=excluded.result',(turn['turn_id'],turn['task_id'],encoded))
            db.execute('INSERT INTO agent_operations VALUES(?,?,?)',(operation,request,encoded))
            db.execute('INSERT INTO agent_events(operation_id,event) VALUES(?,?)',(operation,json.dumps({'schema_version':1,'type':'agent.turn_'+action,'task_id':turn['task_id'],'turn_id':turn['turn_id'],'operation_id':operation})))
            self._transaction_observer('before_commit')
        self._transaction_observer('after_commit')
        return turn
