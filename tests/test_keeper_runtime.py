"""Runtime and desktop integration for the bounded independent Keeper child.

The public Keeper service owns requests and proposals; these tests exercise the
trusted host dispatcher: a real AgentService child, the installed Python worker
subprocess, deterministic operation receipts, replay, the launch-environment
gate and the read-only desktop bridge events.
"""

import json
import threading
import time
import uuid
from pathlib import Path

import pytest

from hostrun import keeper_runtime as runtime_module
from hostrun.agent_service import AgentService, CommandError
from hostrun.desktop_bridge import (KeeperCommands, keeper_bridge_command,
                                    keeper_test_requests_enabled)
from hostrun.frontier import FrontierService
from hostrun.keeper import KeeperPrincipal, KeeperService
from hostrun.keeper_runtime import KeeperRuntime, KeeperRuntimeError, review_event
from hostrun.keeper_worker import verify
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_themes import KnowledgeThemeService
from hostrun.voyager_materials import MaterialsService

TOKEN = 'keeper-runtime-token'
REPORT_TEXT = 'The private report proposes a sourced change.'
EXCERPT = 'Calibrated optical sensors measure received radiation every day.'


class Owner:
    def __init__(self, home):
        self.home = home
        self.agent = AgentService(home, token=TOKEN)
        self.task = self.agent.create_task(
            {'schema_version': 1, 'operation_id': 'task-1', 'title': 'Keeper runtime'},
            token=TOKEN)
        turn = self.agent.begin_turn({
            'schema_version': 1, 'operation_id': 'turn-1',
            'task_id': self.task['task_id'], 'expected_version': 1,
            'text': 'Keep these findings.',
        }, token=TOKEN)
        self.report = self.agent.commit_turn({
            'schema_version': 1, 'operation_id': 'turn-complete',
            'turn_id': turn['turn_id'], 'expected_version': turn['version'],
            'status': 'completed', 'result': REPORT_TEXT,
        }, token=TOKEN)
        self.task = self.agent.read_task(self.task['task_id'], token=TOKEN)
        self.materials = MaterialsService(home, token=TOKEN)
        self.cards = KnowledgeCardService(home, token=TOKEN, materials=self.materials)
        self.themes = KnowledgeThemeService(home, token=TOKEN, cards=self.cards,
                                            materials=self.materials)
        self.frontiers = FrontierService(home, token=TOKEN)
        self.keeper = KeeperService(self.agent, self.materials, self.cards,
                                    self.themes, self.frontiers, token=TOKEN)

    def material(self):
        return self.materials.register_text({
            'schema_version': 1, 'operation_id': 'material-1',
            'title': 'Observatory report', 'source_label': 'Fixtures',
            'text': 'Observatory report\n%s\n%s\n' % (EXCERPT, EXCERPT),
        }, token=TOKEN)

    def source(self, material):
        return {'material_id': material['material_id'],
                'material_version': material['version'],
                'locator': 'p2', 'excerpt': EXCERPT}

    def card_change(self, sources, **fields):
        return {'kind': 'card', 'action': 'create',
                'fields': {'title': 'Calibrated optics', 'content': 'Measured daily.',
                           **fields},
                'reason': 'The report supports this concept.', 'sources': sources}

    def request(self, groups, *, operation='keeper-1'):
        return self.keeper.request({
            'schema_version': 1, 'operation_id': operation,
            'task_id': self.task['task_id'], 'task_version': self.task['version'],
            'report_id': self.report['turn_id'],
            'report_version': self.report['version'], 'groups': groups,
        }, token=TOKEN, principal=KeeperPrincipal('editor', True))

    def direct_context(self, *, request_id=None, source=None):
        return {'request_id': request_id or str(uuid.uuid4()),
                'task_id': self.task['task_id'], 'task_version': self.task['version'],
                'report_id': self.report['turn_id'],
                'report_version': self.report['version'],
                'provenance': {'sources': [source] if source else []},
                'groups': [{'proposal_id': 'proposal-1', 'changes': [{
                    'change_id': 'change-1', 'entity': 'card:Calibrated optics',
                    'action': 'create', 'source': source,
                    'relations': ['card:related']}]}]}


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_runtime_runs_real_child_and_replays_without_a_second_process(tmp_path, monkeypatch):
    owner = Owner(tmp_path)
    material = owner.material()
    receipt = owner.request([{'changes': [
        owner.card_change([owner.source(material)])]}])
    assert receipt['status'] == 'queued'
    runtime = KeeperRuntime(owner.agent, token=TOKEN)
    spawns = []
    original = runtime_module.subprocess.Popen

    def counting(*args, **kwargs):
        spawns.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime_module.subprocess, 'Popen', counting)
    contexts = []

    def dispatch(context):
        contexts.append(context)
        return runtime.dispatch(context)

    result = owner.keeper.review(receipt['request_id'], token=TOKEN, dispatch=dispatch)
    assert result['status'] == 'pending' and len(result['proposal_ids']) == 1
    context = contexts[0]
    assert context['task_version'] == owner.task['version']
    assert context['report_id'] == owner.report['turn_id']
    assert context['report_version'] == owner.report['version']
    assert REPORT_TEXT not in json.dumps(context)

    children = owner.agent.list_children(owner.task['task_id'], token=TOKEN)
    assert len(children) == 1
    child = children[0]
    assert child['role'] == 'keeper' and child['status'] == 'succeeded'
    assert child['input_version'] == owner.task['version']
    assert json.loads(child['result'])['status'] == 'reviewed'
    assert owner.cards.list(token=TOKEN) == []

    replay = runtime.dispatch(context)
    assert replay == {'status': 'reviewed', 'reason': '', 'child_id': child['child_id']}
    assert len(spawns) == 1
    assert len(owner.agent.list_children(owner.task['task_id'], token=TOKEN)) == 1


