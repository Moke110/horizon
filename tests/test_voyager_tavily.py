from hostrun.voyager_tavily import TavilyProvider
from unittest.mock import patch
import pytest

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

def test_confirmed_monthly_limit_bounds_search_when_usage_returns_null_limits(tmp_path):
    unreported = usage()
    unreported['account'].update(plan_limit=1000, paygo_limit=None)
    unreported['key']['limit'] = None
    response = {'usage': {'credits': 1}, 'results': []}
    with patch.object(TavilyProvider, '_request', side_effect=[unreported, response, unreported]) as request:
        assert TavilyProvider(tmp_path, key='secret', monthly_limit=1).search(query())['exhausted']
        assert TavilyProvider(tmp_path, key='secret', monthly_limit=1).search(query()) == {'error_code': 'budget_unknown'}
        assert [call.args[0] for call in request.call_args_list] == ['usage', 'search', 'usage']

@pytest.mark.parametrize('plan,paygo_usage,paygo_limit,allowed_limit', [
    ('Researcher', 0, None, None),
    ('Researcher', 0, 100, 5),
    ('Researcher', 1, None, 5),
    ('Researcher', 0, False, 5),
    ('Bootstrap', 0, None, 5),
])
def test_manual_limit_does_not_override_unverified_plan_or_paygo(
        tmp_path, plan, paygo_usage, paygo_limit, allowed_limit):
    response = usage()
    response['account'].update(current_plan=plan, paygo_usage=paygo_usage, paygo_limit=paygo_limit)
    response['key']['limit'] = None
    with patch.object(TavilyProvider, '_request', return_value=response) as request:
        assert TavilyProvider(tmp_path, key='secret', monthly_limit=allowed_limit).search(query()) == {
            'error_code': 'budget_unknown',
        }
        assert [call.args[0] for call in request.call_args_list] == ['usage']

@pytest.mark.parametrize('section,field', [('account', 'paygo_limit'), ('key', 'limit')])
def test_manual_confirmation_only_covers_explicit_null_limits(tmp_path, section, field):
    response = usage()
    del response[section][field]
    with patch.object(TavilyProvider, '_request', return_value=response) as request:
        assert TavilyProvider(tmp_path, key='secret', monthly_limit=5).search(query()) == {
            'error_code': 'budget_unknown',
        }
        assert [call.args[0] for call in request.call_args_list] == ['usage']

def test_saved_budget_is_used_without_reentry_and_cleared_on_key_rotation(tmp_path):
    from hostrun.desktop_bridge import voyager_command
    from hostrun.voyager_service import VoyagerService

    def action(service, action_name, **values):
        return voyager_command(service, dict(action=action_name, **values), token='desktop')[1]

    service = VoyagerService(tmp_path, token='desktop')
    action(service, 'voyager_save_source_key', source='tavily', key='private-marker')
    action(service, 'voyager_save_tavily_budget', paygo_disabled=True, monthly_limit=5)
    service = VoyagerService(tmp_path, token='desktop')
    assert action(service, 'voyager_source_settings') == {
        'openalex': 'missing', 'tavily': 'saved', 'tavily_monthly_limit': 5,
    }
    planned = action(service, 'voyager_create', operation_id='plan', schema_version=1,
        scope=dict(text='photometry', sources=['tavily']),
        window=dict(mode='incremental', basis='discovered', from_at=1, until_at=2, overlap_seconds=0))
    unreported = usage()
    unreported['account'].update(plan_limit=1000, paygo_limit=None)
    unreported['key']['limit'] = None
    with patch.object(TavilyProvider, '_request', side_effect=[unreported, {'usage': {'credits': 1}, 'results': []}]) as request:
        result = action(service, 'voyager_execute', operation_id='run', schema_version=1,
                        request_id=planned['request_id'], expected_version=1)
    assert result['status'] == 'complete'
    assert [call.args[0] for call in request.call_args_list] == ['usage', 'search']
    assert action(service, 'voyager_save_source_key', source='tavily', key='replacement') == {
        'openalex': 'missing', 'tavily': 'saved',
    }

def test_redirect_handler_never_creates_off_origin_request():
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
