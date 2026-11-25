import React,{useEffect,useRef,useState} from 'react';
import {KeeperGroupUndoPanel} from './KeeperGroupUndoPanel';

export type KeeperKind='card'|'theme'|'frontier';
export type KeeperAction='create'|'update'|'delete';
export type KeeperDecisionChoice='approve'|'reject';
export type KeeperRevisionDecision='adjust'|'cancel';
export type KeeperProposalStatus='pending'|'revising'|'returned'|'cancelled'|'approved'|'rejected';
export type KeeperDecisionStatus='approved'|'rejected'|'revising'|'cancelled'|'returned';
export type KeeperSource={
  material_id:string;
  material_version?:number|null;
  locator?:string|null;
  excerpt?:string|null;
};
export type KeeperMergeFrom={card_id:string;version?:number|null;title?:string|null};
export type KeeperGroupMerge={survivor?:unknown;donor?:unknown;merge_from?:KeeperMergeFrom|null;relations?:unknown;incoming?:unknown;outgoing?:unknown;[key:string]:unknown};
export type KeeperChange={
  kind:KeeperKind;
  action:KeeperAction|string;
  id?:string|null;
  expected_version?:number|null;
  before?:unknown;
  after?:unknown;
  reason?:string|null;
  sources?:KeeperSource[];
  /** Explicit donor reference for a card merge; the donor delete stays in the same group. */
  merge_from?:KeeperMergeFrom|null;
  /** Optional group-level merge metadata that names the same survivor and donor. */
  merge?:KeeperGroupMerge|null;
};
export type KeeperKeepFields={change_index:number;fields:string[]};
/** Owner-editable fields; only these can be kept at their current value. */
export const KEEPER_EDITABLE_FIELDS:Record<KeeperKind,string[]>={
  card:['title','content','aliases','recall_terms','relations','questionable'],
  theme:['judgement','conditions','related_cards','evidence'],
  frontier:['question','scope','hard_constraints','relations','status'],
};
export type KeeperApplied={kind:string;id:string;version:number;status?:string};
export type KeeperUndoFieldChange={
  field:string;
  approved?:unknown;
  current?:unknown;
  inverse?:unknown;
  before?:unknown;
  applied?:unknown;
  proposed?:unknown;
  conflict?:boolean;
};
export type KeeperUndoChange={
  kind:string;
  id:string;
  target_version:number;
  current_version:number;
  action?:string;
  approval_action?:string;
  before?:unknown;
  applied?:unknown;
  current?:unknown;
  proposed?:unknown;
  conflict?:boolean;
  /** Field-level inverse differences from the K9 backend preview targets. */
  field_changes?:KeeperUndoFieldChange[];
};
export type KeeperGroupUndoPreview={
  operation_id?:string;
  proposal_id?:string;
  revision?:number;
  can_undo?:boolean;
  code?:string|null;
  message?:string;
  preview_token?:string;
  expected_versions?:number[];
  changes?:KeeperUndoChange[];
};
/** Persisted whole-group undo receipt returned by keeper_open under proposal.undo. */
export type KeeperUndoReceipt={
  id?:string|null;
  revision?:number|null;
  status?:string|null;
  proposal_id?:string|null;
  operation_id?:string|null;
  applied?:KeeperApplied[];
  reason?:string|null;
  undone_at?:string|number|null;
  [key:string]:unknown;
};
export type KeeperDecision={
  error?:{code:string;message:string};
  operation_id:string;
  decision:KeeperDecisionChoice|KeeperRevisionDecision;
  proposal_id:string;
  revision:number;
  status:KeeperDecisionStatus;
  applied:KeeperApplied[];
  reason?:string|null;
  feedback?:string|null;
  review_id?:string|null;
  decided_at?:string|number|null;
};
export type KeeperRevisionNotes={
  previous_revision?:number|null;
  previous_changes?:KeeperChange[];
  feedback?:string|null;
  reason?:string|null;
  review_id?:string|null;
  child_id?:string|null;
  conflicts?:unknown;
};
export type KeeperAuditEvent={
  event_id?:string;
  kind?:string;
  action?:string;
  operation_id?:string;
  proposal_id?:string;
  revision?:number;
  [key:string]:unknown;
};
export type KeeperProposal={
  proposal_id:string;
  revision?:number|null;
  status?:string|null;
  request_id?:string|null;
  task_id?:string|null;
  term_expires_at?:number|null;
  task_version?:number|null;
  report_id?:string|null;
  report_version?:number|null;
  provenance?:string|null;
  review_reason?:string;
  child_id?:string|null;
  review_id?:string|null;
  changes?:KeeperChange[];
  decision?:KeeperDecisionChoice|KeeperDecision|null;
  revision_notes?:KeeperRevisionNotes|null;
  supersedes_proposal_id?:string;
  new_evidence_reason?:string;
  applied?:KeeperApplied[];
  merge?:KeeperGroupMerge|null;
  undo?:KeeperUndoReceipt|null;
  reason?:string|null;
  feedback?:string|null;
  decision_operation_id?:string|null;
  operation_id?:string|null;
  queued_at?:string|number|null;
  created_at?:string|number|null;
  decided_at?:string|number|null;
};

/** The persisted terminal card may carry either a full receipt or flat fields. */
export const keeperDecisionFromProposal=(proposal:KeeperProposal|null):KeeperDecision|null=>{
  if(!proposal)return null;
  const stored=proposal.decision;
  if(stored&&typeof stored==='object'){
    return {
      ...stored,
      applied:Array.isArray(stored.applied)&&stored.applied.length?stored.applied
        :(Array.isArray(proposal.applied)?proposal.applied:[]),
      reason:stored.reason??proposal.reason??null,
      feedback:stored.feedback??proposal.feedback??null,
      review_id:stored.review_id??proposal.review_id??null,
      decided_at:stored.decided_at??proposal.decided_at??null,
    };
  }
  if(proposal.status!=='approved'&&proposal.status!=='rejected')return null;
  const choice:KeeperDecisionChoice=stored==='approve'||stored==='reject'
    ?stored:(proposal.status==='approved'?'approve':'reject');
  return {
    operation_id:proposal.decision_operation_id||proposal.operation_id||'',
    decision:choice,
    proposal_id:proposal.proposal_id,
    revision:typeof proposal.revision==='number'?proposal.revision:1,
    status:proposal.status,
    applied:Array.isArray(proposal.applied)?proposal.applied:[],
    reason:proposal.reason??null,
    feedback:proposal.feedback??null,
    review_id:proposal.review_id??null,
    decided_at:proposal.decided_at??null,
  };
};
export type KeeperListEvent={operation_id?:string;proposals:KeeperProposal[]};

export const KEEPER_FEEDBACK_LIMIT=500;
export const keeperFeedbackByteLength=(value:string):number=>new TextEncoder().encode(value).length;

const KIND_LABEL:Record<KeeperKind,string>={card:'知识卡片',theme:'命题',frontier:'关注问题'};
const ACTION_LABEL:Record<string,string>={create:'新建',update:'更新',delete:'删除'};
const STATUS_LABEL:Record<string,string>={
  queued:'排队中',pending:'待确认',revising:'修订中',approved:'已批准',rejected:'已拒绝',
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
  previous_revision:'上一版修订',previous_changes:'上一版变更',feedback:'反馈',
  review_id:'审阅编号',child_id:'子会话',conflicts:'冲突',
};
const TERMINAL_STATUSES=new Set<string>(['approved','rejected','returned','cancelled']);

