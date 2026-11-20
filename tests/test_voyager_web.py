from hostrun.voyager_materials import MaterialsService


def test_web_snapshot_replay_uses_body_and_stable_paragraph_anchors(tmp_path,monkeypatch):
    import hostrun.voyager_web as web
    text='This observatory measures solar irradiance with calibrated instruments over decades. '
    body=('<html><head><title>Observatory report</title><meta name="description" content="A snippet only"></head><body><article><h1>Observatory report</h1><p>'+text*6+'</p><p>Second measurement. '+text*4+'</p></article></body></html>').encode()
    calls=[]
    def fetch(url,headers):
        calls.append(url)
        return 200,{'Content-Type':'text/html; charset=utf-8'},body
    monkeypatch.setattr(web,'fetch_public',fetch)
    service=MaterialsService(tmp_path,token='test')
    command=dict(schema_version=1,operation_id='web',url='https://example.org/observatory')
    result=service.register_web(command,token='test')
    package=MaterialsService(tmp_path,token='test').get_package(result['material_id'],token='test')
    assert package['web']['status']=='available'
    assert text.strip() in package['original_text'] and 'A snippet only' not in package['original_text']
    assert len(package['web']['anchors'])>=2
    for anchor in package['web']['anchors']:
        assert package['original_text'][anchor['start']:anchor['end']].strip()
    assert service.register_web(command,token='test')==result and calls==[command['url']]

def test_paywall_and_denied_status_preserve_link_without_article_or_renderer(tmp_path,monkeypatch):
    import hostrun.voyager_web as web
    svc=MaterialsService(tmp_path,token='s')
    for i,(status,body,reason) in enumerate([(403,b'preview','access_denied'),(200,b'<html><script type="application/ld+json">{"isAccessibleForFree":false}</script><article>Subscriber access</article></html>','restricted_page')]):
        monkeypatch.setattr(web,'fetch_public',lambda url,headers:(status,{'content-type':'text/html'},body))
        result=svc.register_web(dict(schema_version=1,operation_id=str(i),url='https://example.org/paywall',dynamic=True),token='s')
        assert result['web']['reason']==reason
        assert result['source']['label']=='https://example.org/paywall'
        assert svc.get_package(result['material_id'],token='s')['original_text']==''


def test_explicit_dynamic_request_extracts_rendered_text_from_shell(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import sys
    import hostrun.voyager_web as web
    shell=b'<html><title>Demo</title><body><div id="root">Please enable JavaScript to continue.</div></body></html>'
    text='The solar observatory measures irradiance each hour. '*12
    full=('<html><title>Solar</title><article><p>'+text+'</p></article></html>').encode()
    monkeypatch.setattr(web,'fetch_public',lambda *_:(200,{'content-type':'text/html'},shell))
    monkeypatch.setitem(sys.modules,'hostrun.voyager_web_renderer',SimpleNamespace(render_public=lambda url,body,cancel=None:full))
    svc=MaterialsService(tmp_path,token='s')
    r=svc.register_web(dict(schema_version=1,operation_id='dynamic',url='https://example.org/spa',dynamic=True),token='s')
    assert r['web']['mode']=='dynamic'
    assert text.strip() in svc.get_package(r['material_id'],token='s')['original_text']

def test_invalid_web_commands_fail_before_network(tmp_path,monkeypatch):
    import pytest
    import hostrun.voyager_web as web
    from hostrun.agent_service import CommandError
    service=MaterialsService(tmp_path,token='launch')
    monkeypatch.setattr(web,'fetch_public',lambda *_:pytest.fail('unexpected outbound request'))
    base=dict(schema_version=1,operation_id='capture',url='https://example.org/')
    with pytest.raises(PermissionError):
        service.register_web(base,token='wrong')
    for url in ['file:///tmp/page','https://user:secret@example.org/test','https://example.org:8080/','https://example.org/page?api_key=fixture']:
        with pytest.raises(CommandError):
            service.register_web(dict(base,url=url),token='launch')

def test_cancelled_web_capture_does_not_publish_a_late_material(tmp_path,monkeypatch):
    import threading
    import pytest
    from hostrun.agent_service import CommandError
    import hostrun.voyager_web as web
    cancelled=threading.Event()
    cancelled.set()
    monkeypatch.setattr(web,'fetch_public',lambda *_:pytest.fail('Cancelled operation fetched a page'))
    svc=MaterialsService(tmp_path,token='s')
    with pytest.raises(CommandError,match='cancelled'):
        svc.register_web(dict(schema_version=1,operation_id='cancel',url='https://example.org/news'),token='s',cancel=cancelled)
    assert svc.list_materials(token='s')==[]

def test_explicit_dynamic_choice_captures_article_even_when_shell_has_navigation_text(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    import hostrun.voyager_web as web
    shell='<html><title>Archive</title><article><p>'+'Archive navigation and help information. '*20+'</p></article></html>'
    actual='<html><title>Observation report</title><article><p>'+'Actual hourly observations from our new report. '*20+'</p></article></html>'
    monkeypatch.setattr(web,'fetch_public',lambda *_:(200,{'content-type':'text/html'},shell.encode()))
    monkeypatch.setitem(sys.modules,'hostrun.voyager_web_renderer',SimpleNamespace(render_public=lambda *args,**kwargs:actual.encode()))
    svc=MaterialsService(tmp_path,token='s')
    result=svc.register_web(dict(schema_version=1,operation_id='explicit',url='https://example.org/app',dynamic=True),token='s')
    assert result['web']['mode']=='dynamic'
    assert 'Actual hourly observations' in svc.get_package(result['material_id'],token='s')['original_text']

def test_redirect_limit_never_persists_unchecked_location(tmp_path,monkeypatch):
    import json
    import hostrun.voyager_web as web
    calls=[]
    def redirect(url,headers):
        calls.append(url)
        target='/step/'+str(len(calls)) if len(calls)<5 else '/private?api_key=private-marker'
        return 302,{'Location':target},b''
    monkeypatch.setattr(web,'fetch_public',redirect)
    svc=MaterialsService(tmp_path,token='s')
    result=svc.register_web(dict(schema_version=1,operation_id='chain',url='https://example.org/'),token='s')
    assert result['web']['status']=='unavailable' and 'private-marker' not in json.dumps(result)
    assert len(calls)<=5


def test_material_list_projection_omits_hydrated_web_content(tmp_path,monkeypatch):
    import hostrun.voyager_web as web
    html=('<html><head><title>Solar</title></head><body><article><p>'+'Solar panels collect light. '*20+'</p></article></body></html>').encode()
    monkeypatch.setattr(web,'fetch_public',lambda *_:(200,{'Content-Type':'text/html'},html))
    svc=MaterialsService(tmp_path,token='s')
    result=svc.register_web(dict(schema_version=1,operation_id='list',url='https://example.org/story'),token='s')
    assert svc.list_summaries(token='s')==[dict(material_id=result['material_id'],title=result['title'],source={'label':'https://example.org/story'})]
    assert svc.get_package(result['material_id'],token='s')['web']['anchors']
