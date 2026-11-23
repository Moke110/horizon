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
from hostrun.voyager_materials import MaterialsService
from hostrun.voyager_service import VoyagerService
from hostrun.frontier import FrontierService
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_history import KnowledgeHistoryService
from hostrun.keeper_group_undo import KeeperGroupUndo
from hostrun.knowledge_themes import KnowledgeThemeService
from hostrun.knowledge_workbench import KnowledgeWorkbenchService
from hostrun.model_catalog import providers
from hostrun.agent_service import AgentService, CommandError, desktop_home
from hostrun.editor_reports import ReportService
from hostrun.keeper_runtime import KeeperRuntime, review_event, test_principal
from pathlib import Path
from hostrun import embedded_agent, console, desktop_runtime, desktop_processes

_AUTHORITY_FIELDS = ('role', 'principal', 'actor', 'output', 'provenance')


def keeper_test_requests_enabled():
    """Launch-environment gate; renderer commands cannot enable this mode."""
    return os.environ.get('HORIZON_KEEPER_TEST_REQUESTS') == '1'


def _reject_caller_authority(command, request=None):
    for source in (command, request):
        if isinstance(source, dict):
            for field in _AUTHORITY_FIELDS:
                if field in source:
                    raise CommandError('invalid_command', 'Caller cannot supply ' + field)


def keeper_bridge_command(commands, command, *, test_enabled):
    """Route reads, Explorer decisions and the explicitly gated test request."""
    action = command.get('action')
    if action in ('keeper_list', 'keeper_open'):
        return commands.read(command)
    if action == 'keeper_decide':
        return commands.decide(command)
    if action == 'keeper_undo_preview':
        return commands.undo_preview(command)
    if action == 'keeper_undo':
        return commands.undo(command)
    if action == 'keeper_revise':
        return commands.revise(command)
    if action == 'keeper_audit':
        return commands.audit(command)
    if action == 'keeper_test_request':
        if not test_enabled:
            raise CommandError('invalid_command', 'Keeper test requests are disabled')
        commands.test_request(dict(command))
        return None
    if action == 'keeper_request':
        raise CommandError('invalid_command',
                           'keeper_request is unavailable; use the explicit test request')
    raise CommandError('invalid_command', 'Unknown Keeper action')


