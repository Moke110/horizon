"""Headless console and agent worker for the TypeScript desktop."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from hostrun import console, console_events, embedded_agent

class DesktopWorker:
    def run_agent(self, task):
        try:
            argv = embedded_agent.argv(self.home, task, kind='agent', code=Path(self.resolved['code']))
            env = dict(os.environ, FRONTIER_PYTHON=sys.executable)
            self.process = subprocess.Popen(argv, cwd=self.resolved['code'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            # Drain stderr without exposing provider credentials in UI logs.
            threading.Thread(target=lambda p=self.process: p.stderr.read(), daemon=True).start()
            if self.cancelled.is_set():
                self.process.terminate()
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                self.events.put(('agent', event))
            code = self.process.wait()
            if code:
                self.events.put(('agent', {'type': 'error', 'message': f'Agent 退出 ({code})'}))
        except Exception as exc:
            self.events.put(('agent', {'type': 'error', 'message': str(exc)}))
        finally:
            self.process = None
            self.events.put(('done', None))

    def stop(self):
        self.cancelled.set()
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
