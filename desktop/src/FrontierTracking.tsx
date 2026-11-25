import {MultilineInput} from './gui/elements/FormElements';
import {Checkbox} from './gui/elements/ChoiceElements';
import {TestTag} from './TestCleanupPanel';
import React,{useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {KnowledgePanel,type Frontier,type FrontierCommandError,type FrontierSummary,type FrontierViewEvent} from './KnowledgePanel';
import type {ArchiveSource} from './ArchiveSourcesPanel';
import type {VoyagerSummary} from './VoyagerPanel';

type Progress={tags?:string[];progress_id:string;frontier_id:string;version:number;content:string;source_ids:string[]};
type Selection={kind:'create';nonce:string}|{kind:'open';nonce:string;id:string;version:number};
type Tracking={frontier_id:string;progress:Progress[];history:VoyagerSummary[]};

const statusText:Record<string,string>={following:'关注中',paused:'已暂停',resolved:'已解决',deleted:'已删除'};

export function FrontierTracking({frontiers,sources,frontier,viewEvent,error,detailHost,openDetail,
  send,openSource,openVoyager}:{
  frontiers:FrontierSummary[];
  sources:ArchiveSource[];
  frontier:Frontier|null;
  viewEvent:FrontierViewEvent|null;
  error:FrontierCommandError|null;
  detailHost:HTMLElement|null;
  openDetail:()=>void;
  send:(command:Record<string,unknown>)=>void;
  openSource:(source:ArchiveSource)=>void;
  openVoyager:(requestId:string)=>void;
}){
  const [selection,setSelection]=useState<Selection|null>(null);
  const [tracking,setTracking]=useState<Tracking|null>(null);
  const [draft,setDraft]=useState<Progress|'new'|null>(null);
  const selectedId=selection?.kind==='open'?selection.id:null;
  const requested=useRef<string|null>(null);
  const sending=useRef(send);
  sending.current=send;
  useEffect(()=>window.horizon.subscribe(event=>{
    if(event.kind==='frontier_tracking'&&event.data.frontier_id===requested.current){
      setTracking(event.data);
    }
    if(event.kind==='frontier_tracking_invalidated'&&requested.current&&event.data.includes(requested.current)){
      sending.current({action:'frontier_tracking',frontier_id:requested.current});
    }
  }),[]);
  useEffect(()=>{
    requested.current=selectedId;
    if(selectedId&&detailHost)sending.current({action:'frontier_tracking',frontier_id:selectedId});
  },[selectedId,detailHost]);
  const show=(next:Selection)=>{
    setSelection(next);
    setTracking(null);
    setDraft(null);
    openDetail();
  };
  const selected=selection?.kind==='create'||frontier?.frontier_id===selectedId?frontier:null;
  const progress=tracking?.frontier_id===selectedId?tracking.progress:[];
  const history=tracking?.frontier_id===selectedId?tracking.history:[];
  return <section className="frontier-tracking" aria-label="前沿">
    <h2>前沿</h2>
    <button type="button" onClick={()=>show({kind:'create',nonce:crypto.randomUUID()})}>新增前沿</button>
    <ul>{frontiers.filter(item=>item.status!=='deleted').map(item=><li key={item.frontier_id}>
      <button type="button" onClick={()=>show({kind:'open',nonce:crypto.randomUUID(),id:item.frontier_id,version:item.version})}>
        {item.question} <TestTag tags={item.tags}/> <small>{statusText[item.status]}</small>
      </button>
    </li>)}</ul>
    {detailHost&&selection&&createPortal(<div aria-label="前沿详情" className="frontier-detail">
      <KnowledgePanel frontiers={frontiers} selected={selected} viewEvent={viewEvent} commandError={error}
        onCommand={send} embedded showList={false} openRequest={selection} titleOnly
        onCommitted={(id,version)=>setSelection(current=>current?{kind:'open',id,version,nonce:current.nonce}:current)}/>
      {selectedId&&tracking?.frontier_id===selectedId&&<section aria-label="前沿进展">
        <h3>进展</h3>
        {selected?.status!=='deleted'&&<button type="button" onClick={()=>setDraft('new')}>新增进展</button>}
        {draft&&<form key={draft==='new'?'new':draft.progress_id} onSubmit={event=>{
          event.preventDefault();
          const fields=new FormData(event.currentTarget);
          send({action:draft==='new'?'progress_create':'progress_update',schema_version:1,
            operation_id:crypto.randomUUID(),frontier_id:selectedId,
            ...(draft==='new'?{}:{progress_id:draft.progress_id,expected_version:draft.version}),
            content:fields.get('content'),source_ids:fields.getAll('source_ids')});
          setDraft(null);
        }}>
          <label>进展内容<MultilineInput name="content" required maxLength={12000} defaultValue={draft==='new'?'':draft.content}/></label>
          <fieldset><legend>资料库来源</legend>{sources.map(source=><Checkbox key={source.source_id} name="source_ids" value={source.source_id}
              defaultChecked={draft!=='new'&&draft.source_ids.includes(source.source_id)}>
            {source.title}
          </Checkbox>)}</fieldset>
          <button type="submit">提交进展审核</button>
          <button type="button" onClick={()=>setDraft(null)}>取消</button>
        </form>}
        {progress.length===0?<p>尚无进展。</p>:progress.map(item=><article key={item.progress_id}>
          <p>{item.content} <TestTag tags={item.tags}/></p>
          {item.source_ids.length>0&&<ul aria-label="进展来源">{item.source_ids.map(id=>{
            const source=sources.find(row=>row.source_id===id);
            return <li key={id}>{source?<button type="button" onClick={()=>openSource(source)}>{source.title}</button>:'来源已移除'}</li>;
          })}</ul>}
          {selected?.status!=='deleted'&&<>
            <button type="button" onClick={()=>setDraft(item)}>编辑进展</button>
            <button type="button" onClick={()=>send({action:'progress_delete',schema_version:1,
              frontier_id:selectedId,progress_id:item.progress_id,expected_version:item.version,
              operation_id:crypto.randomUUID()})}>删除进展</button>
          </>}
        </article>)}
      </section>}
      {selectedId&&tracking?.frontier_id===selectedId&&<section aria-label="前沿检索历史">
        <h3>最近 50 条检索记录</h3>
        {history.length===0?<p>尚无检索记录。</p>:<ul>{history.map(row=><li key={row.request_id}>
          <button type="button" onClick={()=>openVoyager(row.request_id)}>查看检索记录 · {row.scope_text}</button>
        </li>)}</ul>}
      </section>}
    </div>,detailHost)}
  </section>;
}