class KeeperCommands:
    """Keeper reads, trusted Explorer decisions and explicit test review."""

    def __init__(self, service, runtime, *, token, emit):
        self._service = service
        self.runtime = runtime
        self.token = token
        self.emit = emit
        self._lock = threading.Lock()
        self._jobs = set()

    def read(self, command):
        action = command.get('action')
        keeper = self._service()
        if action == 'keeper_list':
            status = command.get('status')
            if status is not None and (not isinstance(status, str) or not status.strip()):
                raise CommandError('invalid_command', 'status must be text')
            proposals = keeper.list(token=self.token, **({'status': status} if status else {}))
            return 'keeper_list', {'operation_id': command.get('operation_id'),
                                   'proposals': proposals}
        if action == 'keeper_open':
            proposal_id = command.get('proposal_id')
            if not isinstance(proposal_id, str) or not proposal_id.strip():
                raise CommandError('invalid_command', 'proposal_id is required')
            proposal = keeper.view(proposal_id, token=self.token)
            return 'keeper_open', {'operation_id': command.get('operation_id'),
                                   'proposal': proposal}
        raise CommandError('invalid_command', 'Unknown Keeper action')

    @staticmethod
    def _undo_payload(command, *, commit):
        """Normalize group-undo input; the principal is never caller-supplied."""
        _reject_caller_authority(command)
        if not isinstance(command, dict):
            raise CommandError('invalid_command', 'Undo command must be an object')
        allowed = {'action', 'schema_version', 'operation_id', 'proposal_id',
                   'revision'}
        if commit:
            allowed |= {'expected_versions', 'preview_token', 'reason'}
        if set(command) - allowed:
            raise CommandError('invalid_command', 'Undo contains unsupported fields')
        if type(command.get('schema_version')) is not int \
                or command['schema_version'] != 1:
            raise CommandError('unsupported_schema', 'Unsupported schema_version')
        operation_id = command.get('operation_id')
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise CommandError('invalid_command', 'operation_id is required')
        proposal_id = command.get('proposal_id')
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise CommandError('invalid_command', 'proposal_id is required')
        revision = command.get('revision')
        if type(revision) is not int or revision < 1:
            raise CommandError('invalid_command', 'revision must be a positive integer')
        payload = {'schema_version': 1, 'operation_id': operation_id,
                   'proposal_id': proposal_id, 'revision': revision}
        if commit:
            expected = command.get('expected_versions')
            if not isinstance(expected, list) or not expected:
                raise CommandError('invalid_command',
                                   'expected_versions must be a nonempty list')
            for version in expected:
                if type(version) is not int or version < 1:
                    raise CommandError(
                        'invalid_command',
                        'expected_versions entries must be positive integers')
            token = command.get('preview_token')
            if not isinstance(token, str) or not token or not token.isascii():
                raise CommandError('invalid_command', 'preview_token is required')
            payload['expected_versions'] = list(expected)
            payload['preview_token'] = token
            if 'reason' in command:
                payload['reason'] = command['reason']
        return payload

    def undo_preview(self, command):
        """Review a whole-group reversal under the trusted Explorer principal."""
        payload = self._undo_payload(command, commit=False)
        from hostrun.keeper import KeeperPrincipal
        try:
            result = KeeperGroupUndo(self._service()).preview(
                payload, token=self.token,
                principal=KeeperPrincipal('explorer', False))
        except CommandError:
            raise
        except PermissionError as error:
            raise CommandError('unauthorized',
                               'Keeper undo preview was not authorized') from error
        except Exception as error:
            raise CommandError('keeper_failed',
                               'Keeper undo preview could not be completed') from error
        if not isinstance(result, dict):
            raise CommandError('invalid_response',
                               'Keeper undo preview returned no plan')
        for field in ('operation_id', 'proposal_id', 'revision'):
            if result.get(field) != payload[field]:
                raise CommandError('invalid_response',
                                   'Keeper undo preview does not match the request')
        return 'keeper_undo_preview', result

    def undo(self, command):
        """Commit one whole-group reversal under the trusted Explorer principal."""
        payload = self._undo_payload(command, commit=True)
        from hostrun.keeper import KeeperPrincipal
        try:
            result = KeeperGroupUndo(self._service()).commit(
                payload, token=self.token,
                principal=KeeperPrincipal('explorer', False))
        except CommandError:
            raise
        except PermissionError as error:
            raise CommandError('unauthorized',
                               'Keeper undo was not authorized') from error
        except Exception as error:
            raise CommandError('keeper_failed',
                               'Keeper undo could not be completed') from error
        if not isinstance(result, dict):
            raise CommandError('invalid_response',
                               'Keeper undo returned no receipt')
        for field in ('operation_id', 'proposal_id', 'revision'):
            if result.get(field) != payload[field]:
                raise CommandError('invalid_response',
                                   'Keeper undo receipt does not match the request')
        if result.get('status') != 'undone' or not isinstance(result.get('applied'), list):
            raise CommandError('invalid_response',
                               'Keeper undo receipt has an unexpected status')
        return 'keeper_undo', result

    @staticmethod
    def _decide_command(command):
        """Normalize the Explorer payload; the principal is never caller-supplied."""
        _reject_caller_authority(command)
        if type(command.get('schema_version')) is not int \
                or command['schema_version'] != 1:
            raise CommandError('unsupported_schema', 'Unsupported schema_version')
        operation_id = command.get('operation_id')
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise CommandError('invalid_command', 'operation_id is required')
        proposal_id = command.get('proposal_id')
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise CommandError('invalid_command', 'proposal_id is required')
        revision = command.get('revision')
        if type(revision) is not int or revision < 1:
            raise CommandError('invalid_command', 'revision must be a positive integer')
        decision = command.get('decision')
        if decision not in ('approve', 'reject'):
            raise CommandError('invalid_command', 'decision must be approve or reject')
        expected_versions = command.get('expected_versions')
        if not isinstance(expected_versions, list) or not expected_versions:
            raise CommandError('invalid_command',
                               'expected_versions must be a nonempty list')
        for version in expected_versions:
            if version is None:
                continue
            if type(version) is not int or version < 1:
                raise CommandError('invalid_command',
                                   'expected_versions entries must be positive integers or null')
        payload = {
            'schema_version': 1, 'operation_id': operation_id,
            'proposal_id': proposal_id, 'revision': revision,
            'expected_versions': list(expected_versions), 'decision': decision,
        }
        if 'reason' in command:
            reason = command['reason']
            if reason is not None and not isinstance(reason, str):
                raise CommandError('invalid_command', 'reason must be text')
            if reason is not None:
                payload['reason'] = reason
        return payload

    def decide(self, command):
        """Apply one whole-group Explorer decision under a trusted Explorer principal."""
        payload = self._decide_command(command)
        from hostrun.keeper import KeeperPrincipal
        try:
            result = self._service().decide(
                payload, token=self.token,
                principal=KeeperPrincipal('explorer', False))
        except CommandError:
            raise
        except PermissionError as error:
            raise CommandError('unauthorized',
                               'Keeper decision was not authorized') from error
        except Exception as error:
            raise CommandError('keeper_failed',
                               'Keeper decision could not be completed') from error
        if not isinstance(result, dict):
            raise CommandError('invalid_response', 'Keeper decision returned no receipt')
        for field in ('operation_id', 'proposal_id', 'revision', 'decision'):
            if result.get(field) != payload[field]:
                raise CommandError('invalid_response',
                                   'Keeper decision receipt does not match the request')
        expected_status = 'approved' if payload['decision'] == 'approve' else 'rejected'
        if result.get('status') == 'revising':
            review_id = result.get('review_id')
            if payload['decision'] != 'approve' or not isinstance(review_id, str) \
                    or not review_id.strip() or result.get('applied') != []:
                raise CommandError('invalid_response',
                                   'Keeper decision receipt has an unexpected status')
        elif result.get('status') != expected_status:
            raise CommandError('invalid_response',
                               'Keeper decision receipt has an unexpected status')
        if not isinstance(result.get('applied'), list):
            raise CommandError('invalid_response',
                               'Keeper decision receipt has no applied list')
        return 'keeper_decide', result

    def revise(self, command):
        """Adjust or cancel one revision under a trusted Explorer principal."""
        _reject_caller_authority(command)
        payload = {key: value for key, value in command.items() if key != 'action'}
        from hostrun.keeper import KeeperPrincipal
        try:
            result = self._service().revise(
                payload, token=self.token,
                principal=KeeperPrincipal('explorer', False))
        except CommandError:
            raise
        except PermissionError as error:
            raise CommandError('unauthorized',
                               'Keeper revision was not authorized') from error
        except Exception as error:
            raise CommandError('keeper_failed',
                               'Keeper revision could not be completed') from error
        if not isinstance(result, dict):
            raise CommandError('invalid_response', 'Keeper revision returned no receipt')
        for field in ('operation_id', 'proposal_id', 'revision', 'decision'):
            if result.get(field) != payload.get(field):
                raise CommandError('invalid_response',
                                   'Keeper revision receipt does not match the request')
        expected_status = 'revising' if payload['decision'] == 'adjust' else 'cancelled'
        if result.get('status') != expected_status or result.get('applied') != []:
            raise CommandError('invalid_response', 'Keeper revision receipt has an unexpected status')
        if expected_status == 'revising' and not result.get('review_id'):
            raise CommandError('invalid_response', 'Keeper revision receipt is missing its review ID')
        return 'keeper_revise', result

    @staticmethod
    def _audit_command(command):
        _reject_caller_authority(command)
        operation_id = command.get('operation_id')
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise CommandError('invalid_command', 'operation_id is required')
        proposal_id = command.get('proposal_id')
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise CommandError('invalid_command', 'proposal_id is required')
        return operation_id, proposal_id

    def audit(self, command):
        """Read the persisted decision audit events for one proposal."""
        operation_id, proposal_id = self._audit_command(command)
        events = self._service().audit(proposal_id, token=self.token)
        return 'keeper_audit', {'operation_id': operation_id,
                                'proposal_id': proposal_id,
                                'events': events}

    def test_request(self, command):
        request = command.get('request')
        if not isinstance(request, dict):
            raise CommandError('invalid_command', 'request is required')
        request = dict(request)
        _reject_caller_authority(command, request)
        if 'schema_version' not in request:
            request['schema_version'] = 1
        operation = request.get('operation_id') or command.get('operation_id')
        if not isinstance(operation, str) or not operation.strip():
            raise CommandError('invalid_command', 'operation_id is required')
        request['operation_id'] = operation
        saved = self._service().request(request, token=self.token,
                                        principal=test_principal())
        request_id = saved['request_id']
        self.emit('keeper_test_request', {'operation_id': command.get('operation_id') or operation,
                                          'request': saved})
        self._queue_review(request_id)

    def resume_queued(self):
        for request_id in self._service().queued(token=self.token):
            self._queue_review(request_id)

    def resume_queued_revisions(self):
        for review_id in self._service().queued_revisions(token=self.token):
            self.queue_revision(review_id)

    def _queue_review(self, request_id):
        with self._lock:
            if request_id in self._jobs:
                return
            self._jobs.add(request_id)
        threading.Thread(target=self._review, args=(request_id,), daemon=True).start()

    def _review(self, request_id):
        payload = None
        error = ''
        try:
            value = self._service().review(request_id, token=self.token,
                                           dispatch=self.runtime.dispatch)
            payload = review_event(request_id, value)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        with self._lock:
            self._jobs.discard(request_id)
        if payload is not None:
            self.emit('keeper_review', payload)
        else:
            self.emit('keeper_review_error', {'request_id': request_id,
                                              'message': error[:1000]})

    def queue_revision(self, review_id):
        """Queue one durable revision job behind the existing job lock."""
        if not isinstance(review_id, str) or not review_id.strip():
            return
        key = ('revision', review_id)
        with self._lock:
            if key in self._jobs:
                return
            self._jobs.add(key)
        threading.Thread(target=self._review_revision, args=(review_id,),
                         daemon=True).start()

    def _review_revision(self, review_id):
        payload, error = None, ''
        try:
            service = self._service()
            value = None
            for _attempt in range(3):
                value = service.review_revision(
                    review_id, token=self.token, dispatch=self.runtime.dispatch)
                if not isinstance(value, dict):
                    raise CommandError('invalid_response',
                                       'Keeper revision returned no receipt')
                if value.get('review_id') != review_id:
                    raise CommandError('invalid_response',
                                       'Keeper revision receipt belongs to another job')
                if value.get('status') != 'queued' \
                        or value.get('reason') != 'snapshot_changed':
                    break
            payload = value
        except Exception:
            error = 'Keeper revision could not be completed'
        with self._lock:
            self._jobs.discard(('revision', review_id))
        if payload is not None:
            self.emit('keeper_revision', payload)
        else:
            self.emit('keeper_revision_error', {'review_id': review_id,
                                                'message': error[:1000]})


