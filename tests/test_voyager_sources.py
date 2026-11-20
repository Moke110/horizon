from hostrun.voyager_sources import EuropePMCProvider

def test_corrections_are_returned_with_pagination():
    provider = EuropePMCProvider()
    page = provider.decode(b'{"hitCount":2,"nextCursorMark":"next","resultList":{"result":[{"id":"12","source":"MED","title":"Correction: evidence","firstPublicationDate":"2026-01-01"}]}}', {'limit':1,'cursor':None})
    assert page['candidates'][0]['source_id']=='MED:12'
    assert page['next_cursor']=='next'
    assert not page['exhausted']
import urllib.error
from hostrun.voyager_sources import ArxivProvider

def test_arxiv_old_identifier_and_error_feed():
    query={'limit':2,'cursor':None}
    body=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/hep-th/9901001v1</id><title>Evidence</title><published>1999-01-01T00:00:00Z</published></entry></feed>'
    assert ArxivProvider().decode(body,query)['candidates'][0]['source_id']=='hep-th/9901001v1'
    assert ArxivProvider().decode(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#bad_query</id><title>Error</title></entry></feed>',query)=={'error_code':'provider_failed'}
import io
import pytest
from unittest.mock import patch

@pytest.mark.parametrize('provider_type',[ArxivProvider,EuropePMCProvider])
def test_retry_after_prevents_followup_network_call(provider_type,tmp_path):
    provider_type._next=0
    provider=provider_type(rate_directory=tmp_path)
    query={'term':'evidence','must_include':[],'exclude':[],'window':{'basis':'discovered'},'limit':1,'cursor':None,'timeout_seconds':1}
    error=urllib.error.HTTPError('https://example.test',429,'limited',{'Retry-After':'120'},io.BytesIO())
    with patch('urllib.request.urlopen',side_effect=error) as opening:
        assert provider.search(query)=={'error_code':'rate_limited'}
        assert provider.search(query)=={'error_code':'rate_limited'}
        assert opening.call_count==1
    provider_type._next=0

@pytest.mark.parametrize('provider_type',[ArxivProvider,EuropePMCProvider])
def test_transport_timeout_is_not_empty_success(provider_type,tmp_path):
    provider_type._next=0
    query={'term':'evidence','must_include':[],'exclude':[],'window':{'basis':'discovered'},'limit':1,'cursor':None,'timeout_seconds':1}
    with patch('urllib.request.urlopen',side_effect=TimeoutError()):
        assert provider_type(rate_directory=tmp_path).search(query)=={'error_code':'timeout'}
    provider_type._next=0
@pytest.mark.parametrize('provider,body',[(ArxivProvider(),b'<html/>'),(EuropePMCProvider(),b'{"resultList":{}}'),(EuropePMCProvider(),b'{"resultList":{"result":[{"id":"1"}]}}')])
def test_malformed_source_page_is_failure(provider,body):
    assert provider.decode(body,{'limit':1,'cursor':None})=={'error_code':'provider_failed'}

def test_success_records_exact_source_request(tmp_path):
    EuropePMCProvider._next=0
    query={'term':'evidence','must_include':['trial'],'exclude':['advert'],'window':{'basis':'discovered'},'limit':1,'cursor':None,'timeout_seconds':1}
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return b'{"resultList":{"result":[]}}'
    with patch('urllib.request.urlopen',return_value=Response()) as opening:
        result=EuropePMCProvider(rate_directory=tmp_path).search(query)
        assert result['source_query']==opening.call_args.args[0].full_url
        assert 'trial' in result['source_query'] and 'advert' in result['source_query']
    EuropePMCProvider._next=0


def test_offset_timestamp_preserves_instant():
    from hostrun.voyager_sources import timestamp
    assert timestamp('2026-01-01T08:00:00+08:00')==timestamp('2026-01-01T00:00:00Z')


def test_separate_instances_share_rate_limit(tmp_path):
    query={'term':'evidence','must_include':[],'exclude':[],'window':{'basis':'discovered'},'limit':1,'cursor':None,'timeout_seconds':1}
    ArxivProvider._next=0
    with patch('urllib.request.urlopen',side_effect=TimeoutError()) as opening:
        assert ArxivProvider(rate_directory=tmp_path).search(query)=={'error_code':'timeout'}
        assert ArxivProvider(rate_directory=tmp_path).search(query)=={'error_code':'rate_limited'}
        assert opening.call_count==1
    ArxivProvider._next=0
