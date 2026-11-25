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
    def record_diagnostic(self, event, **metadata):
        metadata.update(task_id=getattr(self, 'task_id', ''),
                        turn_id=(getattr(self, 'turn', None) or {}).get('turn_id', ''))
        record_event(self.home, event, **metadata)

    def _term_request(self, event):
        from hostrun.agent_service import CommandError
        from hostrun.knowledge_cards import KnowledgeCardService
        from hostrun.librarian import Librarian
        token = self.service_token
        librarian = Librarian(KnowledgeCardService(self.home, token=token), token=token)
        operation = event.get("action")
        if event.get("type") == "source_request":
            return self._source_request(librarian, operation, event, token=token)
        if event.get("type") == "tracking_request":
            return self._tracking_request(librarian, operation, event, token=token)
        if event.get("type") == "question_request":
            return self._question_request(librarian, operation, event, token=token)
        if event.get("type") == "test_cleanup_request":
            return self._test_cleanup_request(librarian, operation, event, token=token)
        if operation == "list":
            return librarian.list(token=token)
        if operation == "view":
            return librarian.view(event.get("termId"), token=token)
        payload = event.get("fields")
        if not isinstance(payload, dict):
            raise CommandError("invalid_command", "需要术语变更内容")
        request_id = event.get("requestId")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 256:
            raise CommandError("invalid_command", "无效的术语请求标识")
        origin = "agent:" + self.task_id
        if operation == "batch":
            changes = payload.get("changes")
            if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
                raise CommandError("invalid_command", "批量写入需要 1 至 100 项变更")
            commands = []
            for index, item in enumerate(changes):
                if not isinstance(item, list) or len(item) != 2 or not isinstance(item[1], dict):
                    raise CommandError("invalid_command", "无效的术语变更")
                action, fields = item
                commands.append((action, {**self._development_fields(event, action, fields), "operation_id": f"{request_id}:{index}",
                                          "schema_version": 1}))
            card = librarian.request_batch(request_id, commands, token=token, origin=origin)
        elif operation in ("create", "update", "archive"):
            command = {**self._development_fields(event, operation, payload),
                       "operation_id": request_id, "schema_version": 1}
            card = librarian.request(operation, command, token=token, origin=origin)
        else:
            raise CommandError("invalid_command", "不支持的术语操作")
        self.events.put(("term_confirmation_changed", self.task_id))
        return card

    def _source_request(self, librarian, operation, event, *, token):
        from hostrun.agent_service import CommandError

        source_id = event.get("sourceId")
        if operation == "list":
            return librarian.list_sources(token=token)
        if operation == "view":
            return librarian.view_source(source_id, token=token)
        if operation == "open":
            package = librarian.open_source(source_id, token=token, task_id=self.task_id)
            if "source_url" in package:
                return package
            text = package["original_text"]
            return {"material_id": package["material_id"], "title": package["title"],
                    "original_text": text[:16000], "original_text_truncated": len(text) > 16000,
                    "pdf": bool(package.get("pdf"))}
        fields = event.get("fields")
        request_id = event.get("requestId")
        if not isinstance(fields, dict) or not isinstance(request_id, str) or not 1 <= len(request_id) <= 256:
            raise CommandError("invalid_command", "需要有效的资料库来源变更")
        command = {**self._development_fields(event, operation, fields), "operation_id": request_id}
        if source_id:
            command["source_id"] = source_id
        card = librarian.request_source(operation, command, token=token, origin="agent:" + self.task_id)
        self.events.put(("term_confirmation_changed", self.task_id))
        return card

    def _tracking_request(self, librarian, operation, event, *, token):
        from hostrun.agent_service import CommandError

        entity = event.get("entity")
        if entity not in ("frontier", "progress"):
            raise CommandError("invalid_command", "请选择前沿或进展")
        frontier_id = event.get("frontierId")
        if operation == "list":
            return (librarian.list_frontiers(token=token) if entity == "frontier"
                    else librarian.list_progress(frontier_id, token=token))
        if operation == "view" and entity == "frontier":
            return librarian.view_frontier(frontier_id, token=token)
        fields, request_id = event.get("fields"), event.get("requestId")
        if not isinstance(fields, dict) or not isinstance(request_id, str) or not 1 <= len(request_id) <= 256:
            raise CommandError("invalid_command", "需要有效的前沿或进展变更")
        command = {**self._development_fields(event, operation, fields),
                   "operation_id": request_id, "schema_version": 1}
        if frontier_id is not None:
            command["frontier_id"] = frontier_id
        submit = librarian.request_frontier if entity == "frontier" else librarian.request_progress
        card = submit(operation, command, token=token, origin="agent:" + self.task_id)
        self.events.put(("term_confirmation_changed", self.task_id))
        return card

    @staticmethod
    def _development_fields(event, action, fields):
        if action == "create" and event.get("testData") is True:
            return {**fields, "test_data": True}
        return fields

    def _question_request(self, librarian, operation, event, *, token):
        from hostrun.agent_service import CommandError

        question_id = event.get("questionId")
        if operation == "list":
            return librarian.list_questions(token=token)
        if operation == "view":
            return librarian.view_question(question_id, token=token)
        fields, request_id = event.get("fields"), event.get("requestId")
        if not isinstance(fields, dict) or not isinstance(request_id, str) or not 1 <= len(request_id) <= 256:
            raise CommandError("invalid_command", "需要有效的百科问题变更")
        command = {**self._development_fields(event, operation, fields),
                   "operation_id": request_id, "schema_version": 1}
        if question_id is not None:
            command["question_id"] = question_id
        card = librarian.request_question(operation, command, token=token, origin="agent:" + self.task_id)
        self.events.put(("term_confirmation_changed", self.task_id))
        return card

    def _test_cleanup_request(self, librarian, operation, event, *, token):
        from hostrun.agent_service import CommandError

        selection = event.get("selection")
        if operation == "preview":
            return librarian.preview_test_cleanup(token=token, selection=selection)
        request_id = event.get("requestId")
        if operation != "request" or not isinstance(request_id, str) or not 1 <= len(request_id) <= 256:
            raise CommandError("invalid_command", "需要有效的测试内容清理请求")
        card = librarian.request_test_cleanup(request_id, token=token, origin="agent:" + self.task_id,
                                             selection=selection)
        self.events.put(("term_confirmation_changed", self.task_id))
        return card

    def run_agent(self, task, session_id=None, history=None):
        completed_text = None
        failure = None
        turn = getattr(self, 'turn', None)
        committed = False
        try:
            if turn and turn.get('late_editor_input'):
                self._attempt(turn)
                outcome = self.delegate({'requestId': 'late-fulltext-editor', 'role': 'editor',
                                         'input': turn['late_editor_input']})
                if outcome['status'] == 'succeeded' and not self.cancelled.is_set():
                    self._commit_result(turn, outcome['result'])
                    committed = True
                else:
                    failure = outcome.get('failure')
                return
            argv = embedded_agent.argv(self.home, task, kind='agent', code=Path(self.resolved['code']), session_id=session_id)
            turn = getattr(self, 'turn', None)
            if turn:
                argv.extend(['--turn-id', turn['turn_id']])
                self._attempt(turn)
            if session_id is not None and history:
                argv.append('--history-stdin')
            env = dict(os.environ, HORIZON_PYTHON=sys.executable)
            self.process = subprocess.Popen(argv, cwd=self.resolved['code'], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.record_diagnostic('task_started', pid=getattr(self.process, 'pid', None), parent_pid=os.getpid())
            # Drain stderr without exposing provider credentials in UI logs.
            threading.Thread(target=lambda p=self.process: p.stderr.read(), daemon=True).start()
            if session_id is not None and history:
                self.process.stdin.write(json.dumps({'type': 'history', 'messages': history}, ensure_ascii=False) + '\n')
                self.process.stdin.flush()
            if self.cancelled.is_set():
                self.process.terminate()
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('type') in ('term_request', 'source_request', 'tracking_request', 'question_request', 'test_cleanup_request'):
                    from hostrun.agent_service import CommandError
                    try:
                        result = self._term_request(event)
                    except CommandError as exc:
                        result = {"status": "returned", "message": str(exc)}
                    self.process.stdin.write(json.dumps({"requestId": event["requestId"],
                                                        "result": result}) + "\n")
                    self.process.stdin.flush()
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
                    self.record_diagnostic(event['type'], **{k:v for k,v in event.items() if k != 'type'})
                if event.get('type') == 'error':
                    failure = {key:event.get(key) for key in ('category','retryable','attempts','message')}
                    if failure.get('category') not in ('authentication','configuration','quota','transient','permanent','cancelled'):
                        failure['category'] = 'permanent'
                if event.get('type') == 'final':
                    completed_text = event.get('text', '')
                self.events.put(('agent', event))
            code = self.process.wait()
            if not code and completed_text is not None and turn:
                self._commit_result(turn, completed_text)
                committed = True
            if code:
                self.events.put(('agent', {'type': 'error', 'message': f'Agent 退出 ({code})'}))
        except embedded_agent.ModelConfigurationMissing:
            failure = {'category':'configuration','retryable':False,'attempts':0,
                       'message':'请在 Horizon 初始化流程中配置模型 API Key'}
            self.record_diagnostic('error', **failure)
            self.events.put(('agent', {'type':'error', **failure}))
        except Exception as exc:
            self.record_diagnostic('error', code=type(exc).__name__)
            self.events.put(('agent', {'type': 'error', 'message': str(exc)}))
        finally:
            self.record_diagnostic('task_finished', status='completed' if committed else 'interrupted')
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
                    self.process = None
                    self.events.put(('done', {'durable': True} if turn else None))

    def _attempt(self, turn):
        self.current_snapshots = self.version_provider()
        self.service.attempt_turn({
            'schema_version': 1, 'operation_id': str(uuid.uuid4()), 'turn_id': turn['turn_id'],
            'snapshots': self.current_snapshots,
        }, token=self.service_token)

    def _commit_result(self, turn, result):
        self.service.commit_turn({
            'schema_version': 1, 'operation_id': str(uuid.uuid5(uuid.UUID(self.task_id), turn['turn_id'] + ':complete')),
            'turn_id': turn['turn_id'], 'expected_version': turn['version'],
            'status': 'completed', 'result': result, 'error': '',
        }, token=self.service_token)

    def delegate(self, event):
        from hostrun.agent_capacity import child_slot
        with child_slot(self.service, self.service_token, self.cancelled):
            return self._delegate(event)

    def _delegate(self, event):
        request_id = str(event.get('requestId', ''))
        if not request_id or len(request_id) > 256:
            raise ValueError('Invalid delegation request')
        operation = str(uuid.uuid5(uuid.UUID(self.task_id), json.dumps([self.input_version, request_id])))
        existing = next((item for item in self.service.list_children(self.task_id, token=self.service_token) if item.get('start_operation_id') == operation), None)
        if existing:
            if existing['role'] != event.get('role') or existing['input_text'] != event.get('input'):
                raise ValueError('Delegation identity conflicts with committed input')
            if existing['status'] != 'running':
                self.record_diagnostic('child_reused', child_id=existing['child_id'], status=existing['status'])
                return existing
        voyager_context = None
        if event.get('role') == 'voyager':
            from hostrun.voyager_child import child_evidence
            from hostrun.voyager_service import VoyagerService
            voyager_context = child_evidence(VoyagerService(self.home, token=self.service_token),
                event.get('input'), self.task_id, token=self.service_token)
        editor_context = None
        saved_report = None
        if event.get('role') == 'editor':
            from hostrun.editor_reports import (DEFAULT_PREFERENCE_VERSION, ReportService, build_editor_context,
                                                read_report_response, report_evidence)
            from hostrun.editor_synthesis import commit_synthesis, confirmed_knowledge, prepare_synthesis, synthesis_input
            from hostrun.knowledge_cards import KnowledgeCardService
            from hostrun.librarian import Librarian
            from hostrun.editor_preferences import PreferenceService

            reports = ReportService(self.home, token=self.service_token)
            material_id = event.get('input')
            synthesis = synthesis_input(material_id)
            if synthesis and {'report_id', 'base_version'} & synthesis.keys():
                raise ValueError('Invalid initial synthesis')
            rewrite = None
            writing = synthesis or {}
            if synthesis is None and isinstance(material_id, str) and material_id.lstrip().startswith('{'):
                command = json.loads(material_id)
                if (isinstance(command, dict) and 'report_id' not in command
                        and isinstance(command.get('material_id'), str)
                        and command.keys() <= {'material_id', 'once', 'frontier_id'}):
                    material_id = command['material_id']
                    writing = command
                else:
                    rewrite = command
            if rewrite is not None:
                if (not isinstance(rewrite, dict) or not {'report_id', 'base_version', 'question'} <= rewrite.keys()
                        or rewrite.keys() - {'report_id', 'base_version', 'question', 'material_id', 'once', 'frontier_id'}
                        or not isinstance(rewrite['report_id'], str)
                        or ('material_id' in rewrite and not isinstance(rewrite['material_id'], str))
                        or not isinstance(rewrite['question'], str)
                        or not 1 <= len(rewrite['question'].strip()) <= 2000
                        or type(rewrite['base_version']) is not int):
                    raise ValueError('Invalid Editor follow-up')
                previous = reports.get(rewrite['report_id'], self.task_id,
                                       version=rewrite['base_version'], token=self.service_token)
                material_id = rewrite.get('material_id', previous['source']['material_id'])
                writing = {'frontier_id': previous['context_versions'].get('writing_frontier_id'), **rewrite}
                if 'sources' in previous:
                    if 'material_id' in rewrite:
                        raise ValueError('A synthesis follow-up cannot replace its source selection')
                    synthesis = {
                        **previous.get('synthesis_options', {}),
                        'material_ids': previous['selected_material_ids'],
                        'question': previous['title'],
                        'report_id': rewrite['report_id'],
                        'base_version': rewrite['base_version'],
                    }
            selected = synthesis['material_ids'] if synthesis else material_id
            saved_report = reports.get_by_operation(operation, self.task_id, selected, token=self.service_token)
            if saved_report is None and synthesis:
                editor_context, evidence, versions, reposts = prepare_synthesis(
                    reports, self.home, self.task_id, synthesis, token=self.service_token)
            elif saved_report is None:
                evidence = report_evidence(reports.materials, material_id, self.task_id,
                                           token=self.service_token)
                known = confirmed_knowledge(self.home, token=self.service_token)
                versions = {'knowledge': [{key: item[key] for key in ('kind', 'id', 'version')}
                                          for item in known],
                            'preferences': [DEFAULT_PREFERENCE_VERSION]}
                editor_context = build_editor_context(evidence, known)
            if editor_context is not None:
                frontier_id = writing.get('frontier_id')
                effective = PreferenceService(self.home, token=self.service_token).effective(
                    token=self.service_token, frontier_id=frontier_id, once=writing.get('once'))
                editor_context['writing_style'] = {key: row['value'] for key, row in effective.items()}
                versions['preferences'] = [{**row, 'key': key} for key, row in effective.items()]
                if frontier_id:
                    versions['writing_frontier_id'] = frontier_id
                    frontier = Librarian(KnowledgeCardService(self.home, token=self.service_token),
                                         token=self.service_token).view_frontier(frontier_id, token=self.service_token)
                    editor_context['frontier'] = {key: frontier[key] for key in ('frontier_id', 'version', 'question')}
                    versions['knowledge'].append({'kind': 'frontier', 'id': frontier_id, 'version': frontier['version']})
            if editor_context is not None and rewrite:
                editor_context['follow_up'] = {
                    'question': rewrite['question'],
                    'previous_claims': [
                        {'kind': claim['kind'], 'text': claim['text'][:240]} for claim in previous['claims'][:8]
                    ],
                }
        child_context = voyager_context if voyager_context is not None else editor_context
        if existing:
            child = existing
        else:
            child = self.service.start_child({'schema_version': 1, 'operation_id': operation,
                'task_id': self.task_id, 'role': event.get('role'), 'input_text': event.get('input'),
                'input_version': self.input_version, 'snapshots': getattr(self, 'current_snapshots', {})}, token=self.service_token)
            child = next(item for item in self.service.list_children(self.task_id, token=self.service_token) if item['child_id'] == child['child_id'])
        if child['status'] != 'running':
            self.record_diagnostic('child_reused', child_id=child['child_id'], status=child['status'])
            return child
        child = self.service.attempt_child({'schema_version':1,'operation_id':str(uuid.uuid4()),'child_id':child['child_id'],'expected_version':child['version'],'snapshots':getattr(self,'current_snapshots',{})},token=self.service_token)
        self.events.put(('children', None))
        output = ''
        status, error = 'failed', ''
        child_failure = None
        saw_voyager_context = False
        saw_editor_context = False
        try:
            if self.cancelled.is_set():
                raise RuntimeError('Task cancelled')
            if saved_report is not None:
                output = json.dumps({'report_id': saved_report['report_id'],
                                     'version': saved_report['version']})
            else:
                task = ('Use voyager_context to return an evidence package for this retrieval.'
                        if voyager_context is not None else 'Read editor_context and return the report JSON.'
                        if editor_context is not None else child['input_text'])
                argv = embedded_agent.argv(self.home, task, kind='agent',
                    code=Path(self.resolved['code']), session_id=child['session_id'])
                argv.extend(['--child-role', child['role']])
                context_pipe = subprocess.PIPE if child_context is not None else subprocess.DEVNULL
                self.child_process = subprocess.Popen(argv, cwd=self.resolved['code'],
                    env=dict(os.environ, HORIZON_PYTHON=sys.executable), stdin=context_pipe,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.record_diagnostic('child_started', child_id=child['child_id'], session_id=child['session_id'],
                                       pid=getattr(self.child_process, 'pid', None), parent_pid=os.getpid())
                threading.Thread(target=lambda p=self.child_process: p.stderr.read(), daemon=True).start()
                if child_context is not None:
                    self.child_process.stdin.write(json.dumps(child_context, ensure_ascii=False) + '\n')
                    self.child_process.stdin.close()
                if self.cancelled.is_set():
                    self.child_process.terminate()
                for line in self.child_process.stdout:
                    try: item = json.loads(line)
                    except ValueError: continue
                    if item.get('type') in ('retry', 'error'):
                        self.record_diagnostic(item['type'], child_id=child['child_id'],
                                               **{k:v for k,v in item.items() if k != 'type'})
                    if item.get('type') == 'error':
                        child_failure = {key:item.get(key) for key in ('category','retryable','attempts','message')}
                    if item.get('type') == 'final': output = str(item.get('text', ''))[:100000]
                    if item.get('type') == 'tool_result':
                        if item.get('name') == 'voyager_context' and not item.get('isError'):
                            saw_voyager_context = True
                        if item.get('name') == 'editor_context' and not item.get('isError'):
                            saw_editor_context = True
                        self.events.put(('agent', {'type': 'tool_result', 'name': child['role'] + ': ' + str(item.get('name')), 'isError': item.get('isError', False)}))
                exit_code = self.child_process.wait()
                if exit_code or self.cancelled.is_set() or not output.strip():
                    raise RuntimeError('Child failed or cancelled')
                if voyager_context is not None:
                    if not saw_voyager_context:
                        raise RuntimeError('Voyager did not read the bound retrieval')
                    output = json.dumps({'evidence': voyager_context, 'analysis': output[:12000]}, ensure_ascii=False)
                if editor_context is not None:
                    if not saw_editor_context:
                        raise RuntimeError('Editor did not read the bound material')
                    reviewed = read_report_response(output, synthesis=True)
                    if synthesis:
                        receipt = commit_synthesis(
                            reports, operation, self.task_id, synthesis, editor_context, evidence, versions,
                            reposts, reviewed, token=self.service_token, task_version=self.input_version)
                    else:
                        report = reports.save(operation, self.task_id, evidence, versions,
                                              reviewed['claims'], token=self.service_token,
                                              keeper_proposals=reviewed.get('keeper_proposals'),
                                              task_version=self.input_version,
                                              report_id=rewrite['report_id'] if rewrite else None,
                                              base_version=rewrite['base_version'] if rewrite else None)
                        receipt = {'report_id': report['report_id'], 'version': report['version']}
                    output = json.dumps(receipt, ensure_ascii=False)
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
        self.record_diagnostic('child_finished', child_id=child['child_id'], status=status)
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
