import json
from hostrun.diagnostics import record_event, export_report

def test_export_is_allowlisted_and_without_configuration(tmp_path):
    home=tmp_path/'home'
    record_event(home,'task_started',task_id='task-1',message='secret',api_key='secret')
    report=export_report(home,tmp_path/'reports',tasks=[{'task_id':'task-1','status':'running','text':'private conversation'}])
    assert (report/'summary.md').exists()
    combined=''.join(p.read_text() for p in report.iterdir())
    assert 'secret' not in combined and 'private conversation' not in combined
    event=json.loads((report/'events.jsonl').read_text())
    assert event['schema_version']==1 and event['task_id']=='task-1'
    assert event['time'].endswith('Z')

def test_rotates_and_exports_only_bounded_metadata(tmp_path,monkeypatch):
    import hostrun.diagnostics as diagnostics
    monkeypatch.setattr(diagnostics,'LIMIT',300)
    for i in range(20): record_event(tmp_path,'retry',attempt=i,category='transient',message='private')
    files=list((tmp_path/'diagnostics').iterdir())
    assert len(files)==2 and all(p.stat().st_size<500 for p in files)
    report=export_report(tmp_path,tmp_path/'out')
    assert len((report/'events.jsonl').read_text().splitlines())<20


def test_cli_exports_without_model_or_database(tmp_path):
    import subprocess,sys
    result=subprocess.run([sys.executable,'-X','utf8','-m','hostrun.diagnostics','--home',str(tmp_path/'empty'),'--output',str(tmp_path/'report')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    from pathlib import Path
    assert (Path(result.stdout.strip())/'summary.md').is_file()
    assert not (tmp_path/'empty'/'horizon.sqlite3').exists()

def test_offline_export_reads_states_without_private_text(tmp_path):
    from hostrun.agent_service import AgentService
    service=AgentService(tmp_path/'home',token='secret')
    task=service.create_task({'schema_version':1,'operation_id':'create','title':'private title'},token='secret')
    service.begin_turn({'schema_version':1,'operation_id':'turn','task_id':task['task_id'],'expected_version':1,'text':'private question'},token='secret')
    report=export_report(service.home,tmp_path/'reports')
    snapshot=json.loads((report/'tasks.json').read_text())
    assert snapshot['schema_version']==1
    assert snapshot['tasks'][0]['task_id']==task['task_id']
    assert snapshot['turns'][0]['status']=='running'
    assert 'private' not in (report/'tasks.json').read_text()

def test_missing_and_corrupt_database_are_not_reported_as_empty(tmp_path):
    report=export_report(tmp_path,tmp_path/'out')
    snapshot=json.loads((report/'tasks.json').read_text())
    assert snapshot['available'] is False and snapshot['error']=='missing_database'
    (tmp_path/'horizon.sqlite3').write_bytes(b'broken')
    report=export_report(tmp_path,tmp_path/'out')
    snapshot=json.loads((report/'tasks.json').read_text())
    assert snapshot['available'] is False and snapshot['error']=='DatabaseError'
    assert 'unavailable' in (report/'summary.md').read_text()

def test_export_releases_database_handle(tmp_path):
    from hostrun.agent_service import AgentService
    service=AgentService(tmp_path/'home',token='secret')
    export_report(service.home,tmp_path/'out')
    database=service.home/'horizon.sqlite3'
    database.rename(service.home/'renamed.sqlite3')
    assert not database.exists()
