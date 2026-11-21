from hostrun.voyager_doi_sources import OpenAlexProvider


def test_crossref_keeps_score_order_when_requesting_next_page(tmp_path, monkeypatch):
    import io
    import json
    from urllib.parse import parse_qs, urlsplit
    from hostrun.voyager_doi_sources import CrossrefProvider

    class Response(io.BytesIO):
        status = 200

    requests = []

    def open_page(request, timeout):
        params = parse_qs(urlsplit(request.full_url).query)
        requests.append(params)
        index = len(requests)
        if params.get('sort') != ['score']:
            index = 0
        return Response(json.dumps({'message': {
            'items': [{'DOI': f'10.1234/paper-{index}', 'title': ['Paper']}],
            'next-cursor': f'opaque-{index}',
        }}).encode())

    monkeypatch.setattr('urllib.request.urlopen', open_page)
    monkeypatch.setattr(CrossrefProvider, '_next', 0)
    provider = CrossrefProvider(rate_directory=tmp_path)
    query = dict(term='wave discovery', cursor=None, limit=1, timeout_seconds=5, window={'basis': 'discovered'})
    first = provider.search(query)
    second = provider.search(dict(query, cursor=first['next_cursor']))
    assert [page['candidates'][0]['doi'] for page in (first, second)] == ['10.1234/paper-1', '10.1234/paper-2']
    assert [request['cursor'] for request in requests] == [['*'], ['opaque-1']]


def test_unconfigured_openalex_stops_before_search(monkeypatch):
    monkeypatch.delenv('OPENALEX_API_KEY', raising=False)
    assert OpenAlexProvider().search({}) == {'error_code': 'unconfigured'}

def test_unknown_quota_never_sends_a_work_query(monkeypatch):
    from io import BytesIO
    import urllib.request

    calls = []
    def open_response(req, timeout):
        calls.append(req.full_url)
        return BytesIO(b'{"rate_limit":{}}')

    monkeypatch.setenv('OPENALEX_API_KEY', 'fixture')
    monkeypatch.setattr(urllib.request, 'urlopen', open_response)
    result = OpenAlexProvider().search({'timeout_seconds': 3})
    assert calls == ['https://api.openalex.org/rate-limit']
    assert result == {'error_code': 'budget_unknown'}


def test_openalex_records_partial_reference_ids_without_leaking_key(monkeypatch):
    from io import BytesIO
    import urllib.request
    import json

    replies = iter([
        {'rate_limit': {'daily_remaining_usd':1, 'endpoint_costs_usd':{'search':0.001}}},
        {'results':[{'id':'https://openalex.org/W1','display_name':'Solar irradiance',
                     'doi':'https://doi.org/10.1000/ABC','referenced_works':['https://openalex.org/W9'],
                     'referenced_works_count':3,'cited_by_count':10}], 'meta':{'next_cursor':'page2'}}
    ])
    monkeypatch.setenv('OPENALEX_API_KEY','private-test-key')
    monkeypatch.setattr(urllib.request, 'urlopen', lambda req, timeout: BytesIO(json.dumps(next(replies)).encode()))
    result = OpenAlexProvider().search(dict(term='solar',limit=1,cursor=None,timeout_seconds=5))
    row = result['candidates'][0]
    assert (row['doi'], row['reference_count'], row['cited_by_count']) == ('10.1000/abc',3,10)
    assert row['reference_ids'] == ['https://openalex.org/W9']
    assert result['next_cursor'] == 'page2' and 'private-test-key' not in json.dumps(result)

def test_same_doi_from_two_sources_keeps_both_reference_receipts(tmp_path):
    from hostrun.voyager_service import VoyagerService

    class Catalog:
        def __init__(self, name, source_id, doi):
            self.row = dict(source=name, source_id=source_id, doi=doi, title='Solar irradiance',
                            url='https://doi.org/10.1000/abc', published_at=150, discovered_at=150,
                            reference_ids=['10.1000/ref'], reference_count=2, cited_by_count=None)

        def search(self, actual_query):
            return dict(candidates=[self.row], receipt='fixture', next_cursor=None, exhausted=True)

    providers = {'openalex':Catalog('openalex','W1','https://doi.org/10.1000/ABC'),
                 'crossref':Catalog('crossref','10.1000/abc','10.1000/abc')}
    service = VoyagerService(tmp_path,token='s',providers=providers)
    plan = service.create(dict(schema_version=1,operation_id='plan',scope=dict(text='solar',sources=list(providers)),
                         window=dict(mode='incremental',basis='published',from_at=100,until_at=200,overlap_seconds=0)), token='s')
    result = service.execute(dict(schema_version=1,request_id=plan['request_id'],expected_version=1,operation_id='run'),token='s')
    rows = VoyagerService(tmp_path,token='s').view(result['request_id'],token='s')['candidates']
    assert [(row['source'],row['source_id']) for row in rows] == [('openalex','W1'),('crossref','10.1000/abc')]
    assert {row['canonical_id'] for row in rows} == {'doi:10.1000/abc'}
    assert all(row['reference_count']==2 and row['reference_ids']==['10.1000/ref'] for row in rows)
