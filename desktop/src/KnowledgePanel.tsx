import {TextInput, MultilineInput} from './gui/elements/FormElements';
import {FixedSelect} from './gui/elements/ChoiceElements';
import {TestTag} from './TestCleanupPanel';
import React,{useEffect,useRef,useState} from 'react';

export type FrontierSummary={tags?:string[];frontier_id:string;version:number;question:string;tracked?:boolean;was_resolved?:boolean;resolved_at?:number;status:string;scope_text:string;term_ids?:string[];updated_at:number};
export type Frontier={tags?:string[];schema_version:number;operation_id:string;read_operation_id?:string;frontier_id:string;version:number;question:string;tracked?:boolean;was_resolved?:boolean;resolved_at?:number;status:string;scope:{text:string;alternatives:string[];sources:string[]};hard_constraints:{must_include:string[];exclude:string[]};relations:unknown[];created_at:number;updated_at:number};
export type FrontierViewEvent={source:'view';operation_id?:string;nonce:string};
export type FrontierCommandError={operation_id?:string;code?:string;message?:string};
type OpenRequest={kind:'create';nonce:string}|{kind:'open';nonce:string;id:string;version:number};
type LifecycleTarget={frontier_id:string;version:number};

const statusLabel:Record<string,string>={following:'关注中',paused:'已暂停',resolved:'已解决',deleted:'已删除'};
const statusOptions:[string,string][]=[['following','关注中'],['paused','已暂停'],['resolved','已解决']];
const lines=(s:string)=>s.split('\n').map(v=>v.trim()).filter(Boolean);
const isFullView=(value:Frontier|null|undefined):value is Frontier=>!!value&&typeof value.operation_id==='string'&&typeof value.frontier_id==='string'&&typeof value.version==='number'&&typeof value.question==='string'&&!!value.scope&&typeof value.scope.text==='string'&&Array.isArray(value.scope.alternatives)&&Array.isArray(value.scope.sources)&&!!value.hard_constraints&&Array.isArray(value.hard_constraints.must_include)&&Array.isArray(value.hard_constraints.exclude)&&Array.isArray(value.relations);

