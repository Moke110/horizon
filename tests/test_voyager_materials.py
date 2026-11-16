import hashlib
import pytest
from hostrun.agent_service import AgentService


def service(home, **kwargs):
    from hostrun.voyager_materials import MaterialsService
    return MaterialsService(home,token='s',**kwargs)


def test_offline_paste_preserves_original_source_and_snapshot_across_restart(tmp_path):
    text='原始资料\n<script>never execute</script>\n末尾\n'
    command=dict(schema_version=1,operation_id='paste',title='手工资料',text=text,source_label='用户粘贴')
    materials=service(tmp_path)
    result=materials.register_text(command,token='s')
    package=service(tmp_path).get_package(result['material_id'],token='s')
    assert package['original_text']==text
    assert package['source']['kind']=='user_input'
    assert package['source']['label']=='用户粘贴'
    assert package['snapshot']['content_hash']==hashlib.sha256(text.encode()).hexdigest()
    assert package['snapshot']['extraction_version']=='pasted_text.v1'
    assert package['summary'] is None
    assert package['source']['received_at'].endswith('Z')
    assert materials.register_text(command,token='s')==result
    another=materials.register_text({**command,'operation_id':'paste-again','source_label':'独立来源语义'},token='s')
    assert another['material_id']!=result['material_id']
    assert another['snapshot']['content_hash']==result['snapshot']['content_hash']


@pytest.mark.parametrize('boundary',['before_publish','after_publish','before_commit','after_commit'])
def test_publication_faults_never_leave_partial_references_and_replay_recovers(tmp_path,boundary):
    def crash(phase):
        if phase==boundary:raise RuntimeError('controlled interruption')
    command=dict(schema_version=1,operation_id='paste',title='source',text='exact original',source_label='offline')
    with pytest.raises(RuntimeError):service(tmp_path,fault_observer=crash).register_text(command,token='s')
    restarted=service(tmp_path)
    saved=restarted.list_materials(token='s')
    assert len(saved)==(1 if boundary=='after_commit' else 0)
    for material in saved:assert restarted.get_package(material['material_id'],token='s')['original_text']=='exact original'
    result=restarted.register_text(command,token='s')
    assert len(restarted.list_materials(token='s'))==1
    assert restarted.get_package(result['material_id'],token='s')['original_text']=='exact original'


def test_replay_conflict_authorization_and_snapshot_tampering_are_refused(tmp_path):
    from hostrun.agent_service import CommandError
    s=service(tmp_path)
    c=dict(schema_version=1,operation_id='paste',title='source',text='original',source_label='offline')
    value=s.register_text(c,token='s')
    with pytest.raises(PermissionError):s.get_package(value['material_id'],token='wrong')
    with pytest.raises(CommandError):s.register_text({**c,'text':'changed'},token='s')
    blob=next((tmp_path/'snapshots/voyager-text').glob('*.txt'));blob.write_text('tampered')
    with pytest.raises(CommandError,match='Snapshot changed'):s.get_package(value['material_id'],token='s')


def test_concurrent_same_operation_and_distinct_sources_share_content(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    s=service(tmp_path)
    c=dict(schema_version=1,operation_id='paste',title='source',text='same original',source_label='offline')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:s.register_text(c,token='s'),range(4)))
    assert len({r['material_id'] for r in results})==1
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda n:s.register_text({**c,'operation_id':str(n),'source_label':'source '+str(n)},token='s'),range(4)))
    assert len({r['material_id'] for r in results})==4
    assert len({r['snapshot']['content_hash'] for r in results})==1
    assert len(list((tmp_path/'snapshots/voyager-text').glob('*.txt')))==1


