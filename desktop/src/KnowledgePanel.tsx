import React,{useEffect,useRef,useState} from 'react';

export type FrontierSummary={frontier_id:string;version:number;question:string;status:string;scope_text:string;updated_at:number};
export type Frontier={schema_version:number;operation_id:string;frontier_id:string;version:number;question:string;status:string;scope:{text:string;alternatives:string[];sources:string[]};hard_constraints:{must_include:string[];exclude:string[]};relations:unknown[];created_at:number;updated_at:number};
export type FrontierViewEvent={source:'view';operation_id?:string;nonce:string};
export type FrontierCommandError={operation_id?:string;code?:string;message?:string};

const localInput=(d:Date)=>new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);
const statusLabel:Record<string,string>={following:'关注中',paused:'已暂停',resolved:'已解决',deleted:'已删除'};
const statusOptions:[string,string][]=[['following','关注中'],['paused','已暂停'],['resolved','已解决']];
const lines=(s:string)=>s.split('\n').map(v=>v.trim()).filter(Boolean);
const isFullView=(value:Frontier|null|undefined):value is Frontier=>!!value&&typeof value.operation_id==='string'&&typeof value.frontier_id==='string'&&typeof value.version==='number'&&typeof value.question==='string'&&!!value.scope&&typeof value.scope.text==='string'&&Array.isArray(value.scope.alternatives)&&Array.isArray(value.scope.sources)&&!!value.hard_constraints&&Array.isArray(value.hard_constraints.must_include)&&Array.isArray(value.hard_constraints.exclude)&&Array.isArray(value.relations);