def authenticate(incoming, token):
    supplied = incoming.readline(129).rstrip('\r\n')
    if not hmac.compare_digest(supplied, token):
        raise PermissionError('Desktop bridge authentication failed')


def task_snapshot(service, selected_task_id, *, token):
    """Keep selection attached to entity identity when background tasks arrive."""
    tasks=service.list_tasks(token=token)
    current=next((i for i,t in enumerate(tasks) if t['task_id']==selected_task_id),0)
    return tasks,current


def material_command(materials, command, *, token):
    """Route authenticated desktop material actions through Voyager's public API."""
    if command.get('action') == 'materials_register':
        value=materials.register_text({k:v for k,v in command.items() if k!='action'},token=token)
        return materials.get_package(value['material_id'],token=token)
    if command.get('action') == 'materials_pdf_register':
        value = materials.register_pdf({k:v for k,v in command.items() if k != 'action'}, token=token)
        return materials.get_package(value['material_id'], token=token)
    if command.get('action') == 'materials_pdf_resolve':
        decision = {key:value for key,value in command.items() if key != 'decision'}
        decision['action'] = command.get('decision')
        value = materials.resolve_pdf(decision, token=token)
        return materials.get_package(value['material_id'], token=token)
    if command.get('action') == 'materials_pending_resolve':
        from hostrun.voyager_pending import resolve
        return materials.get_package(resolve(materials, command, token=token), token=token, task_id=command.get('task_id'))
    if command.get('action') == 'materials_open':
        return materials.get_package(command.get('material_id'), token=token, task_id=command.get('task_id'))
    if command.get('action') == 'materials_archive':
        value = materials.archive_material(command.get('material_id'), token=token)
        return materials.get_package(value['material_id'], token=token)
    if command.get('action') == 'materials_archive_list':
        return materials.list_summaries(archived=True, token=token)
    if command.get('action') == 'materials_delete_impact':
        return materials.deletion_impact(command.get('material_id'), token=token)
    if command.get('action') == 'materials_delete':
        return materials.delete_material(command.get('material_id'), command.get('confirmation'), token=token)
    if command.get('action') == 'materials_collect_orphans':
        return materials.collect_orphan_snapshots(token=token)
    raise CommandError('invalid_command','Unknown materials action')


