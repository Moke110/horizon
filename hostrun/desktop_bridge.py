"""Authenticated loopback bridge; Python owns the Windows job and GUI lifetime."""
import json
import os
import queue
import subprocess
import sys
import threading
import socket
import secrets
from pathlib import Path
from hostrun import embedded_agent, console, desktop_runtime, desktop_processes


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
    env['FRONTIER_DESKTOP_PORT'] = str(listener.getsockname()[1])
    env['FRONTIER_DESKTOP_TOKEN'] = token
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
        if incoming.readline(129).strip() != token:
            raise RuntimeError('Desktop bridge authentication failed')
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
    store = app.home / 'desktop/sessions.json'
    try:
        chats = json.loads(store.read_text(encoding='utf-8'))
    except FileNotFoundError:
        chats = []
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
    def save():
        store.parent.mkdir(parents=True, exist_ok=True)
        temp = store.with_suffix('.tmp')
        temp.write_text(json.dumps(chats, ensure_ascii=False), encoding='utf-8')
        temp.replace(store)
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
                        chats.insert(0, {'title': '新会话', 'messages': []}); current = 0; save()
                    elif action == 'select':
                        index = int(cmd['index'])
                        if not 0 <= index < len(chats): raise ValueError('Invalid session')
                        current = index
                    elif action == 'configure':
                        embedded_agent.configure(app.home, provider=cmd['provider'], model=cmd['model'], api_key=cmd['key'], base_url=cmd.get('url', ''))
                        emit('notice', '模型配置已保存')
                    elif action == 'send':
                        text = str(cmd['text']).strip()
                        if not text: continue
                        if not chats: chats.append({'title': '新会话', 'messages': []})
                        chat = chats[current]
                        if not chat['messages']: chat['title'] = text[:24]
                        chat['messages'].append({'role': 'You', 'text': text})
                        save(); response = ''; app.busy = True; app.cancelled.clear()
                        task = '\n\n'.join(m['role'] + ': ' + m['text'] for m in chat['messages'])
                        threading.Thread(target=app.run_agent, args=(task,), daemon=True).start()
                state()
            except Exception as exc:
                emit('notice', str(exc))
    state()
    threading.Thread(target=commands, daemon=True).start()
    console_thread = threading.Thread(target=app.run_console, daemon=True)
    # Do not start resident business services before the renderer is usable.
    console_started = False
    activation = app.home / 'desktop.activate'
    exit_code = 0
    try:
        while child.poll() is None:
            if ui_ready.is_set() and not console_started:
                console_thread.start()
                console_started = True
            if activation.exists():
                activation.unlink(missing_ok=True); emit('activate')
            try: kind, data = app.events.get(timeout=.1)
            except queue.Empty: continue
            if kind == 'agent':
                if data['type'] == 'text': response += data['text']
                elif data['type'] == 'error': response += '\n错误：' + data['message']
            elif kind == 'done':
                chats[current]['messages'].append({'role': 'Frontier', 'text': response})
                app.busy = False; save(); state()
            emit(kind, data)
        exit_code = child.returncode
    finally:
        outgoing.close()
        incoming.close()
        connection.close()
        app.closed = True; app.stop(); app.answers.put(None)
        if console_started:
            console_thread.join(timeout=10)
        console.write_state(app.home, service='stopped')
        if exit_code:
            raise RuntimeError(f'Electron exited before completing desktop session (exit {exit_code:#x})')
        if os.name == 'nt': os._exit(0)
