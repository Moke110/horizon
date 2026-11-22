"""Trusted host dispatch for an independent, bounded Keeper review child.

The public Keeper service owns pending requests and runs the dispatcher outside
its database transaction. This module turns one review context into a real
AgentService keeper child on the original task, runs the deterministic offline
reviewer as a contained subprocess, and records the reviewer result through the
same child attempt/finish ledger as other Agent children.

Evidence contract (all values bounded by the caller):

    {
      "request_id": str,
      "task_id": str, "task_version": int,
      "report_id": str, "report_version": int,
      "provenance": object,
      "groups": [{"proposal_id": str, "changes": [{
          "change_id": str, "entity": str, "action": str,
          "source": str | object | list, "relations": list?}]}]
    }

The child receives only this document, over private UTF-8 standard input, in a
sanitized environment. It has no credentials, network, tools or storage and is
always owned by the desktop job.
"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from hostrun.agent_capacity import child_slot

KEEPER_ROLE = 'keeper'
MAX_EVIDENCE_BYTES = 262144
WORKER_TIMEOUT = 30
_NAMESPACE = uuid.UUID('a4f84c7e-4bc4-5d4e-9a15-6f2c1e2f7a10')
_REQUIRED_CONTEXT = ('request_id', 'task_id', 'report_id')


class KeeperRuntimeError(RuntimeError):
    """A visible failure of the trusted Keeper child; never a silent review."""

    def __init__(self, message, *, child_id=None):
        super().__init__(message)
        self.child_id = child_id


def _bounded_text(value, name, limit=512):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise KeeperRuntimeError(name + ' must be bounded text')
    return value


def _bounded_version(value, name):
    if type(value) is not int or value < 1:
        raise KeeperRuntimeError(name + ' must be a positive integer')
    return value


def _operation(request_id, suffix):
    return str(uuid.uuid5(_NAMESPACE, 'keeper:' + request_id + ':' + suffix))


def _review_result(child):
    """Read a completed reviewer document without trusting arbitrary output."""
    try:
        document = json.loads(child.get('result') or '')
    except (TypeError, ValueError):
        raise KeeperRuntimeError('Keeper child returned invalid evidence',
                                 child_id=child.get('child_id'))
    status = document.get('status') if isinstance(document, dict) else None
    if status not in ('reviewed', 'returned'):
        raise KeeperRuntimeError('Keeper child returned no reviewer decision',
                                 child_id=child.get('child_id'))
    return {'status': status, 'reason': str(document.get('reason') or '')[:1000],
            'child_id': child['child_id']}


def _child_outcome(child):
    status = child.get('status')
    if status == 'succeeded':
        return _review_result(child)
    if status == 'running':
        raise KeeperRuntimeError('Keeper child did not finish', child_id=child.get('child_id'))
    raise KeeperRuntimeError(str(child.get('error') or 'Keeper child ' + str(status)),
                             child_id=child.get('child_id'))


def review_event(request_id, value):
    """Emit the Keeper receipt for the request the desktop dispatched."""
    if value.get('request_id', request_id) != request_id:
        raise KeeperRuntimeError('Keeper receipt belongs to another request')
    return {**value, 'request_id': request_id}


def test_principal():
    """The only principal a pre-Editor test request may run under."""
    from hostrun.keeper import KeeperPrincipal
    return KeeperPrincipal(role='editor', explicit_test=True)


def _change_source_fields(change):
    fields = change.get('fields') if isinstance(change.get('fields'), dict) else {}
    for value in (change.get('source'), change.get('sources'), change.get('source_id'),
                  change.get('evidence'), fields.get('sources'), fields.get('evidence')):
        if value is not None:
            return value
    return None


def _change_identity(change, kind, position):
    identity = change.get('entity') or change.get('target') or change.get('object') or change.get('id')
    if isinstance(identity, dict):
        identity = identity.get('id') or identity.get('entity_id') or identity.get('name')
    fields = change.get('fields') if isinstance(change.get('fields'), dict) else {}
    if not isinstance(identity, str) or not identity.strip():
        identity = {'card': fields.get('title'), 'theme': fields.get('judgement'),
                    'frontier': fields.get('question')}.get(kind)
    if not isinstance(identity, str) or not identity.strip():
        identity = '%s:%d' % (kind or 'change', position + 1)
    return identity[:512]


def _evidence_groups(groups, *, revision=False):
    """Project the core context or the documented evidence shape into one contract."""
    if not isinstance(groups, list):
        return []
    normalized = []
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            normalized.append(group)
            continue
        proposal_id = group.get('proposal_id') or group.get('id')
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            proposal_id = 'keeper-group-%d' % (index + 1)
        changes = []
        for position, change in enumerate(group.get('changes') or []):
            if not isinstance(change, dict):
                changes.append(change)
                continue
            kind = change.get('kind') if isinstance(change.get('kind'), str) else ''
            if revision:
                item = {"action": change.get("action")}
            else:
                item = dict(change)
            item['change_id'] = change.get('change_id') or '%s:change-%d' % (proposal_id, position + 1)
            item['entity'] = _change_identity(change, kind, position)
            source = _change_source_fields(change)
            if source is not None and 'source' not in item:
                item['source'] = source
            relations = change.get('relations')
            fields = change.get('fields') if isinstance(change.get('fields'), dict) else {}
            if relations is None:
                relations = fields.get('relations') or fields.get('related_cards')
            if revision and relations is None:
                relation_field = {'card': 'relations', 'theme': 'related_cards'}.get(kind)
                before, after = change.get('before') or {}, change.get('after') or {}
                if relation_field and before.get(relation_field) != after.get(relation_field):
                    relations = after.get(relation_field)
            if relations is not None:
                item['relations'] = relations
            changes.append(item)
        normalized.append({'proposal_id': proposal_id, 'changes': changes})
    return normalized


def _sanitized_environment(code_root):
    """No credentials, proxies, desktop tokens or user tool configuration."""
    names = ('SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC')
    env = {name: os.environ[name] for name in names if name in os.environ}
    env.update({
        'PYTHONPATH': str(code_root),
        'PYTHONUTF8': '1',
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONNOUSERSITE': '1',
        'PYTHONHASHSEED': '0',
    })
    return env


class KeeperRuntime:
    """One desktop-owned dispatcher; stop() terminates bounded worker processes."""

    def __init__(self, agent, *, token, python=None, code_root=None, timeout=WORKER_TIMEOUT):
        self.agent = agent
        self.token = token
        self.python = python or sys.executable
        self.code_root = Path(code_root or Path(__file__).resolve().parents[1]).resolve()
        self.timeout = timeout
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._processes = set()

    def stop(self):
        self._cancelled.set()
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
        deadline = time.monotonic() + 4
        for process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except (subprocess.TimeoutExpired, OSError):
                    try:
                        process.kill()
                    except OSError:
                        pass

    def dispatch(self, context):
        """Create or replay one bounded keeper child for an already-recorded request."""
        if not isinstance(context, dict):
            raise KeeperRuntimeError('Keeper review context must be an object')
        for field in _REQUIRED_CONTEXT:
            _bounded_text(context.get(field), field)
        request_id = context['request_id']
        task_id = context['task_id']
        report_id = context['report_id']
        task_version = _bounded_version(context.get('task_version'), 'task_version')
        report_version = _bounded_version(context.get('report_version'), 'report_version')
        provenance = context.get('provenance')
        if provenance is None:
            provenance = {}
        if not isinstance(provenance, dict):
            raise KeeperRuntimeError('provenance must be an object')
        groups = _evidence_groups(context.get('groups'), revision=bool(context.get('review_id')))
        if not groups:
            raise KeeperRuntimeError('groups must be a nonempty list')
        if self._cancelled.is_set():
            raise KeeperRuntimeError('Keeper review cancelled')
        evidence = {'schema_version': 1, 'request_id': request_id,
                    'task_id': task_id, 'task_version': task_version,
                    'report_id': report_id, 'report_version': report_version,
                    'provenance': provenance, 'groups': groups}
        encoded = json.dumps(evidence, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(encoded) > MAX_EVIDENCE_BYTES:
            raise KeeperRuntimeError('Keeper evidence exceeds the bounded input size')

        start_operation = _operation(request_id, 'start')
        child = self._find_child(task_id, start_operation)
        if child is None:
            child = self.agent.start_keeper_child({
                'schema_version': 1, 'operation_id': start_operation, 'task_id': task_id,
                'input_version': task_version, 'input_text': self._input_text(context),
                'snapshots': self._snapshots(context, attempting=False),
            }, token=self.token)
        child = self._refresh_child(task_id, child['child_id'])
        if child['status'] != 'running':
            return _child_outcome(child)
        if self._cancelled.is_set():
            self._finish(child, request_id=request_id, status='failed',
                         error='Keeper review cancelled', failure={'category': 'cancelled'})
            raise KeeperRuntimeError('Keeper review cancelled', child_id=child['child_id'])
        child = self.agent.attempt_child({
            'schema_version': 1, 'operation_id': _operation(request_id, 'attempt:' + str(child['version'])),
            'child_id': child['child_id'], 'expected_version': child['version'],
            'snapshots': self._snapshots(context, attempting=True),
        }, token=self.token)
        child = self._refresh_child(task_id, child['child_id'])
        try:
            with child_slot(self.agent, self.token, self._cancelled):
                result = self._run_worker(encoded)
            if result.get('request_id') != request_id:
                raise KeeperRuntimeError('Keeper worker returned another request')
        except BaseException as exc:
            failure = {'category': 'cancelled' if self._cancelled.is_set() else 'transient',
                       'retryable': not self._cancelled.is_set(), 'attempts': 1,
                       'message': str(exc)[:1000]}
            try:
                self._finish(child, request_id=request_id, status='failed',
                             error=str(exc), failure=failure)
            except Exception:
                pass
            if isinstance(exc, KeeperRuntimeError):
                if exc.child_id is None:
                    exc.child_id = child['child_id']
                raise
            raise KeeperRuntimeError(str(exc), child_id=child['child_id'])
        finished = self._finish(child, request_id=request_id, status='succeeded',
                                result=json.dumps(result, ensure_ascii=False))
        return _child_outcome(finished)

    def _find_child(self, task_id, start_operation):
        return next((item for item in self.agent.list_children(task_id, token=self.token)
                     if item.get('start_operation_id') == start_operation), None)

    def _refresh_child(self, task_id, child_id):
        child = next((item for item in self.agent.list_children(task_id, token=self.token)
                      if item['child_id'] == child_id), None)
        if child is None:
            raise KeeperRuntimeError('Keeper child disappeared from the ledger', child_id=child_id)
        return child

    def _finish(self, child, *, request_id, status, result='', error='', failure=None):
        command = {'schema_version': 1,
                   'operation_id': _operation(request_id, 'finish:' + str(child['version'])),
                   'child_id': child['child_id'], 'expected_version': child['version'],
                   'status': status, 'result': result[:100000], 'error': error[:100000]}
        if failure is not None:
            command['failure'] = failure
        return self.agent.finish_child(command, token=self.token)

    @staticmethod
    def _input_text(context):
        text = ('Review bounded knowledge evidence for request %s, task %s, report %s.' %
                (context['request_id'], context['task_id'], context['report_id']))
        feedback = context.get('feedback')
        if isinstance(feedback, str) and feedback.strip():
            text += ' Explorer feedback: ' + feedback
        return text

    @staticmethod
    def _snapshots(context, *, attempting):
        """Bounded ledger metadata; the task body never enters a snapshot."""
        metadata = {'request_id': context['request_id']}
        if attempting or 'review_id' in context:
            metadata['report_id'] = context['report_id']
        for field in ('review_id', 'proposal_id', 'revision', 'feedback', 'fingerprint'):
            if field in context:
                metadata[field] = context[field]
        return {'keeper': metadata}

    def _run_worker(self, payload):
        # Embedded Pythonw's _pth resolves modules relative to its own install.
        # The standalone worker belongs to the desktop's selected code root.
        worker = self.code_root / 'hostrun' / 'keeper_worker.py'
        if not worker.is_file():
            raise KeeperRuntimeError('Keeper worker script is missing from the desktop code root')
        command = [self.python, '-B', '-X', 'utf8', str(worker)]
        with self._lock:
            if self._cancelled.is_set():
                raise KeeperRuntimeError('Keeper review cancelled')
            process = subprocess.Popen(
                command, cwd=str(self.code_root), env=_sanitized_environment(self.code_root),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self._processes.add(process)
        try:
            try:
                out, _ = process.communicate(payload, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise KeeperRuntimeError('Keeper worker timed out')
            if self._cancelled.is_set():
                raise KeeperRuntimeError('Keeper review cancelled')
            if process.returncode:
                raise KeeperRuntimeError(f'Keeper worker exited with code {process.returncode}')
            lines = [line for line in out.decode('utf-8', errors='replace').splitlines() if line.strip()]
            if not lines:
                raise KeeperRuntimeError('Keeper worker returned no result')
            try:
                result = json.loads(lines[-1])
            except ValueError:
                raise KeeperRuntimeError('Keeper worker returned invalid JSON')
            if not isinstance(result, dict) or result.get('status') not in ('reviewed', 'returned'):
                raise KeeperRuntimeError('Keeper worker returned no reviewer decision')
            return result
        finally:
            with self._lock:
                self._processes.discard(process)
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