export function KnowledgePanel({frontiers,selected,viewEvent,commandError,onCommand}:{frontiers:FrontierSummary[];selected:Frontier|null;viewEvent:FrontierViewEvent|null;commandError:FrontierCommandError|null;onCommand:(c:any)=>void}){
  const [question,setQuestion]=useState(''),[scope,setScope]=useState(''),[alternatives,setAlternatives]=useState(''),[must,setMust]=useState(''),[exclude,setExclude]=useState(''),[sources,setSources]=useState('arxiv'),[status,setStatus]=useState('following');
  const [editing,setEditing]=useState<{frontier_id:string;version:number;status:string}|null>(null);
  const [mode,setMode]=useState('initial'),[basis,setBasis]=useState('published');
  const [from,setFrom]=useState(localInput(new Date(new Date().getFullYear(),0,1))),[until,setUntil]=useState(localInput(new Date())),[overlap,setOverlap]=useState(86400);
  const [loading,setLoading]=useState(false),[saving,setSaving]=useState(false),[creating,setCreating]=useState(''),[message,setMessage]=useState('');
  const pending=useRef<{kind:'load';frontier_id:string;operation_id:string}|{kind:'save';operation_id:string}|null>(null);
  const createPending=useRef<{operation_id:string;frontier_id:string}|null>(null);
  const send=(operation_id:string,action:string,extra:object={})=>onCommand({action,schema_version:1,operation_id,...extra});
  const scopePayload=()=>({text:scope,alternatives:lines(alternatives),sources:sources.split(',').map(v=>v.trim()).filter(Boolean)});
  const clearDraft=()=>{setEditing(null);setQuestion('');setScope('');setAlternatives('');setMust('');setExclude('');setSources('arxiv');setStatus('following');};
  const populate=(view:Frontier)=>{setEditing({frontier_id:view.frontier_id,version:view.version,status:view.status});setQuestion(view.question);setScope(view.scope.text);setAlternatives(view.scope.alternatives.join('\n'));setMust(view.hard_constraints.must_include.join('\n'));setExclude(view.hard_constraints.exclude.join('\n'));setSources(view.scope.sources.join(','));setStatus(view.status==='deleted'?'following':view.status);};
  const load=(f:FrontierSummary)=>{
    if(f.status==='deleted'){setMessage('已删除的 Frontier 不能编辑。');return;}
    const operation_id=crypto.randomUUID();
    pending.current={kind:'load',frontier_id:f.frontier_id,operation_id};
    setLoading(true);setSaving(false);setMessage('正在读取完整已提交版本…');
    send(operation_id,'frontier_open',{frontier_id:f.frontier_id});
  };
  const save=()=>{
    if(loading||saving)return;
    const payload={question,status,scope:scopePayload(),hard_constraints:{must_include:lines(must),exclude:lines(exclude)}};
    const operation_id=crypto.randomUUID();
    pending.current={kind:'save',operation_id};
    setSaving(true);
    setMessage(editing?`正在保存修改（基于版本 ${editing.version}）…`:'正在创建 Frontier…');
    if(editing){send(operation_id,'frontier_update',{frontier_id:editing.frontier_id,expected_version:editing.version,...payload});}
    else{send(operation_id,'frontier_create',{relations:[],...payload});}
  };
  useEffect(()=>{
    if(!viewEvent||viewEvent.source!=='view'||!isFullView(selected))return;
    const work=pending.current;
    if(!work)return;
    if(work.kind==='load'){
      if(work.frontier_id!==selected.frontier_id)return;
      pending.current=null;setLoading(false);setSaving(false);
      if(selected.status==='deleted'){setEditing(null);setMessage('已删除的 Frontier 不能编辑。');return;}
      populate(selected);
      setMessage(`已载入版本 ${selected.version}，保存将按该版本校验。`);
      return;
    }
    if(viewEvent.operation_id!==work.operation_id||selected.operation_id!==work.operation_id)return;
    pending.current=null;setSaving(false);
    clearDraft();
    setMessage(`已保存为版本 ${selected.version}。`);
  },[viewEvent,selected]);
  useEffect(()=>{
    if(!viewEvent||viewEvent.source!=='view'||!editing||!isFullView(selected))return;
    if(selected.frontier_id===editing.frontier_id&&selected.status==='deleted'){
      pending.current=null;setLoading(false);setSaving(false);clearDraft();
      setMessage('该 Frontier 已删除，已退出编辑；历史检索与资料仍保留。');
    }
  },[viewEvent,selected,editing]);
  useEffect(()=>{
    if(!commandError?.operation_id)return;
    const work=pending.current;
    if(work&&commandError.operation_id===work.operation_id){
      pending.current=null;setLoading(false);setSaving(false);
      setMessage(`${work.kind==='load'?'读取':'保存'}未完成：${commandError.message||commandError.code||'请检查后重试'}${work.kind==='save'?'；草稿已保留。':'。'}`);
    }
    const creation=createPending.current;
    if(creation&&commandError.operation_id===creation.operation_id){
      createPending.current=null;setCreating('');
      setMessage(`创建检索计划未完成：${commandError.message||commandError.code||'请检查后重试'}；可重试。`);
    }
  },[commandError]);
  const statusOf=(f:FrontierSummary,next:string)=>send(crypto.randomUUID(),'frontier_update',{frontier_id:f.frontier_id,expected_version:f.version,status:next});
  const retrieve=(f:FrontierSummary)=>{
    if(createPending.current)return;
    const operation_id=crypto.randomUUID();
    createPending.current={operation_id,frontier_id:f.frontier_id};setCreating(f.frontier_id);
    setMessage('正在创建检索计划…');
    send(operation_id,'voyager_create',{scope:{frontier_id:f.frontier_id,expected_version:f.version},window:{mode,basis,from_at:Math.floor(new Date(from).getTime()/1000),until_at:Math.floor(new Date(until).getTime()/1000),overlap_seconds:overlap},limits:{seconds:900,queries:20,candidates:100,fulltexts:20}});
  };
  const stale=editing&&isFullView(selected)&&selected.frontier_id===editing.frontier_id&&selected.version!==editing.version;
  return <div className="trace"><h2>知识库</h2><p>创建关注问题（Frontier），设置范围与硬约束。空关联合法；暂停/解决/删除会停止后续自动检索，不销毁已有资料。保存会直接提交，不经过 Keeper 审批。</p>
  <details><summary>检索窗口（创建检索计划前设置）</summary>
  <label>方式<select value={mode} onChange={e=>setMode(e.target.value)}><option value="initial">首次：基础与近期</option><option value="incremental">增量：含重叠窗口</option></select></label>
  <label>时间依据<select value={basis} onChange={e=>setBasis(e.target.value)}><option value="published">发表时间</option><option value="discovered">发现时间</option></select></label>
  <label>开始（本地时间）<input type="datetime-local" value={from} onChange={e=>setFrom(e.target.value)}/></label>
  <label>结束（不含，本地时间）<input type="datetime-local" value={until} onChange={e=>setUntil(e.target.value)}/></label>
  <label>增量重叠秒数<input type="number" min="0" value={overlap} onChange={e=>setOverlap(Number(e.target.value))}/></label>
  </details>
  <form onSubmit={e=>{e.preventDefault();save();}}>
  <label>关注问题<input required maxLength={1000} disabled={loading} value={question} onChange={e=>setQuestion(e.target.value)}/></label>
  <label>关注状态<select disabled={loading} value={status} onChange={e=>setStatus(e.target.value)}>{statusOptions.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
  <label>原始研究范围<textarea required maxLength={1000} disabled={loading} value={scope} onChange={e=>setScope(e.target.value)}/></label>
  <label>中英文替代查询（每行一个）<textarea disabled={loading} value={alternatives} onChange={e=>setAlternatives(e.target.value)}/></label>
  <label>必须包含（每行一个）<textarea disabled={loading} value={must} onChange={e=>setMust(e.target.value)}/></label>
  <label>排除词（每行一个）<textarea disabled={loading} value={exclude} onChange={e=>setExclude(e.target.value)}/></label>
  <label>来源标识（逗号分隔）<input required disabled={loading} value={sources} onChange={e=>setSources(e.target.value)}/></label>
  <button type="submit" disabled={loading||saving}>{editing?'保存修改':'创建 Frontier'}</button>{editing&&<button type="button" disabled={saving} onClick={()=>{pending.current=null;clearDraft();setMessage('已取消修改。');}}>取消修改</button>}
  </form>
  {message&&<p role="status">{message}</p>}
  {stale&&<p role="alert">该 Frontier 已更新到版本 {selected.version}，当前编辑仍固定在载入的版本 {editing.version}；提交会因版本冲突失败。请取消修改后重新编辑以载入最新版本。</p>}
  {isFullView(selected)&&<section className="child-result"><h3>当前完整已提交版本</h3><p>关注问题：{selected.question}</p><p>状态：{statusLabel[selected.status]||selected.status} · 版本 {selected.version}</p><p>原始研究范围：{selected.scope.text}</p><p>替代查询：{selected.scope.alternatives.join('、')||'无'} · 必须包含：{selected.hard_constraints.must_include.join('、')||'无'} · 排除：{selected.hard_constraints.exclude.join('、')||'无'}</p><p>来源：{selected.scope.sources.join('、')} · 关联：{selected.relations.length?`${selected.relations.length} 项`:'空关联（合法）'}</p></section>}
  <h3>Frontier 列表</h3><p>列表中的范围摘要可能被截断；编辑会读取完整已提交版本。</p>{frontiers.length===0&&<p>尚无 Frontier。填写上方表单创建第一个关注问题。</p>}
  {frontiers.map(f=><section className="child-result" key={f.frontier_id}><h3>{f.question}</h3><p>范围：{f.scope_text} · 状态：{statusLabel[f.status]||f.status} · 版本 {f.version}</p>
  {f.status==='following'&&<button disabled={!!creating} onClick={()=>retrieve(f)}>{creating===f.frontier_id?'正在创建检索计划…':'创建检索计划（不检索）'}</button>}
  {f.status!=='deleted'&&<button disabled={saving} onClick={()=>load(f)}>编辑（读取完整版本）</button>}
  {f.status==='following'&&<><button onClick={()=>statusOf(f,'paused')}>暂停</button><button onClick={()=>statusOf(f,'resolved')}>解决</button></>}
  {(f.status==='paused'||f.status==='resolved')&&<button onClick={()=>statusOf(f,'following')}>重新关注</button>}
  {f.status!=='deleted'&&<button onClick={()=>send(crypto.randomUUID(),'frontier_delete',{frontier_id:f.frontier_id,expected_version:f.version})}>删除</button>}
  {f.status==='deleted'&&<p>已删除，不能编辑；保留历史检索与资料。</p>}</section>)}
  </div>;
}
