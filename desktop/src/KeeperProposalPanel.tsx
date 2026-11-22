import React,{useEffect,useRef} from 'react';

export type KeeperKind='card'|'theme'|'frontier';
export type KeeperAction='create'|'update'|'delete';
export type KeeperSource={
  material_id:string;
  material_version?:number|null;
  locator?:string|null;
  excerpt?:string|null;
};
export type KeeperChange={
  kind:KeeperKind;
  action:KeeperAction|string;
  id?:string|null;
  expected_version?:number|null;
  before?:unknown;
  after?:unknown;
  reason?:string|null;
  sources?:KeeperSource[];
};
export type KeeperProposal={
  proposal_id:string;
  revision?:number|null;
  status?:string|null;
  request_id?:string|null;
  task_id?:string|null;
  task_version?:number|null;
  report_id?:string|null;
  report_version?:number|null;
  provenance?:string|null;
  child_id?:string|null;
  changes?:KeeperChange[];
  queued_at?:string|number|null;
  created_at?:string|number|null;
};
export type KeeperListEvent={operation_id?:string;proposals:KeeperProposal[]};

const KIND_LABEL:Record<KeeperKind,string>={card:'知识卡片',theme:'命题',frontier:'关注问题'};
const ACTION_LABEL:Record<string,string>={create:'新建',update:'更新',delete:'删除'};
const STATUS_LABEL:Record<string,string>={
  queued:'排队中',pending:'待确认',approved:'已确认',rejected:'已拒绝',
  returned:'已退回',stopped:'已停止',cancelled:'已取消',
};
const FIELD_LABEL:Record<string,string>={
  title:'标题',content:'内容',question:'关注问题',judgement:'判断',status:'状态',
  aliases:'别名',recall_terms:'检索词',sources:'来源',relations:'关联',questionable:'存疑',
  provenance:'来源性质',source_pending:'来源待补充',conditions:'适用条件',
  related_cards:'关联知识卡片',evidence:'证据',evidence_sufficiency:'证据充分性',
  scope:'研究范围',text:'范围文本',alternatives:'替代查询',
  hard_constraints:'硬约束',must_include:'必须包含',exclude:'排除词',
  created_at:'创建时间',updated_at:'更新时间',card_id:'知识卡片编号',
  theme_id:'命题编号',frontier_id:'关注问题编号',material_id:'资料编号',
  material_version:'资料版本',locator:'定位',excerpt:'原文摘录',
  content_hash:'内容摘要',source_kind:'来源类型',snapshot_id:'快照编号',
  evidence_id:'证据编号',stance:'立场',reason:'理由',source:'来源',
  version:'版本',operation_id:'操作编号',schema_version:'数据版本',
};

export const keeperKindLabel=(kind:string)=>KIND_LABEL[kind as KeeperKind]||kind;
export const keeperActionLabel=(action:string)=>ACTION_LABEL[action]||action;
export const keeperStatusLabel=(status?:string|null)=>status?(STATUS_LABEL[status]||status):'';
export const keeperTestBadge=(proposal:KeeperProposal):string|null=>{
  if(proposal.report_id)return '测试报道';
  if(proposal.request_id)return '测试请求';
  return proposal.provenance==='editor_test'?'测试提议':null;
};