def frontier_command(frontier, command, *, token):
    """Route desktop Knowledge Base commands through FrontierService's public seam."""
    action = command.get("action")
    if action == "frontier_list":
        return "frontier_list", frontier.list(token=token)
    if action == "frontier_open":
        value = frontier.view(command.get("frontier_id"), token=token)
        if isinstance(command.get("operation_id"), str):
            value = {**value, "read_operation_id": command["operation_id"]}
        return "frontier_view", value
    methods = {"frontier_create": frontier.create, "frontier_update": frontier.update,
               "frontier_delete": frontier.delete}
    if action not in methods:
        raise CommandError("invalid_command", "Unknown Frontier action")
    value = methods[action]({k: v for k, v in command.items() if k != "action"}, token=token)
    return "frontier_view", value


def card_command(cards, command, *, token):
    """Route desktop Knowledge Card commands through the public service."""
    action = command.get("action")
    if action == "card_list":
        return "card_list", cards.list(token=token)
    if action == "card_query":
        return "card_query", cards.query(
            token=token, card_ids=command.get("card_ids"),
            include_archived=command.get("include_archived", False))
    if action == "card_open":
        card = cards.view(command.get("card_id"), token=token)
        if isinstance(command.get("operation_id"), str):
            card["read_operation_id"] = command["operation_id"]
        return "card_view", card
    methods = {"card_create": cards.create,
               "card_update": cards.update,
               "card_archive": cards.archive}
    if action not in methods:
        raise CommandError("invalid_command", "Unknown Knowledge Card action")
    value = methods[action]({k: v for k, v in command.items() if k != "action"},
                            token=token)
    return "card_view", value


