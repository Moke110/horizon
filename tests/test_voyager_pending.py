import pytest

from hostrun.agent_service import AgentService, CommandError
from hostrun.desktop_bridge import material_command
from hostrun.voyager_materials import MaterialsService
from hostrun.voyager_service import VoyagerService


TOKEN = 'desktop'


def pending_material(home, task=None):
    class Catalog:
        def search(self, query):
            return dict(exhausted=True, next_cursor=None, receipt='receipt', candidates=[
                dict(candidate, discovered_at=10)])

    agent = AgentService(home, token=TOKEN)
    if task is None:
        task = agent.create_task(dict(schema_version=1, operation_id='task', title='Research'), token=TOKEN)
    voyager = VoyagerService(home, token=TOKEN, providers={'crossref':Catalog()})
    plan = voyager.create(dict(schema_version=1, operation_id='plan', task_id=task['task_id'],
        scope=dict(text='solar', sources=['crossref']),
        window=dict(mode='incremental', basis='discovered', from_at=0, until_at=20, overlap_seconds=0)), token=TOKEN)
    candidate = dict(source='crossref', source_id='10.1234/solar', title='Solar observations',
                     abstract='Only a short abstract.', url='https://example.org/solar', doi='10.1234/solar')
    voyager.execute(dict(schema_version=1, operation_id='retrieve', request_id=plan['request_id'],
                         expected_version=plan['version']), token=TOKEN)
    materials = MaterialsService(home, token=TOKEN)
    material = materials.register_academic('body', plan['request_id'], candidate, None,
        [dict(stage='landing', outcome='unavailable', http_status=404)], token=TOKEN)
    return materials, plan, task, material


def test_abandon_pending_material_is_durable_and_idempotent(tmp_path):
    materials, plan, task, material = pending_material(tmp_path)
    command = dict(action='materials_pending_resolve', schema_version=1, operation_id='abandon',
                   decision='abandon', expected_version=material['version'], material_id=material['material_id'],
                   request_id=plan['request_id'], task_id=task['task_id'])
    abandoned = material_command(materials, command, token=TOKEN)
    assert abandoned['academic']['status'] == 'abandoned'
    reopened = MaterialsService(tmp_path, token=TOKEN)
    assert material_command(reopened, command, token=TOKEN) == abandoned
    assert material_command(reopened, dict(action='materials_open', material_id=material['material_id']), token=TOKEN) == abandoned
    with pytest.raises(CommandError) as error:
        material_command(reopened, dict(command, operation_id='other', expected_version=abandoned['version']), token=TOKEN)
    assert error.value.code == 'invalid_transition'
    from hostrun.voyager_fulltext import FulltextService

    voyager = VoyagerService(tmp_path, token=TOKEN)
    request = voyager.get(plan['request_id'], token=TOKEN)
    with pytest.raises(CommandError) as retry:
        FulltextService(voyager, materials, fetch=lambda *a, **kw: pytest.fail('abandoned source retried')).acquire(
            dict(schema_version=1, operation_id='try-again', request_id=request['request_id'],
                 expected_version=request['version'], candidate_source='crossref', candidate_id='10.1234/solar'),
            token=TOKEN)
    assert retry.value.code == 'invalid_transition'


def test_explicit_abstract_permission_exposes_limit_only_to_original_task(tmp_path):
    materials, plan, task, material = pending_material(tmp_path)
    command = dict(action='materials_pending_resolve', schema_version=1, operation_id='consent',
                   decision='authorize_abstract', expected_version=material['version'],
                   material_id=material['material_id'], task_id=task['task_id'], request_id=plan['request_id'])
    with pytest.raises(CommandError) as error:
        material_command(materials, command, token=TOKEN)
    assert error.value.code == 'confirmation_required'
    allowed = material_command(materials, dict(command, confirmed=True), token=TOKEN)
    assert allowed['academic']['status'] == 'pending_fulltext'
    assert allowed['original_text'] == ''
    assert allowed['academic']['abstract_authorizations'][0]['abstract'] == 'Only a short abstract.'
    assert '尚未核对全文' in allowed['academic']['abstract_authorizations'][0]['limitation']
    reopened = MaterialsService(tmp_path, token=TOKEN)
    default = material_command(reopened, dict(action='materials_open', material_id=material['material_id']), token=TOKEN)
    assert default['academic']['abstract_authorizations'] == []
    assert material_command(reopened, dict(command, confirmed=True), token=TOKEN) == allowed


