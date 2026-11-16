import pytest
from hostrun.agent_service import AgentService, CommandError


def completed(home):
    s = AgentService(home, token='s')
    task = s.create_task(dict(schema_version=1, operation_id='task', title='delivery'), token='s')
    turn = s.begin_turn(dict(schema_version=1, operation_id='turn', task_id=task['task_id'], expected_version=1, text='question'), token='s')
    s.commit_turn(dict(schema_version=1, operation_id='done', turn_id=turn['turn_id'], expected_version=1, status='completed', result='fixed result'), token='s')
    return s, turn


def test_delivery_commits_outbox_before_channel_and_retains_receipt(tmp_path):
    from hostrun.agent_delivery import DeliveryService
    s, turn = completed(tmp_path)
    observer = DeliveryService(AgentService(tmp_path, token='s'))
    class Channel:
        def send(self, delivery, key):
            saved = observer.list(turn['task_id'], token='s')[0]
            assert saved['status'] == 'sending'
            assert saved['attempts'][0]['key'] == key
            return {'remote_id':'local-1'}
    d = DeliveryService(s, channel=Channel())
    out = d.enqueue(dict(schema_version=1, operation_id='enqueue', turn_id=turn['turn_id']), token='s')
    assert d.enqueue(dict(schema_version=1, operation_id='enqueue', turn_id=turn['turn_id']), token='s') == out
    sent = d.send(dict(schema_version=1, operation_id='send', delivery_id=out['delivery_id'], expected_version=out['version']), token='s')
    assert sent['status'] == 'sent'
    assert sent['receipts'] == [{'remote_id':'local-1'}]
    assert DeliveryService(AgentService(tmp_path, token='s')).list(turn['task_id'], token='s')[0] == sent


def test_unknown_never_resends_and_requires_lookup_and_risk_consent(tmp_path):
    from hostrun.agent_delivery import DeliveryService
    s, turn = completed(tmp_path)
    calls=[]
    class Channel:
        def send(self, delivery, key):
            calls.append(key)
            raise TimeoutError('remote accepted but receipt lost')
        def lookup(self, delivery, key):
            raise TimeoutError('lookup unavailable')
    d=DeliveryService(s,channel=Channel())
    out=d.enqueue(dict(schema_version=1,operation_id='enqueue',turn_id=turn['turn_id']),token='s')
    cmd=dict(schema_version=1,operation_id='send',delivery_id=out['delivery_id'],expected_version=1)
    unknown=d.send(cmd,token='s')
    assert unknown['status']=='unknown'
    d.send(cmd,token='s')
    assert len(calls)==1
    checked=d.reconcile(dict(schema_version=1,operation_id='lookup',delivery_id=out['delivery_id'],expected_version=unknown['version']),token='s')
    assert checked['status']=='unknown' and checked['checked']
    with pytest.raises(CommandError):
        d.resend(dict(schema_version=1,operation_id='no-consent',delivery_id=out['delivery_id'],expected_version=checked['version']),token='s')
    fresh=d.resend(dict(schema_version=1,operation_id='consent',delivery_id=out['delivery_id'],expected_version=checked['version'],accept_duplicate_risk=True),token='s')
    assert fresh['status']=='queued'


def test_known_failures_are_bounded_and_cancel_blocks_new_send(tmp_path):
    from hostrun.agent_delivery import DeliveryService, KnownDeliveryFailure
    s,turn=completed(tmp_path)
    calls=[]
    class Channel:
        def send(self,value,key):
            calls.append(key)
            raise KnownDeliveryFailure()
    d=DeliveryService(s,channel=Channel())
    v=d.enqueue(dict(schema_version=1,operation_id='enqueue',turn_id=turn['turn_id']),token='s')
    for i in range(3):
        v=d.send(dict(schema_version=1,operation_id=f'send{i}',delivery_id=v['delivery_id'],expected_version=v['version']),token='s')
        assert v['status']=='failed'
    with pytest.raises(CommandError):
        d.send(dict(schema_version=1,operation_id='send4',delivery_id=v['delivery_id'],expected_version=v['version']),token='s')
    assert len(calls)==3
    task=s.list_tasks(token='s')[0]
    pending=s.begin_turn(dict(schema_version=1,operation_id='pending',task_id=task['task_id'],expected_version=task['version'],text='later'),token='s')
    s.transition_turn(dict(schema_version=1,operation_id='cancel',turn_id=pending['turn_id'],expected_version=pending['version'],action='cancel'),token='s')
    with pytest.raises(CommandError):
        d.enqueue(dict(schema_version=1,operation_id='enqueue-after-cancel',turn_id=turn['turn_id']),token='s')


def test_cancel_rejects_queued_send_without_losing_prior_receipt(tmp_path):
    from hostrun.agent_delivery import DeliveryService
    s,turn=completed(tmp_path)
    calls=[]
    class Channel:
        def send(self,value,key): calls.append(key);return {'remote_id':'preserved'}
    d=DeliveryService(s,channel=Channel())
    sent=d.enqueue(dict(schema_version=1,operation_id='first',turn_id=turn['turn_id']),token='s')
    sent=d.send(dict(schema_version=1,operation_id='send-first',delivery_id=sent['delivery_id'],expected_version=1),token='s')
    queued=d.enqueue(dict(schema_version=1,operation_id='second',turn_id=turn['turn_id']),token='s')
    task=s.list_tasks(token='s')[0]
    pending=s.begin_turn(dict(schema_version=1,operation_id='pending',task_id=task['task_id'],expected_version=task['version'],text='later'),token='s')
    s.transition_turn(dict(schema_version=1,operation_id='cancel',turn_id=pending['turn_id'],expected_version=1,action='cancel'),token='s')
    with pytest.raises(CommandError,match='Cancelled task'):
        d.send(dict(schema_version=1,operation_id='send-second',delivery_id=queued['delivery_id'],expected_version=1),token='s')
    saved=DeliveryService(AgentService(tmp_path,token='s')).list(turn['task_id'],token='s')
    assert len(calls)==1
    assert saved[0]['receipts']==[{'remote_id':'preserved'}] and saved[0]['status']=='sent'
    assert saved[1]['attempts']==[] and saved[1]['status']=='queued'


