import json

from hostrun.desktop_bridge import voyager_command
from hostrun.voyager_service import VoyagerService


def test_unpaywall_contact_is_write_only_and_survives_restart(tmp_path):
    contact = 'reader@example.org'
    service = VoyagerService(tmp_path, token='desktop')
    kind, status = voyager_command(service, dict(action='voyager_save_source_key',
        source='unpaywall', key=contact), token='desktop')
    assert kind == 'voyager_source_settings'
    assert status['unpaywall'] == 'saved'
    assert contact not in json.dumps(status)
    restarted = VoyagerService(tmp_path, token='desktop')
    assert voyager_command(restarted, {'action': 'voyager_source_settings'}, token='desktop')[1] == status
    _, cleared = voyager_command(restarted, dict(action='voyager_save_source_key',
        source='unpaywall', key=None), token='desktop')
    assert 'unpaywall' not in cleared


def test_candidate_fulltext_keeps_xml_body_and_revision_receipt(tmp_path):
    from hostrun.voyager_fulltext import FulltextService
    from hostrun.voyager_materials import MaterialsService

    class Catalog:
        def __init__(self, source):
            self.source = source

        def search(self, query):
            return dict(exhausted=True, next_cursor=None, receipt='fixture', candidates=[dict(
                source=self.source, source_id='PMC:PMC123' if self.source == 'europepmc' else '10.1234/proceedings',
                native_fulltext_urls=['https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123/fullTextXML'],
                title='Public proceedings',
                url='https://europepmc.org/article/PMC/PMC123', doi='10.1234/proceedings',
                discovered_at=10, published_at=None, pmcid='PMC123')])

    calls = []

    def fetch(url, headers, **limits):
        calls.append(url)
        return 200, {'Content-Type': 'application/xml'}, b'''<?xml version="1.0"?>
<!DOCTYPE article SYSTEM "JATS-journalpublishing.dtd">
<article><front><article-meta><abstract>Do not use this abstract.</abstract></article-meta></front>
<body><sec><title>Results</title><p>The verified public body.</p></sec></body></article>'''

    service = VoyagerService(tmp_path, token='desktop',
        providers={name: Catalog(name) for name in ('europepmc', 'crossref')})
    materials = MaterialsService(tmp_path, token='desktop')
    bodies = FulltextService(service, materials, fetch=fetch)
    plan = service.create(dict(schema_version=1, operation_id='plan',
        scope=dict(text='Public proceedings', sources=['europepmc']),
        window=dict(mode='incremental', basis='discovered', from_at=0, until_at=20, overlap_seconds=0),
        limits=dict(queries=1, candidates=1, fulltexts=1)),
        token='desktop')
    result = service.execute(dict(schema_version=1, operation_id='execute',
        request_id=plan['request_id'], expected_version=plan['version']), token='desktop')
    command = dict(schema_version=1, request_id=plan['request_id'], operation_id='capture',
        expected_version=result['version'], candidate_source='europepmc', candidate_id='PMC:PMC123')
    kind, view = voyager_command(service, dict(action='voyager_fulltext', **command),
        token='desktop', fulltext=bodies)
    assert kind == 'voyager_view'
    work = view['fulltext_work'][0]
    assert work['status'] == 'succeeded'
    material = materials.get_package(work['evidence_ref'].removeprefix('material:'), token='desktop')
    assert material['original_text'] == 'Results\n\nThe verified public body.'
    assert material['academic']['identity'] == 'doi:10.1234/proceedings'
    assert material['academic']['attempts'][0]['stage'] == 'native'
    assert work == voyager_command(service, dict(action='voyager_fulltext', **command),
        token='desktop', fulltext=bodies)[1]['fulltext_work'][0]
    assert calls == ['https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123/fullTextXML']
    other = service.create(dict(schema_version=1, operation_id='other-plan',
        scope=dict(text='Public proceedings in research', sources=['crossref']),
        window=dict(mode='incremental', basis='discovered', from_at=0, until_at=20, overlap_seconds=0)), token='desktop')
    found = service.execute(dict(schema_version=1, operation_id='other-search',
        request_id=other['request_id'], expected_version=other['version']), token='desktop')
    _, other_view = voyager_command(service, dict(action='voyager_fulltext', schema_version=1,
        operation_id='other-capture', request_id=other['request_id'], expected_version=found['version'],
        candidate_source='crossref', candidate_id='10.1234/proceedings'), token='desktop', fulltext=bodies)
    assert other_view['fulltext_work'][0]['evidence_ref'] == work['evidence_ref']
    updated = materials.get_package(material['material_id'], token='desktop')
    assert updated['snapshot'] == material['snapshot']
    assert [(link['request_id'], link['source']) for link in updated['academic']['provenance']] == [
        (plan['request_id'], 'europepmc'), (other['request_id'], 'crossref')]


def test_pending_fulltext_records_locator_and_access_failure_without_leaking_contact(tmp_path):
    from urllib.parse import parse_qs, urlsplit
    from hostrun.voyager_fulltext import FulltextService
    from hostrun.voyager_materials import MaterialsService

    contact = 'reader+oa@example.org'

    class Catalog:
        def search(self, query):
            return dict(exhausted=True, next_cursor=None, receipt='catalog', candidates=[dict(
                source='crossref', source_id='10.1234/closed', doi='10.1234/closed', title='Closed record',
                url='https://publisher.example.com/closed', discovered_at=10,
                native_fulltext_urls=['https://publisher.example.com/wrong.pdf'])])

    requested = []

    def fetch(url, headers, **limits):
        address = urlsplit(url)
        requested.append(address.hostname)
        if address.hostname == 'api.unpaywall.org':
            assert parse_qs(address.query) == {'email': [contact]}
            return 200, {}, b'{"best_oa_location":{"url_for_pdf":"https://archive.example.com/closed"}}'
        if address.hostname == 'api.openalex.org':
            return 200, {}, b'{"best_oa_location":null}'
        if address.path == '/wrong.pdf':
            return 200, {'Content-Type': 'application/pdf'}, b'<html>Paywall</html>'
        return 403, {}, b''

    voyager = VoyagerService(tmp_path, token='desktop', providers={'crossref': Catalog()})
    materials = MaterialsService(tmp_path, token='desktop')
    voyager.save_source_setting('unpaywall', contact, token='desktop')
    plan = voyager.create(dict(schema_version=1, operation_id='plan',
        scope=dict(text='Closed record', sources=['crossref']),
        window=dict(mode='incremental', basis='discovered', from_at=0, until_at=20, overlap_seconds=0)),
        token='desktop')
    result = voyager.execute(dict(schema_version=1, operation_id='execute',
        request_id=plan['request_id'], expected_version=plan['version']), token='desktop')
    _, view = voyager_command(voyager, dict(action='voyager_fulltext', schema_version=1,
        request_id=plan['request_id'], operation_id='capture', expected_version=result['version'],
        candidate_source='crossref', candidate_id='10.1234/closed'), token='desktop',
        fulltext=FulltextService(voyager, materials, fetch=fetch))
    work = view['fulltext_work'][0]
    assert work['status'] == 'failed'
    package = materials.get_package(work['evidence_ref'].removeprefix('material:'), token='desktop')
    assert package['original_text'] == '' and package['academic']['status'] == 'pending_fulltext'
    assert package['academic']['attempts'][0]['outcome'] == 'body_not_verified'
    assert requested == ['publisher.example.com', 'api.openalex.org', 'api.unpaywall.org',
        'archive.example.com', 'publisher.example.com']
    assert contact not in json.dumps(package) and '%40' not in json.dumps(package)
