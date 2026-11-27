import {useMasonry} from './ResultCards';
import {ModuleHeading} from './ModuleHeading';
import {DictionaryIcon} from './DictionaryIcon';
import {Button} from './gui/elements/Button';
import {TextInput} from './gui/elements/FormElements';
import {DictionarySearch} from './DictionarySearch';
import {FixedSelect} from './gui/elements/ChoiceElements';
import {TestTag} from './TestCleanupPanel';
import {createPortal} from 'react-dom';
import React,{useEffect,useMemo,useRef,useState} from 'react';
import {KeeperReviewRetry} from './KeeperReviewRetry';
import {EditorFeedbackPreview,type FeedbackHandoff} from './EditorFeedbackPreview';
import {KnowledgeCardPanel,type CardSummary,type KnowledgeCard,type CardViewEvent,type CardCommandError} from './KnowledgeCardPanel';
import {KnowledgePanel,type FrontierSummary,type Frontier,type FrontierViewEvent,type FrontierCommandError} from './KnowledgePanel';
import {ThemePanel,type ThemeSummary,type Theme,type ThemeViewEvent,type ThemeCommandError} from './ThemePanel';
import {KnowledgeHistoryPanel,type HistoryKind,type HistoryRecord} from './KnowledgeHistoryPanel';
import {KeeperProposalPanel,type KeeperProposal,type KeeperListEvent,type KeeperDecision,type KeeperDecisionChoice,type KeeperProposalStatus,type KeeperRevisionDecision,type KeeperKeepFields,type KeeperUndoReceipt,keeperKindLabel,keeperProposalTitle,keeperStatusLabel,keeperTestBadge,keeperDecisionFromProposal,keeperChangedFields,hasPublishedKeeperRevision,KEEPER_FEEDBACK_LIMIT,keeperFeedbackByteLength} from './KeeperProposalPanel';

type MaterialOption={material_id:string;title:string;version?:number};
type EntityType='card'|'theme'|'frontier';
type TypeFilter='all'|EntityType;
type ScopeFilter='active'|'inactive'|'pending';
type KeeperStatus=KeeperProposalStatus;
type KeeperSelection={proposal_id:string;revision:number|null;status:KeeperStatus;nonce:string};
type KeeperListState=Record<KeeperStatus,KeeperListEvent|null>;
type KeeperListRequests=Record<KeeperStatus,string|null>;
type KeeperDecisionWork={
  operation_id:string;
  proposal_id:string;
  revision:number;
  action:'keeper_decide'|'keeper_revise';
  decision:KeeperDecisionChoice|KeeperRevisionDecision;
  expectedStatus:'approved'|'rejected'|'revising'|'cancelled';
};
type ActiveSelection={type:EntityType;mode:'create'|'open';id?:string;version?:number;nonce:string;previousView?:string};
type PanelRequest={kind:'create';nonce:string}|{kind:'open';nonce:string;id:string;version:number};
type RelationTarget={kind:'card'|'theme';id:string};
type SearchResult={
  kind:EntityType;
  id:string;
  version:number;
  title:string;
  summary:string;
  status:string;
  questionable?:boolean;
  source_pending?:boolean;
};
type SearchPage={
  operation_id?:string;
  results:SearchResult[];
  total:number;
  offset:number;
  limit:number;
};
type SearchValues={query:string;type:TypeFilter;scope:ScopeFilter;relationTarget:string|string[]};
type SearchRequest={operation_id:string;values:SearchValues;offset:number};

const PAGE_SIZE=20;
const KEEPER_STATUSES:KeeperStatus[]=['pending','revising','returned','cancelled','approved','rejected'];
const KEEPER_STATUS_LABEL:Record<KeeperStatus,string>={
  pending:'待确认',revising:'修订中',returned:'已退回',cancelled:'已取消',approved:'已批准',rejected:'已拒绝',
};
function statusRecord<T>(value:T):Record<KeeperStatus,T>{
  return {pending:value,revising:value,returned:value,cancelled:value,approved:value,rejected:value};
}
const asKeeperStatus=(value:unknown):KeeperStatus|null=>typeof value==='string'
  &&(KEEPER_STATUSES as string[]).includes(value)?value as KeeperStatus:null;
const KEEPER_STATUS_RANK:Record<KeeperStatus,number>={
  pending:0,revising:1,returned:2,cancelled:2,approved:2,rejected:2,
};
const EMPTY_KEEPER_LISTS=statusRecord<KeeperListEvent|null>(null);
type KeeperTerminalEvent={
  proposal_id:string;
  revision:number|null;
  status:KeeperStatus|null;
  review_id:string|null;
  job_id:string|null;
  proposal_status:string|null;
  job_status:string|null;
};
/**
 * Terminal job results carry the proposal revision under proposal_revision and
 * the Keeper outcome under proposal_status; `revision` there is the task
 * revision the job ran from and must never be stored as the proposal revision.
 * Older revision events used flat `revision`/`status`, so accept both.
 */
const readKeeperTerminalEvent=(data:Record<string,unknown>|undefined|null):KeeperTerminalEvent|null=>{
  if(!data||typeof data!=='object')return null;
  const proposal_id=typeof data.proposal_id==='string'?data.proposal_id:'';
  if(!proposal_id)return null;
  const job_status=typeof data.status==='string'&&data.status?data.status:null;
  const explicitProposalStatus=typeof data.proposal_status==='string'&&data.proposal_status
    ?data.proposal_status:null;
  const proposal_status=explicitProposalStatus||job_status;
  /* A terminal result's `revision` is the task revision the job ran from;
     only legacy revision events without proposal_status expose the proposal
     revision under `revision`. */
  const revisionSource=typeof data.proposal_revision==='number'?data.proposal_revision
    :explicitProposalStatus?null:typeof data.revision==='number'?data.revision:null;
  const revision=typeof revisionSource==='number'&&Number.isInteger(revisionSource)&&revisionSource>0
    ?revisionSource:null;
  const review_id=typeof data.review_id==='string'&&data.review_id?data.review_id:null;
  const job_id=typeof data.job_id==='string'&&data.job_id?data.job_id:null;
  return {proposal_id,revision,status:asKeeperStatus(proposal_status),review_id,job_id,
    proposal_status:proposal_status??null,job_status};
};
/** Some terminal job events wrap the result; prefer the payload that names a proposal. */
const keeperEventData=(data:Record<string,unknown>|undefined|null):Record<string,unknown>|undefined=>{
  if(!data||typeof data!=='object')return undefined;
  if(typeof data.proposal_id==='string'&&data.proposal_id)return data;
  const result=(data as {result?:unknown}).result;
  return result&&typeof result==='object'&&!Array.isArray(result)
    ?result as Record<string,unknown>:data;
};
const keeperProposalReview=(proposal:KeeperProposal):string|null=>{
  if(typeof proposal.review_id==='string'&&proposal.review_id)return proposal.review_id;
  const notes=proposal.revision_notes;
  return notes&&typeof notes.review_id==='string'&&notes.review_id?notes.review_id:null;
};
const TYPE_LABEL:Record<EntityType,string>={card:'知识卡片',theme:'命题',frontier:'关注问题'};
const STATUS_LABEL:Record<string,string>={active:'有效',archived:'已归档',following:'关注中',paused:'已暂停',resolved:'已解决',deleted:'已删除'};
const truncate=(value:string,length=56)=>value.length>length?value.slice(0,length)+'…':value;
const proposalSearchText=(proposal:KeeperProposal)=>[
  proposal.proposal_id,proposal.status,proposal.reason,proposal.feedback,proposal.review_id,
  keeperDecisionFromProposal(proposal)?.status,keeperDecisionFromProposal(proposal)?.reason,
  proposal.revision_notes?.feedback,proposal.revision_notes?.reason,
  typeof proposal.decision==='string'?proposal.decision:proposal.decision?.decision,
  proposal.request_id,proposal.task_id,
  proposal.report_id,proposal.child_id,proposal.provenance,
  proposal.undo?.id,proposal.undo?.status,proposal.undo?.reason,
  ...(proposal.changes||[]).flatMap(change=>[
    change.kind,change.action,change.id,change.reason,
    JSON.stringify(change.before??''),JSON.stringify(change.after??''),
    ...(change.sources||[]).flatMap(source=>[source.material_id,source.locator,source.excerpt]),
  ]),
  ...(proposal.revision_notes?.previous_changes||[]).flatMap(change=>[
    change.kind,change.action,change.id,change.reason,
    JSON.stringify(change.before??''),JSON.stringify(change.after??''),
  ]),
].filter(Boolean).join(' ').toLowerCase();
const targetOf=(value:string|string[]):RelationTarget|null=>{
  if(Array.isArray(value))return null;
  const index=value.indexOf(':');
  if(index<1)return null;
  const kind=value.slice(0,index);
  const id=value.slice(index+1);
  return (kind==='card'||kind==='theme')&&id?{kind,id}:null;
};
const scopeStatus=(scope:ScopeFilter,row:SearchResult):string=>{
  if(scope==='pending')return '待确认';
  if(scope==='inactive'){
    if(row.kind==='frontier')return STATUS_LABEL[row.status]||row.status;
    return row.status==='archived'?'已归档':'已停用';
  }
  return STATUS_LABEL[row.status]||row.status;
};