const isRecord=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
const fieldLabel=(key:string)=>FIELD_LABEL[key]||key;
const inlineValue=(value:unknown):string=>{
  if(value===null||value===undefined)return '未记录';
  if(Array.isArray(value))return value.length?value.map(inlineValue).join('、'):'（空）';
  if(isRecord(value)){
    const entries=Object.entries(value);
    if(!entries.length)return '（空）';
    return entries.map(([key,item])=>`${fieldLabel(key)}：${inlineValue(item)}`).join('；');
  }
  return String(value);
};
const valueText=(value:unknown):string=>{
  if(value===null||value===undefined)return '';
  if(typeof value==='string')return value.trim()?value:'';
  if(isRecord(value)){
    const entries=Object.entries(value);
    if(!entries.length)return '';
    return entries.map(([key,item])=>`${fieldLabel(key)}：${inlineValue(item)}`).join('\n');
  }
  return inlineValue(value);
};
const recordTitle=(value:unknown):string=>{
  if(!isRecord(value))return '';
  for(const key of ['title','judgement','question']){
    const candidate=value[key];
    if(typeof candidate==='string'&&candidate.trim())return candidate.trim();
  }
  return '';
};
const changeHeadline=(change:KeeperChange):string=>{
  const record=change.action==='delete'?change.before:change.after??change.before;
  return recordTitle(record)||(typeof change.id==='string'&&change.id?change.id:'未命名变更');
};
export const keeperProposalTitle=(proposal:KeeperProposal):string=>{
  const changes=Array.isArray(proposal.changes)?proposal.changes:[];
  for(const change of changes){
    const title=recordTitle(change.action==='delete'?change.before:change.after??change.before);
    if(title)return title;
  }
  const first=changes[0];
  return first&&typeof first.id==='string'&&first.id?first.id:proposal.proposal_id;
};
const timeLabel=(value:unknown):string=>{
  const seconds=typeof value==='number'?value:typeof value==='string'&&/^\d+$/.test(value)?Number(value):NaN;
  return Number.isFinite(seconds)?new Date(seconds*1000).toLocaleString():typeof value==='string'?value:'';
};

function ValueText({value}:{value:unknown}){
  const text=valueText(value);
  if(!text)return <p className="keeper-value-empty">未记录</p>;
  if(text.length<=360)return <p className="keeper-value-text">{text}</p>;
  return <details className="keeper-value-more">
    <summary>{text.slice(0,360)}…</summary>
    <pre>{text}</pre>
  </details>;
}

function SourceList({sources}:{sources:KeeperSource[]}){
  if(!sources.length)return null;
  return <div className="keeper-sources">
    <h4>已保存来源</h4>
    <ul>
      {sources.map((source,index)=><li className="keeper-source" key={`${source.material_id}:${index}`}>
        <p className="keeper-source-pin">资料 {source.material_id}
          {typeof source.material_version==='number'?` · 固定版本 v${source.material_version}`:''}
        </p>
        {source.locator&&<p className="keeper-source-locator">定位：{source.locator}</p>}
        {source.excerpt&&<blockquote>{source.excerpt}</blockquote>}
      </li>)}
    </ul>
  </div>;
}

function ChangeCard({change}:{change:KeeperChange}){
  const sources=Array.isArray(change.sources)?change.sources:[];
  return <article className="keeper-change">
    <header className="keeper-change-head">
      <span className="keeper-action">{keeperActionLabel(change.action)}</span>
      <strong>{changeHeadline(change)}</strong>
      {change.id&&<span className="keeper-change-id">{change.id}</span>}
      {typeof change.expected_version==='number'&&<span className="keeper-change-id">基于 v{change.expected_version}</span>}
    </header>
    <div className="keeper-values">
      <section className="keeper-value">
        <h4>变更前</h4>
        <ValueText value={change.before}/>
      </section>
      <section className="keeper-value">
        <h4>变更后</h4>
        <ValueText value={change.after}/>
      </section>
    </div>
    {change.reason&&<p className="keeper-reason"><strong>理由：</strong>{change.reason}</p>}
    <SourceList sources={sources}/>
  </article>;
}

