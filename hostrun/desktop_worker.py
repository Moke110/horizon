"""Headless console and agent worker for the TypeScript desktop."""
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from hostrun import console, console_events, embedded_agent

class DesktopWorker:
    def run_agent(self, task, session_id=None, history=None):
        history_path = None
        try:
            argv = embedded_agent.argv(self.home, task, kind='agent', code=Path(self.resolved['code']), session_id=session_id)
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
                self.events.put(('agent', event))
            code = self.process.wait()
            if code:
                self.events.put(('agent', {'type': 'error', 'message': f'Agent 退出 ({code})'}))
        except Exception as exc:
            self.events.put(('agent', {'type': 'error', 'message': str(exc)}))
        finally:
            for process in (getattr(self, 'child_process', None), self.process):
                if process and hasattr(process, 'poll') and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
            if history_path is not None:
                history_path.unlink(missing_ok=True)
            self.process = None
            self.events.put(('done', None))

    def delegate(self, event):
        request_id = str(event.get('requestId', ''))
        if not request_id or len(request_id) > 256:
            raise ValueError('Invalid delegation request')
        operation = str(uuid.uuid5(uuid.UUID(self.task_id), request_id))
        child = self.service.start_child({'schema_version': 1, 'operation_id': operation,
            'task_id': self.task_id, 'role': event.get('role'), 'input_text': event.get('input'),
            'input_version': self.input_version}, token=self.service_token)
        child = next(item for item in self.service.list_children(self.task_id, token=self.service_token) if item['child_id'] == child['child_id'])
        if child['status'] != 'running':
            return child
        self.events.put(('children', None))
        output = ''
        status, error = 'failed', ''
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
            for line in self.child_process.stdout:
                try: item = json.loads(line)
                except ValueError: continue
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
            'operation_id': str(uuid.uuid5(uuid.UUID(operation), 'finish')),
            'child_id': child['child_id'], 'status': status, 'result': output, 'error': error}, token=self.service_token)
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