export function KnowledgePanel({frontiers,selected,viewEvent,commandError,onCommand,embedded=false,showList=true,openRequest=null,onCommitted,onRetrieve,titleOnly=false}:{frontiers:FrontierSummary[];selected:Frontier|null;viewEvent:FrontierViewEvent|null;commandError:FrontierCommandError|null;onCommand:(c:any)=>void;embedded?:boolean;showList?:boolean;openRequest?:OpenRequest|null;titleOnly?:boolean;onRetrieve?:(frontier:Frontier)=>void;onCommitted?:(frontier_id:string,version:number)=>void}){
  const [question,setQuestion]=useState(''),[scope,setScope]=useState(''),[alternatives,setAlternatives]=useState(''),[must,setMust]=useState(''),[exclude,setExclude]=useState(''),[sources,setSources]=useState('arxiv'),[status,setStatus]=useState('following');
  const [editing,setEditing]=useState<{frontier_id:string;version:number;status:string}|null>(null);
  const [loading,setLoading]=useState(false),[saving,setSaving]=useState(false),[acting,setActing]=useState(''),[message,setMessage]=useState('');
  const [reason,setReason]=useState('');
  const pending=useRef<{kind:'load';frontier_id:string;operation_id:string}|{kind:'save';operation_id:string;frontier_id?:string}|null>(null);
  const actionPending=useRef<{operation_id:string;frontier_id:string;kind:'status'|'delete'}|null>(null);
  const committed=useRef(onCommitted);
  committed.current=onCommitted;
  const send=(operation_id:string,action:string,extra:object={})=>onCommand({action,schema_version:1,operation_id,...extra});
  const openId=openRequest?.kind==='open'?openRequest.id:'';
  const isCreateDraft=openRequest?.kind==='create';
  const staleDraft=!!openId&&editing?.frontier_id!==openId;
  const scopePayload=()=>({text:scope,alternatives:lines(alternatives),sources:sources.split(',').map(v=>v.trim()).filter(Boolean)});
  const clearDraft=()=>{setEditing(null);setQuestion('');setScope('');setAlternatives('');setMust('');setExclude('');setSources('arxiv');setStatus('following');setReason('');};
  const populate=(view:Frontier)=>{setEditing({frontier_id:view.frontier_id,version:view.version,status:view.status});setQuestion(view.question);setScope(view.scope.text);setAlternatives(view.scope.alternatives.join('\n'));setMust(view.hard_constraints.must_include.join('\n'));setExclude(view.hard_constraints.exclude.join('\n'));setSources(view.scope.sources.join(','));setStatus(view.status==='deleted'?'following':view.status);};
  const load=(f:Pick<FrontierSummary,'frontier_id'>)=>{
    const operation_id=crypto.randomUUID();
    pending.current={kind:'load',frontier_id:f.frontier_id,operation_id};
    setLoading(true);setSaving(false);setMessage('正在读取完整已提交版本…');
    send(operation_id,'frontier_open',{frontier_id:f.frontier_id});
  };
  const save=()=>{
    if(loading||saving)return;
    if(staleDraft){setMessage('当前草稿属于另一个 Frontier；请重新载入本记录后再保存。');return;}
    const payload=titleOnly&&!editing?{question}:{question,status,scope:scopePayload(),hard_constraints:{must_include:lines(must),exclude:lines(exclude)},...(reason.trim()?{reason:reason.trim()}:{})};
    const operation_id=crypto.randomUUID();
    pending.current={kind:'save',operation_id,frontier_id:editing?.frontier_id};
    setSaving(true);
    setMessage(editing?`正在审核修改（基于版本 ${editing.version}）…`:'正在审核新增前沿…');
    if(editing){send(operation_id,'frontier_update',{frontier_id:editing.frontier_id,expected_version:editing.version,...payload});}
    else{send(operation_id,'frontier_create',{relations:[],...payload});}
  };
  useEffect(()=>{
    if(!viewEvent||viewEvent.source!=='view'||!isFullView(selected))return;
    const work=pending.current;
    if(!work)return;
    if(work.kind==='load'){
      if(work.frontier_id!==selected.frontier_id||work.operation_id!==selected.read_operation_id)return;
      pending.current=null;setLoading(false);setSaving(false);
      if(selected.status==='deleted'){setEditing(null);setMessage('已删除的 Frontier 不能编辑。');return;}
      populate(selected);
      setMessage(`已载入版本 ${selected.version}，保存将按该版本校验。`);
      return;
    }
    if(viewEvent.operation_id!==work.operation_id||selected.operation_id!==work.operation_id)return;
    if(work.frontier_id&&work.frontier_id!==selected.frontier_id)return;
    pending.current=null;setSaving(false);
    populate(selected);
    setReason('');
    setMessage(work.frontier_id?`已保存为版本 ${selected.version}。`:`已创建为版本 ${selected.version}。`);
    committed.current?.(selected.frontier_id,selected.version);
  },[viewEvent,selected]);
  useEffect(()=>{
    if(!viewEvent||viewEvent.source!=='view'||!editing||!isFullView(selected))return;
    if(selected.frontier_id===editing.frontier_id&&selected.status==='deleted'){
      pending.current=null;setLoading(false);setSaving(false);clearDraft();
      setMessage('该 Frontier 已删除，已退出编辑；历史检索与资料仍保留。');
    }
  },[viewEvent,selected,editing]);
  useEffect(()=>{
    if(!viewEvent||viewEvent.source!=='view'||!isFullView(selected))return;
    const action=actionPending.current;
    if(!action||viewEvent.operation_id!==action.operation_id||selected.operation_id!==action.operation_id||selected.frontier_id!==action.frontier_id)return;
    actionPending.current=null;setActing('');
    if(action.kind==='delete'){
      setMessage(`已删除版本 ${selected.version}；该 Frontier 现为只读，历史检索与资料仍保留。`);
    }else{
      setEditing(current=>current?.frontier_id===selected.frontier_id?{...current,version:selected.version,status:selected.status}:current);
      setStatus(selected.status);
      setMessage(`已更新为「${statusLabel[selected.status]||selected.status}」 · 版本 ${selected.version}。`);
    }
    committed.current?.(selected.frontier_id,selected.version);
  },[viewEvent,selected]);
  useEffect(()=>{
    if(!commandError?.operation_id)return;
    const work=pending.current;
    if(work&&commandError.operation_id===work.operation_id){
      pending.current=null;setLoading(false);setSaving(false);
      setMessage(`${work.kind==='load'?'读取':'保存'}未完成：${commandError.message||commandError.code||'请检查后重试'}${work.kind==='save'?'；草稿已保留。':'。'}`);
    }
    const action=actionPending.current;
    if(action&&commandError.operation_id===action.operation_id){
      actionPending.current=null;setActing('');
      setMessage(`${action.kind==='delete'?'删除':'状态更新'}未完成：${commandError.message||commandError.code||'请检查后重试'}；当前记录未改变。`);
    }
  },[commandError]);
  useEffect(()=>{
    const unsubscribe=window.horizon.subscribe(e=>{
      if(e.kind==='frontier_confirmation'&&['rejected','expired'].includes(e.data.status)){
        const operationId=e.data.operation_id;
        if(pending.current?.operation_id===operationId){
          pending.current=null;
          setSaving(false);
          setMessage(e.data.status==='expired'?'确认已过期。':'已取消修改。');
        }
        if(actionPending.current?.operation_id===operationId){
          actionPending.current=null;
          setActing('');
          setMessage(e.data.status==='expired'?'确认已过期。':'已取消修改。');
        }
      }
    });
    return unsubscribe;
  },[]);
  useEffect(()=>{
    if(!openRequest)return;
    if(openRequest.kind==='create'){
      pending.current=null;actionPending.current=null;setLoading(false);setSaving(false);setActing('');
      clearDraft();
      setMessage('');
      return;
    }
    load({frontier_id:openRequest.id});
  },[openRequest?.nonce]);
  const lifecycle=(f:LifecycleTarget,next:string)=>{
    if(actionPending.current)return;
    const operation_id=crypto.randomUUID();
    const kind:'status'|'delete'=next==='deleted'?'delete':'status';
    actionPending.current={operation_id,frontier_id:f.frontier_id,kind};
    setActing(f.frontier_id);
    setMessage(next==='deleted'?'正在删除 Frontier…':`正在更新为「${statusLabel[next]||next}」…`);
    if(kind==='delete')send(operation_id,'frontier_delete',{frontier_id:f.frontier_id,expected_version:f.version});
    else send(operation_id,'frontier_update',{frontier_id:f.frontier_id,expected_version:f.version,status:next});
  };
  const stale=editing&&isFullView(selected)&&selected.frontier_id===editing.frontier_id&&selected.version!==editing.version;
  const locked=!isCreateDraft&&isFullView(selected)&&selected.status==='deleted';
  return <div className={`trace${embedded?' workbench-panel':''}`}>{!embedded&&<h2>知识库</h2>}
  {isCreateDraft&&titleOnly?<form onSubmit={e=>{e.preventDefault();save();}}>
    <label>前沿题目<TextInput required maxLength={1000} value={question} onChange={e=>setQuestion(e.target.value)}/></label>
    <button type="submit" disabled={saving}>提交前沿审核</button>
  </form>:!locked&&!staleDraft&&<>
  <form onSubmit={e=>{e.preventDefault();save();}}>
  <label>关注问题<TextInput required maxLength={1000} disabled={loading} value={question} onChange={e=>setQuestion(e.target.value)}/></label>
  <label>关注状态<FixedSelect disabled={loading} value={status} onChange={e=>setStatus(e.target.value)}>{statusOptions.map(([value,label])=><option key={value} value={value}>{label}</option>)}</FixedSelect></label>
  <label>原始研究范围<MultilineInput required maxLength={1000} disabled={loading} value={scope} onChange={e=>setScope(e.target.value)}/></label>
  <label>中英文替代查询（每行一个）<MultilineInput disabled={loading} value={alternatives} onChange={e=>setAlternatives(e.target.value)}/></label>
  <label>必须包含（每行一个）<MultilineInput disabled={loading} value={must} onChange={e=>setMust(e.target.value)}/></label>
  <label>排除词（每行一个）<MultilineInput disabled={loading} value={exclude} onChange={e=>setExclude(e.target.value)}/></label>
  <label>来源标识（逗号分隔）<TextInput required disabled={loading} value={sources} onChange={e=>setSources(e.target.value)}/></label>
  <label>本次保存理由（可选）<TextInput maxLength={500} value={reason} onChange={e=>setReason(e.target.value)} placeholder="例如：调整研究范围"/></label>
  <p>理由会写入版本历史；保存成功后清空，失败时与草稿一起保留。</p>
  <button type="submit" disabled={loading||saving}>{editing?'保存修改':'创建 Frontier'}</button>{editing&&<button type="button" disabled={saving} onClick={()=>load(editing)}>取消修改并重新读取</button>}
  </form></>}
  {locked&&<p>已删除的 Frontier 以只读方式显示；历史检索与资料仍然保留。</p>}
  {message&&<p role="status">{message}</p>}
  {stale&&<p role="alert">该 Frontier 已更新到版本 {selected.version}，当前编辑仍固定在载入的版本 {editing.version}；提交会因版本冲突失败。请取消修改后重新编辑以载入最新版本。</p>}
  {!isCreateDraft&&isFullView(selected)&&<section className="child-result"><h3>当前完整已提交版本</h3><p>关注问题：{selected.question} <TestTag tags={selected.tags}/></p><p>状态：{statusLabel[selected.status]||selected.status} · 版本 {selected.version}</p><p>追踪中：{(selected.tracked??(selected.status==='following'))?'是':'否'}</p>{selected.was_resolved&&<p>解决记录：{selected.resolved_at!=null?new Date(selected.resolved_at*1000).toLocaleString():'已标记解决'}</p>}<p>原始研究范围：{selected.scope.text}</p><p>替代查询：{selected.scope.alternatives.join('、')||'无'} · 必须包含：{selected.hard_constraints.must_include.join('、')||'无'} · 排除：{selected.hard_constraints.exclude.join('、')||'无'}</p><p>来源：{selected.scope.sources.join('、')} · 关联：{selected.relations.length?`${selected.relations.length} 项`:'空关联（合法）'}</p>
  {selected.status!=='deleted'&&<div className="workbench-actions">
    {selected.status==='following'&&onRetrieve&&<button type="button" disabled={loading||saving} onClick={()=>onRetrieve(selected)}>在会话中检索</button>}
    {selected.status==='following'&&<><button type="button" disabled={!!acting||loading||saving} onClick={()=>lifecycle(selected,'paused')}>停止追踪</button><button type="button" disabled={!!acting||loading||saving} onClick={()=>lifecycle(selected,'resolved')}>标记解决</button></>}
    {(selected.status==='paused'||selected.status==='resolved')&&<button type="button" disabled={!!acting||loading||saving} onClick={()=>lifecycle(selected,'following')}>开始追踪</button>}
    <button type="button" disabled={!!acting||loading||saving} onClick={()=>lifecycle(selected,'deleted')}>删除</button>
  </div>}
  </section>}
  {showList&&<><h3>Frontier 列表</h3><p>列表中的范围摘要可能被截断；编辑会读取完整已提交版本。</p>{frontiers.length===0&&<p>尚无 Frontier。填写上方表单创建第一个关注问题。</p>}
  {frontiers.map(f=><section className="child-result" key={f.frontier_id}><h3>{f.question}</h3><p>范围：{f.scope_text} · 状态：{statusLabel[f.status]||f.status} · 版本 {f.version}</p>
  {f.status!=='deleted'&&<button disabled={saving} onClick={()=>load(f)}>编辑（读取完整版本）</button>}
  {f.status==='following'&&<><button disabled={!!acting} onClick={()=>lifecycle(f,'paused')}>停止追踪</button><button disabled={!!acting} onClick={()=>lifecycle(f,'resolved')}>标记解决</button></>}
  {(f.status==='paused'||f.status==='resolved')&&<button disabled={!!acting} onClick={()=>lifecycle(f,'following')}>开始追踪</button>}
  {f.status!=='deleted'&&<button disabled={!!acting} onClick={()=>lifecycle(f,'deleted')}>删除</button>}
  {f.status==='deleted'&&<p>已删除，不能编辑；保留历史检索与资料。</p>}</section>)}</>}
  </div>;
}
