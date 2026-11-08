"""Headless console and agent worker for the TypeScript desktop."""
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from hostrun import console, console_events, embedded_agent
from hostrun.diagnostics import record_event

class DesktopWorker:
    def run_agent(self, task, session_id=None, history=None):
        record_event(self.home, "task_started", task_id=getattr(self,"task_id", ""))
        history_path = None
        completed_text = None
        failure = None
        turn = getattr(self, 'turn', None)
        committed = False
        try:
            argv = embedded_agent.argv(self.home, task, kind='agent', code=Path(self.resolved['code']), session_id=session_id)
            turn = getattr(self, 'turn', None)
            if turn:
                argv.extend(['--turn-id', turn['turn_id']])
                self.current_snapshots = self.version_provider()
                self.service.attempt_turn({'schema_version':1,'operation_id':str(uuid.uuid4()),'turn_id':turn['turn_id'], 'snapshots': self.current_snapshots}, token=self.service_token)
            if session_id is not None and history:
                history_path = self.home / 'agent' / 'history' / (session_id + '.json')
                history_path.parent.mkdir(parents=True, exist_ok=True)
                history_path.write_text(json.dumps(history, ensure_ascii=False), encoding='utf-8')
                argv.extend(['--history-file', str(history_path)])
            env = dict(os.environ, HORIZON_PYTHON=sys.executable)
            self.process = subprocess.Popen(argv, cwd=self.resolved['code'], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            # Drain stderr without exposing provider credentials in UI logs.
            threading.Thread(target=lambda p=self.process: p.stderr.read(), daemon=True).start()
            if self.cancelled.is_set():
                self.process.terminate()
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('type') == 'delegate_request':
                    try:
                        result = self.delegate(event)
                    except Exception:
                        result = {'status': 'failed', 'error': 'Delegation rejected by the Agent service'}
                    self.process.stdin.write(json.dumps({'requestId': event['requestId'], 'result': result}) + '\n')
                    self.process.stdin.flush()
                    continue
                if event.get('type') in ('retry','error'):
                    record_event(self.home, event['type'], **{k:v for k,v in event.items() if k != 'type'})
                if event.get('type') == 'error':
                    failure = {key:event.get(key) for key in ('category','retryable','attempts','message')}
                    if failure.get('category') not in ('authentication','configuration','quota','transient','permanent','cancelled'):
                        failure['category'] = 'permanent'
                if event.get('type') == 'final':
                    completed_text = event.get('text', '')
                self.events.put(('agent', event))
            code = self.process.wait()
            if not code and completed_text is not None and turn:
                self.service.commit_turn({'schema_version':1,'operation_id':str(uuid.uuid5(uuid.UUID(self.task_id), turn['turn_id'] + ':complete')), 'turn_id':turn['turn_id'], 'expected_version':turn['version'], 'status':'completed','result':completed_text,'error':''},token=self.service_token)
                committed = True
            if code:
                self.events.put(('agent', {'type': 'error', 'message': f'Agent 退出 ({code})'}))
        except Exception as exc:
            self.events.put(('agent', {'type': 'error', 'message': str(exc)}))
        finally:
            record_event(self.home, "task_finished", task_id=getattr(self,"task_id", ""), status="completed" if committed else "interrupted")
            try:
                if turn and not committed:
                    self.service.commit_turn({'schema_version':1,'operation_id':str(uuid.uuid5(uuid.UUID(self.task_id),turn['turn_id']+':incomplete:'+str(turn['version']))),'turn_id':turn['turn_id'],'expected_version':turn['version'],'status':'cancelled' if self.cancelled.is_set() else 'recovery_required','result':'','error':(failure or {}).get('message') or 'Execution interrupted; review before retrying','failure':failure},token=self.service_token)
            except Exception:
                self.events.put(('agent', {'type':'error','message':'Could not persist execution state; recovery will consult the committed ledger'}))
            finally:
                try:
                    for process in (getattr(self, 'child_process', None), self.process):
                        if process and hasattr(process, 'poll') and process.poll() is None:
                            try:
                                process.terminate()
                                process.wait(timeout=10)
                            except Exception:
                                process.kill()
                                process.wait(timeout=10)
                finally:
                    try:
                        if history_path is not None:
                            history_path.unlink(missing_ok=True)
                    finally:
                        self.process = None
                        self.events.put(('done', {'durable': True} if turn else None))

    def delegate(self, event):
        request_id = str(event.get('requestId', ''))
        if not request_id or len(request_id) > 256:
            raise ValueError('Invalid delegation request')
        operation = str(uuid.uuid5(uuid.UUID(self.task_id), json.dumps([self.input_version, request_id])))
        existing = next((item for item in self.service.list_children(self.task_id, token=self.service_token) if item.get('start_operation_id') == operation), None)
        if existing:
            if existing['role'] != event.get('role') or existing['input_text'] != event.get('input'):
                raise ValueError('Delegation identity conflicts with committed input')
            child = existing
        else:
            child = self.service.start_child({'schema_version': 1, 'operation_id': operation,
                'task_id': self.task_id, 'role': event.get('role'), 'input_text': event.get('input'),
                'input_version': self.input_version, 'snapshots': getattr(self, 'current_snapshots', {})}, token=self.service_token)
            child = next(item for item in self.service.list_children(self.task_id, token=self.service_token) if item['child_id'] == child['child_id'])
        record_event(self.home, 'child_started', child_id=child['child_id'], session_id=child['session_id'], status=child['status'])
        if child['status'] != 'running':
            return child
        child = self.service.attempt_child({'schema_version':1,'operation_id':str(uuid.uuid4()),'child_id':child['child_id'],'expected_version':child['version'],'snapshots':getattr(self,'current_snapshots',{})},token=self.service_token)
        self.events.put(('children', None))
        output = ''
        status, error = 'failed', ''
        child_failure = None
        try:
            if self.cancelled.is_set():
                raise RuntimeError('Task cancelled')
            argv = embedded_agent.argv(self.home, child['input_text'], kind='agent',
                code=Path(self.resolved['code']), session_id=child['session_id'])
            argv.extend(['--child-role', child['role']])
            self.child_process = subprocess.Popen(argv, cwd=self.resolved['code'],
                env=dict(os.environ, HORIZON_PYTHON=sys.executable), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            threading.Thread(target=lambda p=self.child_process: p.stderr.read(), daemon=True).start()
            if self.cancelled.is_set():
                self.child_process.terminate()
            for line in self.child_process.stdout:
                try: item = json.loads(line)
                except ValueError: continue
                if item.get('type') == 'error':
                    child_failure = {key:item.get(key) for key in ('category','retryable','attempts','message')}
                if item.get('type') == 'final': output = str(item.get('text', ''))[:100000]
                if item.get('type') == 'tool_result':
                    self.events.put(('agent', {'type': 'tool_result', 'name': child['role'] + ': ' + str(item.get('name')), 'isError': item.get('isError', False)}))
            exit_code = self.child_process.wait()
            if exit_code or self.cancelled.is_set() or not output.strip():
                raise RuntimeError('Child failed or cancelled')
            status = 'succeeded'
        except Exception:
            error = 'Child execution failed or was cancelled; completed sibling results are retained.'
        finally:
            child_process = getattr(self, 'child_process', None)
            if child_process and child_process.poll() is None:
                child_process.terminate()
                child_process.wait(timeout=10)
            self.child_process = None
        completed = self.service.finish_child({'schema_version': 1,
            'operation_id': str(uuid.uuid5(uuid.UUID(operation), 'finish:'+str(child['version']))),
            'child_id': child['child_id'], 'expected_version': child['version'], 'status': status, 'result': output, 'error': error, 'failure': child_failure}, token=self.service_token)
        record_event(self.home, 'child_finished', child_id=child['child_id'], status=status)
        self.events.put(('children', None))
        return completed

    def stop(self):
        self.cancelled.set()
        child = getattr(self, 'child_process', None)
        if child and child.poll() is None:
            child.terminate()
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def read(self, prompt=''):
        if self.closed:
            raise EOFError
        self.events.put(('prompt', prompt))
        value = self.answers.get()
        if value is None:
            raise EOFError
        return value

    def write(self, text):
        self.events.put(('log', text))

    def flush(self):
        pass

    def run_console(self):
        try:
            from hostrun import update_kb_review
            result = console.serve(self.home, resolved=self.resolved, isatty=lambda: True, reader=self.read, out=self, set_title=lambda _: True, events=console_events.live,
                review=lambda home, service=None: update_kb_review.run(home, service=service, out=self, isatty=lambda: True, key_reader=lambda: self.read('审阅选项：')))
            self.write('\nConsole: ' + str(result.get('reason', 'stopped')) + '\n')
        except Exception as exc:
            self.write('\nConsole error: ' + str(exc) + '\n')
        finally:
            self.events.put(('console_done', None))
