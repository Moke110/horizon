import React, {useEffect, useState} from 'react';

export type FrontierChoice = {frontier_id:string; question:string; status:string};
export type WritingRequest = {frontier_id?:string|null; once?:Partial<Record<StyleKey,string>>};
type StyleKey = 'length'|'structure';
type Provenance = {kind:string; report_id?:string; report_version?:number; feedback_revision?:number; score?:number};
export type WritingPreference = {id:string; version:number; key:StyleKey; value:string;
  scope?:string; frontier_id?:string|null; status?:string; source?:Provenance};

const labels:Record<StyleKey,Record<string,string>> = {
  length:{standard:'标准', concise:'简洁', detailed:'详细'},
  structure:{standard:'标准', sections:'分节', conclusion_first:'结论前置'},
};
const names:Record<StyleKey,string> = {length:'篇幅', structure:'结构'};
const keys:StyleKey[] = ['length', 'structure'];
const scopeLabels:Record<string,string> = {default:'默认', global:'全局', frontier:'Frontier', once:'本次'};

function StyleChoices({kind, value, onChange, inherit}:{
  kind:StyleKey; value:string; onChange:(value:string)=>void; inherit?:boolean;
}) {
  return <label>{names[kind]} <select value={value} onChange={event=>onChange(event.target.value)}>
    {inherit && <option value="">沿用偏好</option>}
    {Object.entries(labels[kind]).map(([key,label])=><option key={key} value={key}>{label}</option>)}
  </select></label>;
}

export function CurrentWritingOptions({frontiers, value, inherited, onChange}:{
  frontiers:FrontierChoice[]; value:WritingRequest; inherited?:string; onChange:(value:WritingRequest)=>void;
}) {
  return <details><summary>本次写作样式</summary>
    <label>报道所属 Frontier <select value={value.frontier_id===null?'__global':value.frontier_id || ''} onChange={event=>{
      const selected=event.target.value;
      onChange({...value, frontier_id:selected==='__global'?null:selected || undefined});
    }}>
      <option value="">沿用（改写时 {frontiers.find(row=>row.frontier_id===inherited)?.question || '全局'}，新报道用全局）</option>
      <option value="__global">全局</option>
      {frontiers.filter(row=>row.status==='following').map(row=>
        <option key={row.frontier_id} value={row.frontier_id}>{row.question}</option>)}
    </select></label>
    {keys.map(key=><StyleChoices key={key} kind={key} value={value.once?.[key] || ''} inherit
      onChange={next=>{
        const once={...value.once};
        if(next) once[key]=next;
        else delete once[key];
        onChange({...value, once});
      }}/>)}
  </details>;
}

export function UsedWritingPreferences({rows, frontiers}: {rows:WritingPreference[]; frontiers:FrontierChoice[]}) {
  const preferences = rows.filter(row=>row.key);
  if (!preferences.length) return null;
  return <details><summary>本版使用的写作偏好</summary><ul>{preferences.map(row=>
    <li key={row.key}>{names[row.key]} · {labels[row.key][row.value]} · {scopeLabels[row.scope || 'default']}{row.frontier_id && ` · ${frontiers.find(item=>item.frontier_id===row.frontier_id)?.question || row.frontier_id}`} · v{row.version}</li>)}</ul></details>;
}

export function EditorPreferences({taskId, frontiers, send}:{
  taskId:string; frontiers:FrontierChoice[]; send:(command:unknown)=>void;
}) {
  const [data,setData] = useState<{preferences:WritingPreference[]; candidates:WritingPreference[]}>({preferences:[],candidates:[]});
  const [scope,setScope] = useState('');
  const [kind,setKind] = useState<StyleKey>('length');
  const [value,setValue] = useState('standard');
  useEffect(()=>{
    const unsubscribe = window.horizon.subscribe(event=>{
      if(event.kind==='editor_preferences' && event.data.task_id===taskId) setData(event.data);
    });
    send({action:'editor_preferences'});
    return unsubscribe;
  }, [taskId, send]);
  const current = (key:StyleKey, frontier=scope) => data.preferences.find(row=>
    row.key===key && (row.frontier_id || '')===frontier);
  const change = (operation:string, fields:Record<string,unknown>)=>
    send({action:'editor_preference_change', change:operation, operation_id:crypto.randomUUID(), ...fields});
  const from = (row:WritingPreference) => ({
    key:row.key, frontier_id:row.frontier_id, expected_version:row.version,
  });
  const scopeName = (id?:string|null) => id ? frontiers.find(row=>row.frontier_id===id)?.question || id : '全局';
  return <details>
    <summary>写作偏好</summary>
    <form onSubmit={event=>{
      event.preventDefault();
      change('save',{key:kind,value,frontier_id:scope || null,expected_version:current(kind)?.version || 0});
    }}>
      <label>长期生效范围 <select value={scope} onChange={event=>setScope(event.target.value)}>
        <option value="">全局</option>
        {frontiers.filter(row=>row.status==='following').map(row=>
          <option key={row.frontier_id} value={row.frontier_id}>{row.question}</option>)}
      </select></label>
      <label>样式类别 <select value={kind} onChange={event=>{setKind(event.target.value as StyleKey);setValue('standard');}}>
        {keys.map(key=><option key={key} value={key}>{names[key]}</option>)}
      </select></label>
      <StyleChoices kind={kind} value={value} onChange={setValue}/>
      <button type="submit">保存为长期偏好</button>
      <button type="button" onClick={()=>change('reset',{frontier_id:scope || null,
        expected_revisions:Object.fromEntries(data.preferences.filter(row=>(row.frontier_id || '')===scope)
          .map(row=>[row.key,row.version]))})}>恢复此范围默认</button>
    </form>
    <button type="button" onClick={()=>send({action:'editor_preferences'})}>刷新写作偏好</button>
    {data.preferences.length>0 && <ul aria-label="已保存写作偏好">{data.preferences.map(row=>
      <li key={row.id}>
        {scopeName(row.frontier_id)} · {names[row.key]} · {labels[row.key][row.value]} ·
        {row.status==='disabled'?' 已禁用':' 已启用'} · v{row.version} ·
        {row.source?.kind==='feedback'
          ? ` 来自报道 ${row.source.report_id?.slice(0,8)} v${row.source.report_version} 的评分候选`
          : ' 探索者明确保存'}
        <button type="button" onClick={()=>{setScope(row.frontier_id || '');setKind(row.key);setValue(row.value);}}>编辑</button>
        <button type="button" onClick={()=>change(row.status==='disabled'?'enable':'disable',from(row))}>
          {row.status==='disabled'?'启用':'禁用'}
        </button>
        <button type="button" onClick={()=>change('delete',from(row))}>删除</button>
      </li>)}</ul>}
    {data.candidates.length>0 && <ul aria-label="写作偏好候选">{data.candidates.map(row=>
      <li key={row.id}>
        {names[row.key]} · {labels[row.key][row.value]} · 报道 {row.source?.report_id?.slice(0,8)}
        {' '}v{row.source?.report_version} · {row.source?.score} 分 · 尚未采用
        <button type="button" onClick={()=>change('adopt',{
          candidate_id:row.id, frontier_id:scope || null, expected_version:current(row.key)?.version || 0,
        })}>采用到{scopeName(scope)}</button>
        <button type="button" onClick={()=>change('dismiss',{candidate_id:row.id})}>忽略</button>
      </li>)}</ul>}
  </details>;
}