function Meta({proposal}:{proposal:KeeperProposal}){
  const rows:Array<[string,React.ReactNode]>=[['提议编号',proposal.proposal_id]];
  if(typeof proposal.revision==='number')rows.push(['修订',`v${proposal.revision}`]);
  const status=keeperStatusLabel(proposal.status);
  if(status)rows.push(['状态',status]);
  if(proposal.request_id)rows.push(['测试请求',proposal.request_id]);
  if(proposal.task_id)rows.push(['任务',`${proposal.task_id}${typeof proposal.task_version==='number'?` · 固定版本 v${proposal.task_version}`:''}`]);
  if(proposal.report_id)rows.push(['测试报道',`${proposal.report_id}${typeof proposal.report_version==='number'?` · 固定版本 v${proposal.report_version}`:''}`]);
  if(proposal.child_id)rows.push(['子会话',proposal.child_id]);
  if(proposal.provenance)rows.push(['来源',proposal.provenance==='editor_test'?'测试（editor_test）':proposal.provenance]);
  if(proposal.queued_at!==null&&proposal.queued_at!==undefined)rows.push(['入队时间',timeLabel(proposal.queued_at)]);
  if(proposal.created_at!==null&&proposal.created_at!==undefined)rows.push(['创建时间',timeLabel(proposal.created_at)]);
  return <section className="keeper-meta" aria-label="提议来源与固定信息">
    <dl>{rows.map(([label,value])=><React.Fragment key={label}>
      <dt>{label}</dt><dd>{value}</dd>
    </React.Fragment>)}</dl>
  </section>;
}

/**
 * Read-only Keeper confirmation card. It renders the proposal exactly as the
 * authenticated bridge returned it: no approval controls, no request creation
 * and no inferred empty result when the bridge has not answered yet.
 */
export function KeeperProposalPanel({
  proposalId,proposal,loading,error,onRetry,onClose,
}:{
  proposalId:string;
  proposal:KeeperProposal|null;
  loading:boolean;
  error:string;
  onRetry:()=>void;
  onClose:()=>void;
}){
  const heading=useRef<HTMLHeadingElement>(null);
  useEffect(()=>{heading.current?.focus();},[]);
  const changes=proposal&&Array.isArray(proposal.changes)?proposal.changes:[];
  const groups:{kind:string;changes:KeeperChange[]}[]=[];
  changes.forEach(change=>{
    let group=groups.find(item=>item.kind===change.kind);
    if(!group){group={kind:change.kind,changes:[]};groups.push(group);}
    group.changes.push(change);
  });
  const status=proposal?keeperStatusLabel(proposal.status):'';
  const testBadge=proposal?keeperTestBadge(proposal):null;
  const headline=proposal?keeperProposalTitle(proposal):proposalId;
  return <div className="keeper-proposal" aria-busy={loading}>
    <header className="keeper-proposal-head">
      <div>
        <span className="workbench-kind">Keeper 待确认</span>
        <h2 ref={heading} tabIndex={-1}>{headline}</h2>
        <p>只读确认卡片 · {proposal?`${changes.length} 项变更`:'等待完整提议'} · 不会修改本地知识库</p>
      </div>
      <div className="keeper-proposal-actions">
        <div className="keeper-badges">
          {status&&<span className="keeper-badge keeper-badge-status">{status}</span>}
          {testBadge&&<span className="keeper-badge keeper-badge-test">{testBadge}</span>}
          {groups.map(group=><span className="keeper-badge" key={group.kind}>{keeperKindLabel(group.kind)}</span>)}
        </div>
        <button type="button" onClick={onClose}>关闭</button>
      </div>
    </header>
    {proposal&&<Meta proposal={proposal}/>}
    {loading&&<p className="workbench-status keeper-loading" role="status">正在读取完整提议…</p>}
    {error&&<div className="workbench-error" role="alert"><p>{error}</p><button type="button" onClick={onRetry}>重试</button></div>}
    {groups.length?groups.map(group=><section className="keeper-group" key={group.kind} aria-label={`${keeperKindLabel(group.kind)}变更`}>
      <h3>{keeperKindLabel(group.kind)} · {group.changes.length} 项</h3>
      {group.changes.map((change,index)=><ChangeCard key={`${change.kind}:${change.id||'new'}:${index}`} change={change}/>)}
    </section>):proposal&&!loading&&!error?<div className="workbench-empty keeper-empty" role="status">
      <p>该提议没有返回变更详情。</p>
    </div>:null}
  </div>;
}
