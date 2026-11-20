from hostrun.voyager_subscriptions import Subscriptions

def test_feed_refresh_persists_version_and_detects_no_change(tmp_path):
    xml=b'<rss version="2.0"><channel><title>News</title><item><guid>a</guid><title>Solar Update</title><link>https://example.com/news/a</link></item></channel></rss>'
    responses=iter([(200,{'ETag':'"1"'},xml),(304,{},b''),(200,{'ETag':'"2"'},xml.replace(b'Solar Update',b'Solar Update Revised'))])
    sent=[]
    def fetch(url,headers):
        sent.append(headers)
        return next(responses)
    service=Subscriptions(tmp_path,fetch=fetch)
    sub=service.configure('feed','https://example.com/news/rss.xml')
    assert service.refresh(sub['id'])['changes'][0]['version']==1
    assert Subscriptions(tmp_path,fetch=fetch).refresh(sub['id'])['changes']==[]
    assert sent[1]['If-None-Match']=='"1"'
    assert service.refresh(sub['id'])['changes'][0]['version']==2

def test_section_stops_at_path_boundary_and_limits_next_pages(tmp_path):
    seen=[]
    def fetch(url, headers):
        seen.append(url)
        body=b'<a href="/news/story">Solar</a><a href="/outside">outside</a><a href="https://other.net/news/no">foreign</a><a href="?page=2" rel="next">More</a>'
        if url.endswith('=2'):
            body=b'<a href="/news/more">Moon</a><a href="/outside" rel="next">More</a>'
        return 200,{},body
    service=Subscriptions(tmp_path,fetch=fetch)
    sub=service.configure('section','https://example.com/news/')
    result=service.refresh(sub['id'])
    assert [x['title'] for x in result['changes']] == ['Solar','Moon']
    assert len(seen)==2 and result['gap'] is None

def test_malformed_feed_preserves_last_success_and_saved_candidates(tmp_path):
    import time
    from hostrun.voyager_service import VoyagerService
    bodies=iter([b'<rss version="2.0"><channel><title>Science</title><item><title>Solar irradiance</title><guid>sun</guid><link>https://example.org/solar</link></item></channel></rss>',b'<broken'])
    service=Subscriptions(tmp_path,fetch=lambda *_:(200,{},next(bodies)))
    sub=service.configure('feed','https://example.org/feed')
    first=service.refresh(sub['id'])
    owner=VoyagerService(tmp_path,token='s')
    source='sub_'+sub['id']
    plan=owner.create(dict(schema_version=1,operation_id='create',scope=dict(text='Solar',sources=[source]),window=dict(mode='incremental',basis='discovered',from_at=1,until_at=int(time.time())+60,overlap_seconds=0)),token='s')
    result=owner.execute(dict(schema_version=1,operation_id='run',request_id=plan['request_id'],expected_version=1),token='s')
    assert result['candidates'][0]['title']=='Solar irradiance'
    failed=service.refresh(sub['id'])
    assert failed['gap']=='malformed_feed' and failed['last_success']==first['last_success']
    assert owner.get(plan['request_id'],token='s')['candidates']==result['candidates']
