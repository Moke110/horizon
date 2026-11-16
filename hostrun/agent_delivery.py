"""Agent-owned durable delivery ledger; channels never own task state."""
import json
import uuid
from hostrun.agent_service import CommandError


class KnownDeliveryFailure(Exception):
    """Channel proves this attempt had no remote side effect."""


class DeliveryService:
    def __init__(self, agent, *, channel=None):
        self.agent, self.channel = agent, channel
        with agent._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS agent_deliveries (delivery_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, result TEXT NOT NULL)')

    def list(self, task_id, *, token):
        self.agent._authorize(token)
        with self.agent._connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT result FROM agent_deliveries WHERE task_id=? ORDER BY rowid', (task_id,))]

    def _command(self, command, token, change):
        self.agent._authorize(token)
        if not isinstance(command, dict) or type(command.get('schema_version')) is not int or command['schema_version'] != 1 or not isinstance(command.get('operation_id'), str) or not command['operation_id'].strip():
            raise CommandError('invalid_command', 'schema_version and operation_id required')
        request = json.dumps(command, sort_keys=True)
        with self.agent._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT request,result FROM agent_operations WHERE operation_id=?', (command['operation_id'],)).fetchone()
            if prior:
                if prior[0] != request: raise CommandError('operation_conflict', 'operation_id conflicts')
                return json.loads(prior[1])
            result = change(db)
            encoded = json.dumps(result)
            db.execute('INSERT INTO agent_deliveries VALUES(?,?,?) ON CONFLICT(delivery_id) DO UPDATE SET result=excluded.result', (result['delivery_id'], result['task_id'], encoded))
            db.execute('INSERT INTO agent_operations VALUES(?,?,?)', (command['operation_id'], request, encoded))
            db.execute('INSERT INTO agent_events(operation_id,event) VALUES(?,?)', (command['operation_id'], json.dumps(dict(schema_version=1,type='agent.delivery_changed',delivery_id=result['delivery_id'],task_id=result['task_id'],status=result['status']))))
            self.agent._transaction_observer('before_commit')
        self.agent._transaction_observer('after_commit')
        return result

    def enqueue(self, command, *, token):
        def change(db):
            row = db.execute('SELECT result FROM agent_turns WHERE turn_id=?', (command.get('turn_id'),)).fetchone()
            if not row: raise CommandError('not_found', 'Unknown turn')
            turn = json.loads(row[0])
            if turn['status'] != 'completed': raise CommandError('invalid_state', 'Only completed generation can be delivered')
            self._not_cancelled(db,turn)
            return dict(schema_version=1, delivery_id=str(uuid.uuid4()),task_id=turn['task_id'],turn_id=turn['turn_id'],text=turn['result'],version=1,status='queued',attempts=[],receipts=[])
        return self._command(command, token, change)

    def _load(self, db, command):
        row = db.execute('SELECT result FROM agent_deliveries WHERE delivery_id=?',(command.get('delivery_id'),)).fetchone()
        if not row: raise CommandError('not_found','Unknown delivery')
        value = json.loads(row[0])
        if type(command.get('expected_version')) is not int or command['expected_version'] != value['version']: raise CommandError('version_conflict','Delivery changed')
        return value

    def send(self, command, *, token):
        if self.channel is None: raise CommandError('configuration','No delivery channel configured')
        claimed = False
        def reserve(db):
            nonlocal claimed
            value = self._load(db, command)
            if value['status'] not in ('queued','failed'): raise CommandError('invalid_state','Delivery requires reconciliation')
            self._not_cancelled(db,value)
            if len(value['attempts']) >= 3: raise CommandError('retry_exhausted','Delivery attempt budget exhausted')
            claimed = True
            value['status']='sending'; value['version']+=1
            value['attempts'].append({'key':str(uuid.uuid4())})
            return value
        value = self._command(command, token, reserve)
        if not claimed:
            return next(v for v in self.list(value['task_id'],token=token) if v['delivery_id']==value['delivery_id'])
        # Keep SQLite's cross-process writer lease while the bounded channel
        # request is in flight. Readers still see the committed reservation.
        # Reconcile/cancel cannot overtake an active send and authorize a second.
        with self.agent._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self._load(db, dict(delivery_id=value['delivery_id'],expected_version=value['version']))
            self._not_cancelled(db,current)
            try:
                receipt = self.channel.send(value, value['attempts'][-1]['key'])
                if not isinstance(receipt,dict) or not isinstance(receipt.get('remote_id'),str) or not receipt['remote_id']:
                    raise ValueError('Channel did not return a verified receipt')
                status='sent'
            except KnownDeliveryFailure:
                receipt=None
                status='failed'
            except Exception:
                receipt=None
                status='unknown'
            current.update(status=status,version=current['version']+1)
            if receipt is not None: current['receipts'].append(receipt)
            encoded=json.dumps(current)
            db.execute('UPDATE agent_deliveries SET result=? WHERE delivery_id=?',(encoded,current['delivery_id']))
            finish_operation=command['operation_id']+':receipt'
            db.execute('INSERT INTO agent_events(operation_id,event) VALUES(?,?)',(finish_operation,json.dumps(dict(schema_version=1,type='agent.delivery_changed',delivery_id=current['delivery_id'],task_id=current['task_id'],status=status))))
            self.agent._transaction_observer('before_commit')
        self.agent._transaction_observer('after_commit')
        return current

    def _not_cancelled(self,db,value):
        turns=[json.loads(r[0]) for r in db.execute('SELECT result FROM agent_turns WHERE task_id=?',(value['task_id'],))]
        if any(t['status']=='cancelled' for t in turns): raise CommandError('cancelled','Cancelled task cannot send')

    def reconcile(self,command,*,token):
        self.agent._authorize(token)
        with self.agent._connect() as db:
            prior=db.execute('SELECT request,result FROM agent_operations WHERE operation_id=?',(command.get('operation_id'),)).fetchone()
            if prior:
                if prior[0]!=json.dumps(command,sort_keys=True): raise CommandError('operation_conflict','operation_id conflicts')
                return json.loads(prior[1])
            value=self._load(db,command)
        if value['status'] not in ('sending','unknown'): raise CommandError('invalid_state','Only uncertain deliveries require lookup')
        try: receipt=self.channel.lookup(value,value['attempts'][-1]['key'])
        except Exception: receipt=None
        if not isinstance(receipt,dict) or not isinstance(receipt.get('remote_id'),str) or not receipt['remote_id']: receipt=None
        def change(db):
            current=self._load(db,command)
            current.update(status='sent' if receipt else 'unknown',checked=True,version=current['version']+1)
            if receipt: current['receipts'].append(receipt)
            return current
        return self._command(command,token,change)

    def handle(self, command, *, token):
        """Authenticated bridge command hook; caller refreshes its state after return."""
        action=command.get('action')
        methods={'enqueue_delivery':self.enqueue,'send_delivery':self.send,
                 'reconcile_delivery':self.reconcile,'resend_delivery':self.resend}
        if action not in methods: raise CommandError('invalid_command','Unknown delivery action')
        return methods[action](command,token=token)

    def resend(self,command,*,token):
        def change(db):
            current=self._load(db,command)
            self._not_cancelled(db,current)
            if current['status']!='unknown' or not current.get('checked') or command.get('accept_duplicate_risk') is not True:
                raise CommandError('consent_required','Reconcile first; explicit duplicate-risk consent required')
            current.update(status='queued',checked=False,version=current['version']+1)
            return current
        return self._command(command,token,change)
