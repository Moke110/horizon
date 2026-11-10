"""Local, allowlisted diagnostics. Never copies configuration or conversation files."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sqlite3
import hashlib
from collections import Counter
from contextlib import closing
import threading
import uuid

_LOCK=threading.Lock()
LIMIT=256*1024
_FIELDS={'task_id','turn_id','status','category','attempt','attempts','delayMs','code','pid','parent_pid','exit_code','version','component','child_id','session_id'}
_EVENTS={'desktop_started','desktop_exit','task_started','task_finished','retry','error','upgrade_started','backup_created','schema_migration','result','diagnostics_exported','recovery_started','recovery_completed','child_started','child_finished','child_reused','task_transition'}

def _safe(values):
    return {key:value for key,value in values.items() if key in _FIELDS and isinstance(value,(int,bool,str)) and (not isinstance(value,str) or (len(value)<=100 and all(c.isalnum() or c in '-_.' for c in value)))}

def record_event(home,event,**metadata):
    if event not in _EVENTS: return
    try:
        row={'schema_version':1,'time':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'event':event,**_safe(metadata)}
        with _LOCK:
            from hostrun.managed_diagnostics import append_managed_event
            if append_managed_event(home, (json.dumps(row)+'\n').encode('utf-8'), limit=LIMIT):
                return
            folder=Path(home)/'diagnostics';folder.mkdir(parents=True,exist_ok=True)
            path=folder/'events.jsonl'
            if path.exists() and path.stat().st_size>=LIMIT:
                path.replace(folder/'events.previous.jsonl')
            with path.open('a',encoding='utf-8') as stream: stream.write(json.dumps(row)+'\n')
    except (OSError, ValueError, RuntimeError):
        pass  # Diagnostics must never prevent application recovery.

def export_report(home,output,*,code=None,tasks=()):
    folder=Path(output)/('horizon-report-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    events=[]
    for name in ('events.previous.jsonl','events.jsonl'):
        try:
            with (Path(home)/'diagnostics'/name).open(encoding='utf-8') as stream:
                for line in stream.read(LIMIT+4096).splitlines():
                    try: row=json.loads(line)
                    except ValueError: continue
                    if not isinstance(row,dict) or not isinstance(row.get('event'),str) or row['event'] not in _EVENTS: continue
                    timestamp=row.get('time','')
                    try: datetime.fromisoformat(timestamp.replace('Z','+00:00'))
                    except (ValueError,TypeError,AttributeError): continue
                    events.append({'schema_version':1,'time':timestamp,'event':row['event'],**_safe(row)})
        except OSError: pass
    (folder/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events),encoding='utf-8')
    (folder/'system.json').write_text(json.dumps({'schema_version':1,'os':platform.system(),'release':platform.release(),'version':platform.version(),'arch':platform.machine(),'python':platform.python_version()},indent=2),encoding='utf-8')
    snapshot={'schema_version':1,'available':False,'error':'missing_database','children':[],'tasks':[],'turns':[_safe(task) for task in tasks]}
    database=Path(home)/'horizon.sqlite3'
    if database.is_file():
        try:
            with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=0.25)) as db:
                snapshot.update(available=True,error=None)
                for table, destination in [('agent_tasks','tasks'),('agent_turns','turns'),('agent_children','children')]:
                    try:
                        rows=db.execute('SELECT result FROM '+table+' ORDER BY rowid DESC LIMIT 100').fetchall()
                        snapshot[destination]=[]
                        for row in rows:
                            source=json.loads(row[0]); safe=_safe(source)
                            if isinstance(source.get('failure'),dict): safe['failure']=_safe(source['failure'])
                            if isinstance(source.get('messages'),list): safe['message_count']=len(source['messages'])
                            snapshot[destination].append(safe)
                    except (sqlite3.Error,ValueError,TypeError,AttributeError) as exc:
                        snapshot.update(available=False,error=type(exc).__name__)
                        if isinstance(exc, sqlite3.Error):
                            break  # A busy/hot/unreadable snapshot is incomplete; do not retry each table.
        except sqlite3.Error as exc: snapshot.update(available=False,error=type(exc).__name__)
    (folder/'tasks.json').write_text(json.dumps(snapshot,indent=2),encoding='utf-8')
    build={'schema_version':1,'components':{}}
    if code:
        for component in ('agent','desktop'):
            try:
                package=json.loads((Path(code)/component/'package.json').read_text(encoding='utf-8'))
                version=package.get('version','unknown')
                if isinstance(version,str) and len(version)<100: build['components'][component]=version
            except (OSError,ValueError,TypeError,AttributeError): pass
        for name in ('manifest.json','runtime-components.json'):
            path=Path(code)/name
            try:
                raw=path.read_bytes(); document=json.loads(raw)
                if name == 'runtime-components.json':
                    for component in document.get('components',[]):
                        if component.get('name') in ('python','node','electron','@earendil-works/pi-coding-agent') and isinstance(component.get('version'),str):
                            build['components'][component['name']]=component['version'][:100]
                build[name]={'sha256':hashlib.sha256(raw).hexdigest(),**{key:document[key] for key in ('version','built_from_commit','artifact_sha256') if isinstance(document.get(key),str) and len(document[key])<100}}
            except (OSError,ValueError,TypeError,AttributeError): pass
    (folder/'build.json').write_text(json.dumps(build,indent=2),encoding='utf-8')
    (folder/'summary.md').write_text('# Horizon local diagnostic report\n\nCopy this entire folder for review. No automatic upload.\n\nContains bounded event metadata, OS/component versions and task states. Configuration, credentials, conversation text, database and Pi transcripts are excluded. Missing events may mean logging was unavailable.\n',encoding='utf-8')
    with (folder/'summary.md').open('a',encoding='utf-8') as summary:
        summary.write('\nDatabase snapshot: '+('available' if snapshot['available'] else 'unavailable ('+str(snapshot['error'])+')')+'\n')
        summary.write('\nEvents: '+str(len(events))+'\n\nTime range: '+(events[0]['time']+' — '+events[-1]['time'] if events else 'unavailable')+'\n\nTask states: '+json.dumps(dict(Counter(t.get('status','unknown') for t in snapshot['turns'])))+'\n\nError categories: '+json.dumps(dict(Counter(e.get('category','unknown') for e in events if e['event']=='error')))+'\n')
    return folder

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--code');parser.add_argument('--output',required=True);parser.add_argument('--home')
    args=parser.parse_args()
    from hostrun.agent_service import desktop_home
    print(export_report(Path(args.home) if args.home else desktop_home(),args.output,code=args.code))

if __name__=='__main__': main()