def test_managed_material_snapshot_is_identity_owned_and_cleanup_preserves_original(tmp_path,monkeypatch):
    import os
    if os.name!='nt':pytest.skip('native managed identity boundary')
    from hostrun.cleanup import prepare_instance,initialize_layout,cleanup_instance
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path))
    control=tmp_path/'Horizon/test-instances';control.mkdir(parents=True)
    instance=prepare_instance(control);initialize_layout(control,instance['instance_id'])
    AgentService(instance['home'],token='s')
    s=service(instance['home'])
    value=s.register_text(dict(schema_version=1,operation_id='paste',title='source',text='private original',source_label='offline'),token='s')
    assert s.get_package(value['material_id'],token='s')['original_text']=='private original'
    blobs=list((instance['archive']/'originals/voyager-text').glob('*.txt'))
    assert len(blobs)==1
    kept=cleanup_instance(control,instance['instance_id'],tmp_path/'kept',mode='keep-data',confirmation=instance['instance_id'])
    assert kept['status']=='uninstalled' and blobs[0].exists()
    assert service(instance['home']).get_package(value['material_id'],token='s')['original_text']=='private original'
    cleaned=cleanup_instance(control,instance['instance_id'],tmp_path/'cleaned',confirmation=instance['instance_id'])
    assert cleaned['status']=='cleaned' and not blobs[0].exists()


def test_unknown_same_name_and_missing_snapshot_are_refused(tmp_path):
    from hostrun.agent_service import CommandError
    text='known original';digest=hashlib.sha256(text.encode()).hexdigest()
    folder=tmp_path/'snapshots/voyager-text';folder.mkdir(parents=True)
    blob=folder/(digest+'.txt');blob.write_text('unknown foreign data')
    command=dict(schema_version=1,operation_id='paste',title='source',text=text,source_label='offline')
    with pytest.raises(CommandError):service(tmp_path).register_text(command,token='s')
    assert blob.read_text()=='unknown foreign data'
    blob.unlink()
    value=service(tmp_path).register_text(command,token='s');blob.unlink()
    with pytest.raises(CommandError,match='Missing snapshot'):service(tmp_path).get_package(value['material_id'],token='s')


def test_concurrent_foreign_snapshot_publication_is_not_overwritten(tmp_path):
    from hostrun.agent_service import CommandError
    text='original';digest=hashlib.sha256(text.encode()).hexdigest()
    target=tmp_path/'snapshots/voyager-text'/(digest+'.txt')
    def insert(phase):
        if phase=='before_publish':target.write_text('foreign concurrent bytes')
    with pytest.raises(CommandError):
        service(tmp_path,fault_observer=insert).register_text(dict(schema_version=1,operation_id='paste',title='source',text=text,source_label='offline'),token='s')
    assert target.read_text()=='foreign concurrent bytes'
    assert service(tmp_path).list_materials(token='s')==[]


@pytest.mark.parametrize('kind',['hardlink','symlink','dangling'])
def test_linked_snapshot_names_are_never_adopted_or_replaced(tmp_path,kind):
    from hostrun.agent_service import CommandError
    text='original';digest=hashlib.sha256(text.encode()).hexdigest()
    folder=tmp_path/'snapshots/voyager-text';folder.mkdir(parents=True)
    external=tmp_path/'external.txt'
    if kind!='dangling':external.write_text(text)
    blob=folder/(digest+'.txt')
    try:
        if kind=='hardlink':blob.hardlink_to(external)
        else:blob.symlink_to(external)
    except OSError as error:
        if getattr(error,'winerror',None)==1314:pytest.skip('native symlink privilege unavailable')
        raise
    with pytest.raises(CommandError):service(tmp_path).register_text(dict(schema_version=1,operation_id='paste',title='source',text=text,source_label='offline'),token='s')
    if kind=='hardlink':assert blob.stat().st_nlink==2
    else:assert blob.is_symlink()
    if kind!='dangling':assert external.read_text()==text