def theme_command(themes, command, *, token):
    """Route desktop Theme commands through the public knowledge service."""
    action = command.get("action")
    if action == "theme_list":
        return "theme_list", themes.list(token=token)
    if action == "theme_query":
        return "theme_query", themes.query(
            token=token, theme_ids=command.get("theme_ids"),
            include_archived=command.get("include_archived", False))
    if action == "theme_open":
        theme = themes.view(command.get("theme_id"), token=token)
        if isinstance(command.get("operation_id"), str):
            theme["read_operation_id"] = command["operation_id"]
        return "theme_view", theme
    if action == "theme_source":
        source = themes.open_evidence(command.get("theme_id"), command.get("evidence_id"),
                                      expected_version=command.get("expected_version"), token=token)
        if isinstance(command.get("operation_id"), str):
            source["operation_id"] = command["operation_id"]
        return "theme_source", source
    methods = {"theme_create": themes.create,
               "theme_update": themes.update,
               "theme_archive": themes.archive}
    if action not in methods:
        raise CommandError("invalid_command", "Unknown Theme action")
    value = methods[action]({k: v for k, v in command.items() if k != "action"},
                            token=token)
    return "theme_view", value


def workbench_command(workbench, command, *, token):
    """Route the read-only Knowledge Workbench search through its public seam."""
    if command.get("action") != "knowledge_search":
        raise CommandError("invalid_command", "Unknown Knowledge Workbench action")
    operation_id = command.get("operation_id")
    if operation_id is not None and (
            not isinstance(operation_id, str) or not operation_id.strip()):
        raise CommandError("invalid_command", "operation_id must be text")
    data = workbench.search(
        command.get("query", ""), token=token,
        kind=command.get("kind"), scope=command.get("scope", "active"),
        related_to=command.get("related_to"),
        limit=command.get("limit", 40), offset=command.get("offset", 0))
    return "knowledge_search", {"operation_id": operation_id, **data}


def knowledge_history_command(services, command, *, token):
    """Route history, undo preview and undo across the three knowledge owners."""
    if not isinstance(command, dict):
        raise CommandError("invalid_command", "Unknown knowledge history action")
    history = KnowledgeHistoryService(services)
    action = command.get("action")
    if action == "knowledge_history":
        return "knowledge_history", history.history(command, token=token)
    if action == "knowledge_undo_preview":
        return "knowledge_undo_preview", history.preview(command, token=token)
    if action == "knowledge_undo":
        record = history.undo(command, token=token)
        return "knowledge_undo", {
            "operation_id": command.get("operation_id"),
            "kind": command.get("kind"),
            "id": command.get("id"),
            "record": record,
        }
    raise CommandError("invalid_command", "Unknown knowledge history action")


def voyager_command(voyager, command, *, token, fulltext=None, cancelled=None):
    """Route desktop scope commands through Voyager's authenticated public seam."""
    action=command.get('action')
    if action=='voyager_source_settings':
        return 'voyager_source_settings', voyager.source_settings(token=token)
    if action=='voyager_save_source_key' and 'key' in command:
        return 'voyager_source_settings', voyager.save_source_setting(command.get('source'), command['key'], token=token)
    if action=='voyager_save_tavily_budget' and 'monthly_limit' in command:
        return 'voyager_source_settings', voyager.save_tavily_budget_setting(
            command.get('paygo_disabled'), command['monthly_limit'], token=token)
    if action=='voyager_fulltext' and fulltext is not None:
        return 'voyager_view', fulltext.acquire({k:v for k,v in command.items() if k!='action'},
                                                token=token, cancelled=cancelled)
    if action=='voyager_page':return 'voyager_page',voyager.recent(token=token,before=command.get('before'))
    if action=='voyager_history':return 'voyager_history',voyager.query_history(command.get('request_id'),command.get('query_id'),token=token,offset=command.get('offset',0))
    if action=='voyager_open':return 'voyager_view',voyager.view(command.get('request_id'),token=token)
    methods={'voyager_create':voyager.create,'voyager_execute':voyager.execute,'voyager_cancel':voyager.cancel}
    if action not in methods:raise CommandError('invalid_command','Unknown Voyager action')
    value=methods[action]({k:v for k,v in command.items() if k!='action'},token=token)
    return 'voyager_view',voyager.view(value['request_id'],token=token)


def serve(resolved):
    from hostrun.cleanup import _lexical_home, control_directory
    code = Path(resolved['code'])
    home = _lexical_home(resolved['runtime'])
    managed = os.name == 'nt' and home.parent.parent == control_directory()
    if managed and code == home.parent / 'app/current':
        with desktop_runtime.managed_executable(code, home) as executable:
            return _serve(resolved, executable, managed)
    return _serve(resolved, desktop_runtime.executable(code), managed)