def readable_pdf():
    import base64
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=150, height=150)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({
        NameObject('/F1'): DictionaryObject({NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 10 Tf 10 100 Td (Verified solar body) Tj ET')
    page.replace_contents(stream)
    output = BytesIO()
    writer.write(output)
    return base64.b64encode(output.getvalue()).decode('ascii')


def test_supplement_queues_same_plan_without_advancing_coverage_or_resuming(tmp_path):
    from hostrun import agent_scheduling as schedules, voyager_late_fulltext

    agent = AgentService(tmp_path, token=TOKEN)
    plan = agent.schedule_command(dict(schema_version=1, operation_id='schedule', title='Solar',
        scope='solar', next_at=20, start_at=10, interval_seconds=50), token=TOKEN)
    run = schedules.tick(agent, now=20, token=TOKEN)[0]
    turn = schedules.bind_run(agent, run, token=TOKEN)
    task = next(item for item in agent.list_tasks(token=TOKEN) if item['task_id']==turn['task_id'])
    materials, request, _, material = pending_material(tmp_path, task)
    from hostrun.editor_reports import ReportService, report_evidence
    pending = material_command(materials, dict(action='materials_pending_resolve', schema_version=1,
        operation_id='summary-first', expected_version=material['version'], decision='authorize_abstract',
        material_id=material['material_id'], task_id=task['task_id'], confirmed=True,
        request_id=request['request_id']), token=TOKEN)
    evidence = report_evidence(materials, material['material_id'], task['task_id'], token=TOKEN)
    reports = ReportService(tmp_path, token=TOKEN)
    summary = reports.save('summary-report', task['task_id'], evidence, {},
                           [{'kind': 'unknown', 'text': '全文未知'}], token=TOKEN)
    material = pending
    command = dict(action='materials_pending_resolve', schema_version=1, operation_id='pdf-late',
                   decision='supplement', expected_version=material['version'], material_id=material['material_id'],
                   pdf_base64=readable_pdf())
    paused = agent.list_schedules(token=TOKEN)[0]
    paused = agent.schedule_command(dict(schema_version=1, operation_id='pause', schedule_id=plan['schedule_id'],
             expected_version=paused['version'], action='pause'), token=TOKEN)
    result = material_command(materials, command, token=TOKEN)
    assert result['academic']['status'] == 'available'
    assert 'Verified solar body' in result['original_text']
    assert result['entity_id'] == material['entity_id']
    assert material_command(MaterialsService(tmp_path, token=TOKEN), command, token=TOKEN) == result
    assert not voyager_late_fulltext.claim(agent, materials, token=TOKEN, occupied=0)
    assert agent.list_schedules(token=TOKEN)[0]['status'] == 'paused'
    agent.commit_turn(dict(schema_version=1, operation_id='finish-original', turn_id=turn['turn_id'],
        expected_version=turn['version'], status='completed', result='Original result'), token=TOKEN)
    ended = schedules.finish_run(agent, run['run_id'], covered_until=20, token=TOKEN)
    assert not voyager_late_fulltext.claim(agent, materials, token=TOKEN, occupied=0)
    resumed = agent.schedule_command(dict(schema_version=1, operation_id='resume', schedule_id=plan['schedule_id'],
        expected_version=ended['version'], action='resume'), token=TOKEN)
    (operation, followup), = voyager_late_fulltext.claim(agent, materials, token=TOKEN, occupied=0)
    assert followup['task_id'] == task['task_id'] and followup['turn_id'] != turn['turn_id']
    assert summary['report_id'] in followup['text'] and 'base_version' in followup['text']
    assert result['material_id'] in followup['text']
    evidence = report_evidence(materials, result['material_id'], task['task_id'], token=TOKEN)
    revised = reports.save('late-report', task['task_id'], evidence, {},
                           [{'kind': 'source', 'text': '全文正文可用。',
                             'citations': [{'locator':'page 1', 'excerpt':'Verified solar body'}]}],
                           report_id=summary['report_id'], base_version=1, token=TOKEN)
    assert revised['version'] == 2
    assert revised['source']['material_id'] == result['material_id']
    assert reports.get(summary['report_id'], task['task_id'], version=1, token=TOKEN) == summary
    assert agent.list_schedules(token=TOKEN)[0] == resumed
    assert not schedules.tick(agent, now=70, token=TOKEN)
    assert voyager_late_fulltext.claim(agent, materials, token=TOKEN, occupied=0) == [(operation, followup)]


def test_editor_abstract_requires_task_permission_and_stops_opening_after_deletion(tmp_path):
    from hostrun.editor_reports import ReportService, report_evidence

    materials, plan, task, material = pending_material(tmp_path)
    owner = task['task_id']
    material_id = material['material_id']
    with pytest.raises(CommandError, match='摘要授权'):
        report_evidence(materials, material_id, owner, token=TOKEN)
    material_command(materials, dict(action='materials_pending_resolve', schema_version=1,
        operation_id='consent', decision='authorize_abstract', expected_version=material['version'],
        confirmed=True, material_id=material_id, task_id=owner, request_id=plan['request_id']), token=TOKEN)
    with pytest.raises(CommandError, match='摘要授权'):
        report_evidence(materials, material_id, 'different-task', token=TOKEN)

    evidence = report_evidence(materials, material_id, owner, token=TOKEN)
    reports = ReportService(tmp_path, token=TOKEN)
    report = reports.save('save', owner, evidence, {}, [
        dict(kind='source', text='已有一份简短摘要。', citations=[
            dict(locator='abstract', excerpt='Only a short abstract.')])
    ], token=TOKEN)
    report_id = report['report_id']
    assert reports.citation(report_id, owner, 0, 0, token=TOKEN)['segment'] == 'Only a short abstract.'
    with pytest.raises(CommandError, match='找不到报道'):
        reports.citation(report_id, 'different-task', 0, 0, token=TOKEN)

    materials.archive_material(material_id, token=TOKEN)
    impact = materials.deletion_impact(material_id, token=TOKEN)
    assert any(item['owner_id'].startswith(f'report:{report_id}:') for item in impact['references'])
    materials.delete_material(material_id, impact['confirmation'], token=TOKEN)
    assert reports.citation(report_id, owner, 0, 0, token=TOKEN) == {'status': 'removed', 'segment': None}
    assert reports.get(report_id, owner, token=TOKEN)['claims'][0]['citations'][0]['excerpt'] == '原摘要不可用'
