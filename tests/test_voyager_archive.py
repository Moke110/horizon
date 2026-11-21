import base64
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from hostrun.agent_service import CommandError
from hostrun.desktop_bridge import material_command
from hostrun.voyager_materials import MaterialsService


def test_archived_reference_and_exact_deletion_confirmation_survive_reopen(tmp_path):
    token = 'desktop'
    service = MaterialsService(tmp_path, token=token)
    def material(operation):
        return service.register_text(dict(schema_version=1, operation_id=operation,
            title='Shared paper', text='Shared original', source_label='Public catalog'), token=token)
    original, shared = material('first'), material('shared')
    mid = original['material_id']
    def reference(owner):
        return service.register_reference(mid, owner, original['snapshot']['content_hash'], token=token)
    reference('report:one')
    material_command(service, dict(action='materials_archive', material_id=mid), token=token)
    reopened = MaterialsService(tmp_path, token=token)
    assert [item['material_id'] for item in reopened.list_materials(token=token)] == [shared['material_id']]
    assert [item['material_id'] for item in reopened.list_materials(archived=True, token=token)] == [mid]
    assert reopened.get_package(mid, token=token)['original_text'] == 'Shared original'
    before = material_command(reopened, dict(action='materials_delete_impact', material_id=mid), token=token)
    assert [item['owner_id'] for item in before['references']] == ['report:one']
    reference('report:two')
    with pytest.raises(CommandError, match='删除影响已变更'):
        reopened.delete_material(mid, before['confirmation'], token=token)
    preview = reopened.deletion_impact(mid, token=token)
    reopened.delete_material(mid, preview['confirmation'], token=token)
    assert reopened.list_materials(archived=True, token=token) == []
    assert reopened.get_package(shared['material_id'], token=token)['original_text'] == 'Shared original'
    assert reopened.resolve_reference(mid, 'report:one', token=token)['status'] == 'removed'
    reopened.archive_material(shared['material_id'], token=token)
    preview = reopened.deletion_impact(shared['material_id'], token=token)
    reopened.delete_material(shared['material_id'], preview['confirmation'], token=token)
    report = reopened.collect_orphan_snapshots(token=token)
    assert report['removed'] == [original['snapshot']['content_hash']]


def test_reclaimer_waits_for_inflight_snapshot_registration(tmp_path):
    published, allow_commit, collecting = Event(), Event(), Event()

    def observe(phase):
        if phase == 'after_publish':
            published.set()
            assert allow_commit.wait(12)

    publisher = MaterialsService(tmp_path, token='desktop', fault_observer=observe)
    collector = MaterialsService(tmp_path, token='desktop')

    def collect():
        collecting.set()
        return collector.collect_orphan_snapshots(token='desktop')

    command = dict(schema_version=1, operation_id='register', title='Original',
                   text='In-flight original', source_label='Public catalog')
    with ThreadPoolExecutor(max_workers=2) as workers:
        registration = workers.submit(publisher.register_text, command, token='desktop')
        try:
            assert published.wait(10)
            reclamation = workers.submit(collect)
            assert collecting.wait(10)
            with pytest.raises(TimeoutError):
                reclamation.result(timeout=0.2)
        finally:
            allow_commit.set()
        material = registration.result(timeout=10)
        assert reclamation.result(timeout=10) == {'removed': []}
    assert collector.get_package(material['material_id'], token='desktop')['original_text'] == command['text']


def test_pdf_edits_retain_the_cited_version_and_older_operation_snapshots(tmp_path):
    token = 'desktop'
    service = MaterialsService(tmp_path, token=token)
    result = service.register_pdf(dict(schema_version=1, operation_id='pdf', title='Scanned',
        source_label='User PDF', pdf_base64=base64.b64encode(b'%PDF-1.7\ninvalid').decode()), token=token)
    mid = result['material_id']
    original = service.resolve_pdf(dict(schema_version=1, operation_id='first-text', material_id=mid,
        expected_version=1, action='supplement', text='First readable version'), token=token)
    service.register_reference(mid, 'report:v1', original['snapshot']['content_hash'], token=token)
    service.resolve_pdf(dict(schema_version=1, operation_id='second-text', material_id=mid,
        expected_version=2, action='supplement', text='Second readable version'), token=token)
    service.archive_material(mid, token=token)
    assert service.collect_orphan_snapshots(token=token) == {'removed': []}
    assert service.resolve_reference(mid, 'report:v1', token=token)['original_text'] == 'First readable version'
    assert service.get_package(mid, token=token)['original_text'] == 'Second readable version'


def test_reimport_does_not_reuse_deleted_academic_versions(tmp_path):
    token = 'desktop'
    service = MaterialsService(tmp_path, token=token)
    candidate = dict(source='crossref', source_id='work', doi='10.1234/work',
                     title='Proceedings', url='https://example.org/proceedings')

    def register(operation, body):
        capture = dict(kind='xml', original=body.encode(), text=body, url=candidate['url'])
        return service.register_academic(operation, 'request', candidate, capture, [], token=token)

    first = register('one', 'Version one')
    second = register('two', 'Version two')
    for material in (first, second):
        mid = material['material_id']
        service.archive_material(mid, token=token)
        confirmed = service.deletion_impact(mid, token=token)
        service.delete_material(mid, confirmed['confirmation'], token=token)
    restored = register('three', 'Version two')
    assert restored['entity_id'] == first['entity_id']
    assert restored['version'] == 3
    assert restored['material_id'] != second['material_id']


@pytest.mark.skipif(os.name != 'nt', reason='managed Windows snapshot identity boundary')
def test_managed_reclamation_releases_enrolled_blob_for_reregistration(tmp_path, monkeypatch):
    from hostrun.cleanup import initialize_layout, prepare_instance

    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    control = tmp_path / 'Horizon/test-instances'
    control.mkdir(parents=True)
    instance = prepare_instance(control)['instance_id']
    initialize_layout(control, instance)
    service = MaterialsService(control / instance / 'data', token='desktop')
    command = dict(schema_version=1, operation_id='one', title='Original',
                   text='Managed original', source_label='Public catalog')
    result = service.register_text(command, token='desktop')
    mid = result['material_id']
    service.archive_material(mid, token='desktop')
    impact = service.deletion_impact(mid, token='desktop')
    service.delete_material(mid, impact['confirmation'], token='desktop')
    assert service.collect_orphan_snapshots(token='desktop')['removed'] == [result['snapshot']['content_hash']]
    repeated = service.register_text(dict(command, operation_id='two'), token='desktop')
    assert service.get_package(repeated['material_id'], token='desktop')['original_text'] == command['text']