def test_controlled_http_remote_acceptance_lost_receipt_reconciles_without_resend(tmp_path):
    import json, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from hostrun.agent_delivery import DeliveryService
    remote={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            remote[data['key']]={'remote_id':'controlled-1'}
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps(remote[data['key']]).encode())
        def do_GET(self):
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps(remote.get(self.path[1:])).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}'
    class Channel:
        def send(self,value,key):
            with urlopen(Request(url,data=json.dumps({'key':key,'text':value['text']}).encode()),timeout=3) as r: json.load(r)
            raise SystemExit('process ended after remote accepted')
        def lookup(self,value,key):
            with urlopen(url+'/'+key,timeout=3) as r: return json.load(r)
    try:
        s,turn=completed(tmp_path);d=DeliveryService(s,channel=Channel())
        value=d.enqueue(dict(schema_version=1,operation_id='enqueue',turn_id=turn['turn_id']),token='s')
        cmd=dict(schema_version=1,operation_id='send',delivery_id=value['delivery_id'],expected_version=1)
        with pytest.raises(SystemExit): d.send(cmd,token='s')
        restarted=DeliveryService(AgentService(tmp_path,token='s'),channel=Channel())
        uncertain=restarted.send(cmd,token='s')
        assert uncertain['status']=='sending' and len(remote)==1
        check=dict(schema_version=1,operation_id='lookup',delivery_id=value['delivery_id'],expected_version=uncertain['version'])
        sent=restarted.reconcile(check,token='s')
        assert sent['status']=='sent' and sent['receipts']==[{'remote_id':'controlled-1'}]
        assert restarted.reconcile(check,token='s')==sent
        assert len(remote)==1
    finally:
        server.shutdown();server.server_close();thread.join()


def test_active_send_cannot_be_overtaken_by_reconcile(tmp_path):
    import threading,time
    from hostrun.agent_delivery import DeliveryService
    s,turn=completed(tmp_path)
    entered=threading.Event();release=threading.Event();results=[]
    class Channel:
        def send(self,value,key): entered.set();release.wait(3);return {'remote_id':'one'}
        def lookup(self,value,key): return None
    d=DeliveryService(s,channel=Channel())
    observer=DeliveryService(s,channel=Channel())
    value=d.enqueue(dict(schema_version=1,operation_id='enqueue',turn_id=turn['turn_id']),token='s')
    worker=threading.Thread(target=lambda:results.append(d.send(dict(schema_version=1,operation_id='send',delivery_id=value['delivery_id'],expected_version=1),token='s')))
    worker.start();assert entered.wait(3)
    sending=observer.list(turn['task_id'],token='s')[0]
    def lookup():
        try: observer.reconcile(dict(schema_version=1,operation_id='lookup',delivery_id=value['delivery_id'],expected_version=sending['version']),token='s')
        except CommandError as e: results.append(e.code)
    checker=threading.Thread(target=lookup);checker.start();time.sleep(.1)
    release.set();worker.join(5);checker.join(5)
    assert not worker.is_alive() and not checker.is_alive()
    assert results[0]['status']=='sent'
    assert results[1]=='version_conflict'
    assert observer.list(turn['task_id'],token='s')[0]['receipts']==[{'remote_id':'one'}]


@pytest.mark.parametrize('boundary',['before_send','after_accept'])
def test_native_exit_preserves_uncertainty_without_automatic_resend(tmp_path,boundary):
    import os,subprocess,sys,json
    from pathlib import Path
    from hostrun.agent_delivery import DeliveryService
    s,turn=completed(tmp_path);d=DeliveryService(s)
    value=d.enqueue(dict(schema_version=1,operation_id='enqueue',turn_id=turn['turn_id']),token='s')
    script='''
import os,sys,json
from pathlib import Path
from hostrun.agent_service import AgentService
from hostrun.agent_delivery import DeliveryService
home=Path(sys.argv[1])
class Channel:
 def send(self,value,key):
  if sys.argv[3]=='after_accept': (home/'controlled-remote.json').write_text(json.dumps({'remote_id':'accepted','key':key}))
  os._exit(71)
d=DeliveryService(AgentService(home,token='s'),channel=Channel())
d.send(dict(schema_version=1,operation_id='native-send',delivery_id=sys.argv[2],expected_version=1),token='s')
'''
    result=subprocess.run([sys.executable,'-X','utf8','-c',script,str(tmp_path),value['delivery_id'],boundary],cwd=Path(__file__).resolve().parents[1],capture_output=True,timeout=15)
    assert result.returncode==71,result.stderr
    class Channel:
        def send(self,*args): pytest.fail('must not resend')
        def lookup(self,value,key):
            path=tmp_path/'controlled-remote.json'
            return {'remote_id':'accepted'} if path.exists() else None
    restarted=DeliveryService(AgentService(tmp_path,token='s'),channel=Channel())
    sending=restarted.send(dict(schema_version=1,operation_id='native-send',delivery_id=value['delivery_id'],expected_version=1),token='s')
    assert sending['status']=='sending' and len(sending['attempts'])==1
    checked=restarted.reconcile(dict(schema_version=1,operation_id='lookup',delivery_id=value['delivery_id'],expected_version=sending['version']),token='s')
    assert checked['status']==('sent' if boundary=='after_accept' else 'unknown')
