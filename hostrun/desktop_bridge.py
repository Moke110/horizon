"""Authenticated loopback bridge; Python owns the Windows job and GUI lifetime."""
import json
import hmac
import os
import queue
import subprocess
import sys
import threading
import socket
import secrets
import uuid
from hostrun.agent_service import AgentService, CommandError, desktop_home
from pathlib import Path
from hostrun import embedded_agent, console, desktop_runtime, desktop_processes


def authenticate(incoming, token):
    supplied = incoming.readline(129).rstrip('\r\n')
    if not hmac.compare_digest(supplied, token):
        raise PermissionError('Desktop bridge authentication failed')


def serve(resolved):
    from hostrun.desktop_worker import DesktopWorker
    code = Path(resolved['code'])
    executable = desktop_runtime.executable(code)
    env = dict(os.environ)
    env.pop('ELECTRON_RUN_AS_NODE', None)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(15)
    token = secrets.token_hex(32)
    env['HORIZON_DESKTOP_PORT'] = str(listener.getsockname()[1])
    env['HORIZON_DESKTOP_TOKEN'] = token
    log_path = Path(resolved['runtime']) / 'desktop-electron.log'
    env['ELECTRON_LOG_FILE'] = str(log_path)
    env['ELECTRON_ENABLE_LOGGING'] = '1'
    with log_path.open('w', encoding='utf-8') as error_log:
        child = desktop_processes.GuiProcess([str(executable), str(code / 'desktop')], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=error_log, env=env)
    try:
        connection, _ = listener.accept()
        connection.settimeout(15)
        incoming = connection.makefile('r', encoding='utf-8')
        outgoing = connection.makefile('w', encoding='utf-8')
        authenticate(incoming, token)
        connection.settimeout(None)
    except Exception:
        child.terminate()
        child.wait(timeout=10)
        raise
    finally:
        listener.close()
    app = DesktopWorker()
    app.resolved, app.home = resolved, Path(resolved['runtime'])
    app.events, app.answers = queue.Queue(), queue.Queue()
    app.closed, app.busy, app.process = False, False, None
    app.cancelled = threading.Event()
    service = AgentService(desktop_home(), token=token)
    chats = service.list_tasks(token=token)
    current, response = 0, ''
    lock = threading.Lock()
    ui_ready = threading.Event()
    def emit(kind, data=None):
        with lock:
            try:
                outgoing.write(json.dumps({'kind': kind, 'data': data}, ensure_ascii=False) + '\n')
                outgoing.flush()
            except (OSError, ValueError):
                pass
    def create(operation=None):
        nonlocal current
        result = service.create_task({'schema_version': 1, 'operation_id': operation or str(uuid.uuid4()),
                             'title': '新会话'}, token=token)
        chats[:] = service.list_tasks(token=token)
        current = next(index for index, task in enumerate(chats) if task['task_id'] == result['task_id'])
    def message(role, text, operation=None, expected_version=None):
        previous_version = chats[current]['version']
        result = service.append_message({'schema_version': 1, 'operation_id': operation or str(uuid.uuid4()),
                                'task_id': chats[current]['task_id'], 'expected_version': previous_version if expected_version is None else expected_version, 'role': role, 'text': text}, token=token)
        chats[:] = service.list_tasks(token=token)
        return result['version'] > previous_version
    def state():
        facts = embedded_agent.runtime(app.home, code=code)
        emit('state', {'chats': chats, 'current': current, 'busy': app.busy,
                       'model': facts['model'], 'provider': facts['provider'], 'configured': facts['configured']})
    def commands():
        nonlocal current, response
        for line in incoming:
            try:
                cmd = json.loads(line)
                action = cmd.get('action')
                if action == 'ready': ui_ready.set()
                elif action == 'stop': app.stop()
                elif action == 'console': app.answers.put(str(cmd['text']))
                elif not app.busy:
                    if action == 'new':
                        create(cmd.get('operation_id'))
                    elif action == 'select':
                        index = int(cmd['index'])
                        if not 0 <= index < len(chats): raise ValueError('Invalid session')
                        current = index
                    elif action == 'configure':
                        embedded_agent.configure(app.home, provider=cmd['provider'], model=cmd['model'], api_key=cmd['key'], base_url=cmd.get('url', ''))
                        emit('notice', '模型配置已保存')
                    elif action == 'send':
                        if not isinstance(cmd.get('operation_id'), str) or not cmd['operation_id'].strip() or type(cmd.get('expected_version')) is not int:
                            raise CommandError('invalid_command', 'operation_id and expected_version are required')
                        text = str(cmd['text']).strip()
                        if not text: continue
                        if not chats: create()
                        if not message('You', text, cmd.get('operation_id'), cmd.get('expected_version')):
                            state()
                            continue
                        chat = chats[current]
                        response = ''; app.busy = True; app.cancelled.clear()
                        task = '\n\n'.join(m['role'] + ': ' + m['text'] for m in chat['messages'])
                        threading.Thread(target=app.run_agent, args=(task,), daemon=True).start()
                state()
            except CommandError as exc:
                chats[:] = service.list_tasks(token=token)
                state()
                emit('command_error', {'schema_version': 1, 'operation_id': cmd.get('operation_id'), 'code': exc.code, 'message': str(exc)})
                emit('notice', str(exc))
            except Exception as exc:
                emit('notice', str(exc))
    state()
    threading.Thread(target=commands, daemon=True).start()
    emit('console_done', None)
    activation = app.home / 'desktop.activate'
    exit_code = 0
    try:
        while child.poll() is None:
            if activation.exists():
                activation.unlink(missing_ok=True); emit('activate')
            try: kind, data = app.events.get(timeout=.1)
            except queue.Empty: continue
            if kind == 'agent':
                if data['type'] == 'text': response += data['text']
                elif data['type'] == 'error': response += '\n错误：' + data['message']
            elif kind == 'done':
                message('Horizon', response)
                app.busy = False; state()
            emit(kind, data)
        exit_code = child.returncode
    finally:
        outgoing.close()
        incoming.close()
        connection.close()
        app.closed = True; app.stop(); app.answers.put(None)
        if exit_code:
            raise RuntimeError(f'Electron exited before completing desktop session (exit {exit_code:#x})')
        if os.name == 'nt': os._exit(0)
