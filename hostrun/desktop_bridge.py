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
from contextlib import ExitStack
from hostrun.diagnostics import record_event, export_report
from hostrun.agent_delivery import DeliveryService
from hostrun.model_catalog import providers
from hostrun.agent_service import AgentService, CommandError, desktop_home
from pathlib import Path
from hostrun import embedded_agent, console, desktop_runtime, desktop_processes


def authenticate(incoming, token):
    supplied = incoming.readline(129).rstrip('\r\n')
    if not hmac.compare_digest(supplied, token):
        raise PermissionError('Desktop bridge authentication failed')


def task_snapshot(service, selected_task_id, *, token):
    """Keep selection attached to entity identity when background tasks arrive."""
    tasks=service.list_tasks(token=token)
    current=next((i for i,t in enumerate(tasks) if t['task_id']==selected_task_id),0)
    return tasks,current


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
    from hostrun.cleanup import _lexical_home, control_directory
    home = _lexical_home(resolved['runtime'])
    managed = os.name == 'nt' and home.parent.parent == control_directory()
    with ExitStack() as logging:
        if managed:
            env.pop('ELECTRON_LOG_FILE', None)
            env.pop('ELECTRON_ENABLE_LOGGING', None)
            error_log = subprocess.DEVNULL
        else:
            log_path = home / 'desktop-electron.log'
            env['ELECTRON_LOG_FILE'] = str(log_path)
            env['ELECTRON_ENABLE_LOGGING'] = '1'
            error_log = logging.enter_context(log_path.open('w', encoding='utf-8'))
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
        failed_exit = child.wait(timeout=10)
        record_event(home, 'error', code='renderer_start_failed', exit_code=failed_exit)
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
    deliveries = DeliveryService(service)
    from hostrun.context_versions import query_versions
    app.version_provider = query_versions
    from hostrun.schedule_runtime import ScheduleRuntime
    from hostrun import agent_scheduling
    scheduler = ScheduleRuntime(service, token, resolved, app.version_provider)
    chats = service.list_tasks(token=token)
    current, response = 0, ''
    lock = threading.Lock()
    dispatch_lock = threading.RLock()
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
    def refresh_chats():
        nonlocal current
        with dispatch_lock:
            selected=chats[current]['task_id'] if chats and 0<=current<len(chats) else None
            snapshot,current=task_snapshot(service,selected,token=token)
            chats[:]=snapshot

    def create(operation=None):
        nonlocal current
        result = service.create_task({'schema_version': 1, 'operation_id': operation or str(uuid.uuid4()),
                             'title': '新会话'}, token=token)
        refresh_chats()
        current = next(index for index, task in enumerate(chats) if task['task_id'] == result['task_id'])
    def message(role, text, operation=None, expected_version=None):
        previous_version = chats[current]['version']
        result = service.append_message({'schema_version': 1, 'operation_id': operation or str(uuid.uuid4()),
                                'task_id': chats[current]['task_id'], 'expected_version': previous_version if expected_version is None else expected_version, 'role': role, 'text': text}, token=token)
        refresh_chats()
        return result['version'] > previous_version
    def state():
        with dispatch_lock:
            facts = embedded_agent.runtime(app.home, code=code)
            emit('state', {'chats': chats, 'current': current, 'busy': app.busy,
                           'deliveries':deliveries.list(chats[current]['task_id'],token=token) if chats else [], 'delivery_enabled':deliveries.channel is not None, 'schedules':service.list_schedules(token=token), 'schedule_limits':agent_scheduling.get_limits(service,token=token), 'configuring': configuring.is_set(), 'model': facts['model'], 'provider': facts['provider'], 'configured': facts['configured'], 'models': facts.get('models', []), 'providers': providers(), 'turns': service.list_turns(chats[current]['task_id'], token=token) if chats else [], 'children': service.list_children(chats[current]['task_id'], token=token) if chats else []})
    def start_turn(turn):
        nonlocal current, response
        refresh_chats()
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

    def delivery_command(command):
        try:
            deliveries.handle(command,token=token)
        except CommandError as exc:
            emit('command_error',{'schema_version':1,'operation_id':command.get('operation_id'),'code':exc.code,'message':str(exc)})
        except Exception:
            emit('notice','投递操作未能完成，请核实投递状态。')
        finally:
            state()

    def commands():
        nonlocal current, response
        for line in incoming:
            with dispatch_lock:
                try:
                    cmd = json.loads(line)
                    action = cmd.get('action')
                    if configuring.is_set() and action in ('configure', 'refresh_models', 'select_model', 'send'):
                        raise CommandError('configuration_busy', '正在查询模型，请稍后重试')
                    if action in ('enqueue_delivery','send_delivery','reconcile_delivery','resend_delivery'):
                        if exiting.is_set(): raise CommandError('exiting','Desktop is exiting')
                        threading.Thread(target=delivery_command,args=(dict(cmd),),daemon=True).start()
                    elif action == 'schedule_command': service.schedule_command(cmd['command'],token=token)
                    elif action == 'schedule_limits': agent_scheduling.set_limits(service,main=cmd['main'],children=cmd['children'],token=token)
                    elif action == 'ready': ui_ready.set()
                    elif action == 'export_diagnostics':
                        report = export_report(service.home, code=code, tasks=service.list_turns(token=token))
                        emit('notice', '诊断报告已导出：' + str(report))
                    elif action == 'exit':
                        exiting.set()
                        scheduler.stopped=True
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
                        if action != 'resume_turn':
                            for scheduled_worker, scheduled_thread in list(scheduler.workers.values()):
                                if scheduled_worker.turn['turn_id']==turn_id:scheduled_worker.stop()
                            if (getattr(app,'turn',None) or {}).get('turn_id') == turn_id: app.stop()
                    elif action == 'console': app.answers.put(str(cmd['text']))
                    elif not app.busy:
                        if action == 'new':
                            create(cmd.get('operation_id'))
                        elif action == 'select':
                            index = next((i for i,t in enumerate(chats) if t['task_id']==cmd['task_id']),-1) if 'task_id' in cmd else int(cmd['index'])
                            if not 0 <= index < len(chats): raise ValueError('Invalid session')
                            current = index
                        elif action in ('configure', 'refresh_models'):
                            configuring.set()
                            threading.Thread(target=discover_models, args=(dict(cmd),), daemon=True).start()
                        elif action == 'select_model':
                            embedded_agent.select_model(app.home, cmd['model'])
                        elif action == 'send':
                            if len(scheduler.workers)>=agent_scheduling.get_limits(service,token=token)['main']:
                                raise CommandError('capacity_busy','执行位已满，请稍后发送')
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
                    refresh_chats()
                    state()
                    emit('command_error', {'schema_version': 1, 'operation_id': cmd.get('operation_id'), 'code': exc.code, 'message': str(exc)})
                    emit('notice', str(exc))
                except Exception as exc:
                    emit('notice', str(exc))
    state()
    threading.Thread(target=commands, daemon=True).start()
    emit('console_done', None)
    from hostrun.managed_desktop import consume_activation
    exit_code = 0
    try:
        while child.poll() is None:
            with dispatch_lock:
                if not exiting.is_set() and not app.busy and ui_ready.is_set():
                    pending_turns = [t for t in service.recoverable_turns(token=token) if t['task_id'] not in scheduler.task_ids()]
                    if pending_turns and len(scheduler.workers)<agent_scheduling.get_limits(service,token=token)['main']:
                        start_turn(pending_turns[0]); state()
                if ui_ready.is_set() and scheduler.step(foreground_busy=app.busy):
                    refresh_chats();state()
            if consume_activation(app.home):
                emit('activate')
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
                refresh_chats()
                app.busy = False; state()
            emit(kind, data)
        exit_code = child.returncode
    finally:
        outgoing.close()
        incoming.close()
        connection.close()
        scheduler.stop()
        app.closed = True; app.stop(); app.answers.put(None)
        if exit_code:
            record_event(service.home, 'error', code='renderer_exit', exit_code=exit_code)
            raise RuntimeError(f'Electron exited before completing desktop session (exit {exit_code:#x})')
        if os.name == 'nt':
            record_event(service.home, 'desktop_exit', pid=os.getpid(), exit_code=0,
                         status='explicit_exit' if exiting.is_set() else 'renderer_closed')
            os._exit(0)