/**
 * Integrated Knowledge Base workbench. The left pane searches the committed
 * records through the read-only knowledge_search seam; the right pane renders
 * the existing K1-K3 panels, so every edit, save and citation keeps using the
 * stable handlers instead of a second implementation.
 */
export function KnowledgeWorkbench({
  initialType='all',frontiers,cards,themes,materials,
  cardSelected,cardViewEvent,cardError,
  frontierSelected,frontierViewEvent,frontierError,
  themeSelected,themeViewEvent,themeError,
  onCommand,onRetrieve,onPendingCount,openPendingNonce=0,editorFeedback,closeEditorFeedback,termDetail,
}:{
  initialType?:TypeFilter;
  termDetail?:{host:HTMLDivElement|null;visible:boolean;open:()=>void;close:()=>void};
  frontiers:FrontierSummary[];
  cards:CardSummary[];
  themes:ThemeSummary[];
  materials:MaterialOption[];
  cardSelected:KnowledgeCard|null;
  cardViewEvent:CardViewEvent|null;
  cardError:CardCommandError|null;
  frontierSelected:Frontier|null;
  frontierViewEvent:FrontierViewEvent|null;
  frontierError:FrontierCommandError|null;
  themeSelected:Theme|null;
  themeViewEvent:ThemeViewEvent|null;
  themeError:ThemeCommandError|null;
  onCommand:(command:unknown)=>void;
  onRetrieve?:(frontier:Frontier)=>void;
  onPendingCount?:(count:number|null)=>void;
  openPendingNonce?:number;
  editorFeedback?:FeedbackHandoff|null;
  closeEditorFeedback?:()=>void;
}){
  const dictionary=Boolean(termDetail);
  const kindLabel=(kind:EntityType)=>dictionary&&kind==='card'?'术语':TYPE_LABEL[kind];
  const [query,setQuery]=useState('');
  const [type,setType]=useState<TypeFilter>(initialType);
  const [scope,setScope]=useState<ScopeFilter>('active');
  const [relationTarget,setRelationTarget]=useState<string|string[]>('');
  const [page,setPage]=useState(0);
  const [active,setActive]=useState<ActiveSelection|null>(null);
  const [historyOpen,setHistoryOpen]=useState(false);
  const [pageData,setPageData]=useState<SearchPage|null>(null);
  const [loading,setLoading]=useState(true);
  const [searchError,setSearchError]=useState('');
  const [keeperStatus,setKeeperStatus]=useState<KeeperStatus>('pending');
  const [keeperData,setKeeperData]=useState<KeeperListState>(EMPTY_KEEPER_LISTS);
  const [keeperLoading,setKeeperLoading]=useState(false);
  const [keeperError,setKeeperError]=useState('');
  const [pendingSelection,setPendingSelection]=useState<KeeperSelection|null>(null);
  const [pendingProposal,setPendingProposal]=useState<KeeperProposal|null>(null);
  const [pendingOpenLoading,setPendingOpenLoading]=useState(false);
  const [pendingOpenError,setPendingOpenError]=useState('');
  const [decisionPending,setDecisionPending]=useState<KeeperDecisionWork|null>(null);
  const [decisionError,setDecisionError]=useState('');
  const [decisionNotice,setDecisionNotice]=useState('');
  const [decisionReceipt,setDecisionReceipt]=useState<KeeperDecision|null>(null);
  const [decisionRefreshError,setDecisionRefreshError]=useState('');
  const results=useRef<HTMLUListElement>(null);
  const detailHeading=useRef<HTMLHeadingElement>(null);
  const historyButton=useRef<HTMLButtonElement>(null);
  const latestSearch=useRef<SearchRequest|null>(null);
  const focusAfterSearch=useRef(false);
  const pendingFocusAfterRefresh=useRef(false);
  const skipHeadingFocus=useRef(false);
  const pendingActive=useRef(false);
  const keeperStatusRef=useRef<KeeperStatus>('pending');
  const keeperRefreshQueued=useRef<Record<KeeperStatus,boolean>>(statusRecord(false));
  const keeperListPending=useRef<KeeperListRequests>(statusRecord<string|null>(null));
  const keeperDataRef=useRef<KeeperListState>(statusRecord<KeeperListEvent|null>(null));
  const latestRevisionRef=useRef<Record<string,number>>({});
  const latestStatusRef=useRef<Record<string,{revision:number;status:KeeperStatus}>>({});
  const reviewHistoryRef=useRef<Record<string,string[]>>({});
  const pendingOpenPending=useRef<{operation_id:string;proposal_id:string;keepProposal?:boolean}|null>(null);
  const decisionRefreshPending=useRef<{operation_id:string;proposal_id:string;revision:number;status:KeeperStatus}|null>(null);
  const decisionWork=useRef<KeeperDecisionWork|null>(null);
  const decisionReceiptRef=useRef<KeeperDecision|null>(null);
  const pendingSelectionRef=useRef<KeeperSelection|null>(null);
  const pendingProposalRef=useRef<KeeperProposal|null>(null);
  const pendingCountCallback=useRef(onPendingCount);
  pendingActive.current=scope==='pending';
  keeperStatusRef.current=keeperStatus;
  keeperDataRef.current=keeperData;
  pendingSelectionRef.current=pendingSelection;
  pendingProposalRef.current=pendingProposal;
  decisionWork.current=decisionPending;
  decisionReceiptRef.current=decisionReceipt;
  pendingCountCallback.current=onPendingCount;

  const rememberKeeperState=(
    proposal_id:string,revision:number|null,status:KeeperStatus|null,authoritative=false,
  )=>{
    if(!status)return;
    const prior=latestStatusRef.current[proposal_id];
    if(prior&&revision!==null&&!authoritative){
      if(revision<prior.revision)return;
      if(revision===prior.revision&&KEEPER_STATUS_RANK[status]<KEEPER_STATUS_RANK[prior.status])return;
    }
    latestStatusRef.current[proposal_id]={revision:revision??prior?.revision??0,status};
  };

  /**
   * Keeper jobs are correlated by review_id (or job_id). A newly seen job may
   * legitimately move a returned proposal at the same revision back to
   * revising; an event whose job was already superseded is late and must not
   * overwrite the newer local state. An authoritative open read resets the
   * history because the persisted proposal names the current job.
   */
  const noteReview=(proposal_id:string,candidate:string|null,{reset=false}:{reset?:boolean}={}):
    'unknown'|'new'|'current'|'stale'=>{
    if(!candidate)return 'unknown';
    const history=reviewHistoryRef.current[proposal_id]||[];
    if(reset){
      /* Keep older jobs so a late event from a superseded job is still
         recognisable; the read names the current job. */
      const rest=history.filter(item=>item!==candidate);
      reviewHistoryRef.current[proposal_id]=[...rest,candidate].slice(-20);
      return 'current';
    }
    const index=history.indexOf(candidate);
    if(index<0){
      reviewHistoryRef.current[proposal_id]=[...history,candidate].slice(-20);
      return history.length?'new':'current';
    }
    return index===history.length-1?'current':'stale';
  };

  const searchNow=(values:SearchValues,offset:number,background=false)=>{
    const target=targetOf(values.relationTarget);
    const operation_id=crypto.randomUUID();
    latestSearch.current={operation_id,values,offset};
    setLoading(true);
    if(!background){
      setPageData(null);
      setSearchError('');
    }
    onCommand({
      action:'knowledge_search',schema_version:1,operation_id,
      query:values.query,
      ...(values.type==='all'?{}:{kind:values.type}),
      scope:values.scope,
      ...(target?{related_to:target}:{}),
      ...(Array.isArray(values.relationTarget) && values.relationTarget.length ? {related_to:values.relationTarget.map(id=>({kind:'card',id}))} : {}),
      ...(dictionary ? {kind:'card',all_terms:true} : {offset,limit:PAGE_SIZE}),
    });
  };

  const requestKeeperList=(status:KeeperStatus,{silent=false}:{silent?:boolean}={})=>{
    if(keeperListPending.current[status]){
      keeperRefreshQueued.current[status]=true;
      return;
    }
    const operation_id=crypto.randomUUID();
    keeperListPending.current[status]=operation_id;
    if(!silent){
      setKeeperLoading(true);
      setKeeperError('');
    }
    onCommand({action:'keeper_list',schema_version:1,operation_id,status});
  };
  const openProposal=(proposal:KeeperProposal)=>{
    const operation_id=crypto.randomUUID();
    const revision=typeof proposal.revision==='number'?proposal.revision:null;
    pendingOpenPending.current={operation_id,proposal_id:proposal.proposal_id};
    decisionRefreshPending.current=null;
    rememberKeeperState(proposal.proposal_id,revision,asKeeperStatus(proposal.status));
    if(revision!==null)latestRevisionRef.current[proposal.proposal_id]=Math.max(
      latestRevisionRef.current[proposal.proposal_id]??-1,revision);
    setPendingSelection({proposal_id:proposal.proposal_id,revision,status:keeperStatus,nonce:crypto.randomUUID()});
    setPendingProposal(Array.isArray(proposal.changes)&&proposal.changes.length?proposal:null);
    setPendingOpenLoading(true);
    setPendingOpenError('');
    setDecisionError('');
    setDecisionNotice('');
    setDecisionReceipt(null);
    setDecisionRefreshError('');
    onCommand({action:'keeper_open',schema_version:1,operation_id,proposal_id:proposal.proposal_id});
  };
  const reopenSelectedProposal=(proposal_id:string,revision:number|null=null):boolean=>{
    const selected=pendingSelectionRef.current;
    if(!selected||selected.proposal_id!==proposal_id)return false;
    const visibleRevision=Math.max(
      typeof selected.revision==='number'?selected.revision:-1,
      typeof pendingProposalRef.current?.revision==='number'?pendingProposalRef.current.revision:-1);
    if(revision!==null&&visibleRevision>=0&&revision<visibleRevision)return false;
    const operation_id=crypto.randomUUID();
    pendingOpenPending.current={operation_id,proposal_id,keepProposal:true};
    setPendingOpenLoading(true);
    setPendingOpenError('');
    onCommand({action:'keeper_open',schema_version:1,operation_id,proposal_id});
    return true;
  };
  const retryPendingOpen=()=>{
    const selected=pendingSelection;
    if(!selected)return;
    const operation_id=crypto.randomUUID();
    pendingOpenPending.current={operation_id,proposal_id:selected.proposal_id};
    setPendingOpenLoading(true);
    setPendingOpenError('');
    onCommand({action:'keeper_open',schema_version:1,operation_id,proposal_id:selected.proposal_id});
  };
  const closePendingProposal=()=>{
    const proposal_id=pendingSelection?.proposal_id;
    pendingOpenPending.current=null;
    decisionRefreshPending.current=null;
    setPendingSelection(null);
    setPendingProposal(null);
    setPendingOpenLoading(false);
    setPendingOpenError('');
    setDecisionError('');
    setDecisionNotice('');
    setDecisionReceipt(null);
    setDecisionRefreshError('');
    window.setTimeout(()=>{
      const list=results.current;
      if(!list)return;
      const buttons=Array.from(list.querySelectorAll<HTMLButtonElement>('button[data-pending-result]'));
      (buttons.find(button=>button.dataset.proposalId===proposal_id)||buttons[0])?.focus();
    },0);
  };
  /** Keep the undo receipt separate from the original decision and reload it via keeper_open. */
  const groupUndoApplied=(receipt:KeeperUndoReceipt)=>{
    const proposal=pendingProposalRef.current;
    if(proposal&&(!receipt.proposal_id||proposal.proposal_id===receipt.proposal_id)){
      const next={...proposal,undo:receipt};
      pendingProposalRef.current=next;
      setPendingProposal(next);
    }
    const proposalId=receipt.proposal_id||proposal?.proposal_id||pendingSelectionRef.current?.proposal_id;
    if(proposalId)reopenSelectedProposal(proposalId);
    requestKeeperList('approved',{silent:true});
  };

  useEffect(()=>{
    const unsubscribe=window.horizon.subscribe(event=>{
      const latest=latestSearch.current;
      if(event.kind==='keeper_list'){
        const operation_id=event.data?.operation_id;
        const status=KEEPER_STATUSES.find(item=>keeperListPending.current[item]===operation_id);
        if(!status)return;
        keeperListPending.current[status]=null;
        const proposals:KeeperProposal[]=Array.isArray(event.data?.proposals)?event.data.proposals:[];
        const nextData={...keeperDataRef.current,[status]:{operation_id,proposals}};
        keeperDataRef.current=nextData;
        setKeeperData(nextData);
        const active=keeperStatusRef.current===status;
        if(active){
          setKeeperLoading(false);
          setKeeperError('');
        }
        if(status==='pending')pendingCountCallback.current?.(proposals.length);
        if(active&&pendingFocusAfterRefresh.current){
          pendingFocusAfterRefresh.current=false;
          if(proposals.length>0){
            window.setTimeout(()=>results.current?.querySelector<HTMLButtonElement>('button[data-pending-result]')?.focus(),0);
          }
        }
        if(keeperRefreshQueued.current[status]){
          keeperRefreshQueued.current[status]=false;
          requestKeeperList(status,{silent:!active});
        }
        return;
      }
      if(event.kind==='keeper_open'){
        const refresh=decisionRefreshPending.current;
        if(refresh&&event.data?.operation_id===refresh.operation_id){
          decisionRefreshPending.current=null;
          const proposal=event.data.proposal as KeeperProposal|undefined;
          const selected=pendingSelectionRef.current;
          if(!proposal||typeof proposal!=='object'||proposal.proposal_id!==refresh.proposal_id){
            setDecisionRefreshError('决定已提交，但重新读取持久状态不完整；可以重试打开该提议。');
            return;
          }
          if(!selected||selected.proposal_id!==refresh.proposal_id){
            setDecisionRefreshError('');
            return;
          }
          setPendingProposal(proposal);
          pendingProposalRef.current=proposal;
          const refreshReview=keeperProposalReview(proposal);
          const knownRevision=latestRevisionRef.current[proposal.proposal_id]??-1;
          const refreshedRevision=typeof proposal.revision==='number'?proposal.revision:null;
          if(refreshedRevision!==null&&refreshedRevision<knownRevision){
            setDecisionRefreshError('决定已提交，但读取的持久状态落后于已知修订；可以重试打开该提议。');
            return;
          }
          if(refreshReview&&(refreshedRevision===null||refreshedRevision>=knownRevision)){
            noteReview(proposal.proposal_id,refreshReview,{reset:true});
          }
          if(refreshedRevision!==null)latestRevisionRef.current[proposal.proposal_id]=Math.max(
            knownRevision,refreshedRevision);
          const refreshStatus=asKeeperStatus(proposal.status);
          rememberKeeperState(proposal.proposal_id,
            typeof proposal.revision==='number'?proposal.revision:null,refreshStatus,true);
          setPendingSelection(current=>current&&current.proposal_id===proposal.proposal_id
            ?{
              ...current,
              revision:typeof proposal.revision==='number'?proposal.revision:current.revision,
              status:refreshStatus||current.status,
            }
            :current);
          if(proposal.status===refresh.status){
            setDecisionRefreshError('');
          }else{
            setDecisionRefreshError('决定回执已返回，但持久状态与回执不一致；请重新打开提议核对。');
          }
          return;
        }
        const work=pendingOpenPending.current;
        if(!work||event.data?.operation_id!==work.operation_id)return;
        const proposal=event.data.proposal as KeeperProposal|undefined;
        pendingOpenPending.current=null;
        setPendingOpenLoading(false);
        if(!proposal||typeof proposal!=='object'||proposal.proposal_id!==work.proposal_id){
          setPendingProposal(null);
          setPendingOpenError('提议详情响应不完整；可重试。');
          return;
        }
        const incomingRevision=typeof proposal.revision==='number'&&Number.isInteger(proposal.revision)
          ?proposal.revision:null;
        const selected=pendingSelectionRef.current;
        if(incomingRevision!==null&&selected&&typeof selected.revision==='number'
          &&incomingRevision<selected.revision)return;
        const knownRevision=latestRevisionRef.current[proposal.proposal_id]??-1;
        if(incomingRevision!==null&&incomingRevision<knownRevision){
          setPendingOpenError('提议详情落后于已知修订；可重试读取当前持久状态。');
          return;
        }
        if(incomingRevision!==null)latestRevisionRef.current[proposal.proposal_id]=Math.max(
          knownRevision,incomingRevision);
        const incomingReview=keeperProposalReview(proposal);
        if(incomingReview&&(incomingRevision===null||incomingRevision>=knownRevision)){
          noteReview(proposal.proposal_id,incomingReview,{reset:true});
        }
        if(incomingRevision!==null&&selected&&typeof selected.revision==='number'
          &&incomingRevision>selected.revision){
          setDecisionReceipt(null);
          setDecisionNotice('');
          setDecisionError('');
          setDecisionRefreshError('');
        }
        setPendingOpenError('');
        const prior=pendingProposalRef.current;
        const nextProposal=prior&&prior.proposal_id===proposal.proposal_id
          &&prior.undo&&!proposal.undo?{...proposal,undo:prior.undo}:proposal;
        pendingProposalRef.current=nextProposal;
        setPendingProposal(nextProposal);
        const incomingStatus=asKeeperStatus(proposal.status);
        rememberKeeperState(proposal.proposal_id,incomingRevision,incomingStatus,
          incomingRevision===null||incomingRevision>=knownRevision);
        const currentReceipt=decisionReceiptRef.current;
        if(currentReceipt&&incomingStatus&&currentReceipt.status!==incomingStatus
          &&incomingStatus!=='pending'){
          setDecisionReceipt(null);
          setDecisionNotice('');
          setDecisionRefreshError('');
        }
        setPendingSelection(current=>current&&current.proposal_id===proposal.proposal_id
          ?{
            ...current,
            revision:incomingRevision??current.revision,
            status:incomingStatus||current.status,
          }
          :current);
        return;
      }
      if(event.kind==='keeper_decide'||event.kind==='keeper_revise'){
        const work=decisionWork.current;
        if(!work||event.data?.operation_id!==work.operation_id)return;
        const wrongKind=work.action==='keeper_decide'?event.kind!=='keeper_decide':event.kind!=='keeper_revise';
        const wrongDecision=typeof event.data?.decision==='string'&&event.data.decision!==work.decision;
        if(wrongKind||wrongDecision||event.data?.proposal_id!==work.proposal_id
          ||event.data?.revision!==work.revision){
          decisionWork.current=null;
          setDecisionPending(null);
          const selected=pendingSelectionRef.current;
          if(selected?.proposal_id===work.proposal_id&&selected?.revision===work.revision){
            setDecisionError('响应与当前提议、修订或操作不匹配；未确认成功，可重试。');
          }
          return;
        }
        if(event.data?.status==='expired'){
          decisionWork.current=null;
          setDecisionPending(null);
          setDecisionError('确认已过期。');
          setPendingProposal(previous=>previous?.proposal_id===work.proposal_id
            ?{...previous,status:'expired'}:previous);
          return;
        }
        const resultStatus=asKeeperStatus(event.data?.status);
        const accepted=resultStatus!==null&&(work.action==='keeper_decide'
          ?resultStatus===work.expectedStatus||resultStatus==='revising'||resultStatus==='returned'
          :work.decision==='adjust'
            ?resultStatus==='revising'||resultStatus==='cancelled'
            :resultStatus==='cancelled');
        if(!accepted||!resultStatus){
          decisionWork.current=null;
          setDecisionPending(null);
          const selected=pendingSelectionRef.current;
          if(selected?.proposal_id===work.proposal_id&&selected?.revision===work.revision){
            setDecisionError('响应状态与当前提议不匹配；未确认成功，可重试。');
          }
          return;
        }
        const result=event.data as KeeperDecision;
        rememberKeeperState(work.proposal_id,work.revision,resultStatus);
        decisionWork.current=null;
        setDecisionPending(null);
        setDecisionError('');
        const selected=pendingSelectionRef.current;
        const proposal=pendingProposalRef.current;
        const sameSelection=selected?.proposal_id===work.proposal_id
          &&selected?.revision===work.revision
          &&proposal?.proposal_id===work.proposal_id
          &&proposal?.revision===work.revision;
        const notice=resultStatus==='revising'
          ?(work.action==='keeper_decide'
            ?'决定目标已过期，Keeper 已转入修订；本次决定没有写入知识库。'
            :'已提交调整请求，Keeper 正在修订；修订完成前不能批准、拒绝或再次调整。')
          :resultStatus==='cancelled'
            ?(work.action==='keeper_decide'
              ?'决定目标已过期，Keeper 已取消本次修订；没有写入知识库。'
              :'已取消本次修订，没有写入知识库。')
            :'';
        if(sameSelection&&selected&&proposal){
          if(resultStatus==='approved'||resultStatus==='rejected'||resultStatus==='returned'){
            setDecisionReceipt(result);
            setDecisionNotice(result.error?.message||'');
            setDecisionRefreshError('');
            const nextProposal:KeeperProposal={...proposal,status:resultStatus,decision:result};
            pendingProposalRef.current=nextProposal;
            setPendingProposal(nextProposal);
            const nextSelection:KeeperSelection={...selected,status:resultStatus,revision:work.revision};
            pendingSelectionRef.current=nextSelection;
            setPendingSelection(nextSelection);
            keeperStatusRef.current=resultStatus;
            setKeeperStatus(resultStatus);
            requestKeeperList(resultStatus);
            const operation_id=crypto.randomUUID();
            decisionRefreshPending.current={operation_id,proposal_id:work.proposal_id,
              revision:work.revision,status:resultStatus};
            onCommand({action:'keeper_open',schema_version:1,operation_id,
              proposal_id:work.proposal_id});
          }else{
            setDecisionReceipt(result);
            setDecisionNotice(notice);
            setDecisionRefreshError('');
            const nextProposal:KeeperProposal={...proposal,status:resultStatus,decision:result};
            pendingProposalRef.current=nextProposal;
            setPendingProposal(nextProposal);
            const nextSelection:KeeperSelection={...selected,status:resultStatus,revision:work.revision};
            pendingSelectionRef.current=nextSelection;
            setPendingSelection(nextSelection);
            keeperStatusRef.current=resultStatus;
            setKeeperStatus(resultStatus);
            requestKeeperList(resultStatus);
            requestKeeperList('pending',{silent:true});
            reopenSelectedProposal(work.proposal_id,work.revision);
          }
        }else{
          requestKeeperList(resultStatus,{silent:true});
          requestKeeperList('pending',{silent:true});
        }
        return;
      }
      if(event.kind==='keeper_revision'||event.kind==='keeper_review'){
        const refreshPending=()=>requestKeeperList('pending',{silent:!pendingActive.current});
        const terminal=readKeeperTerminalEvent(keeperEventData(event.data));
        if(terminal){
          const proposal_id=terminal.proposal_id;
          const revision=terminal.revision;
          const status=terminal.status;
          const seen=latestRevisionRef.current[proposal_id]??-1;
          if(revision!==null&&revision<seen){
            if(event.kind==='keeper_review')refreshPending();
            return;
          }
          const verdict=noteReview(proposal_id,terminal.review_id||terminal.job_id);
          if(verdict==='stale'){
            if(event.kind==='keeper_review')refreshPending();
            return;
          }
          if(revision!==null)latestRevisionRef.current[proposal_id]=Math.max(seen,revision);
          const prior=latestStatusRef.current[proposal_id];
          if(status&&revision!==null&&prior&&revision===prior.revision
            &&verdict!=='new'
            &&KEEPER_STATUS_RANK[status]<KEEPER_STATUS_RANK[prior.status]){
            if(event.kind==='keeper_review')refreshPending();
            return;
          }
          rememberKeeperState(proposal_id,revision,status,verdict==='new');
          const selected=pendingSelectionRef.current;
          const sameSelected=selected?.proposal_id===proposal_id;
          if(status){
            if(sameSelected){
              keeperStatusRef.current=status;
              setKeeperStatus(status);
              const current=pendingProposalRef.current;
              if(current&&current.proposal_id===proposal_id
                &&!(revision!==null&&typeof current.revision==='number'&&revision<current.revision)){
                // The completion event names the new revision. Only the
                // correlated open response supplies that revision's changes.
                const nextProposal=revision!==null&&revision!==current.revision
                  ?null:{...current,status};
                pendingProposalRef.current=nextProposal;
                setPendingProposal(nextProposal);
              }
              setPendingSelection(currentSelection=>{
                if(!currentSelection||currentSelection.proposal_id!==proposal_id)return currentSelection;
                return {...currentSelection,status,revision:revision!==null?Math.max(
                  typeof currentSelection.revision==='number'?currentSelection.revision:revision,revision):currentSelection.revision};
              });
              const currentReceipt=decisionReceiptRef.current;
              const advanced=revision!==null&&selected&&typeof selected.revision==='number'
                &&revision>selected.revision;
              if(currentReceipt&&(verdict==='new'||advanced
                ||(currentReceipt.status!==status&&status!=='pending'))){
                setDecisionReceipt(null);
                setDecisionNotice('');
                setDecisionError('');
                setDecisionRefreshError('');
              }
              requestKeeperList(status,{silent:false});
            }else{
              requestKeeperList(status,{silent:keeperStatusRef.current!==status});
            }
            if(status!=='pending')requestKeeperList('pending',{silent:keeperStatusRef.current!=='pending'});
          }
          reopenSelectedProposal(proposal_id,revision);
        }
        if(event.kind==='keeper_review')refreshPending();
        return;
      }
      if(event.kind==='keeper_revision_error'){
        const data=keeperEventData(event.data);
        const proposal_id=typeof data?.proposal_id==='string'?data.proposal_id:'';
        const review_id=typeof data?.review_id==='string'&&data.review_id?data.review_id:null;
        const job_id=typeof data?.job_id==='string'&&data.job_id?data.job_id:null;
        const activeReview=pendingProposalRef.current
          ?keeperProposalReview(pendingProposalRef.current):null;
        const receiptReview=decisionReceiptRef.current?.review_id;
        if(!proposal_id&&(!review_id||(review_id!==activeReview&&review_id!==receiptReview)))return;
        if(proposal_id&&noteReview(proposal_id,review_id||job_id)==='stale')return;
        const message=typeof data?.message==='string'&&data.message?data.message
          :typeof data?.code==='string'&&data.code?`Keeper 修订未完成（${data.code}）；可刷新提议后重试。`
          :'Keeper 修订未完成；可刷新提议后重试。';
        const work=decisionWork.current;
        if(work&&(!proposal_id||work.proposal_id===proposal_id)){
          decisionWork.current=null;
          setDecisionPending(null);
        }
        const selected=pendingSelectionRef.current;
        if(selected&&(!proposal_id||selected.proposal_id===proposal_id)){
          setPendingOpenError(message);
          setDecisionError('');
        }
        if(proposal_id)requestKeeperList('pending',{silent:!pendingActive.current});
        return;
      }
      if(event.kind==='knowledge_search'){
        if(!latest||event.data?.operation_id!==latest.operation_id)return;
        const data=event.data as SearchPage;
        setLoading(false);
        setSearchError('');
        setPageData(data);
        const nextPage=Math.max(0,Math.floor((data.offset||0)/PAGE_SIZE));
        setPage(nextPage);
        const maxPage=Math.max(0,Math.ceil(data.total/PAGE_SIZE)-1);
        if(nextPage>maxPage){
          setPage(maxPage);
          searchNow(latest.values,maxPage*PAGE_SIZE,true);
          return;
        }
        if(focusAfterSearch.current){
          focusAfterSearch.current=false;
          if(data.results.length>0){
            window.setTimeout(()=>results.current?.querySelector<HTMLButtonElement>('button[data-result]')?.focus(),0);
          }
        }
        return;
      }
      if(event.kind==='command_error'&&event.data?.operation_id){
        const operation_id=event.data.operation_id;
        const message=event.data.message||event.data.code||'请求未能完成；可重试。';
        const listStatus=KEEPER_STATUSES.find(item=>keeperListPending.current[item]===operation_id);
        if(listStatus){
          keeperListPending.current[listStatus]=null;
          pendingFocusAfterRefresh.current=false;
          keeperRefreshQueued.current[listStatus]=false;
          if(keeperStatusRef.current===listStatus){
            setKeeperLoading(false);
            setKeeperError(message);
            if(listStatus==='pending'&&!keeperDataRef.current.pending){
              pendingCountCallback.current?.(null);
            }
          }
          return;
        }
        const work=decisionWork.current;
        if(work&&work.operation_id===operation_id){
          decisionWork.current=null;
          setDecisionPending(null);
          const selected=pendingSelectionRef.current;
          const proposal=pendingProposalRef.current;
          if(selected?.proposal_id===work.proposal_id&&proposal?.proposal_id===work.proposal_id
            &&proposal?.revision===work.revision){
            setDecisionError(message);
          }
          return;
        }
        if(decisionRefreshPending.current?.operation_id===operation_id){
          decisionRefreshPending.current=null;
          setDecisionRefreshError(message);
          return;
        }
        if(pendingOpenPending.current?.operation_id===operation_id){
          const open=pendingOpenPending.current;
          if(!open)return;
          pendingOpenPending.current=null;
          setPendingOpenLoading(false);
          setPendingOpenError(message);
          if(!open.keepProposal)setPendingProposal(null);
          return;
        }
        if(!latest||operation_id!==latest.operation_id)return;
        focusAfterSearch.current=false;
        setLoading(false);
        setPageData(null);
        setSearchError(message);
      }
    });
    return unsubscribe;
  },[]);

  /* Summary versions change after every committed mutation; refresh the last
     committed query and page so the workbench list stays aligned with the
     right-side panels. */
  const signature=useMemo(()=>[
    ...cards.map(card=>`${card.card_id}:${card.version}:${card.status}`),
    '#',
    ...themes.map(theme=>`${theme.theme_id}:${theme.version}:${theme.status}`),
    '#',
    ...frontiers.map(frontier=>`${frontier.frontier_id}:${frontier.version}:${frontier.status}`),
  ].join('|'),[cards,themes,frontiers]);
  const firstSignature=useRef(true);
  useEffect(()=>{
    if(firstSignature.current){firstSignature.current=false;return;}
    const latest=latestSearch.current;
    if(latest)searchNow(latest.values,latest.offset,true);
  },[signature]);

  useEffect(()=>{
    focusAfterSearch.current=false;
    setType(initialType);
    setScope('active');
    setQuery('');
    setRelationTarget('');
    setPage(0);
    setActive(null);
    setHistoryOpen(false);
    searchNow({query:'',type:initialType,scope:'active',relationTarget:''},0);
  },[initialType]);

  useEffect(()=>{
    if(!openPendingNonce)return;
    focusAfterSearch.current=false;
    pendingFocusAfterRefresh.current=false;
    keeperRefreshQueued.current=statusRecord(false);
    latestRevisionRef.current={};
    latestStatusRef.current={};
    reviewHistoryRef.current={};
    pendingOpenPending.current=null;
    decisionRefreshPending.current=null;
    setType('all');
    setScope('pending');
    setQuery('');
    setRelationTarget('');
    setPage(0);
    setHistoryOpen(false);
    keeperStatusRef.current='pending';
    setKeeperStatus('pending');
    setPendingSelection(null);
    setPendingProposal(null);
    setPendingOpenLoading(false);
    setPendingOpenError('');
    setDecisionPending(null);
    setDecisionError('');
    setDecisionNotice('');
    setDecisionReceipt(null);
    setDecisionRefreshError('');
    requestKeeperList('pending');
  },[openPendingNonce]);

  useEffect(()=>{
    if(!active)return;
    if(skipHeadingFocus.current){skipHeadingFocus.current=false;return;}
    detailHeading.current?.focus();
  },[active?.nonce,termDetail?.host]);

  useEffect(()=>{
    if(termDetail?.visible===false){
      setHistoryOpen(false);
      setActive(null);
    }
  },[termDetail?.visible]);

  const pageCount=dictionary?1:Math.max(1,Math.ceil((pageData?.total||0)/PAGE_SIZE));
  const currentPage=Math.min(page,pageCount-1);
  const rows=pageData?.results||[];
  const loadingBlocking=loading&&!pageData&&!searchError;
  const keeperList=keeperData[keeperStatus];
  const statusProposals=keeperList?.proposals||[];
  const statusNeedle=query.trim().toLowerCase();
  const statusRows=statusProposals.filter(proposal=>
    (type==='all'||(proposal.changes||[]).some(change=>change.kind===type))
    &&(!statusNeedle||proposalSearchText(proposal).includes(statusNeedle)));
  const keeperLoadingBlocking=keeperLoading&&!keeperList&&!keeperError;
  const currentKeeperStatusLabel=KEEPER_STATUS_LABEL[keeperStatus];
  const target=targetOf(relationTarget);

  const activeCard=active?.type==='card'
    ?((active.mode==='open'?cardSelected?.card_id===active.id:cardViewEvent?.nonce!==active.previousView)?cardSelected:null)
    :null;
  const activeTheme=active?.type==='theme'
    ?((active.mode==='open'?themeSelected?.theme_id===active.id:themeViewEvent?.nonce!==active.previousView)?themeSelected:null)
    :null;
  const activeFrontier=active?.type==='frontier'
    ?((active.mode==='open'?frontierSelected?.frontier_id===active.id:frontierViewEvent?.nonce!==active.previousView)?frontierSelected:null)
    :null;
  const activeSelected=activeCard||activeTheme||activeFrontier;
  const cardsById=useMemo(()=>new Map(cards.map(card=>[card.card_id,card])),[cards]);
  const activeCardSummary=active?.type==='card' && active.id?cardsById.get(active.id):undefined;
  const activeThemeSummary=active?.type==='theme'?themes.find(theme=>theme.theme_id===active.id):undefined;
  const activeFrontierSummary=active?.type==='frontier'?frontiers.find(frontier=>frontier.frontier_id===active.id):undefined;
  const activeTitle=active?.mode==='create'?`新建${kindLabel(active.type)}`
    :active?.type==='card'?(activeCard?.title||activeCardSummary?.title||active?.id||'')
    :active?.type==='theme'?(activeTheme?.judgement||activeThemeSummary?.judgement||active?.id||'')
    :(activeFrontier?.question||activeFrontierSummary?.question||active?.id||'');
  const activeStatus=activeSelected?.status||activeCardSummary?.status||activeThemeSummary?.status||activeFrontierSummary?.status||'';
  const activeVersion=activeSelected?.version??activeCardSummary?.version??activeThemeSummary?.version??activeFrontierSummary?.version??active?.version;

  const requestFor=(kind:EntityType):PanelRequest|null=>{
    if(!active||active.type!==kind)return null;
    return active.mode==='create'
      ?{kind:'create',nonce:active.nonce}
      :{kind:'open',nonce:active.nonce,id:active.id||'',version:active.version??1};
  };
  const openResult=(row:SearchResult)=>{
    termDetail?.open();
    setHistoryOpen(false);
    setActive({type:row.kind,mode:'open',id:row.id,version:row.version,nonce:crypto.randomUUID()});
  };
  useEffect(()=>{
    const frontier=editorFeedback?.frontier_scope;
    if (editorFeedback?.owner!=='frontier' || !frontier) return;
    setHistoryOpen(false);
    setActive({type:'frontier',mode:'open',id:frontier.frontier_id,version:frontier.version,nonce:crypto.randomUUID()});
  },[editorFeedback]);
  const startCreate=(kind:EntityType)=>{
    termDetail?.open();
    setHistoryOpen(false);
    const values:SearchValues={query:'',type:kind,scope:'active',relationTarget:''};
    focusAfterSearch.current=false;
    setType(kind);
    setScope('active');
    setRelationTarget('');
    setQuery('');
    setPage(0);
    searchNow(values,0);
    const previousView={card:cardViewEvent,theme:themeViewEvent,frontier:frontierViewEvent}[kind]?.nonce;
    setActive({type:kind,mode:'create',nonce:crypto.randomUUID(),previousView});
  };
  const committed=(kind:EntityType)=>(id:string,version:number)=>{
    const wasCreate=active?.type===kind&&active.mode==='create';
    setActive(current=>current&&current.type===kind
      ?{type:kind,mode:'open',id,version,nonce:current.nonce}
      :{type:kind,mode:'open',id,version,nonce:crypto.randomUUID()});
    if(wasCreate||scope==='pending'){
      const values:SearchValues={query,type,scope:'active',relationTarget};
      setScope('active');
      setPage(0);
      searchNow(values,0,true);
    }else if(dictionary){
      searchNow({query,type,scope,relationTarget},currentPage,true);
    }
  };
  const undoApplied=(kind:HistoryKind,id:string,record:HistoryRecord)=>{
    skipHeadingFocus.current=true;
    setActive(current=>{
      if(!current||current.mode!=='open'||current.type!==kind||current.id!==id)return current;
      return {...current,version:typeof record.version==='number'?record.version:current.version,nonce:crypto.randomUUID()};
    });
  };
  const closeHistory=()=>{
    setHistoryOpen(false);
    window.setTimeout(()=>historyButton.current?.focus(),0);
  };
  const changeType=(next:TypeFilter)=>{focusAfterSearch.current=false;pendingFocusAfterRefresh.current=false;setType(next);setPage(0);if(scope!=='pending')searchNow({query,type:next,scope,relationTarget},0);};
  const clearDecisionView=()=>{
    setPendingSelection(null);
    setPendingProposal(null);
    setPendingOpenLoading(false);
    setPendingOpenError('');
    setDecisionError('');
    setDecisionNotice('');
    setDecisionReceipt(null);
    setDecisionRefreshError('');
    pendingOpenPending.current=null;
    decisionRefreshPending.current=null;
  };
  const changeScope=(next:ScopeFilter)=>{
    if(next==='pending')termDetail?.close();
    focusAfterSearch.current=false;
    pendingFocusAfterRefresh.current=false;
    keeperRefreshQueued.current=statusRecord(false);
    setScope(next);
    setPage(0);
    clearDecisionView();
    if(next==='pending'){
      if(keeperData[keeperStatus])setKeeperLoading(false);
      requestKeeperList(keeperStatus,{silent:!!keeperData[keeperStatus]});
    }else searchNow({query,type,scope:next,relationTarget},0);
  };
  const changeKeeperStatus=(next:KeeperStatus)=>{
    if(next===keeperStatus)return;
    pendingFocusAfterRefresh.current=false;
    keeperStatusRef.current=next;
    setKeeperStatus(next);
    clearDecisionView();
    setKeeperError('');
    if(keeperData[next])setKeeperLoading(false);
    requestKeeperList(next,{silent:!!keeperData[next]});
  };
  const changeRelation=(next:string|string[])=>{focusAfterSearch.current=false;setRelationTarget(next);setPage(0);if(scope!=='pending')searchNow({query,type,scope,relationTarget:next},0);};
  const submit=()=>{
    setPage(0);
    if(scope==='pending'){
      pendingFocusAfterRefresh.current=true;
      requestKeeperList(keeperStatus);
      return;
    }
    focusAfterSearch.current=true;
    searchNow({query,type,scope,relationTarget},0);
  };
  const deciding=!!decisionPending
    &&pendingSelection?.proposal_id===decisionPending.proposal_id
    &&pendingSelection?.revision===decisionPending.revision;
  const decidingAction=deciding&&decisionPending?decisionPending.decision:null;
  const goPage=(next:number)=>{
    const values=latestSearch.current?.values||{query,type,scope,relationTarget};
    setPage(next);
    searchNow(values,next*PAGE_SIZE);
  };
  const retry=()=>{
    if(scope==='pending'){
      requestKeeperList(keeperStatus);
      return;
    }
    const latest=latestSearch.current;
    if(latest)searchNow(latest.values,latest.offset);
  };
  const decideProposal=(decision:KeeperDecisionChoice,reason:string)=>{
    const selected=pendingSelection;
    const proposal=pendingProposal;
    if(!selected||!proposal||decisionWork.current||selected.status!=='pending')return;
    if(proposal.proposal_id!==selected.proposal_id||proposal.status!=='pending')return;
    if(typeof selected.revision==='number'&&typeof proposal.revision==='number'
      &&selected.revision!==proposal.revision){
      setDecisionError('当前显示的修订已变化；请重新打开提议核对后再决定。');
      return;
    }
    const latestSeen=latestRevisionRef.current[proposal.proposal_id];
    if(typeof latestSeen==='number'&&typeof proposal.revision==='number'
      &&latestSeen>proposal.revision){
      setDecisionError(`Keeper 已发布更新的修订 v${latestSeen}；请重新打开提议核对后再决定。`);
      return;
    }
    const revision=typeof proposal.revision==='number'?proposal.revision:0;
    if(!Number.isInteger(revision)||revision<1){
      setDecisionError('提议缺少有效修订，暂不能决定；请重新打开提议核对。');
      return;
    }
    const changes=Array.isArray(proposal.changes)?proposal.changes:[];
    if(!changes.length){
      setDecisionError('提议没有可决定的变更。');
      return;
    }
    const expected_versions=changes.map(change=>change.action==='create'?null:change.expected_version);
    if(expected_versions.some((version,index)=>changes[index].action!=='create'
      &&!(typeof version==='number'&&Number.isInteger(version)&&version>0))){
      setDecisionError('提议中的更新或删除缺少固定版本，暂不能决定；请重新打开提议核对。');
      return;
    }
    const operation_id=crypto.randomUUID();
    const work:KeeperDecisionWork={
      operation_id,proposal_id:proposal.proposal_id,revision,
      action:'keeper_decide',decision,expectedStatus:decision==='approve'?'approved':'rejected',
    };
    decisionWork.current=work;
    setDecisionPending(work);
    setDecisionError('');
    setDecisionNotice('');
    const trimmed=reason.trim();
    onCommand({
      action:'keeper_decide',schema_version:1,operation_id,
      proposal_id:proposal.proposal_id,revision,expected_versions,decision,
      ...(trimmed?{reason:trimmed}:{}),
    });
  };
  const reviseProposal=(decision:KeeperRevisionDecision,feedback:string,omitChanges:number[],keepCurrentFields:KeeperKeepFields[]=[])=>{
    const selected=pendingSelection;
    const proposal=pendingProposal;
    if(!selected||!proposal||decisionWork.current)return;
    if(proposal.proposal_id!==selected.proposal_id)return;
    if(typeof selected.revision==='number'&&typeof proposal.revision==='number'
      &&selected.revision!==proposal.revision){
      setDecisionError('当前显示的修订已变化；请重新打开提议核对后再操作。');
      return;
    }
    const latestSeen=latestRevisionRef.current[proposal.proposal_id];
    if(typeof latestSeen==='number'&&typeof proposal.revision==='number'
      &&latestSeen>proposal.revision){
      setDecisionError(`Keeper 已发布更新的修订 v${latestSeen}；请重新打开提议核对后再操作。`);
      return;
    }
    const revision=typeof proposal.revision==='number'?proposal.revision:0;
    if(!Number.isInteger(revision)||revision<1){
      setDecisionError('提议缺少有效修订，暂不能调整；请重新打开提议核对。');
      return;
    }
    const proposalStatus=asKeeperStatus(proposal.status);
    const adjustableStatus=(value:KeeperStatus)=>value==='pending'||value==='returned';
    const cancellableStatus=(value:KeeperStatus)=>value==='pending'||value==='returned'||value==='revising';
    if(decision==='adjust'){
      if(!proposalStatus||!adjustableStatus(selected.status)||!adjustableStatus(proposalStatus)
        ||selected.status!==proposalStatus){
        setDecisionError('只有待确认或已退回的提议可以要求调整；请刷新提议核对。');
        return;
      }
      const changes=Array.isArray(proposal.changes)?proposal.changes:[];
      if(!changes.length){
        setDecisionError('提议没有可调整的变更。');
        return;
      }
      const trimmed=feedback.trim();
      if(!trimmed){
        setDecisionError('请填写调整说明后再提交。');
        return;
      }
      if(keeperFeedbackByteLength(trimmed)>KEEPER_FEEDBACK_LIMIT){
        setDecisionError(`调整说明不能超过 ${KEEPER_FEEDBACK_LIMIT} 个 UTF-8 字节。`);
        return;
      }
      const ordered=Array.from(new Set(omitChanges.filter(index=>
        Number.isInteger(index)&&index>=0&&index<changes.length))).sort((left,right)=>left-right);
      const keepPayload:KeeperKeepFields[]=[];
      for(const item of keepCurrentFields){
        if(!item||!Number.isInteger(item.change_index))continue;
        const index=item.change_index;
        if(index<0||index>=changes.length||ordered.includes(index))continue;
        const allowed=keeperChangedFields(changes[index]);
        const fields=Array.from(new Set((Array.isArray(item.fields)?item.fields:[])
          .filter(field=>typeof field==='string'&&allowed.includes(field))));
        if(fields.length)keepPayload.push({change_index:index,fields});
      }
      const operation_id=crypto.randomUUID();
      const work:KeeperDecisionWork={
        operation_id,proposal_id:proposal.proposal_id,revision,
        action:'keeper_revise',decision:'adjust',expectedStatus:'revising',
      };
      decisionWork.current=work;
      setDecisionPending(work);
      setDecisionError('');
      setDecisionNotice('');
      onCommand({
        action:'keeper_revise',schema_version:1,operation_id,
        proposal_id:proposal.proposal_id,revision,decision:'adjust',
        feedback:trimmed,
        ...(ordered.length?{omit_changes:ordered}:{}),
        ...(keepPayload.length?{keep_current_fields:keepPayload}:{}),
      });
      return;
    }
    if(!proposalStatus||!cancellableStatus(selected.status)||!cancellableStatus(proposalStatus)
      ||selected.status!==proposalStatus){
      setDecisionError('只有待确认、已退回或修订中的提议可以取消；请刷新提议核对。');
      return;
    }
    const operation_id=crypto.randomUUID();
    const work:KeeperDecisionWork={
      operation_id,proposal_id:proposal.proposal_id,revision,
      action:'keeper_revise',decision:'cancel',expectedStatus:'cancelled',
    };
    decisionWork.current=work;
    setDecisionPending(work);
    setDecisionError('');
    setDecisionNotice('');
    onCommand({
      action:'keeper_revise',schema_version:1,operation_id,
      proposal_id:proposal.proposal_id,revision,decision:'cancel',
    });
  };

  useMasonry(results,dictionary && scope!=='pending' && !loadingBlocking && !searchError,pageData);

  const listContent=
    <section className="workbench-list" aria-label={dictionary?'词典搜索':'知识库搜索'}>
      {dictionary && <ModuleHeading icon={<DictionaryIcon/>} title="词典">
        <Button variant="primary" type="button" onClick={()=>startCreate('card')}>创建术语</Button>
      </ModuleHeading>}
      {editorFeedback && closeEditorFeedback && <EditorFeedbackPreview draft={editorFeedback} onClose={closeEditorFeedback}>
        {editorFeedback.owner==='knowledge' && <Button type="button"
          disabled={new TextEncoder().encode(editorFeedback.note).length>4096}
          onClick={()=>setQuery(editorFeedback.note)}>填入知识库搜索草稿</Button>}
      </EditorFeedbackPreview>}
      <KeeperReviewRetry/>
      {dictionary ? <DictionarySearch query={query} setQuery={setQuery} scope={scope} changeScope={changeScope}
        cards={cards} selected={Array.isArray(relationTarget) ? relationTarget : []}
        changeSelected={changeRelation} submit={submit}/>
      : <form className="workbench-filters" role="search" onSubmit={event=>{event.preventDefault();submit();}}>
        <label>搜索<TextInput type="search" value={query} onChange={event=>setQuery(event.target.value)} placeholder={dictionary?'搜索术语和释义':'标题、判断、问题或范围'}/></label>
        <div className="workbench-filter-row">
          {!dictionary&&<label>类型<FixedSelect value={type} onChange={event=>changeType(event.target.value as TypeFilter)}>
            <option value="all">全部</option>
            <option value="card">知识卡片</option>
            <option value="theme">命题</option>
            <option value="frontier">关注问题</option>
          </FixedSelect></label>}
          <label>范围<FixedSelect value={scope} onChange={event=>changeScope(event.target.value as ScopeFilter)}>
            <option value="active">当前有效</option>
            <option value="inactive">归档与停用</option>
            <option value="pending">待确认</option>
          </FixedSelect></label>
        </div>
        <label>关联记录<FixedSelect value={relationTarget} onChange={event=>changeRelation(event.target.value)} disabled={scope==='pending'||(!cards.length&&!themes.length)}>
          <option value="">全部关联</option>
          {cards.length>0&&<optgroup label={dictionary?'术语':'知识卡片'}>{cards.map(card=><option key={card.card_id} value={`card:${card.card_id}`}>{truncate(card.title)}{card.status==='archived'?'（已归档）':''} · v{card.version}</option>)}</optgroup>}
          {!dictionary&&themes.length>0&&<optgroup label="命题">{themes.map(theme=><option key={theme.theme_id} value={`theme:${theme.theme_id}`}>{truncate(theme.judgement)}{theme.status==='archived'?'（已归档）':''} · v{theme.version}</option>)}</optgroup>}
        </FixedSelect></label>
        <div className="workbench-filter-actions"><span>{scope==='pending'?'待确认按 Keeper 持久状态筛选；在右侧批准、拒绝、要求调整或取消提议':target?'只显示与该记录直接关联的记录（双向）':'不筛选关联记录'}</span><Button type="submit">{scope==='pending'?`刷新${currentKeeperStatusLabel}`:'搜索'}</Button></div>
      </form>}
      {!dictionary && <div className="workbench-create" role="group" aria-label="新建知识库记录">
        <span>新建</span>
        <Button type="button" onClick={()=>startCreate('card')}>知识卡片</Button>
        <Button type="button" onClick={()=>startCreate('theme')}>命题</Button>
        <Button type="button" onClick={()=>startCreate('frontier')}>关注问题</Button>
      </div>}
      {scope==='pending'&&<div className="workbench-keeper-tabs" role="tablist" aria-label="确认卡状态">
        {KEEPER_STATUSES.map(item=><Button key={item} type="button" role="tab" aria-selected={keeperStatus===item} className={keeperStatus===item?'selected':''} onClick={()=>changeKeeperStatus(item)}>
          {KEEPER_STATUS_LABEL[item]}
          {item==='pending'&&keeperData.pending?` ${keeperData.pending.proposals.length}`:''}
        </Button>)}
      </div>}
      <p className="workbench-count" aria-live="polite">{scope==='pending'
        ?(keeperLoading?(keeperList?`正在刷新${currentKeeperStatusLabel}提议…`:`正在读取${currentKeeperStatusLabel}提议…`):keeperError&&!keeperList?`${currentKeeperStatusLabel}提议未完成`:`共 ${statusProposals.length} 条${currentKeeperStatusLabel}提议${statusRows.length!==statusProposals.length?` · 当前筛选 ${statusRows.length} 条`:''}`)
        :<>{loading?'正在搜索…':searchError?'搜索未完成':`共 ${pageData?.total||0} 条`}{!loading&&!searchError&&pageCount>1?` · 第 ${currentPage+1}/${pageCount} 页`:''}</>}</p>
      {scope==='pending'
        ?(keeperLoadingBlocking?<p className="workbench-status" role="status">正在读取 Keeper {currentKeeperStatusLabel}提议…</p>
          :keeperError&&!keeperList?<div className="workbench-error" role="alert"><p>{keeperError}</p><Button type="button" onClick={retry}>重试</Button></div>
          :<>
            {keeperError&&<div className="workbench-error workbench-error-inline" role="alert"><p>{keeperError}</p><Button type="button" onClick={retry}>重试</Button></div>}
            {keeperList&&statusProposals.length===0?<div className="workbench-empty workbench-pending" role="status">
              <h3>{currentKeeperStatusLabel}：当前为空</h3>
              <p>{keeperStatus==='pending'?'Keeper 返回的待确认提议为 0 条；这里不会显示未经确认的提议。'
                :keeperStatus==='revising'?'当前没有 Keeper 正在修订的提议；修订完成或取消后会更新到相应状态。'
                :keeperStatus==='returned'?'当前没有已退回提议；退回原因与反馈会随持久状态保留。'
                :keeperStatus==='cancelled'?'当前没有已取消提议；取消结果不会写入知识库。'
                :'该状态没有 Keeper 提议；已批准和已拒绝会保留持久回执供重新打开。'}</p>
            </div>
            :statusRows.length===0?<div className="workbench-empty" role="status"><p>{keeperList?`没有匹配当前筛选的${currentKeeperStatusLabel}提议；实际返回 ${statusProposals.length} 条。`:`尚未读取${currentKeeperStatusLabel}提议。`}</p></div>
            :<ul className="workbench-results" ref={results} aria-label={`${currentKeeperStatusLabel}提议`}>
              {statusRows.map(proposal=>{
                const changes=Array.isArray(proposal.changes)?proposal.changes:[];
                const kinds=Array.from(new Set(changes.map(change=>change.kind)));
                const persistedDecision=keeperDecisionFromProposal(proposal);
                const reason=persistedDecision?.reason||proposal.feedback||proposal.reason
                  ||proposal.revision_notes?.feedback||proposal.revision_notes?.reason
                  ||changes.map(change=>change.reason).find(Boolean);
                const status=keeperStatusLabel(proposal.status||persistedDecision?.status);
                const badge=keeperTestBadge(proposal);
                const savedSelection=pendingSelection?.proposal_id===proposal.proposal_id&&pendingSelection.status===keeperStatus;
                return <li key={proposal.proposal_id}>
                  <Button type="button" data-pending-result data-proposal-id={proposal.proposal_id} className={savedSelection?'selected':''} aria-current={savedSelection?true:undefined} onClick={()=>openProposal(proposal)}>
                    <span className="workbench-result-title">{keeperProposalTitle(proposal)}</span>
                    <span className="workbench-result-summary">{reason||`${changes.length} 项变更`}</span>
                    <span className="workbench-result-meta">
                      {kinds.map(kind=><span key={kind}>{keeperKindLabel(kind)}</span>)}
                      {status&&<span>{status}</span>}
                      {typeof proposal.revision==='number'&&<span>修订 v{proposal.revision}</span>}
                      {hasPublishedKeeperRevision(proposal)&&<span className="workbench-badge-revision">新修订</span>}
                      {badge&&<span className="workbench-badge-test">{badge}</span>}
                      {typeof proposal.report_version==='number'&&<span>报道 v{proposal.report_version}</span>}
                    </span>
                  </Button>
                </li>;
              })}
            </ul>}
          </>)
        :loadingBlocking?<p className="workbench-status" role="status">{dictionary?'正在读取术语…':'正在读取知识卡片、命题与关注问题…'}</p>
        :searchError?<div className="workbench-error" role="alert"><p>{searchError}</p><Button type="button" onClick={retry}>重试</Button></div>
        :rows.length===0?<div className="workbench-empty" role="status"><p>{target?'所选关联记录在当前范围与类型下没有直接关联记录。':query.trim()?`没有匹配「${query.trim()}」的记录。`:dictionary?'当前范围没有术语。':'当前范围没有知识卡片、命题或关注问题。可在左侧新建。'}</p></div>
        :<ul className="workbench-results" ref={results} aria-label="搜索结果">
          {rows.map(row=><li key={`${row.kind}:${row.id}`}>
            <Button type="button" data-result className={active?.mode==='open'&&active.type===row.kind&&active.id===row.id?'selected':''} aria-current={active?.mode==='open'&&active.type===row.kind&&active.id===row.id?true:undefined} onClick={()=>openResult(row)}>
              <span className="workbench-result-title">{row.title||'（无标题）'} {row.kind==='card'&&<TestTag tags={cardsById.get(row.id)?.tags}/>}</span>
              {dictionary ? (cardsById.get(row.id)?.aliases || []).slice(0,5).map((alias,index)=>
                <span className="dictionary-card-alias" key={index}>{alias}</span>
              ) : row.summary&&<span className="workbench-result-summary">{row.summary}</span>}
              <span className="workbench-result-meta">
                {!dictionary&&<span>{kindLabel(row.kind)}</span>}
                <span>{scopeStatus(scope,row)}</span>
                {!dictionary&&<span>版本 v{row.version}</span>}
                {row.questionable&&<span className="workbench-badge-questionable">存疑</span>}
                {row.source_pending&&<span>来源待补充</span>}
              </span>
            </Button>
          </li>)}
        </ul>}
      {scope!=='pending'&&!loadingBlocking&&!searchError&&pageCount>1&&<nav className="workbench-paging" aria-label="搜索结果分页">
        <Button type="button" disabled={currentPage===0} onClick={()=>goPage(currentPage-1)}>上一页</Button>
        <span>第 {currentPage+1} / {pageCount} 页</span>
        <Button type="button" disabled={currentPage>=pageCount-1} onClick={()=>goPage(currentPage+1)}>下一页</Button>
      </nav>}
    </section>;

  const detailContent=
    <section className={dictionary&&scope!=='pending'?'dictionary-detail':'workbench-detail'} aria-label={scope==='pending'?`Keeper ${currentKeeperStatusLabel}提议详情`:(dictionary?'术语详情':'知识库详情')}>
      {scope==='pending'
        ?(pendingSelection
          ?<KeeperProposalPanel key={pendingSelection.nonce} proposalId={pendingSelection.proposal_id} proposal={pendingProposal} loading={pendingOpenLoading} error={pendingOpenError} deciding={deciding} decidingAction={decidingAction} decisionError={decisionError} decisionNotice={decisionNotice||decisionRefreshError} receipt={decisionReceipt} onRetry={retryPendingOpen} onDecide={decideProposal} onRevise={reviseProposal} onUndoCommand={onCommand} onUndoApplied={groupUndoApplied} onClose={closePendingProposal}/>
          :<div className="workbench-empty workbench-pending-intro">
            <h2>Keeper {currentKeeperStatusLabel}</h2>
            <p>左侧按 Keeper 持久状态列出{currentKeeperStatusLabel}提议，默认打开待确认。选择一条后，这里显示整组确认卡片：按知识卡片、命题、关注问题分组的变更、变更前/后、理由、固定来源与任务/报道链接。</p>
            <p>{keeperStatus==='pending'?'待确认卡片可对整组批准或拒绝，也可在“要求调整”中填写说明并选择希望 Keeper 保留的当前字段；说明会发给原始任务，不会被自动理解。批准和拒绝都绑定提议修订与每个对象的固定版本。'
              :keeperStatus==='revising'?'修订中的卡片不能批准、拒绝或再次调整，只能对当前修订明确取消；Keeper 完成修订后会显示新的提议修订与差异。'
              :keeperStatus==='returned'?'已退回卡片只读显示退回原因、反馈与此前父级/报道来源，且没有写入知识库；可以在“要求调整”中结合退回原因重新提交，或取消该提议。'
              :keeperStatus==='cancelled'?'已取消卡片只读显示取消结果，不会写入知识库。'
              :'已批准和已拒绝卡片只读显示持久回执；重新打开工作台或切换状态后仍读取同一结果。'}</p>
            {keeperList&&statusProposals.length>0&&<p>当前有 {statusProposals.length} 条{currentKeeperStatusLabel}提议，请从左侧选择。</p>}
          </div>)
        :active?<>
          <header className="workbench-detail-head">
            <div>
              <span className="workbench-kind">{kindLabel(active.type)}</span>
              <h2 ref={detailHeading} tabIndex={-1}>{activeTitle||'知识库记录'}</h2>
              <p>{active.mode==='create'?(active.type==='card'?'提交后需要确认':'保存会直接创建并提交'):`${STATUS_LABEL[activeStatus]||activeStatus||'已提交'}${activeVersion?` · 版本 v${activeVersion}`:''}`}</p>
            </div>
            <div className="workbench-detail-actions">
              {active.mode==='open'&&active.id&&<Button ref={historyButton} type="button" aria-expanded={historyOpen} aria-controls="knowledge-history" onClick={()=>{if(historyOpen)closeHistory();else setHistoryOpen(true);}}>{historyOpen?'收起版本历史':'版本历史'}</Button>}
              {!dictionary&&<Button type="button" onClick={()=>{setHistoryOpen(false);setActive(null);}}>关闭</Button>}
            </div>
          </header>
          {active.type==='card'&&<KnowledgeCardPanel key={active.nonce} cards={cards} materials={materials} selected={activeCard} viewEvent={cardViewEvent} commandError={cardError} onCommand={onCommand} embedded showList={false} openRequest={requestFor('card')} termMode={dictionary} onCommitted={committed('card')}/>}
          {active.type==='theme'&&<ThemePanel key={active.nonce} themes={themes} materials={materials} cards={cards} selected={activeTheme} viewEvent={themeViewEvent} commandError={themeError} onCommand={onCommand} embedded showList={false} openRequest={requestFor('theme')} onCommitted={committed('theme')}/>}
          {active.type==='frontier'&&<KnowledgePanel key={active.nonce} frontiers={frontiers} selected={activeFrontier} viewEvent={frontierViewEvent} commandError={frontierError} onCommand={onCommand} onRetrieve={onRetrieve} embedded showList={false} openRequest={requestFor('frontier')} onCommitted={committed('frontier')}/>}
          {historyOpen&&active.mode==='open'&&active.id&&<KnowledgeHistoryPanel key={`${active.type}:${active.id}`} kind={active.type} id={active.id} currentVersion={activeVersion} onCommand={onCommand} onUndoApplied={undoApplied} onClose={closeHistory}/>}
        </>:<div className="workbench-empty">
          <h2>知识库工作台</h2>
          <p>从左侧选择一条已提交记录，在右侧读取完整版本并编辑、保存或检查来源；也可以新建知识卡片、命题或关注问题。</p>
          <p>搜索支持当前有效、归档与停用、待确认范围；待确认范围按 Keeper 持久状态分为待确认、已批准和已拒绝。</p>
        </div>}
    </section>;

  return <div className={dictionary&&scope!=='pending'?'workbench dictionary-workbench':dictionary?'workbench dictionary-pending':'workbench'}>
    {listContent}
    {dictionary&&scope!=='pending'
      ?(termDetail?.host&&active?createPortal(detailContent,termDetail.host):null)
      :detailContent}
  </div>;
}
