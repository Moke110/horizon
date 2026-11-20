from hostrun.voyager_tavily import TavilyProvider
from unittest.mock import patch

def query():
    return {'term':'research','limit':2,'timeout_seconds':30}

def usage():
    return {'account':{'current_plan':'Researcher','plan_usage':0,'plan_limit':1,'paygo_usage':0,'paygo_limit':0},'key':{'usage':0,'limit':1}}

def test_missing_key_never_calls_network(tmp_path):
    with patch('urllib.request.urlopen') as request:
        assert TavilyProvider(tmp_path,key='').search(query())=={'error_code':'unconfigured'}
        request.assert_not_called()

def test_basic_search_local_budget_survives_restart(tmp_path):
    response={'usage':{'credits':1},'results':[{'title':'Research','url':'https://example.org','content':'Metadata snippet'}]}
    with patch.object(TavilyProvider,'_request',side_effect=[usage(),response]) as request:
        result=TavilyProvider(tmp_path,key='secret').search(query())
        assert result['candidates'][0]['source_type']=='search_metadata'
        payload=request.call_args.args[2]
        assert payload['search_depth']=='basic' and payload['auto_parameters'] is False
        assert 'secret' not in str(result)
    with patch.object(TavilyProvider,'_request',return_value=usage()) as request:
        assert TavilyProvider(tmp_path,key='secret').search(query())=={'error_code':'budget_unknown'}
        assert request.call_count==1

def test_unknown_call_cannot_replay_free(tmp_path):
    with patch.object(TavilyProvider,'_request',side_effect=[usage(),TimeoutError()]):
        assert TavilyProvider(tmp_path,key='secret').search(query())=={'error_code':'timeout'}
    with patch.object(TavilyProvider,'_request',return_value=usage()) as request:
        assert TavilyProvider(tmp_path,key='secret').search(query())=={'error_code':'budget_unknown'}
        assert request.call_count==1

def test_redirect_handler_never_creates_off_origin_request():
    import pytest
    import urllib.request
    import urllib.error
    from hostrun.voyager_tavily import RejectRedirects
    request=urllib.request.Request('https://api.tavily.com/usage',headers={'Authorization':'Bearer secret'})
    with pytest.raises(urllib.error.URLError):
        RejectRedirects().redirect_request(request,None,302,'redirect',{},'https://evil.example/collect')

def test_corrupt_negative_reservation_fails_closed(tmp_path):
    (tmp_path/'tavily-budget.json').write_text('{"reserved":-1,"ceiling":100}')
    with patch.object(TavilyProvider,'_request',return_value=usage()) as request:
        assert TavilyProvider(tmp_path,key='secret').search(query())=={'error_code':'budget_unknown'}
        assert request.call_count==1

def test_rejected_tavily_key_leaves_other_source_usable(tmp_path):
    import io
    from urllib.error import HTTPError
    from hostrun.voyager_service import VoyagerService

    class EmptyCatalog:
        def search(self, actual_query):
            return dict(candidates=[], receipt='empty-catalog', next_cursor=None, exhausted=True)

    service = VoyagerService(tmp_path, token='local', providers={
        'tavily': TavilyProvider(tmp_path, key='fixture-key'), 'catalog': EmptyCatalog(),
    })
    planned = service.create(dict(schema_version=1, operation_id='plan',
        scope=dict(text='photometry', sources=['tavily', 'catalog']),
        window=dict(mode='incremental', basis='discovered', from_at=1, until_at=2,
                    overlap_seconds=0)), token='local')
    denied = HTTPError('https://api.tavily.com/usage', 401, 'fixture-key', {}, io.BytesIO())
    with patch.object(TavilyProvider, '_request', side_effect=denied) as request:
        view = service.execute(dict(schema_version=1, operation_id='run',
            request_id=planned['request_id'], expected_version=1), token='local')
    assert request.call_count == 1
    assert view['status'] == 'partial'
    assert view['gaps'][0]['reason'] == 'authentication'
    assert view['queries'][1]['status'] == 'complete'
    assert 'fixture-key' not in str(service.get(planned['request_id'], token='local'))