export const keeperKindLabel=(kind:string)=>KIND_LABEL[kind as KeeperKind]||kind;
export const keeperActionLabel=(action:string)=>ACTION_LABEL[action]||action;
export const keeperStatusLabel=(status?:string|null)=>status?(STATUS_LABEL[status]||status):'';
export const keeperTestBadge=(proposal:KeeperProposal):string|null=>{
  if(proposal.report_id)return '测试报道';
  if(proposal.request_id)return '测试请求';
  return proposal.provenance==='editor_test'?'测试提议':null;
};
/** In-progress and returned reviews can carry notes without publishing a revision. */
export const hasPublishedKeeperRevision=(proposal:KeeperProposal|null):boolean=>{
  if(!proposal)return false;
  const current=proposal.revision;
  const previous=proposal.revision_notes?.previous_revision;
  return typeof current==='number'&&Number.isInteger(current)&&current>0
    &&typeof previous==='number'&&Number.isInteger(previous)&&previous>0&&current>previous;
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
const changeRecord=(change:KeeperChange,side:'before'|'after'):Record<string,unknown>|null=>{
  const value=side==='before'?change.before:change.after;
  return isRecord(value)?value:null;
};
const asMergeFrom=(value:unknown):KeeperMergeFrom|null=>{
  if(!isRecord(value))return null;
  const card_id=typeof value.card_id==='string'?value.card_id:typeof value.id==='string'?value.id:'';
  if(!card_id)return null;
  return {
    card_id,
    version:typeof value.version==='number'?value.version:null,
    title:typeof value.title==='string'?value.title:null,
  };
};
const mergeMetadataOf=(proposal:KeeperProposal):KeeperGroupMerge[]=>{
  const result:KeeperGroupMerge[]=[];
  if(isRecord(proposal.merge))result.push(proposal.merge as KeeperGroupMerge);
  for(const change of proposal.changes||[])if(isRecord(change.merge))result.push(change.merge as KeeperGroupMerge);
  return result;
};
const metadataSurvivorId=(metadata:KeeperGroupMerge):string=>{
  const survivor=isRecord(metadata.survivor)?metadata.survivor:null;
  return (typeof metadata.survivor_id==='string'?metadata.survivor_id:'')
    ||(survivor&&typeof survivor.card_id==='string'?survivor.card_id:'')
    ||(survivor&&typeof survivor.id==='string'?survivor.id:'')
    ||(typeof metadata.card_id==='string'?metadata.card_id:'');
};
const metadataDonorRef=(metadata:KeeperGroupMerge):KeeperMergeFrom|null=>{
  const donor=isRecord(metadata.donor)?metadata.donor:null;
  const explicit=asMergeFrom(metadata.merge_from)||asMergeFrom(donor);
  if(explicit)return explicit;
  const id=typeof metadata.donor_id==='string'?metadata.donor_id:'';
  if(!id)return null;
  return {
    card_id:id,
    version:typeof metadata.donor_version==='number'?metadata.donor_version:null,
    title:typeof metadata.donor_title==='string'?metadata.donor_title:null,
  };
};
export type KeeperCardMerge={survivor:KeeperChange;donor:KeeperChange;donorRef:KeeperMergeFrom;metadata?:KeeperGroupMerge};
/**
 * A merge is only rendered for a card update paired with its donor delete in
 * the same group. The update may name the donor through merge_from, or a
 * matching group/proposal merge metadata block may name both cards.
 */
export const keeperCardMerges=(proposal:KeeperProposal):KeeperCardMerge[]=>{
  const changes=Array.isArray(proposal.changes)?proposal.changes:[];
  const result:KeeperCardMerge[]=[];
  const add=(survivor:KeeperChange,donorRef:KeeperMergeFrom,metadata?:KeeperGroupMerge)=>{
    const donor=changes.find(change=>change.kind==='card'
      &&change.action==='delete'&&change.id===donorRef.card_id);
    if(!donor)return;
    if(result.some(item=>item.survivor===survivor&&item.donor===donor))return;
    result.push({survivor,donor,donorRef,metadata});
  };
  for(const change of changes){
    if(change.kind!=='card'||change.action!=='update')continue;
    const ref=asMergeFrom(change.merge_from);
    if(ref)add(change,ref,isRecord(change.merge)?change.merge as KeeperGroupMerge:undefined);
  }
  for(const metadata of mergeMetadataOf(proposal)){
    const survivorId=metadataSurvivorId(metadata);
    const donorRef=metadataDonorRef(metadata);
    if(!survivorId||!donorRef)continue;
    const survivor=changes.find(change=>change.kind==='card'
      &&change.action==='update'&&change.id===survivorId);
    if(survivor)add(survivor,donorRef,metadata);
  }
  return result;
};
type MergeSourceView={material_id?:string;title?:string;material_version?:number;locator?:string;excerpt?:string;status?:string};
const sourceViews=(value:unknown):MergeSourceView[]=>{
  if(!Array.isArray(value))return [];
  return value.flatMap<MergeSourceView>(item=>{
    if(typeof item==='string')return [{locator:item}];
    if(!isRecord(item))return [];
    const source=isRecord(item.source)?item.source:item;
    return [{
      material_id:typeof source.material_id==='string'?source.material_id:undefined,
      title:typeof source.title==='string'?source.title:undefined,
      material_version:typeof source.material_version==='number'?source.material_version:undefined,
      locator:typeof source.locator==='string'?source.locator:undefined,
      excerpt:typeof source.excerpt==='string'?source.excerpt:undefined,
      status:typeof source.status==='string'?source.status:undefined,
    }];
  });
};
type RelationView={id:string;version?:number};
const relationViews=(value:unknown):RelationView[]=>{
  if(!Array.isArray(value))return [];
  return value.flatMap(item=>{
    if(typeof item==='string')return [{id:item}];
    if(!isRecord(item))return [];
    const id=typeof item.card_id==='string'?item.card_id:typeof item.id==='string'?item.id:'';
    if(!id)return [];
    return [{id,version:typeof item.version==='number'?item.version:undefined}];
  });
};
const relationName=(relation:RelationView):string=>relation.version===undefined
  ?relation.id:`${relation.id}（v${relation.version}）`;
const relationValue=(metadata:KeeperGroupMerge|undefined,key:'outgoing'|'inbound'):unknown=>{
  if(!metadata)return undefined;
  const relations=isRecord(metadata.relations)?metadata.relations:null;
  if(relations&&relations[key]!==undefined)return relations[key];
  if(key==='inbound'){
    if(metadata.inbound!==undefined)return metadata.inbound;
    if(metadata.incoming!==undefined)return metadata.incoming;
  }else if(metadata.outgoing!==undefined)return metadata.outgoing;
  return undefined;
};
type RelationRedirect={kind:'card'|'theme';id:string;before:string[];after:string[]};
const relationIds=(value:unknown):string[]=>relationViews(value).map(relation=>relation.id);
/** Inbound redirects are read from the same checked group changes, never guessed. */
const inboundRedirects=(changes:KeeperChange[],survivorId:string,donorId:string):RelationRedirect[]=>{
  const result:RelationRedirect[]=[];
  const consider=(change:KeeperChange,field:'relations'|'related_cards')=>{
    if(!change.id||change.action!=='update')return;
    const before=relationIds(changeRecord(change,'before')?.[field]);
    const after=relationIds(changeRecord(change,'after')?.[field]);
    if(!before.includes(donorId)||!after.includes(survivorId))return;
    result.push({kind:change.kind==='theme'?'theme':'card',id:change.id,before,after});
  };
  for(const change of changes){
    if(change.id===survivorId)continue;
    if(change.kind==='card')consider(change,'relations');
    if(change.kind==='theme')consider(change,'related_cards');
  }
  return result;
};
type ThemeEvidenceView={stance:'supports'|'opposes';reason:string;source:MergeSourceView};
type ThemeEvidenceSnapshotView={supports:ThemeEvidenceView[];opposes:ThemeEvidenceView[]};
const evidenceItem=(value:unknown,stance:'supports'|'opposes'):ThemeEvidenceView|null=>{
  if(!isRecord(value))return null;
  const source=isRecord(value.source)?value.source
    :isRecord(value.snapshot)?value.snapshot:isRecord(value.evidence)?value.evidence:value;
  const reason=typeof value.reason==='string'?value.reason
    :typeof value.rationale==='string'?value.rationale
    :typeof value.note==='string'?value.note:'';
  const view:ThemeEvidenceView={
    stance,
    reason,
    source:{
      material_id:typeof source.material_id==='string'?source.material_id:undefined,
      title:typeof source.title==='string'?source.title:undefined,
      material_version:typeof source.material_version==='number'?source.material_version:undefined,
      locator:typeof source.locator==='string'?source.locator:undefined,
      excerpt:typeof source.excerpt==='string'?source.excerpt:undefined,
      status:typeof source.status==='string'?source.status:undefined,
    },
  };
  return view.reason||view.source.material_id||view.source.locator||view.source.excerpt?view:null;
};
const evidenceGroup=(value:unknown,stance:'supports'|'opposes'):ThemeEvidenceView[]=>{
  if(Array.isArray(value))return value.flatMap(item=>{
    const view=evidenceItem(item,stance);
    return view?[view]:[];
  });
  if(isRecord(value)){
    for(const key of ['items','evidence','sources']){
      const nested=value[key];
      if(Array.isArray(nested))return evidenceGroup(nested,stance);
    }
    const view=evidenceItem(value,stance);
    return view?[view]:[];
  }
  return [];
};
/** Accepts canonical Theme evidence arrays and normalized supports/opposes snapshots. */
const themeEvidenceSnapshot=(value:unknown):ThemeEvidenceSnapshotView=>{
  const snapshot=isRecord(value)?value:null;
  if(!snapshot)return {supports:[],opposes:[]};
  const evidence=snapshot.evidence??snapshot.evidence_snapshot??snapshot;
  if(Array.isArray(evidence)){
    const supports:ThemeEvidenceView[]=[];
    const opposes:ThemeEvidenceView[]=[];
    for(const item of evidence){
      if(!isRecord(item))continue;
      const stance=item.stance==='opposes'||item.side==='opposes'||item.direction==='opposes'
        ?'opposes':'supports';
      const view=evidenceItem(item,stance);
      if(view)(stance==='opposes'?opposes:supports).push(view);
    }
    return {supports,opposes};
  }
  if(isRecord(evidence)){
    return {
      supports:evidenceGroup(evidence.supports??evidence.support??evidence.supporting,'supports'),
      opposes:evidenceGroup(evidence.opposes??evidence.oppose??evidence.opposing,'opposes'),
    };
  }
  return {supports:[],opposes:[]};
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

function MergeSourceList({title,sources}:{title:string;sources:MergeSourceView[]}){
  return <div className="keeper-merge-source-list">
    <h5>{title}</h5>
    {sources.length?<ul>
      {sources.map((source,index)=><li key={`${source.material_id||source.title||'source'}:${index}`}>
        <p className="keeper-source-pin">
          {source.material_id?`资料 ${source.material_id}`:source.title||'来源未记录'}
          {typeof source.material_version==='number'?` · 固定版本 v${source.material_version}`:''}
          {source.status==='removed'?' · 原资料已永久移除':''}
        </p>
        {source.locator&&<p className="keeper-source-locator">定位：{source.locator}</p>}
        {source.excerpt&&<blockquote>{source.excerpt}</blockquote>}
      </li>)}
    </ul>:<p className="keeper-value-empty">未记录</p>}
  </div>;
}

function RelationChangeView({label,value,missingNote}:{label:string;value:unknown;missingNote:string}){
  const record=isRecord(value)?value:null;
  if(!record)return <section className="keeper-relation-change">
    <h5>{label}</h5>
    <p className="keeper-value-empty">{missingNote}</p>
  </section>;
  const before=relationViews(record.before);
  const after=relationViews(record.after);
  const explicitAdded=relationViews(record.added);
  const explicitRemoved=relationViews(record.removed);
  const hasPair='before' in record||'after' in record||before.length>0||after.length>0;
  if(!hasPair&&!explicitAdded.length&&!explicitRemoved.length)return <section className="keeper-relation-change">
    <h5>{label}</h5>
    <p className="keeper-value-empty">预检未提供{label}变化。</p>
  </section>;
  const added=explicitAdded.length?explicitAdded:after.filter(item=>!before.some(current=>current.id===item.id));
  const removed=explicitRemoved.length?explicitRemoved:before.filter(item=>!after.some(current=>current.id===item.id));
  return <section className="keeper-relation-change">
    <h5>{label}</h5>
    {hasPair&&<>
      <p><span>变更前：</span>{before.map(relationName).join('、')||'无'}</p>
      <p><span>变更后：</span>{after.map(relationName).join('、')||'无'}</p>
    </>}
    <p><span>新增：</span>{added.map(relationName).join('、')||'无'}</p>
    <p><span>移除：</span>{removed.map(relationName).join('、')||'无'}</p>
  </section>;
}

function RelationRedirectView({redirects}:{redirects:RelationRedirect[]}){
  return <section className="keeper-relation-change">
    <h5>入向关系</h5>
    <ul className="keeper-redirects">
      {redirects.map((redirect,index)=><li key={`${redirect.kind}:${redirect.id}:${index}`}>
        <p className="keeper-redirect-head"><strong>{keeperKindLabel(redirect.kind)}</strong> · {redirect.id}</p>
        <p><span>变更前：</span>{redirect.before.join('、')||'无'}</p>
        <p><span>变更后：</span>{redirect.after.join('、')||'无'}</p>
      </li>)}
    </ul>
  </section>;
}

function MergeSection({merges,changes}:{merges:KeeperCardMerge[];changes:KeeperChange[]}){
  if(!merges.length)return null;
  return <section className="keeper-merge" aria-label="合并知识卡片">
    <h3>合并知识卡片</h3>
    <p>本组包含卡片合并：保留卡片接收捐赠卡片的正文与来源，捐赠卡片在同一整组中删除。以下只展开提议携带的已校验快照，不在客户端推断合并结果。</p>
    {merges.map(({survivor,donor,donorRef,metadata})=>{
      const survivorBefore=changeRecord(survivor,'before');
      const survivorAfter=changeRecord(survivor,'after');
      const donorBefore=changeRecord(donor,'before');
      const survivorTitle=recordTitle(survivorAfter)||recordTitle(survivorBefore)||survivor.id||'未命名保留卡片';
      const donorTitle=donorRef.title||recordTitle(donorBefore)||donorRef.card_id;
      const donorVersion=typeof donorRef.version==='number'?donorRef.version:donor.expected_version;
      const outgoing=relationValue(metadata,'outgoing')??{
        before:survivorBefore?.relations,
        after:survivorAfter?.relations,
      };
      const inbound=relationValue(metadata,'inbound');
      const redirects=inboundRedirects(changes,survivor.id||'',donorRef.card_id);
      const donorRelations=relationViews(donorBefore?.relations);
      return <article className="keeper-merge-card" key={`${survivor.id||'survivor'}:${donorRef.card_id}`}>
        <header className="keeper-change-head">
          <span className="keeper-action">合并</span>
          <strong>{survivorTitle} ← {donorTitle}</strong>
        </header>
        <dl className="keeper-merge-meta">
          <dt>保留卡片</dt>
          <dd>{survivor.id||'未命名'} · {survivorTitle} · {typeof survivor.expected_version==='number'?`校验 v${survivor.expected_version}`:'缺少固定版本'}</dd>
          <dt>并入卡片</dt>
          <dd>{donorRef.card_id} · {donorTitle} · {typeof donorVersion==='number'?`校验 v${donorVersion}`:'缺少固定版本'} · 同组删除</dd>
        </dl>
        <div className="keeper-merge-pair">
          <section className="keeper-merge-side">
            <h4>保留正文 · 合并前</h4>
            <ValueText value={survivorBefore?.content}/>
          </section>
          <section className="keeper-merge-side">
            <h4>保留正文 · 合并后</h4>
            <ValueText value={survivorAfter?.content}/>
          </section>
        </div>
        <section className="keeper-merge-sources">
          <h4>来源证据</h4>
          <div className="keeper-merge-pair">
            <MergeSourceList title="保留卡片 · 合并前" sources={sourceViews(survivorBefore?.sources)}/>
            <MergeSourceList title="保留卡片 · 合并后" sources={sourceViews(survivorAfter?.sources)}/>
          </div>
          <MergeSourceList title="并入卡片来源" sources={sourceViews(donorBefore?.sources)}/>
        </section>
        <div className="keeper-merge-relations">
          <div className="keeper-relation-stack">
            <RelationChangeView label="出向关系" value={outgoing} missingNote="没有记录出向关系。"/>
            {donorRelations.length>0&&<p className="keeper-relation-note">并入卡片原有关联：{donorRelations.map(relationName).join('、')}</p>}
          </div>
          {redirects.length?<RelationRedirectView redirects={redirects}/>
            :<RelationChangeView label="入向关系" value={inbound} missingNote="没有入向关系需要调整。"/>}
        </div>
      </article>;
    })}
  </section>;
}

function ThemeEvidenceSide({title,items}:{title:string;items:ThemeEvidenceView[]}){
  return <section className="keeper-theme-side">
    <h5>{title} · {items.length} 条</h5>
    {items.length?<ul>
      {items.map((item,index)=><li key={`${item.source.material_id||'source'}:${index}`}>
        <p className="keeper-source-pin">
          {item.source.title||(item.source.material_id?`资料 ${item.source.material_id}`:'来源未记录')}
          {typeof item.source.material_version==='number'?` · 固定版本 v${item.source.material_version}`:''}
        </p>
        {item.reason&&<p className="keeper-theme-reason">理由：{item.reason}</p>}
        {item.source.locator&&<p className="keeper-source-locator">定位：{item.source.locator}</p>}
        {item.source.excerpt&&<blockquote>{item.source.excerpt}</blockquote>}
      </li>)}
    </ul>:<p className="keeper-value-empty">无</p>}
  </section>;
}

function ThemeEvidenceSnapshot({value}:{value:unknown}){
  const snapshot=themeEvidenceSnapshot(value);
  if(!snapshot.supports.length&&!snapshot.opposes.length)return null;
  return <section className="keeper-theme-evidence" aria-label="命题证据快照">
    <h4>命题证据快照</h4>
    <div className="keeper-theme-evidence-sides">
      <ThemeEvidenceSide title="支持" items={snapshot.supports}/>
      <ThemeEvidenceSide title="反对" items={snapshot.opposes}/>
    </div>
  </section>;
}

function UndoReceipt({receipt}:{receipt:KeeperUndoReceipt}){
  const applied=Array.isArray(receipt.applied)?receipt.applied:[];
  return <section className="keeper-receipt keeper-undo-receipt" aria-label="整组撤销回执">
    <h3>整组撤销已生效</h3>
    <dl>
      <dt>撤销编号</dt><dd>{receipt.id||receipt.operation_id||'未记录'}</dd>
      <dt>状态</dt><dd>{receipt.status==='undone'?'已撤销':receipt.status||'已撤销'}</dd>
      <dt>提议修订</dt><dd>{typeof receipt.revision==='number'?`v${receipt.revision}`:'未记录'}</dd>
      {receipt.reason&&<><dt>撤销理由</dt><dd>{receipt.reason}</dd></>}
      {receipt.undone_at!==null&&receipt.undone_at!==undefined&&<><dt>撤销时间</dt><dd>{timeLabel(receipt.undone_at)}</dd></>}
    </dl>
    <h4>已恢复对象</h4>
    {applied.length?<AppliedList applied={applied}/>:<p className="keeper-receipt-empty">撤销回执没有列出对象；请重新打开提议核对。</p>}
  </section>;
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
    {change.kind==='theme'&&<ThemeEvidenceSnapshot value={change.after??change.before}/>}
    {change.reason&&<p className="keeper-reason"><strong>理由：</strong>{change.reason}</p>}
    <SourceList sources={sources}/>
  </article>;
}

function Meta({proposal}:{proposal:KeeperProposal}){
  const rows:Array<[string,React.ReactNode]>=[['提议编号',proposal.proposal_id]];
  if(typeof proposal.revision==='number')rows.push(['修订',`v${proposal.revision}`]);
  const status=keeperStatusLabel(proposal.status);
  if(status)rows.push(['状态',status]);
  if(proposal.review_id)rows.push(['审阅编号',proposal.review_id]);
  if(proposal.request_id)rows.push(['测试请求',proposal.request_id]);
  if(proposal.task_id)rows.push(['任务',`${proposal.task_id}${typeof proposal.task_version==='number'?` · 固定版本 v${proposal.task_version}`:''}`]);
  if(proposal.report_id)rows.push(['测试报道',`${proposal.report_id}${typeof proposal.report_version==='number'?` · 固定版本 v${proposal.report_version}`:''}`]);
  if(proposal.child_id)rows.push(['子会话',proposal.child_id]);
  if(proposal.supersedes_proposal_id)rows.push(['此前拒绝的提议',proposal.supersedes_proposal_id]);
  if(proposal.new_evidence_reason)rows.push(['新证据说明',proposal.new_evidence_reason]);
  if(proposal.review_reason)rows.push(['Keeper 审核',proposal.review_reason]);
  if(proposal.provenance)rows.push(['来源',proposal.provenance==='editor_test'?'测试（editor_test）':proposal.provenance]);
  if(proposal.queued_at!==null&&proposal.queued_at!==undefined)rows.push(['入队时间',timeLabel(proposal.queued_at)]);
  if(proposal.created_at!==null&&proposal.created_at!==undefined)rows.push(['创建时间',timeLabel(proposal.created_at)]);
  return <section className="keeper-meta" aria-label="提议来源与固定信息">
    <dl>{rows.map(([label,value])=><React.Fragment key={label}>
      <dt>{label}</dt><dd>{value}</dd>
    </React.Fragment>)}</dl>
  </section>;
}

function DecisionTargets({changes}:{changes:KeeperChange[]}){
  return <ul className="keeper-decision-targets" aria-label="本组固定版本">
    {changes.map((change,index)=>{
      const created=change.action==='create';
      const version=created?null:change.expected_version;
      return <li key={`${change.kind}:${change.id||'new'}:${index}`}>
        <span className="keeper-decision-kind">{keeperKindLabel(change.kind)}</span>
        <span>{keeperActionLabel(change.action)}</span>
        <span className="keeper-decision-name">{changeHeadline(change)}</span>
        <span className="keeper-decision-version">{created?'新建 · 无固定版本':typeof version==='number'?`校验 v${version}`:'缺少固定版本'}</span>
      </li>;
    })}
  </ul>;
}

function AppliedList({applied}:{applied:KeeperApplied[]}){
  if(!applied.length)return <p className="keeper-receipt-empty">本次决定未写入知识库。</p>;
  return <ul className="keeper-applied" aria-label="已应用的知识对象">
    {applied.map((item,index)=><li key={`${item.kind}:${item.id}:${index}`}>
      <span>{keeperKindLabel(item.kind)}</span>
      <span className="keeper-applied-id">{item.id}</span>
      <span>版本 v{item.version}</span>
    </li>)}
  </ul>;
}

function TerminalReceipt({decision,proposal}:{decision:KeeperDecision|null;proposal:KeeperProposal}){
  const status=decision?.status||(proposal.status==='approved'||proposal.status==='rejected'||proposal.status==='cancelled'?proposal.status:null);
  const label=status==='approved'?'已批准整组':status==='rejected'?'已拒绝整组':status==='cancelled'?'已取消修订':'已处理';
  const reason=decision?.reason??proposal.reason??null;
  const feedback=decision?.feedback??proposal.feedback??null;
  const reviewId=decision?.review_id??proposal.review_id??null;
  const applied=Array.isArray(decision?.applied)?decision!.applied:[];
  const decisionLabel=decision?.decision==='approve'?'批准整组'
    :decision?.decision==='reject'?'拒绝'
    :decision?.decision==='adjust'?'请求调整'
    :decision?.decision==='cancel'?'取消修订'
    :label;
  const copy=status==='approved'?'该确认卡已批准，持久结果如下；其他窗口或重启后重新打开仍读取同一状态。'
    :status==='rejected'?'该确认卡已拒绝，未写入知识库；其他窗口或重启后重新打开仍读取同一状态。'
    :status==='cancelled'?'本次修订已取消，未写入知识库；结果已持久记录。'
    :'该确认卡已进入终态，只读显示持久结果。';
  return <section className={`keeper-receipt${status?` keeper-receipt-${status}`:''}`} aria-label="持久决定回执">
    <h3>{label}</h3>
    <p>{copy}</p>
    <dl>
      <dt>决定</dt><dd>{decisionLabel}</dd>
      <dt>状态</dt><dd>{keeperStatusLabel(status||undefined)||'已处理'}</dd>
      {decision?.operation_id&&<><dt>操作编号</dt><dd>{decision.operation_id}</dd></>}
      {typeof decision?.revision==='number'&&<><dt>提议修订</dt><dd>v{decision.revision}</dd></>}
      {reviewId&&<><dt>审阅编号</dt><dd>{reviewId}</dd></>}
      {decision?.decided_at!==null&&decision?.decided_at!==undefined&&<><dt>决定时间</dt><dd>{timeLabel(decision.decided_at)}</dd></>}
      {reason&&<><dt>决定理由</dt><dd>{reason}</dd></>}
      {feedback&&<><dt>调整反馈</dt><dd>{feedback}</dd></>}
    </dl>
    <h4>已应用对象</h4>
    {applied.length>0?<AppliedList applied={applied}/>:<p className="keeper-receipt-empty">本次决定未写入知识库。</p>}
  </section>;
}

function PriorProvenance({proposal,notes}:{proposal:KeeperProposal;notes:KeeperRevisionNotes|null}){
  const rows:Array<[string,React.ReactNode]>=[];
  if(proposal.request_id)rows.push(['测试请求',proposal.request_id]);
  if(proposal.task_id)rows.push(['任务',`${proposal.task_id}${typeof proposal.task_version==='number'?` · 固定版本 v${proposal.task_version}`:''}`]);
  if(proposal.report_id)rows.push(['测试报道',`${proposal.report_id}${typeof proposal.report_version==='number'?` · 固定版本 v${proposal.report_version}`:''}`]);
  if(proposal.child_id||notes?.child_id)rows.push(['子会话',proposal.child_id||notes?.child_id]);
  if(proposal.review_id||notes?.review_id)rows.push(['审阅编号',proposal.review_id||notes?.review_id]);
  if(proposal.review_reason)rows.push(['Keeper 审核',proposal.review_reason]);
  if(proposal.provenance)rows.push(['来源',proposal.provenance==='editor_test'?'测试（editor_test）':proposal.provenance]);
  if(proposal.supersedes_proposal_id)rows.push(['此前拒绝的提议',proposal.supersedes_proposal_id]);
  if(!rows.length)return null;
  return <div className="keeper-prior-provenance">
    <h4>此前父级与报道来源</h4>
    <dl>{rows.map(([label,value])=><React.Fragment key={label}>
      <dt>{label}</dt><dd>{value}</dd>
    </React.Fragment>)}</dl>
  </div>;
}

function ReturnedReceipt({proposal,notes}:{proposal:KeeperProposal;notes:KeeperRevisionNotes|null}){
  const reason=proposal.reason||notes?.reason||'';
  const feedback=proposal.feedback||notes?.feedback||'';
  const reviewId=proposal.review_id||notes?.review_id||'';
  return <section className="keeper-receipt keeper-receipt-returned" aria-label="持久退回回执">
    <h3>已退回</h3>
    <p>Keeper 已退回该提议，本次没有写入知识库。可根据下方反馈，在“要求调整”中重新提交调整要求。</p>
    <dl>
      <dt>状态</dt><dd>已退回</dd>
      {reason&&<><dt>退回原因</dt><dd>{reason}</dd></>}
      {feedback&&<><dt>Keeper 反馈</dt><dd>{feedback}</dd></>}
      {reviewId&&<><dt>审阅编号</dt><dd>{reviewId}</dd></>}
      {typeof proposal.revision==='number'&&<><dt>提议修订</dt><dd>v{proposal.revision}</dd></>}
    </dl>
    {!reason&&!feedback&&<p className="keeper-receipt-empty">该退回没有附带原因或反馈。</p>}
    <PriorProvenance proposal={proposal} notes={notes}/>
  </section>;
}

const baseChangeKey=(change:KeeperChange)=>`${change.kind}|${change.action}|${change.id||'new'}`;
const canonical=(value:unknown):unknown=>{
  if(Array.isArray(value))return value.map(canonical);
  if(isRecord(value)){
    const result:Record<string,unknown>={};
    for(const key of Object.keys(value).sort())result[key]=canonical(value[key]);
    return result;
  }
  return value;
};
const deepEqual=(left:unknown,right:unknown):boolean=>
  JSON.stringify(canonical(left))===JSON.stringify(canonical(right));
/**
 * Fields whose current value actually differs between before/after on an
 * update. Creates and deletes carry no existing value to keep, and fields
 * outside the owner-editable list are never offered or sent.
 */
export const keeperChangedFields=(change:KeeperChange):string[]=>{
  if(change.action!=='update'||change.merge_from)return [];
  const before=change.before;
  const after=change.after;
  if(!isRecord(before)||!isRecord(after))return [];
  const fields=KEEPER_EDITABLE_FIELDS[change.kind as KeeperKind];
  if(!fields)return [];
  return fields.filter(field=>!deepEqual(before[field],after[field]));
};
const changeFingerprint=(change:KeeperChange)=>JSON.stringify(canonical({
  kind:change.kind,action:change.action,id:change.id??null,
  expected_version:change.expected_version??null,before:change.before??null,
  after:change.after??null,reason:change.reason??null,sources:change.sources??null,
}));
type RevisionDiffKind='added'|'removed'|'changed'|'unchanged';
type RevisionDiffEntry={kind:RevisionDiffKind;key:string;previous?:KeeperChange;current?:KeeperChange};
const indexChanges=(changes:KeeperChange[])=>{
  const counts:Record<string,number>={};
  return changes.map(change=>{
    const base=baseChangeKey(change);
    const occurrence=counts[base]||0;
    counts[base]=occurrence+1;
    return {change,key:`${base}#${occurrence}`};
  });
};
const revisionDiff=(previous:KeeperChange[],current:KeeperChange[]):RevisionDiffEntry[]=>{
  const prior=indexChanges(previous);
  const next=indexChanges(current);
  const priorMap=new Map(prior.map(entry=>[entry.key,entry.change]));
  const used=new Set<string>();
  const entries:RevisionDiffEntry[]=[];
  for(const entry of next){
    const before=priorMap.get(entry.key);
    if(!before){
      entries.push({kind:'added',key:entry.key,current:entry.change});
      continue;
    }
    used.add(entry.key);
    entries.push({
      kind:changeFingerprint(before)===changeFingerprint(entry.change)?'unchanged':'changed',
      key:entry.key,previous:before,current:entry.change,
    });
  }
  for(const entry of prior)if(!used.has(entry.key))entries.push({kind:'removed',key:entry.key,previous:entry.change});
  return entries;
};
const DIFF_LABEL:Record<RevisionDiffKind,string>={
  added:'新增',removed:'移除',changed:'调整',unchanged:'未变',
};

function DiffSide({title,change}:{title:string;change:KeeperChange}){
  return <section className="keeper-revision-side">
    <h5>{title}</h5>
    <div className="keeper-revision-line"><span>变更前</span><ValueText value={change.before}/></div>
    <div className="keeper-revision-line"><span>变更后</span><ValueText value={change.after}/></div>
    {change.reason&&<p className="keeper-revision-reason"><span>理由</span>{change.reason}</p>}
  </section>;
}

function RevisionDiffView({proposal,notes}:{proposal:KeeperProposal;notes:KeeperRevisionNotes}){
  const hasPrevious=Array.isArray(notes.previous_changes);
  const previous=hasPrevious?notes.previous_changes as KeeperChange[]:[];
  const current=Array.isArray(proposal.changes)?proposal.changes:[];
  const entries=revisionDiff(previous,current);
  const visible=entries.filter(entry=>entry.kind!=='unchanged');
  const unchanged=entries.length-visible.length;
  const previousRevision=typeof notes.previous_revision==='number'?notes.previous_revision:null;
  const currentRevision=typeof proposal.revision==='number'?proposal.revision:null;
  return <div className="keeper-revision-diff-block">
    <h4>上一版与本次提议差异</h4>
    {!hasPrevious?<p className="keeper-revision-unchanged">上一版变更快照未提供；请直接核对下方本次提议。</p>
      :visible.length===0?<p className="keeper-revision-unchanged">上一版与本次提议内容一致；仍需对本次修订明确批准。</p>
      :<ul className="keeper-revision-diff">
        {visible.map(entry=>{
          const change=entry.current||entry.previous!;
          return <li key={entry.key} className={`keeper-revision-entry keeper-revision-${entry.kind}`}>
            <div className="keeper-revision-entry-head">
              <span className={`keeper-revision-badge keeper-revision-badge-${entry.kind}`}>{DIFF_LABEL[entry.kind]}</span>
              <span>{keeperKindLabel(change.kind)}</span>
              <span>{keeperActionLabel(change.action)}</span>
              <strong>{changeHeadline(change)}</strong>
            </div>
            <div className="keeper-revision-pair">
              {entry.previous&&<DiffSide title={`上一版${previousRevision!==null?` v${previousRevision}`:''}`} change={entry.previous}/>}
              {entry.current&&<DiffSide title={`本次${currentRevision!==null?` v${currentRevision}`:''}`} change={entry.current}/>}
            </div>
          </li>;
        })}
      </ul>}
    {unchanged>0&&<p className="keeper-revision-unchanged">其余 {unchanged} 项与上一版一致，未在此重复。</p>}
  </div>;
}

function RevisionNotesSection({proposal,notes}:{proposal:KeeperProposal;notes:KeeperRevisionNotes}){
  const previousRevision=typeof notes.previous_revision==='number'?notes.previous_revision:null;
  const currentRevision=typeof proposal.revision==='number'?proposal.revision:null;
  const feedback=notes.feedback||proposal.feedback||'';
  const reason=notes.reason||proposal.reason||'';
  const reviewId=notes.review_id||proposal.review_id||'';
  const childId=notes.child_id||proposal.child_id||'';
  return <section className="keeper-revision-notes" aria-label="本次修订说明">
    <header className="keeper-revision-notes-head">
      <span className="keeper-badge keeper-badge-revision">修订版本</span>
      <h3>Keeper 已生成新的提议修订{currentRevision!==null?` v${currentRevision}`:''}</h3>
    </header>
    <p>上一版为{previousRevision!==null?` v${previousRevision}`:'上一版'}，本次为{currentRevision!==null?` v${currentRevision}`:'当前修订'}。以下差异只比较两版提议本身，不代表已写入知识库；请重新核对后再明确批准本次修订。</p>
    <dl className="keeper-revision-meta">
      {feedback&&<><dt>调整反馈</dt><dd>{feedback}</dd></>}
      {reason&&<><dt>修订原因</dt><dd>{reason}</dd></>}
      {reviewId&&<><dt>审阅编号</dt><dd>{reviewId}</dd></>}
      {childId&&<><dt>子会话</dt><dd>{childId}</dd></>}
    </dl>
    {notes.conflicts!==null&&notes.conflicts!==undefined&&<div className="keeper-revision-conflicts">
      <h4>修订冲突</h4>
      <ValueText value={notes.conflicts}/>
    </div>}
    <RevisionDiffView proposal={proposal} notes={notes}/>
  </section>;
}

/**
 * Keeper confirmation card. Pending cards expose one whole-group decision with
 * an optional reason and a bounded adjustment request; revising cards only
 * permit cancelling that exact revision; approved, rejected, returned and
 * cancelled cards are read-only persisted results. The panel never creates
 * Keeper requests or edits owners.
 */
export function KeeperProposalPanel({
  proposalId,proposal,loading,error,onRetry,onClose,
  deciding=false,decidingAction=null,decisionError='',decisionNotice='',receipt=null,onDecide,onRevise,
  onUndoCommand,onUndoApplied,
}:{
  proposalId:string;
  proposal:KeeperProposal|null;
  loading:boolean;
  error:string;
  onRetry:()=>void;
  onClose:()=>void;
  deciding?:boolean;
  decidingAction?:KeeperDecisionChoice|KeeperRevisionDecision|null;
  decisionError?:string;
  decisionNotice?:string;
  receipt?:KeeperDecision|null;
  onDecide?:(decision:KeeperDecisionChoice,reason:string)=>void;
  onRevise?:(decision:KeeperRevisionDecision,feedback:string,omitChanges:number[],keepCurrentFields:KeeperKeepFields[])=>void;
  onUndoCommand?:(command:unknown)=>void;
  onUndoApplied?:(receipt:KeeperUndoReceipt)=>void;
}){
  const heading=useRef<HTMLHeadingElement>(null);
  const [reason,setReason]=useState('');
  const [feedback,setFeedback]=useState('');
  const [omit,setOmit]=useState<Set<number>>(()=>new Set());
  const [keep,setKeep]=useState<Set<string>>(()=>new Set());
  useEffect(()=>{heading.current?.focus();},[]);
  useEffect(()=>{
    setReason('');
    setFeedback('');
    setOmit(new Set());
    setKeep(new Set());
  },[proposalId,proposal?.revision]);
  const changes=proposal&&Array.isArray(proposal.changes)?proposal.changes:[];
  const merges=proposal?keeperCardMerges(proposal):[];
  const groups:{kind:string;changes:KeeperChange[]}[]=[];
  changes.forEach(change=>{
    let group=groups.find(item=>item.kind===change.kind);
    if(!group){group={kind:change.kind,changes:[]};groups.push(group);}
    group.changes.push(change);
  });
  const effectiveDecision=receipt||keeperDecisionFromProposal(proposal);
  const [elapsed,setElapsed]=useState(false);
  useEffect(()=>{
    setElapsed(false);
    if(proposal?.status!=='pending'||!proposal.term_expires_at)return;
    const delay=proposal.term_expires_at*1000-Date.now();
    if(delay<=0){setElapsed(true);return;}
    const timer=window.setTimeout(()=>setElapsed(true),delay);
    return ()=>window.clearTimeout(timer);
  },[proposalId,proposal?.status,proposal?.term_expires_at]);
  const rawStatus=elapsed&&proposal?.status==='pending'?'expired':proposal?.status||'';
  const effectiveStatus=effectiveDecision?.status||rawStatus;
  const terminal=TERMINAL_STATUSES.has(effectiveStatus);
  const revising=!terminal&&(effectiveStatus==='revising'||rawStatus==='revising');
  const actionable=!!proposal&&!terminal&&!revising&&rawStatus==='pending'&&changes.length>0;
  const revisionValid=!!proposal&&typeof proposal.revision==='number'
    &&Number.isInteger(proposal.revision)&&proposal.revision>0;
  const versionsValid=changes.every(change=>change.action==='create'
    ?change.expected_version===null||change.expected_version===undefined
    :typeof change.expected_version==='number'&&Number.isInteger(change.expected_version)
      &&change.expected_version>0);
  const canDecide=actionable&&revisionValid&&versionsValid&&!loading&&!deciding&&!!onDecide;
  const adjustable=!!proposal&&changes.length>0&&!loading
    &&(effectiveStatus==='pending'||effectiveStatus==='returned');
  const canAdjust=adjustable&&revisionValid&&versionsValid&&!deciding&&!!onRevise;
  const cancellableStatus=effectiveStatus==='pending'||effectiveStatus==='returned'||effectiveStatus==='revising';
  const canCancel=cancellableStatus&&!!proposal&&revisionValid
    &&!loading&&!deciding&&!!onRevise;
  const status=effectiveStatus==='expired'?'已过期':keeperStatusLabel(effectiveStatus);
  const testBadge=proposal?keeperTestBadge(proposal):null;
  const headline=proposal?keeperProposalTitle(proposal):proposalId;
  const revisionNotes=proposal&&proposal.revision_notes&&typeof proposal.revision_notes==='object'
    ?proposal.revision_notes:null;
  const publishedRevision=hasPublishedKeeperRevision(proposal);
  const revised=publishedRevision&&proposal?.status==='pending';
  const revisionLabel=proposal&&typeof proposal.revision==='number'?` v${proposal.revision}`:'';
  const feedbackText=feedback.trim();
  const feedbackBytes=keeperFeedbackByteLength(feedbackText);
  const feedbackTooLong=feedbackBytes>KEEPER_FEEDBACK_LIMIT;
  const keepCurrentFields:KeeperKeepFields[]=changes.flatMap((change,index)=>{
    if(omit.has(index))return [];
    const fields=keeperChangedFields(change).filter(field=>keep.has(`${index}:${field}`));
    return fields.length?[{change_index:index,fields}]:[];
  });
  const hasSelection=omit.size>0||keepCurrentFields.length>0;
  const omitsEveryChange=omit.size>0&&omit.size===changes.length;
  const busyAction=(choice:KeeperDecisionChoice|KeeperRevisionDecision)=>deciding&&decidingAction===choice;
  const decide=(choice:KeeperDecisionChoice)=>{
    if(!canDecide)return;
    onDecide?.(choice,reason);
  };
  const revise=(choice:KeeperRevisionDecision)=>{
    if(choice==='adjust'){
      if(!canAdjust||!feedbackText||feedbackTooLong||omitsEveryChange)return;
      const ordered=Array.from(omit).sort((left,right)=>left-right);
      onRevise?.(choice,feedbackText,ordered,keepCurrentFields);
      return;
    }
    if(!canCancel)return;
    onRevise?.(choice,'',[],[]);
  };
  const toggleOmit=(index:number)=>{
    setOmit(current=>{
      const next=new Set(current);
      if(next.has(index))next.delete(index);else next.add(index);
      return next;
    });
  };
  const toggleKeep=(index:number,field:string)=>{
    setKeep(current=>{
      const key=`${index}:${field}`;
      const next=new Set(current);
      if(next.has(key))next.delete(key);else next.add(key);
      return next;
    });
  };
  return <div className="keeper-proposal" aria-busy={loading||deciding}>
    <header className="keeper-proposal-head">
      <div>
        <span className="workbench-kind">{terminal?(effectiveStatus==='returned'?'Keeper 已退回':'Keeper 已处理'):revising?'Keeper 修订中':actionable?'Keeper 待确认':'Keeper 提议'}</span>
        <h2 ref={heading} tabIndex={-1}>{headline}</h2>
        <p>{terminal?(effectiveStatus==='returned'?'已退回确认卡片 · 可重新要求调整':'只读确认卡片 · 结果已持久记录')
          :revising?'只读确认卡片 · Keeper 正在修订'
          :actionable?'整组确认卡片 · 一次决定覆盖整组'
          :'等待完整提议'} · {proposal?`${changes.length} 项变更`:'等待完整提议'}</p>
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
    {proposal&&revisionNotes&&publishedRevision&&<RevisionNotesSection proposal={proposal} notes={revisionNotes}/>}
    {terminal&&proposal&&(effectiveStatus==='returned'
      ?<ReturnedReceipt proposal={proposal} notes={revisionNotes}/>
      :<TerminalReceipt decision={effectiveDecision} proposal={proposal}/>)}
    {proposal?.undo&&<UndoReceipt receipt={proposal.undo}/>}
    {terminal&&decisionNotice&&<div className="keeper-decision-notice" role="alert"><p>{decisionNotice}</p></div>}
    {revising&&proposal&&<section className="keeper-revision-progress" aria-label="Keeper 正在修订">
      <h3>Keeper 正在修订{revisionLabel}</h3>
      <p>修订完成前不能批准、拒绝或再次调整。你可以取消当前修订{revisionLabel}；取消只作用于这一修订，不会写入知识库。</p>
      <dl>
        {typeof proposal.revision==='number'&&<><dt>当前修订</dt><dd>v{proposal.revision}</dd></>}
        {(effectiveDecision?.review_id||proposal.review_id)&&<><dt>审阅编号</dt><dd>{effectiveDecision?.review_id||proposal.review_id}</dd></>}
        {proposal.child_id&&<><dt>子会话</dt><dd>{proposal.child_id}</dd></>}
      </dl>
      {decisionError&&<div className="keeper-decision-error" role="alert"><p>{decisionError}</p><p className="keeper-decision-note">取消失败；当前修订仍未确认取消，可重试。</p></div>}
      {decisionNotice&&<div className="keeper-decision-notice" role="alert"><p>{decisionNotice}</p></div>}
      <div className="keeper-revision-progress-actions">
        <button type="button" className="keeper-cancel-revision" disabled={!canCancel} onClick={()=>revise('cancel')}>
          {busyAction('cancel')?'正在取消…':'取消本次修订'}
        </button>
      </div>
      <p className="keeper-decision-note">只有当前修订明确处于修订中时才可取消；较旧的事件不会覆盖较新的修订。</p>
    </section>}
    {actionable&&proposal&&<section className="keeper-decision" aria-label="整组决定">
      <h3>{revised?`批准本次修订${revisionLabel}`:'整组决定'}</h3>
      <p>一次决定覆盖本组全部 {changes.length} 项变更。批准时按下列固定版本在同一事务中校验并写入；任一项失败则整组不落地。拒绝不会写入知识库。</p>
      {revised&&<div className="keeper-revision-approval" role="status">
        <strong>这是修订后的新提议。</strong> 批准仅针对本次修订{revisionLabel}；上一版的确认和输入不会沿用，请重新核对差异后明确批准。
      </div>}
      <DecisionTargets changes={changes}/>
      {(!revisionValid||!versionsValid)&&<p className="keeper-decision-warning" role="alert">该提议缺少可绑定的修订或固定版本，暂不能决定；请重新打开提议核对。</p>}
      <label className="keeper-decision-reason">决定理由（可选）
        <textarea value={reason} maxLength={500} disabled={deciding} onChange={event=>setReason(event.target.value)} placeholder="批准或拒绝这组变更的原因；再次打开时以持久回执为准"/>
      </label>
      {decisionError&&<div className="keeper-decision-error" role="alert"><p>{decisionError}</p><p className="keeper-decision-note">决定未确认成功；已填写的理由、调整说明和字段选择仍然保留，可重试。</p></div>}
      <div className="keeper-decision-actions">
        <button type="button" className="keeper-approve" disabled={!canDecide} onClick={()=>decide('approve')}>
          {busyAction('approve')?'正在提交…':revised?`批准本次修订${revisionLabel}`:'批准整组'}
        </button>
        <button type="button" className="keeper-reject" disabled={!canDecide} onClick={()=>decide('reject')}>
          {busyAction('reject')?'正在提交…':'拒绝'}
        </button>
      </div>
      <p className="keeper-decision-note">两个按钮都作用于整组，不存在逐项批准。</p>
    </section>}
    {adjustable&&proposal&&<section className="keeper-adjust" aria-label="要求调整">
      <h3>要求调整</h3>
      <p>说明会随复核交回原任务。勾选变更或字段可以修改方案。只填写说明时，Keeper 按原有变更复核。</p>
      {effectiveStatus==='returned'&&<div className="keeper-revision-approval" role="status">
        <strong>该提议已退回。</strong> 可以结合退回原因再次提交调整要求；重新提交不会写入知识库，只有批准新的待确认修订才会写入。
      </div>}
      <label className="keeper-adjust-feedback">调整说明（必填）
        <textarea value={feedback} disabled={deciding} onChange={event=>setFeedback(event.target.value)} placeholder="说明调整或重新复核的原因"/>
      </label>
      <p className={`keeper-feedback-count${feedbackTooLong?' keeper-feedback-over':''}`} aria-live="polite">
        {feedbackBytes} / {KEEPER_FEEDBACK_LIMIT} 字节{feedbackTooLong?' · 超出上限，暂不能提交':''}
      </p>
      <fieldset className="keeper-omit" disabled={deciding||merges.length>0}>
        <legend>本次整项移除的变更（可选）</legend>
        {changes.map((change,index)=><label className="keeper-omit-row" key={`${change.kind}:${change.id||'new'}:${index}`}>
          <input type="checkbox" checked={omit.has(index)} onChange={()=>toggleOmit(index)}/>
          <span>第 {index+1} 项 · {keeperKindLabel(change.kind)} · {keeperActionLabel(change.action)} · {changeHeadline(change)}</span>
        </label>)}
      </fieldset>
      {merges.length>0&&<p>合并和它的关系调整作为一组复核。</p>}
      <fieldset className="keeper-keep" disabled={deciding}>
        <legend>要求 Keeper 保留当前字段（可选）</legend>
        {changes.map((change,index)=>{
          const fields=keeperChangedFields(change);
          if(!fields.length)return null;
          const omitted=omit.has(index);
          return <div className={`keeper-keep-row${omitted?' keeper-keep-row-omitted':''}`} key={`keep:${change.kind}:${change.id||'new'}:${index}`}>
            <p className="keeper-keep-head">第 {index+1} 项 · {keeperKindLabel(change.kind)} · {changeHeadline(change)}</p>
            <div className="keeper-keep-fields">
              {fields.map(field=><label className="keeper-keep-field" key={field}>
                <input type="checkbox" checked={!omitted&&keep.has(`${index}:${field}`)} disabled={deciding||omitted||!canAdjust} onChange={()=>toggleKeep(index,field)}/>
                <span>{fieldLabel(field)}</span>
              </label>)}
            </div>
          </div>;
        })}
      </fieldset>
      {(!revisionValid||!versionsValid)&&<p className="keeper-decision-warning" role="alert">该提议缺少可绑定的修订或固定版本，暂不能要求调整；请刷新提议核对。</p>}
      {!actionable&&decisionError&&<div className="keeper-decision-error" role="alert"><p>{decisionError}</p><p className="keeper-decision-note">请求未确认完成；已填写的说明、整项移除和字段选择仍然保留，可刷新后重试。</p></div>}
      <div className="keeper-decision-actions">
        <button type="button" className="keeper-adjust-submit" disabled={!canAdjust||!feedbackText||feedbackTooLong||omitsEveryChange} onClick={()=>revise('adjust')}>
          {busyAction('adjust')?'正在提交…':hasSelection?'提交调整请求':'记录说明并重新复核'}
        </button>
        <button type="button" className="keeper-cancel-revision" disabled={!canCancel} onClick={()=>revise('cancel')}>
          {busyAction('cancel')?'正在取消…':'取消该提议'}
        </button>
      </div>
      {omitsEveryChange&&<p className="keeper-decision-note" role="status">如果想放弃全部变更，可以取消该提议。</p>}
    </section>}
    {merges.length>0&&<MergeSection merges={merges} changes={changes}/>}
    {proposal&&effectiveStatus==='approved'&&!proposal.undo&&onUndoCommand&&onUndoApplied
      &&<KeeperGroupUndoPanel proposal={proposal} onCommand={onUndoCommand} onUndone={onUndoApplied}/>}
    {groups.length?groups.map(group=><section className="keeper-group" key={group.kind} aria-label={`${keeperKindLabel(group.kind)}变更`}>
      <h3>{keeperKindLabel(group.kind)} · {group.changes.length} 项</h3>
      {group.changes.map((change,index)=><ChangeCard key={`${change.kind}:${change.id||'new'}:${index}`} change={change}/>)}
    </section>):proposal&&!loading&&!error?<div className="workbench-empty keeper-empty" role="status">
      <p>该提议没有返回变更详情。</p>
    </div>:null}
  </div>;
}
