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
from hostrun.diagnostics import record_event, export_report
from hostrun.model_catalog import providers
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
    app.home = service.home
    app.service, app.service_token = service, token
    from hostrun.context_versions import query_versions
    app.version_provider = query_versions
    chats = service.list_tasks(token=token)
    current, response = 0, ''
    lock = threading.Lock()
    ui_ready = threading.Event()
    exiting = threading.Event()
    configuring = threading.Event()
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
                       'configuring': configuring.is_set(), 'model': facts['model'], 'provider': facts['provider'], 'configured': facts['configured'], 'models': facts.get('models', []), 'providers': providers(), 'turns': service.list_turns(chats[current]['task_id'], token=token) if chats else [], 'children': service.list_children(chats[current]['task_id'], token=token) if chats else []})
    def start_turn(turn):
        nonlocal current, response
        chats[:] = service.list_tasks(token=token)
        current = next(i for i,t in enumerate(chats) if t['task_id'] == turn['task_id'])
        chat = chats[current]
        response = ''; app.busy = True; app.cancelled.clear()
        app.task_id, app.input_version, app.turn = turn['task_id'], turn['input_version'], turn
        threading.Thread(target=app.run_agent, args=(turn['text'],turn['session_id'],chat['messages'][:-1]),daemon=True).start()

    def discover_models(command):
        try:
            if command['action'] == 'configure':
                embedded_agent.discover_and_configure(app.home, provider=command['provider'], api_key=command['key'], base_url=command.get('url', ''))
                emit('configuration_saved')
            else:
                embedded_agent.refresh_models(app.home)
        except Exception as exc:
            emit('configuration_error' if command['action'] == 'configure' else 'notice', str(exc))
        finally:
            configuring.clear()
            state()

    def commands():
        nonlocal current, response
        for line in incoming:
            try:
                cmd = json.loads(line)
                action = cmd.get('action')
                if configuring.is_set() and action in ('configure', 'refresh_models', 'select_model', 'send'):
                    raise CommandError('configuration_busy', '正在查询模型，请稍后重试')
                if action == 'ready': ui_ready.set()
                elif action == 'export_diagnostics':
                    report = export_report(service.home, service.home / 'diagnostics' / 'reports', code=code, tasks=service.list_turns(token=token))
                    emit('notice', '诊断报告已导出：' + str(report))
                elif action == 'exit':
                    exiting.set()
                    for turn in service.recoverable_turns(token=token):
                        while turn['status'] == 'running':
                            try:
                                service.transition_turn({'schema_version':1,'operation_id':str(uuid.uuid4()),'turn_id':turn['turn_id'],'expected_version':turn['version'],'action':'pause'},token=token)
                                break
                            except CommandError as exc:
                                if exc.code != 'version_conflict': raise
                                turn = next(t for t in service.list_turns(token=token) if t['turn_id'] == turn['turn_id'])
                    app.stop()
                    emit('exit_ready')
                    break
                elif action in ('stop','resume_turn','cancel_turn'):
                    turn_id = cmd.get('turn_id') or getattr(app,'turn',{}).get('turn_id')
                    target = next(t for t in service.list_turns(token=token) if t['turn_id'] == turn_id)
                    service.transition_turn({'schema_version':1,'operation_id':cmd.get('operation_id') or str(uuid.uuid4()),'turn_id':turn_id,'expected_version':target['version'],'action':'resume' if action == 'resume_turn' else 'cancel'},token=token)
                    record_event(service.home, 'task_transition', turn_id=turn_id, status='running' if action == 'resume_turn' else 'cancelled')
                    if action != 'resume_turn' and (getattr(app,'turn',None) or {}).get('turn_id') == turn_id: app.stop()
                elif action == 'console': app.answers.put(str(cmd['text']))
                elif not app.busy:
                    if action == 'new':
                        create(cmd.get('operation_id'))
                    elif action == 'select':
                        index = int(cmd['index'])
                        if not 0 <= index < len(chats): raise ValueError('Invalid session')
                        current = index
                    elif action in ('configure', 'refresh_models'):
                        configuring.set()
                        threading.Thread(target=discover_models, args=(dict(cmd),), daemon=True).start()
                    elif action == 'select_model':
                        embedded_agent.select_model(app.home, cmd['model'])
                    elif action == 'send':
                        if not isinstance(cmd.get('operation_id'), str) or not cmd['operation_id'].strip() or type(cmd.get('expected_version')) is not int:
                            raise CommandError('invalid_command', 'operation_id and expected_version are required')
                        text = str(cmd['text']).strip()
                        if not text: continue
                        if not chats: create()
                        turn = service.begin_turn({'schema_version':1,'operation_id':cmd['operation_id'],'task_id':chats[current]['task_id'],'expected_version':cmd['expected_version'],'text':text},token=token)
                        latest = next(t for t in service.list_turns(turn['task_id'],token=token) if t['turn_id'] == turn['turn_id'])
                        if latest['status'] == 'running': start_turn(latest)
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
            if not exiting.is_set() and not app.busy and ui_ready.is_set():
                pending_turns = service.recoverable_turns(token=token)
                if pending_turns: start_turn(pending_turns[0]); state()
            if activation.exists():
                activation.unlink(missing_ok=True); emit('activate')
            try: kind, data = app.events.get(timeout=.1)
            except queue.Empty: continue
            if kind == 'children':
                state()
            if kind == 'agent':
                if data['type'] == 'text': response += data['text']
                elif data['type'] == 'error': response += '\n错误：' + data['message']
            elif kind == 'done':
                if not data or not data.get('durable'):
                    if getattr(app, 'turn', None):
                        turn = app.turn
                        service.commit_turn({'schema_version':1,'operation_id':str(uuid.uuid5(uuid.UUID(turn['task_id']),turn['turn_id']+':complete')),'turn_id':turn['turn_id'],'expected_version':turn['version'],'status':'completed','result':response,'error':''},token=token)
                    else: message('Horizon', response)
                chats[:] = service.list_tasks(token=token)
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
        if os.name == 'nt':
            record_event(service.home, 'desktop_exit', pid=os.getpid(), exit_code=0,
                         status='explicit_exit' if exiting.is_set() else 'renderer_closed')
            os._exit(0)