def _serve(resolved, executable, managed):
    from hostrun.cleanup import _lexical_home
    from hostrun.desktop_worker import DesktopWorker
    code = Path(resolved['code'])
    home = _lexical_home(resolved['runtime'])
    env = dict(os.environ)
    env.pop('ELECTRON_RUN_AS_NODE', None)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(15)
    token = secrets.token_hex(32)
    env['HORIZON_DESKTOP_PORT'] = str(listener.getsockname()[1])
    env['HORIZON_DESKTOP_TOKEN'] = token
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
    materials = MaterialsService(service.home,token=token)
    reports = ReportService(service.home, token=token)
    voyager = VoyagerService(service.home,token=token,on_change=lambda event:app.events.put(('voyager_changed',event['request_id'])))
    frontier = FrontierService(service.home,token=token,on_change=lambda event:app.events.put(('frontier_changed',event['frontier_id'])))
    knowledge_cards = KnowledgeCardService(service.home,token=token,materials=materials,on_change=lambda event:app.events.put(('card_changed',event['card_id'])))
    knowledge_themes = KnowledgeThemeService(service.home,token=token,cards=knowledge_cards,materials=materials,on_change=lambda event:app.events.put(('theme_changed',event['theme_id'])))
    knowledge_workbench = KnowledgeWorkbenchService(service.home,token=token,cards=knowledge_cards,themes=knowledge_themes,frontiers=frontier)
    keeper_runtime = KeeperRuntime(service, token=token, code_root=code)
    keeper_service = None
    keeper_test_enabled = keeper_test_requests_enabled()

    def keeper_service_factory():
        nonlocal keeper_service
        if keeper_service is None:
            from hostrun.keeper import KeeperService
            keeper_service = KeeperService(service, materials, knowledge_cards,
                                           knowledge_themes, frontier, token=token)
        return keeper_service
    from hostrun.voyager_fulltext import FulltextService
    fulltext = FulltextService(voyager, materials)
    voyager_current = None
    from hostrun.voyager_subscriptions import Subscriptions
    subscriptions = Subscriptions(service.home)
    subscription_busy = set()
    web_jobs = {}
    voyager_busy = set()
    fulltext_jobs = {}
    from hostrun.context_versions import query_versions
    app.version_provider = query_versions
    from hostrun.schedule_runtime import ScheduleRuntime
    from hostrun import agent_scheduling
    scheduler = ScheduleRuntime(service, token, resolved, app.version_provider, materials=materials)
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
    keeper_commands = KeeperCommands(keeper_service_factory, keeper_runtime, token=token,
                                     emit=emit)
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
            emit('state', {'chats': chats, 'current': current, 'busy': app.busy,'voyager_requests':voyager.recent(token=token),'voyager_busy':list(voyager_busy),'web_jobs':list(web_jobs),'subscriptions':subscriptions.summaries(),'subscription_busy':list(subscription_busy),'materials':materials.list_summaries(token=token,include_version=True),'frontiers':frontier.list(token=token),'cards':knowledge_cards.list(token=token),'themes':knowledge_themes.list(token=token),
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

    def execute_voyager(command):
        try:
            cancel = fulltext_jobs.get(command.get('request_id'))
            kind,data=voyager_command(voyager,command,token=token,fulltext=fulltext,
                                      cancelled=cancel.is_set if cancel else None)
            with dispatch_lock:
                if voyager_current==command.get('request_id'):emit(kind,data)
        except CommandError as exc:
            emit('command_error',{'schema_version':1,'operation_id':command.get('operation_id'),'code':exc.code,'message':str(exc)})
        except Exception:emit('notice','检索未能完成，已保留执行预算与缺口，请核实后继续。')
        finally:
            with dispatch_lock:
                voyager_busy.discard(command.get('request_id'))
                fulltext_jobs.pop(command.get('request_id'), None)
                if not exiting.is_set():
                    state()
                    if voyager_current==command.get('request_id'):emit('voyager_view',voyager.view(voyager_current,token=token))

    def capture_web(command, cancelled):
        try:
            result = materials.register_web(command,token=token,cancel=cancelled)
            with dispatch_lock:
                if not cancelled.is_set() and not exiting.is_set():
                    package = materials.get_package(result['material_id'],token=token)
                    emit('private_pdf',None)
                    emit('web_material',package)
        except Exception:
            if not cancelled.is_set():emit('notice','网页获取失败，请检查原链接')
        finally:
            with dispatch_lock:
                web_jobs.pop(command['operation_id'],None)
                if not exiting.is_set():state()

    def update_subscription(identity):
        try:
            subscriptions.refresh(identity)
        except Exception:
            emit('notice', '订阅刷新失败')
        finally:
            with dispatch_lock:
                subscription_busy.discard(identity)
                state()

    def commands():
        nonlocal current, response, voyager_current
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
                    elif action == 'report_open':
                        if not chats:
                            raise CommandError('not_found', '找不到报道')
                        report = reports.get(cmd.get('report_id'), chats[current]['task_id'], token=token, version=cmd.get('version'))
                        emit('report_view', report)
                    elif action == 'report_citation':
                        if not chats:
                            raise CommandError('not_found', '找不到报道')
                        citation = reports.citation(cmd.get('report_id'), chats[current]['task_id'],
                                                    cmd.get('claim_index'), cmd.get('citation_index'),
                                                    token=token, snapshot_offset=cmd.get('snapshot_offset'),
                                                    version=cmd.get('version'))
                        emit('report_citation', {'report_id': cmd.get('report_id'), 'version': cmd.get('version'), 'citation': citation})
                    elif action == 'report_feedback':
                        if not chats:
                            raise CommandError('not_found', '找不到报道')
                        task_id = chats[current]['task_id']
                        reports.set_feedback(cmd.get('report_id'), task_id, cmd.get('version'),
                                             cmd.get('operation_id'), cmd.get('expected_revision'),
                                             score=cmd.get('score'), reason=cmd.get('reason'),
                                             claim_index=cmd.get('claim_index'), token=token)
                        emit('report_view', reports.get(cmd.get('report_id'), task_id,
                                                        version=cmd.get('version'), token=token))
                    elif action=='materials_web':
                        if exiting.is_set() or web_jobs:
                            raise CommandError('web_busy','已有网页正在获取')
                        command={k:v for k,v in cmd.items() if k!='action'}
                        operation=command.get('operation_id')
                        if not isinstance(operation,str) or not operation:
                            raise CommandError('invalid_command','缺少网页获取标识')
                        cancel=threading.Event()
                        web_jobs[operation]=cancel
                        threading.Thread(target=capture_web,args=(command,cancel),daemon=True).start()
                        state()
                    elif action=='materials_web_cancel':
                        cancel=web_jobs.get(cmd.get('operation_id'))
                        if cancel:cancel.set()
                    elif action=='subscription_add':
                        subscriptions.configure(cmd['kind'],cmd['url'])
                        state()
                    elif action=='subscription_refresh':
                        identity=cmd['id']
                        subscriptions.get(identity)
                        if identity not in subscription_busy and not exiting.is_set():
                            subscription_busy.add(identity)
                            threading.Thread(target=update_subscription,args=(identity,),daemon=True).start()
                            state()
                    elif action in ('voyager_source_settings', 'voyager_save_source_key', 'voyager_save_tavily_budget'):
                        try:
                            kind, data = voyager_command(voyager, cmd, token=token)
                            emit(kind, data)
                        except CommandError as exc:
                            emit('voyager_source_settings_error', str(exc))
                        except Exception:
                            emit('voyager_source_settings_error', '无法读取或保存本机检索来源配置，请重试。')
                        continue
                    elif action in ('voyager_execute','voyager_fulltext'):
                        if exiting.is_set():raise CommandError('exiting','Desktop is exiting')
                        if cmd.get('request_id') in voyager_busy:raise CommandError('execution_busy','Retrieval request is already running')
                        voyager_current=cmd.get('request_id');voyager_busy.add(voyager_current)
                        if action=='voyager_fulltext':
                            fulltext_jobs[voyager_current]=threading.Event()
                        threading.Thread(target=execute_voyager,args=(dict(cmd),),daemon=True).start()
                        state()
                    elif action in ('voyager_create','voyager_open','voyager_cancel','voyager_history','voyager_page'):
                        if exiting.is_set():raise CommandError('exiting','Desktop is exiting')
                        kind,data=voyager_command(voyager,cmd,token=token)
                        if action=='voyager_cancel' and cmd.get('request_id') in fulltext_jobs:
                            fulltext_jobs[cmd['request_id']].set()
                        if kind=='voyager_view':voyager_current=data['request_id']
                        emit(kind,data)
                    elif action in ('frontier_create','frontier_update','frontier_delete','frontier_open','frontier_list'):
                        if exiting.is_set():raise CommandError('exiting','Desktop is exiting')
                        kind,data=frontier_command(frontier,cmd,token=token)
                        emit(kind,data)
                    elif action in ('card_create','card_update','card_archive',
                                    'card_open','card_list','card_query'):
                        if exiting.is_set():raise CommandError('exiting','Desktop is exiting')
                        kind,data=card_command(knowledge_cards,cmd,token=token)
                        emit(kind,data)
                    elif action in ('theme_create','theme_update','theme_archive',
                                    'theme_open','theme_list','theme_query','theme_source'):
                        if exiting.is_set():raise CommandError('exiting','Desktop is exiting')
                        kind,data=theme_command(knowledge_themes,cmd,token=token)
                        emit(kind,data)
                    elif action == 'knowledge_search':
                        if exiting.is_set():raise CommandError('exiting','Desktop is exiting')
                        kind,data=workbench_command(knowledge_workbench,cmd,token=token)
                        emit(kind,data)
                    elif action in ('knowledge_history','knowledge_undo_preview','knowledge_undo'):
                        if exiting.is_set():raise CommandError('exiting','Desktop is exiting')
                        kind,data=knowledge_history_command(
                            {'card': knowledge_cards, 'theme': knowledge_themes,
                             'frontier': frontier}, cmd, token=token)
                        emit(kind,data)
                    elif action in ('materials_archive_list','materials_delete_impact','materials_delete','materials_collect_orphans'):
                        if exiting.is_set():raise CommandError('exiting','Desktop is exiting')
                        result = material_command(materials, cmd, token=token)
                        if action == 'materials_archive_list':
                            emit('material_archive_list', result)
                        elif action == 'materials_delete_impact':
                            emit('material_deletion_impact', result)
                        elif action == 'materials_delete':
                            emit('private_pdf', None)
                            emit('material_package', None)
                            emit('material_archive_list', materials.list_summaries(archived=True, token=token))
                            emit('notice', '资料已永久删除')
                        else:
                            emit('notice', f"已整理 {len(result['removed'])} 个无引用快照")
                    elif action in ('materials_register','materials_open','materials_pdf_register','materials_pdf_resolve',
                                    'materials_pending_resolve','materials_archive'):
                        if exiting.is_set():raise CommandError('exiting','Desktop is exiting')
                        package = material_command(materials,cmd,token=token)
                        if package.get('pdf'):
                            import base64
                            original = materials.pdf_original(package['material_id'], token=token)
                            emit('private_pdf', {'id':package['material_id'], 'data':base64.b64encode(original).decode('ascii')})
                        else:
                            emit('private_pdf', None)
                        emit('material_package', package)
                        if action == 'materials_archive':
                            emit('material_archive_list', materials.list_summaries(archived=True, token=token))
                    elif action in ('keeper_list', 'keeper_open', 'keeper_decide',
                                    'keeper_undo_preview', 'keeper_undo',
                                    'keeper_revise', 'keeper_audit',
                                    'keeper_test_request', 'keeper_request'):
                        if exiting.is_set(): raise CommandError('exiting','Desktop is exiting')
                        result = keeper_bridge_command(keeper_commands, cmd,
                                                       test_enabled=keeper_test_enabled)
                        if result is not None:
                            emit(*result)
                            receipt = result[1]
                            if action in ('keeper_decide', 'keeper_revise') \
                                    and isinstance(receipt, dict) \
                                    and receipt.get('status') == 'revising':
                                keeper_commands.queue_revision(receipt.get('review_id'))
                    elif action == 'schedule_command': service.schedule_command(cmd['command'],token=token)
                    elif action == 'schedule_limits': agent_scheduling.set_limits(service,main=cmd['main'],children=cmd['children'],token=token)
                    elif action == 'ready':
                        if not ui_ready.is_set():
                            if keeper_test_enabled:
                                keeper_commands.resume_queued()
                            keeper_commands.resume_queued_revisions()
                        ui_ready.set()
                    elif action == 'export_diagnostics':
                        report = export_report(service.home, code=code, tasks=service.list_turns(token=token))
                        emit('notice', '诊断报告已导出：' + str(report))
                    elif action == 'exit':
                        exiting.set()
                        for cancel in web_jobs.values():cancel.set()
                        for cancel in fulltext_jobs.values():cancel.set()
                        keeper_runtime.stop()
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
                    error = {'schema_version': 1, 'operation_id': cmd.get('operation_id'),
                             'code': exc.code, 'message': str(exc)}
                    details = getattr(exc, 'details', None)
                    if details is not None:
                        error['details'] = details
                    emit('command_error', error)
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
            if kind == 'voyager_changed':
                with dispatch_lock:
                    state()
                    if voyager_current==data:emit('voyager_view',voyager.view(data,token=token))
                continue
            if kind == 'frontier_changed':
                with dispatch_lock:
                    state()
                continue
            if kind == 'card_changed':
                with dispatch_lock:
                    state()
                continue
            if kind == 'theme_changed':
                with dispatch_lock:
                    state()
                continue
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
        keeper_runtime.stop()
        scheduler.stop()
        app.closed = True; app.stop(); app.answers.put(None)
        if exit_code:
            record_event(service.home, 'error', code='renderer_exit', exit_code=exit_code)
            raise RuntimeError(f'Electron exited before completing desktop session (exit {exit_code:#x})')
        if os.name == 'nt':
            record_event(service.home, 'desktop_exit', pid=os.getpid(), exit_code=0,
                         status='explicit_exit' if exiting.is_set() else 'renderer_closed')
            os._exit(0)