def test_runtime_returns_insufficient_evidence_with_a_real_child(tmp_path):
    owner = Owner(tmp_path)
    context = owner.direct_context(source=None)
    runtime = KeeperRuntime(owner.agent, token=TOKEN)
    result = runtime.dispatch(context)
    assert result['status'] == 'returned'
    assert 'no source' in result['reason']
    child = owner.agent.list_children(owner.task['task_id'], token=TOKEN)[0]
    assert child['status'] == 'succeeded'
    document = json.loads(child['result'])
    assert document['status'] == 'returned' and 'no source' in document['reason']


def test_revision_dispatches_a_real_child_with_feedback_on_the_original_task(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    card = owner.cards.create({
        'schema_version': 1, 'operation_id': 'owner-create',
        'title': 'An existing card', 'content': 'Original body',
    }, token=TOKEN)
    request = owner.request([{'changes': [{
        'kind': 'card', 'action': 'update', 'id': card['card_id'],
        'expected_version': card['version'],
        'fields': {'title': 'Sourced title', 'content': 'Proposed replacement body'},
        'reason': 'Review the terminology.', 'sources': [owner.source(material)],
    }]}])
    runtime = KeeperRuntime(owner.agent, token=TOKEN)
    first = owner.keeper.review(request['request_id'], token=TOKEN, dispatch=runtime.dispatch)
    proposal_id = first['proposal_ids'][0]
    task_id = owner.task['task_id']
    owner.agent.begin_turn({
        'schema_version': 1, 'operation_id': 'later-turn', 'task_id': task_id,
        'expected_version': owner.task['version'], 'text': 'An independent task turn',
    }, token=TOKEN)
    current = owner.cards.update({
        'schema_version': 1, 'operation_id': 'manual-content',
        'card_id': card['card_id'], 'expected_version': card['version'],
        'content': 'A newer manual body',
    }, token=TOKEN)
    feedback = 'Keep my independently edited body.'
    adjusted = owner.keeper.revise({
        'schema_version': 1, 'operation_id': 'explorer-feedback', 'proposal_id': proposal_id,
        'revision': 1, 'decision': 'adjust', 'feedback': feedback,
        'keep_current_fields': [{'change_index': 0, 'fields': ['content']}],
    }, token=TOKEN, principal=KeeperPrincipal('explorer', False))
    revised = owner.keeper.review_revision(adjusted['review_id'], token=TOKEN,
                                            dispatch=runtime.dispatch)
    proposal = owner.keeper.view(proposal_id, token=TOKEN)
    assert proposal['status'] == 'pending' and proposal['revision'] == 2
    assert proposal['report_id'] == owner.report['turn_id']
    assert proposal['changes'][0]['after']['content'] == 'A newer manual body'
    assert owner.cards.view(card['card_id'], token=TOKEN) == current
    children = owner.agent.list_children(task_id, token=TOKEN)
    assert len(children) == 2
    child = children[1]
    assert child['child_id'] == revised['child_id']
    assert child['role'] == 'keeper' and child['status'] == 'succeeded'
    assert child['parent_session_id'] == owner.task['session_id']
    assert feedback in child['input_text']
    assert child['snapshots']['keeper']['report_id'] == owner.report['turn_id']


def test_revision_with_new_theme_evidence_completes_one_real_review(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    source = owner.source(material)
    theme = owner.themes.create({
        'schema_version': 1, 'operation_id': 'theme-base',
        'judgement': 'Calibration remains useful', 'evidence': [],
    }, token=TOKEN)
    request = owner.request([{'changes': [{
        'kind': 'theme', 'action': 'update', 'id': theme['theme_id'],
        'expected_version': theme['version'],
        'fields': {'evidence': [{
            'stance': 'supports', 'reason': 'The report records calibration.', 'source': source,
        }]},
        'reason': 'Record the supporting source.', 'sources': [source],
    }]}])
    runtime = KeeperRuntime(owner.agent, token=TOKEN)
    first = owner.keeper.review(request['request_id'], token=TOKEN, dispatch=runtime.dispatch)
    proposal_id = first['proposal_ids'][0]
    manual = owner.themes.update({
        'schema_version': 1, 'operation_id': 'manual-judgement',
        'theme_id': theme['theme_id'], 'expected_version': 1,
        'judgement': 'Calibration is useful for this sensor only',
    }, token=TOKEN)
    principal = KeeperPrincipal('explorer', False)
    staged = owner.keeper.decide({
        'schema_version': 1, 'operation_id': 'old-theme-confirm',
        'proposal_id': proposal_id, 'revision': 1, 'expected_versions': [1],
        'decision': 'approve',
    }, token=TOKEN, principal=principal)
    revised = owner.keeper.review_revision(staged['review_id'], token=TOKEN,
                                            dispatch=runtime.dispatch)
    proposal = owner.keeper.view(proposal_id, token=TOKEN)
    assert revised['status'] == 'completed' and proposal['revision'] == 2
    assert len(owner.agent.list_children(owner.task['task_id'], token=TOKEN)) == 2
    assert owner.themes.view(theme['theme_id'], token=TOKEN) == manual
    decided = owner.keeper.decide({
        'schema_version': 1, 'operation_id': 'new-theme-confirm',
        'proposal_id': proposal_id, 'revision': 2, 'expected_versions': [2],
        'decision': 'approve',
    }, token=TOKEN, principal=principal)
    committed = owner.themes.view(theme['theme_id'], token=TOKEN)
    assert decided['applied'][0]['version'] == 3
    assert committed['judgement'] == manual['judgement']
    assert committed['evidence'][0]['source']['anchor']['id'] == 'p2'


def test_failed_child_is_visible_and_exact_replay_does_not_spawn_again(tmp_path, monkeypatch):
    owner = Owner(tmp_path)
    material = owner.material()
    receipt = owner.request([{'changes': [
        owner.card_change([owner.source(material)])]}])
    runtime = KeeperRuntime(owner.agent, token=TOKEN,
                            python=str(tmp_path / 'missing-python.exe'))
    contexts = []

    def dispatch(context):
        contexts.append(context)
        return runtime.dispatch(context)

    result = owner.keeper.review(receipt['request_id'], token=TOKEN, dispatch=dispatch)
    child = owner.agent.list_children(owner.task['task_id'], token=TOKEN)[0]
    assert child['status'] == 'failed'
    assert child['role'] == 'keeper'
    assert result['status'] == 'failed' and result['child_id'] == child['child_id']
    assert owner.keeper.receipt(receipt['request_id'], token=TOKEN)['status'] == 'failed'
    assert owner.keeper.review(receipt['request_id'], token=TOKEN,
                               dispatch=lambda context: pytest.fail('re-dispatched')) == result

    with pytest.raises(KeeperRuntimeError):
        runtime.dispatch(contexts[0])
    assert len(owner.agent.list_children(owner.task['task_id'], token=TOKEN)) == 1


def test_timeout_finishes_the_child_and_returns_cleanly(tmp_path, monkeypatch):
    owner = Owner(tmp_path)
    material = owner.material()
    context = owner.direct_context(source=owner.source(material))
    runtime = KeeperRuntime(owner.agent, token=TOKEN, timeout=0.0001)

    class SlowProcess:
        returncode = 0
        killed = False

        def __init__(self):
            self.calls = 0

        def communicate(self, payload=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise runtime_module.subprocess.TimeoutExpired('worker', timeout)
            return b'', b''

        def kill(self):
            self.killed = True

        def poll(self):
            return 0

    slow = SlowProcess()
    monkeypatch.setattr(runtime_module.subprocess, 'Popen', lambda *a, **kw: slow)
    with pytest.raises(KeeperRuntimeError) as raised:
        runtime.dispatch(context)
    assert 'timed out' in str(raised.value)
    child = owner.agent.list_children(owner.task['task_id'], token=TOKEN)[0]
    assert child['status'] == 'failed' and 'timed out' in child['error']
    assert slow.killed


def test_start_keeper_child_role_is_trusted_and_survives_a_new_turn(tmp_path):
    agent = AgentService(tmp_path, token=TOKEN)
    task = agent.create_task({'schema_version': 1, 'operation_id': 'task',
                              'title': 'role'}, token=TOKEN)
    with pytest.raises(CommandError):
        agent.start_child({'schema_version': 1, 'operation_id': 'generic',
                           'task_id': task['task_id'], 'input_version': task['version'],
                           'role': 'keeper', 'input_text': 'review',
                           'snapshots': {}}, token=TOKEN)
    keeper = agent.start_keeper_child({'schema_version': 1,
                                       'operation_id': 'keeper-direct',
                                       'task_id': task['task_id'],
                                       'input_version': task['version'],
                                       'input_text': 'review', 'snapshots': {}},
                                      token=TOKEN)
    assert keeper['role'] == 'keeper'
    agent.begin_turn({'schema_version': 1, 'operation_id': 'new-plan',
                      'task_id': task['task_id'], 'expected_version': task['version'],
                      'text': 'A later plan'}, token=TOKEN)
    finished = agent.finish_child({'schema_version': 1, 'operation_id': 'keeper-finish',
                                   'child_id': keeper['child_id'],
                                   'expected_version': keeper['version'],
                                   'status': 'succeeded', 'result': 'ok', 'error': ''},
                                  token=TOKEN)
    assert finished['status'] == 'succeeded'
    with pytest.raises(CommandError):
        agent.start_keeper_child({'schema_version': 1, 'operation_id': 'too-new',
                                  'task_id': task['task_id'],
                                  'input_version': task['version'] + 99,
                                  'input_text': 'review', 'snapshots': {}},
                                 token=TOKEN)


def test_worker_requires_sources_and_rejects_duplicates():
    base = {'schema_version': 1, 'request_id': 'r', 'task_id': 't',
            'task_version': 1, 'report_id': 'turn', 'report_version': 1,
            'provenance': {'sources': ['material-1']},
            'groups': [{'proposal_id': 'p', 'changes': [{
                'change_id': 'c', 'entity': 'card:One', 'action': 'create',
                'source': [{'material_id': 'material-1', 'material_version': 1,
                            'locator': 'p2', 'excerpt': EXCERPT, 'content_hash': 'a' * 64,
                            'anchor': {'id': 'p2', 'start': 0, 'end': len(EXCERPT)}}],
                'relations': ['card:Two']}]}]}
    assert verify(base)['status'] == 'reviewed'

    no_source = json.loads(json.dumps(base))
    no_source['groups'][0]['changes'][0]['source'] = None
    assert verify(no_source)['status'] == 'returned'

    unverified = json.loads(json.dumps(base))
    unverified['groups'][0]['changes'][0]['source'][0].pop('content_hash')
    assert verify(unverified)['status'] == 'returned'

    duplicate = json.loads(json.dumps(base))
    duplicate['groups'][0]['proposal_id'] = 'p'
    duplicate['groups'].append(json.loads(json.dumps(duplicate['groups'][0])))
    assert verify(duplicate)['status'] == 'returned'

    duplicate_relation = json.loads(json.dumps(base))
    duplicate_relation['groups'][0]['changes'][0]['relations'] = ['card:Two', 'card:Two']
    assert verify(duplicate_relation)['status'] == 'returned'


def test_bridge_ack_is_async_and_replay_reuses_the_completed_review(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    emitted = []
    reviewed = threading.Event()

    def emit(kind, data):
        emitted.append((kind, data))
        if kind in ('keeper_review', 'keeper_review_error'):
            reviewed.set()

    runtime = KeeperRuntime(owner.agent, token=TOKEN)
    commands = KeeperCommands(lambda: owner.keeper, runtime, token=TOKEN, emit=emit)
    request = {'schema_version': 1, 'operation_id': 'bridge-request',
               'task_id': owner.task['task_id'], 'task_version': owner.task['version'],
               'report_id': owner.report['turn_id'],
               'report_version': owner.report['version'],
               'groups': [{'changes': [owner.card_change([owner.source(material)])]}]}
    command = {'action': 'keeper_test_request', 'operation_id': 'bridge-request',
               'request': request}
    commands.test_request(command)
    assert emitted[0][0] == 'keeper_test_request'
    assert reviewed.wait(20)
    assert [kind for kind, _ in emitted] == ['keeper_test_request', 'keeper_review']
    review = emitted[1][1]
    assert review['status'] == 'pending' and len(review['proposal_ids']) == 1
    assert len(owner.agent.list_children(owner.task['task_id'], token=TOKEN)) == 1

    reviewed.clear()
    commands.test_request(command)
    assert reviewed.wait(2)
    assert [kind for kind, _ in emitted[-2:]] == ['keeper_test_request', 'keeper_review']
    assert emitted[-1][1] == review
    assert len(owner.agent.list_children(owner.task['task_id'], token=TOKEN)) == 1
    assert len(owner.keeper.list(token=TOKEN, status='pending')) == 1

    kind, data = commands.read({'action': 'keeper_list', 'operation_id': 'list-1'})
    assert kind == 'keeper_list' and data['operation_id'] == 'list-1'
    assert len(data['proposals']) == 1
    proposal_id = data['proposals'][0]['proposal_id']
    kind, data = commands.read({'action': 'keeper_open', 'operation_id': 'open-1',
                                'proposal_id': proposal_id})
    assert kind == 'keeper_open' and data['operation_id'] == 'open-1'
    assert data['proposal']['proposal_id'] == proposal_id
    assert data['proposal']['status'] == 'pending'


def test_enabled_bridge_recovers_queued_review_after_reopen(tmp_path):
    owner = Owner(tmp_path)
    material = owner.material()
    receipt = owner.request([{'changes': [owner.card_change([owner.source(material)])]}])
    reopened = KeeperService(owner.agent, owner.materials, owner.cards,
                              owner.themes, owner.frontiers, token=TOKEN)
    events = []
    commands = KeeperCommands(lambda: reopened, KeeperRuntime(owner.agent, token=TOKEN),
                              token=TOKEN, emit=lambda kind, data: events.append((kind, data)))
    commands.resume_queued()
    assert wait_for(lambda: bool(events))
    assert events[0][0] == 'keeper_review'
    assert events[0][1]['request_id'] == receipt['request_id']
    assert events[0][1]['status'] == 'pending'
    assert reopened.queued(token=TOKEN) == []
    assert reopened.view(events[0][1]['proposal_ids'][0], token=TOKEN)['child_id'] == events[0][1]['child_id']


def test_bridge_rejects_caller_authority_and_gates_the_test_request(tmp_path, monkeypatch):
    emitted = []
    commands = KeeperCommands(lambda: pytest.fail('service must not run'),
                              object(), token=TOKEN,
                              emit=lambda kind, data: emitted.append((kind, data)))
    for field in ('role', 'principal', 'output', 'provenance'):
        with pytest.raises(CommandError):
            commands.test_request({'action': 'keeper_test_request',
                                   'operation_id': 'op',
                                   'request': {'schema_version': 1, 'operation_id': 'op',
                                               field: 'caller'}})
    assert emitted == []
    with pytest.raises(CommandError):
        keeper_bridge_command(commands, {'action': 'keeper_request'}, test_enabled=True)
    with pytest.raises(CommandError):
        keeper_bridge_command(commands, {'action': 'keeper_test_request'}, test_enabled=False)
    monkeypatch.setenv('HORIZON_KEEPER_TEST_REQUESTS', '1')
    assert keeper_test_requests_enabled()
    monkeypatch.setenv('HORIZON_KEEPER_TEST_REQUESTS', '0')
    assert not keeper_test_requests_enabled()


def test_worker_environment_is_sanitized(monkeypatch):
    monkeypatch.setenv('HORIZON_DESKTOP_TOKEN', 'secret')
    monkeypatch.setenv('OPENAI_API_KEY', 'secret')
    env = runtime_module._sanitized_environment(Path('.'))
    assert 'HORIZON_DESKTOP_TOKEN' not in env
    assert 'OPENAI_API_KEY' not in env
    assert env['PYTHONUTF8'] == '1'
    assert env['PYTHONDONTWRITEBYTECODE'] == '1'


def test_review_event_keeps_the_persisted_receipt_status():
    assert review_event('r', {'status': 'pending', 'proposal_ids': ['p']}) == {
        'request_id': 'r', 'status': 'pending', 'proposal_ids': ['p']}
    assert review_event('r', {'status': 'returned', 'proposal_ids': [],
                              'reason': 'evidence_insufficient'})['reason'] == \
        'evidence_insufficient'
