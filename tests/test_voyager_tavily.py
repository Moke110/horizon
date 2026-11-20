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