def test_real_pi_cannot_write_published_material_snapshot(tmp_path):
    import json,os,shutil,subprocess,sys,threading
    from http.server import BaseHTTPRequestHandler
    from test_embedded_agent import local_model_server,ROOT
    from hostrun import embedded_agent
    if not shutil.which('node') or not (ROOT/'agent/dist/main.js').exists():pytest.skip('requires built Pi')
    s=service(tmp_path)
    value=s.register_text(dict(schema_version=1,operation_id='paste',title='source',text='immutable original',source_label='offline'),token='s')
    path=next((tmp_path/'snapshots/voyager-text').glob('*.txt'));requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            chunks=[({'role':'assistant','content':'write rejected'},None),({},'stop')]
            if len(requests)==1:
                chunks=[({'role':'assistant','tool_calls':[{'index':0,'id':'write_snapshot','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':str(path),'content':'corruption'})}}]},None),({},'tool_calls')]
            for delta,finish in chunks:
                row={'id':'test','object':'chat.completion.chunk','created':1,'model':'test-model','choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
                self.wfile.write(('data: '+json.dumps(row)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server=local_model_server(Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        embedded_agent.configure(tmp_path,provider='horizon-test',model='test-model',api_key='local-test',base_url=f'http://127.0.0.1:{server.server_port}/v1')
        process=subprocess.run(embedded_agent.argv(tmp_path,'try controlled write',kind='push',code=ROOT),cwd=ROOT,env={**os.environ,'HORIZON_PYTHON':sys.executable},capture_output=True,text=True,encoding='utf8',timeout=30)
        assert process.returncode==0,process.stdout+process.stderr
        events=[json.loads(line) for line in process.stdout.splitlines()]
        assert any(e['type']=='tool_result' and e['name']=='write_file' and e['isError'] for e in events)
        assert any(e['type']=='final' and e['text']=='write rejected' for e in events)
        assert s.get_package(value['material_id'],token='s')['original_text']=='immutable original'
        print({'real_pi_tool':'write_file','rejected':True,'final':'write rejected','original_preserved':True})
    finally:
        server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('boundary',['before_publish','after_publish','before_commit','after_commit'])
def test_native_process_exit_does_not_publish_partial_material_reference(tmp_path,boundary):
    import subprocess,sys
    from pathlib import Path
    code='''
import os,sys
from hostrun.voyager_materials import MaterialsService
def crash(phase):
 if phase==sys.argv[2]:os._exit(73)
s=MaterialsService(sys.argv[1],token='s',fault_observer=crash)
s.register_text(dict(schema_version=1,operation_id='paste',title='source',text='original survives process death',source_label='offline'),token='s')
'''
    process=subprocess.run([sys.executable,'-B','-X','utf8','-c',code,str(tmp_path),boundary],cwd=Path(__file__).resolve().parents[1],capture_output=True,timeout=15)
    assert process.returncode==73,process.stderr
    s=service(tmp_path)
    assert len(s.list_materials(token='s'))==(1 if boundary=='after_commit' else 0)
    value=s.register_text(dict(schema_version=1,operation_id='paste',title='source',text='original survives process death',source_label='offline'),token='s')
    assert s.get_package(value['material_id'],token='s')['original_text']=='original survives process death'


def test_desktop_material_command_authenticates_and_returns_public_original(tmp_path):
    from hostrun.desktop_bridge import material_command
    s=service(tmp_path)
    c=dict(action='materials_register',schema_version=1,operation_id='paste',title='source',text='<script>literal original</script>',source_label='offline')
    with pytest.raises(PermissionError):material_command(s,c,token='wrong')
    assert s.list_materials(token='s')==[]
    package=material_command(s,c,token='s')
    reopened=material_command(service(tmp_path),dict(action='materials_open',material_id=package['material_id']),token='s')
    assert reopened==package
    assert package['original_text']=='<script>literal original</script>' and package['summary'] is None


def test_native_directory_junction_cannot_redirect_material_publication(tmp_path):
    import os,subprocess
    if os.name!='nt':pytest.skip('Windows junction boundary')
    from hostrun.agent_service import CommandError
    outside=tmp_path/'outside';outside.mkdir()
    snapshots=tmp_path/'snapshots';snapshots.mkdir()
    link=snapshots/'voyager-text'
    made=subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],capture_output=True,text=True)
    assert made.returncode==0,made.stdout+made.stderr
    with pytest.raises(CommandError,match='Linked snapshot directory'):
        service(tmp_path).register_text(dict(schema_version=1,operation_id='paste',title='source',text='original',source_label='offline'),token='s')
    assert list(outside.iterdir())==[]
