import {RetrieverPanel} from './RetrieverPanel';
import {MigrationReviewCard,type MigrationReview} from './MigrationReviewCard';
import {HpediaIcon} from './HpediaIcon';

import {Button} from './gui/elements/Button';
import {TextInput, MultilineInput} from './gui/elements/FormElements';
import {FixedSelect} from './gui/elements/ChoiceElements';
import React,{useEffect,useState,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import Markdown from 'react-markdown';
import './style.css';
import './window-caption.css';
import {SubscriptionPanel,type Subscription} from './SubscriptionPanel';
import {VoyagerPanel,type VoyagerSummary} from './VoyagerPanel';
import {SchedulePanel,type Schedule} from './SchedulePanel';
import {ArchiveDocumentView,ArchiveSourcesPanel,type ArchiveDocument,type ArchiveSource} from './ArchiveSourcesPanel';
import {MaterialsPanel,type MaterialSummary,type MaterialPackage} from './MaterialsPanel';
import {KnowledgePanel,type FrontierSummary,type Frontier,type FrontierViewEvent,type FrontierCommandError} from './KnowledgePanel';
import {KnowledgeCardPanel,type CardSummary,type KnowledgeCard,type CardViewEvent,type CardCommandError} from './KnowledgeCardPanel';
import {ThemePanel,type ThemeSummary,type Theme,type ThemeViewEvent,type ThemeCommandError} from './ThemePanel';
import {FrontierTracking} from './FrontierTracking';
import {BulletinBoard,type BulletinLink,type BoardKind} from './BulletinBoard';
import {convergenceLabel,type Convergence} from './ConvergenceSelect';
import {HpediaPanel,type Question} from './HpediaPanel';
import {KnowledgeWorkbench} from './KnowledgeWorkbench';
import {EditorReports} from './EditorReports';
import {readLocalHtml} from './LocalHtml';
import {CleanupEntries,TestCleanupPanel,TestTag,type CleanupPlan} from './TestCleanupPanel';
import {FeishuSetup} from './FeishuSetup';
import type {FeedbackHandoff} from './EditorFeedbackPreview';
type Child={child_id:string;session_id:string;parent_session_id:string;role:string;input_version:number;status:string;artifact_ref:string;result?:string;error?:string};
type EditorTermProposal={proposal_id:string;revision:number;status:string;term_expires_at:number;decision?:{error?:{code:string;message:string}};changes:{kind:string;action:string;id?:string;expected_version?:number;after?:Record<string,unknown>;reason?:string}[]};
type TermConfirmation={entity?:'question'|'source'|'frontier'|'progress'|'test_cleanup';operation_id:string;status:string;expires_at:number;changes:[string,Record<string,unknown>][];summaries?:string[];cleanup?:CleanupPlan;error?:{code:string;message:string}};

function TermConfirmationCard({card,decide,terms=[],sources=[]}:{card:TermConfirmation;terms?:CardSummary[];sources?:ArchiveSource[];decide:(choice:'confirm'|'reject')=>void}){
  const [elapsed,setElapsed]=useState(false);
  useEffect(()=>{
    if(card.status!=='pending')return;
    const delay=card.expires_at*1000-Date.now();
    if(delay<=0){setElapsed(true);return;}
    const timer=window.setTimeout(()=>setElapsed(true),delay);
    return ()=>window.clearTimeout(timer);
  },[card.status,card.expires_at]);
  const status=elapsed&&card.status==='pending'?'expired':card.status;
  const labels:Record<string,string>={aliases:'别名',sources:'来源',relations:'关系',
    validity:'状态',tracked:'追踪中',questionable:'存疑',convergence:'收敛状态',reason:'修改理由',summary:'简介',citation:'学术引用',source_files:'缓存原件',label:'来源说明',url:'原始链接',material_id:'原文文件',source_ids:'资料库来源',status:'关注状态',scope:'研究范围',hard_constraints:'硬约束'};
  const fieldText=(field:string,value:unknown)=>{
    if(field==='validity') return value==='questionable'?'存疑':'有效';
    if(field==='tracked') return value?'是':'否';
    if(typeof value==='object'&&value){
      const row=value as Record<string,unknown>;
      if(field==='scope')return [
        row.text,Array.isArray(row.alternatives)&&row.alternatives.length?`替代查询 ${row.alternatives.join('、')}`:null,
        Array.isArray(row.sources)?`来源 ${row.sources.join('、')}`:null,
      ].filter(Boolean).join(' · ');
      if(field==='hard_constraints')return [
        `必须包含 ${Array.isArray(row.must_include)&&row.must_include.length?row.must_include.join('、'):'无'}`,
        `排除 ${Array.isArray(row.exclude)&&row.exclude.length?row.exclude.join('、'):'无'}`,
      ].join(' · ');
    }
    return typeof value==='string'?value:JSON.stringify(value);
  };
  const previewText=(value:unknown)=>typeof value==='string'?value.replace(
    /\[\[term:([^\[\]\s]+)\]\]/g,(_,id:string)=>terms.find(term=>term.card_id===id)?.title??'术语'):'';
  return <article className="assistant" aria-label={card.entity==='question'?'百科问题变更确认':card.entity==='source'?'来源变更确认':card.entity==='frontier'?'前沿变更确认':card.entity==='progress'?'进展变更确认':card.entity==='test_cleanup'?'测试内容清理确认':'术语变更确认'}>
    <div className="author">图书管理员</div>
    {card.cleanup&&<CleanupEntries plan={card.cleanup}/>}
    {card.changes.map(([action,change],index)=><div key={index}>
      <strong>{({create:'新增',register:'登记文档',update:'修改',archive:'归档',delete:'删除'} as Record<string,string>)[action]} {previewText(card.summaries?.[index]??change.prompt??change.title??change.question??change.question_id??change.card_id??sources.find(source=>source.source_id===change.source_id)?.title??change.source_id??change.progress_id??change.frontier_id)}</strong> {change.test_data===true&&<TestTag tags={['测试']}/>}
      {card.entity!=='progress'&&previewText(change.answer??change.content)&&<p>{previewText(change.answer??change.content)}</p>}
      <dl>{Object.entries(change).filter(([field])=>field in labels).map(([field,value])=><React.Fragment key={field}>
        <dt>{labels[field]}</dt><dd>{field==='convergence'?convergenceLabel(value as Convergence,card.entity==='question'?'question':'term'):fieldText(field,value)}</dd>
      </React.Fragment>)}</dl>
    </div>)}
    {card.error&&<p role="alert">{card.error.message}</p>}
    {status==='pending'?<>
      <button onClick={()=>decide('confirm')}>确认</button>
      <button onClick={()=>decide('reject')}>取消</button>
    </>:<p>{({confirmed:'已确认',rejected:'已取消',expired:'已过期',returned:'已退回',revising:'正在复审',cancelled:'已取消'} as Record<string,string>)[status]}</p>}
  </article>;
}
type Chat={task_id:string;version:number;title:string;messages:{role:string;text:string}[]};
declare global {interface Window {horizon:{send:(c:unknown)=>void;subscribe:(f:(e:any)=>void)=>()=>void}}}
const childRoleLabel=(role:string)=>role==='voyager'?'历史检索子会话':role==='keeper'?'Keeper 知识确认子会话':`开发工具 · ${role}`;
function App(){
const [state,setState]=useState<{hpedia_deleted?:Record<string,string>;migration_reviews?:MigrationReview[];web_jobs?:string[];archive_sources?:ArchiveSource[];subscriptions?:Subscription[];subscription_busy?:string[];voyager_requests?:VoyagerSummary[];voyager_busy?:string[];materials?:(MaterialSummary&{version?:number})[];frontiers?:FrontierSummary[];cards?:CardSummary[];questions?:Question[];bulletin_links?:BulletinLink[];term_confirmations?:TermConfirmation[];gui_term_confirmations?:TermConfirmation[];editor_term_proposals?:EditorTermProposal[];themes?:ThemeSummary[];deliveries?:{delivery_id:string;version:number;status:string;attempts:unknown[];receipts:unknown[];checked?:boolean}[];delivery_enabled?:boolean;schedules?:Schedule[];schedule_limits?:{main:number;children:number};chats:Chat[];current:number;busy:boolean;model:string;provider:string;turns:{turn_id:string;status:string;version:number;error?:string;failure?:{category:string;retryable:boolean;attempts:number;message:string}}[];children:Child[];configured:boolean;configuring:boolean;providers:{id:string;label:string}[];models:string[]}>({chats:[],current:0,busy:false,model:'',provider:'',turns:[],children:[],configured:false,configuring:false,providers:[],models:[]});
const [text,setText]=useState(''),[stream,setStream]=useState(''),[tab,setTab]=useState('chat'),[tools,setTools]=useState<string[]>([]),[settings,setSettings]=useState(false),[notice,setNotice]=useState('');
const [boardLink,setBoardLink]=useState<BulletinLink|null>(null);
const [boardKind,setBoardKind]=useState<BoardKind>('frontier');
useEffect(()=>window.horizon.subscribe(event=>{
  if(event.kind==='board_reset_type')setBoardKind('frontier');
}),[]);
const [navCollapsed,setNavCollapsed]=useState(false);
const [sessionSearchOpen,setSessionSearchOpen]=useState(false);
const [sessionQuery,setSessionQuery]=useState('');
const sessionSearchButton=useRef<HTMLButtonElement>(null);
const sessionNeedle=sessionQuery.trim().toLocaleLowerCase();
const matchingSessions=state.chats.map((chat,index)=>({chat,index})).filter(({chat})=>!sessionNeedle||chat.title.toLocaleLowerCase().includes(sessionNeedle));
const requestedSource=useRef<string|null>(null);
const [sourceUrl,setSourceUrl]=useState<string|null>(null);
const [archiveDocument,setArchiveDocument]=useState<ArchiveDocument|null>(null);
const [selectedMaterial,setSelectedMaterial]=useState<MaterialPackage|null>(null);
const [detailHost,setDetailHost]=useState<HTMLDivElement|null>(null);
const [detail,setDetail]=useState<'material'|'report'|'html'|'term'|'hpedia'|'bulletin'|'archive-document'|'source-url'|'frontier'|null>(null);
const [localHtml,setLocalHtml]=useState<{name:string;content:React.ReactNode}|null>(null);
const htmlRequest=useRef(0);
const [detailWidth,setDetailWidth]=useState(440);
const workzone=useRef<HTMLDivElement>(null);
const dragStart=useRef<{x:number;width:number}|null>(null);
const requestedMaterial=useRef<string|null>(null);
const resizeDetail=(width:number)=>setDetailWidth(Math.max(240,Math.min(width,(workzone.current?.clientWidth||900)-320)));
const closeDetail=()=>{setBoardLink(null);htmlRequest.current++;requestedMaterial.current=null;requestedSource.current=null;setDetail(null);setLocalHtml(null);setSourceUrl(null);setArchiveDocument(null);};
const [feedbackHandoff,setFeedbackHandoff]=useState<FeedbackHandoff|null>(null);
const [provider,setProvider]=useState(''),[configuring,setConfiguring]=useState(false),[configError,setConfigError]=useState('');
const [voyagerView,setVoyagerView]=useState<any>(null),[voyagerHistory,setVoyagerHistory]=useState<{request_id:string}|null>(null),[frontierView,setFrontierView]=useState<Frontier|null>(null),[frontierViewEvent,setFrontierViewEvent]=useState<FrontierViewEvent|null>(null),[frontierError,setFrontierError]=useState<FrontierCommandError|null>(null);
const [cardView,setCardView]=useState<KnowledgeCard|null>(null),[cardViewEvent,setCardViewEvent]=useState<CardViewEvent|null>(null),[cardError,setCardError]=useState<CardCommandError|null>(null);
const [themeView,setThemeView]=useState<Theme|null>(null),[themeViewEvent,setThemeViewEvent]=useState<ThemeViewEvent|null>(null),[themeError,setThemeError]=useState<ThemeCommandError|null>(null);
const [keeperCount,setKeeperCount]=useState<number|null>(null),[keeperReview,setKeeperReview]=useState(0),[openPendingNonce,setOpenPendingNonce]=useState(0);
const pending=useRef<Record<string,string>>({});
const bottom=useRef<HTMLDivElement>(null);
const send=window.horizon.send;
useEffect(()=>{const unsubscribe=window.horizon.subscribe(e=>{if(e.kind==='archive_document'&&e.data.document.source_id===requestedSource.current){setArchiveDocument(e.data);setDetail('archive-document');}if(e.kind==='archive_source_url'&&e.data.source_id===requestedSource.current){setSourceUrl(e.data.url);setDetail('source-url');}if(e.kind==='material_package' && requestedMaterial.current && (!e.data || e.data.material_id===requestedMaterial.current)){setSelectedMaterial(e.data);setDetail(e.data?'material':null);}if(e.kind==='voyager_view'){setVoyagerView(e.data);setVoyagerHistory(current=>current?.request_id===e.data?.request_id?current:null);}if(e.kind==='voyager_history')setVoyagerHistory(e.data);if(e.kind==='frontier_view'){setFrontierView(e.data);setFrontierViewEvent({source:'view',operation_id:e.data?.operation_id,nonce:crypto.randomUUID()});setFrontierError(null);}if(e.kind==='card_view'){setCardView(e.data);setCardViewEvent({source:'view',operation_id:e.data?.operation_id,nonce:crypto.randomUUID()});setCardError(null);}if(e.kind==='theme_view'){setThemeView(e.data);setThemeViewEvent({source:'view',operation_id:e.data?.operation_id,nonce:crypto.randomUUID()});setThemeError(null);}if(e.kind==='keeper_review'){setKeeperCount(null);setKeeperReview(n=>n+1);}if(e.kind==='configuration_saved'){setConfiguring(false);setSettings(false);setConfigError('');}if(e.kind==='configuration_error'){setConfiguring(false);setConfigError(e.data);}if(e.kind==='state'){setState(e.data);if(e.data.busy)pending.current={};if(!e.data.busy)setStream('');} if(e.kind==='agent'){if(e.data.type==='retry')setNotice(`请求暂时失败，正在重试 ${e.data.attempt}/3，等待 ${Math.ceil(e.data.delayMs/1000)} 秒`);if(e.data.type==='text')setStream(s=>s+e.data.text);if(e.data.type==='error')setStream(s=>s+'\n错误：'+e.data.message);if(e.data.type==='tool_call'||e.data.type==='tool_result')setTools(s=>[...s,`${e.data.type==='tool_call'?'调用':'完成'} · ${e.data.name}${e.data.isError?' · 失败':''}`]);}if(e.kind==='command_error'){setNotice(e.data.code==='unfinished_turn'?'请先继续或放弃未完成的轮次，再发送新消息。':e.data.message);setFrontierError({operation_id:e.data.operation_id,code:e.data.code,message:e.data.message});setCardError({operation_id:e.data.operation_id,code:e.data.code,message:e.data.message,details:e.data.details});setThemeError({operation_id:e.data.operation_id,code:e.data.code,message:e.data.message,details:e.data.details});const saved=pending.current[e.data.operation_id];if(saved!==undefined){setText(current=>current||saved);delete pending.current[e.data.operation_id];}}if(e.kind==='notice')setNotice(e.data);});send({action:'ready'});return unsubscribe;},[]);
useEffect(()=>{bottom.current?.scrollIntoView({behavior:'smooth'});},[stream,state.chats,tab]);
const openSettings=()=>{setProvider(state.provider||state.providers[0]?.id||'deepseek');setConfigError('');setSettings(true);};
useEffect(()=>window.horizon.subscribe(event=>{
  if(event.kind==='open_settings') openSettings();
}),[state.provider,state.providers]);
const optionsMenu = useRef<HTMLDivElement>(null);
function chooseOption(action: () => void) {
  optionsMenu.current?.hidePopover();
  action();
}
const openKeeperPending=()=>{closeDetail();setOpenPendingNonce(value=>value+1);setTab('knowledge');};
const chat=state.chats[state.current];
const ownerFeedback=feedbackHandoff?.task_id===chat?.task_id?feedbackHandoff:null;
const closeOwnerFeedback=()=>setFeedbackHandoff(null);
const conversationOpen=Boolean(chat) && ['chat','trace'].includes(tab);
const submit=()=>{if(!text.trim()||state.busy||state.configuring)return;setTools([]);const operation_id=crypto.randomUUID();pending.current[operation_id]=text;send({action:'send',text,operation_id,expected_version:state.chats[state.current]?.version||1});setText('');};
const navigate=(next:string)=>{closeDetail();setTab(next);};
const materialsCommand=(value:unknown)=>{
  const command=value as {action:string;material_id?:string};
  if(command.action==='materials_open'){closeDetail();requestedMaterial.current=command.material_id||null;}
  send(value);
};
const openHtml=async(file:File)=>{
  closeDetail();
  const request=htmlRequest.current;
  try {
    const content=await readLocalHtml(file);
    if(request!==htmlRequest.current)return;
    setLocalHtml({name:file.name,content});
    setDetail('html');
  } catch {
    if(request===htmlRequest.current)setNotice('请选择可读取且不超过 10 MiB 的 HTML 文件。');
  }
};
const openArchiveSource=(source:ArchiveSource)=>{
  closeDetail();
  requestedSource.current=source.source_id;
  requestedMaterial.current=source.material_id;
  send({action:'source_open',source_id:source.source_id,task_id:chat?.task_id});
};
const previousFrontierDraft=useRef("");
const prepareFrontierSearch=(frontier:Frontier)=>{
  const context={
    前沿:frontier.question,前沿标识:frontier.frontier_id,版本:frontier.version,研究范围:frontier.scope.text,
    替代查询:frontier.scope.alternatives,必须包含:frontier.hard_constraints.must_include,排除:frontier.hard_constraints.exclude,
  };
  const draft='请直接使用检索器检索这个前沿，给出可追溯来源并说明覆盖缺口。需要保存资料时，先通过图书管理员征求我的确认。\n'+
    JSON.stringify(context,null,2);
  const previous=previousFrontierDraft.current;
  previousFrontierDraft.current=draft;
  setText(current=>{
    if(current.startsWith(draft))return current;
    const additional=previous&&current.startsWith(previous)
      ? current.slice(previous.length).replace(/^\n\n补充说明：\n/,'') : current;
    return draft+(additional.trim()?'\n\n补充说明：\n'+additional:'');
  });
  navigate('chat');
};
const openSearchHistory=(requestId?:string)=>{
  setVoyagerView(null);
  setVoyagerHistory(null);
  navigate('voyager');
  if(requestId)send({action:'voyager_open',request_id:requestId});
};
const renderFrontiers=(matches:React.ComponentProps<typeof FrontierTracking>['matches'],createRequest:number,lookup?:React.ComponentProps<typeof FrontierTracking>['lookup'],boardFrontier?:FrontierSummary)=><FrontierTracking deleted={state.hpedia_deleted} lookup={lookup} boardFrontier={boardFrontier} terms={state.cards||[]} matches={matches} createRequest={createRequest} frontiers={state.frontiers||[]} sources={state.archive_sources||[]} frontier={frontierView}
    viewEvent={frontierViewEvent} error={frontierError} detailHost={detail==='frontier'?detailHost:null}
    openDetail={()=>{closeDetail();setDetail('frontier');}} send={send} openSource={openArchiveSource}
    onRetrieve={prepareFrontierSearch} openVoyager={openSearchHistory}/>;
const navigation:[string,React.ReactNode,string][]=[
  ['materials','▱','资料库'],['hpedia',<HpediaIcon/>,'百科'],['bulletin',<span className="bulletin-icon"/>,'白板'],
  ['retriever','⌕','检索器'],['schedules','◷','自动任务'],
];
return <div className={'shell'+(navCollapsed?' navigation-collapsed':'')}>
  <div className="window-bar">
    <button className="navigation-toggle" aria-controls="workspace-navigation" aria-expanded={!navCollapsed}
      aria-label={navCollapsed?'展开导航':'折叠导航'} onClick={()=>setNavCollapsed(value=>!value)}></button>
  </div>
  <aside id="workspace-navigation">
    <div className="brand"><span className="mark">◈</span><span className="nav-label">horizon</span><small>RESEARCH</small></div>
    <button className="new" aria-label="新会话" title="新会话" disabled={state.busy} onClick={()=>{send({action:'new',operation_id:crypto.randomUUID()});navigate('chat');}}>⊕ <span className="nav-label">新会话</span></button>
    <nav className="nav" aria-label="工作区">
      {navigation.map(([id,icon,label])=><button key={id} title={label} aria-label={label}
        aria-current={tab===id||undefined} onClick={()=>navigate(id)}>
        <span aria-hidden="true">{icon}</span><span className="nav-label">{label}</span>
      </button>)}
    </nav>
    <div className="section">会话
      <Button type="button" ref={sessionSearchButton} aria-label="搜索会话" aria-pressed={sessionSearchOpen}
        title="按标题搜索会话" onClick={()=>{setSessionSearchOpen(!sessionSearchOpen);setSessionQuery('');}}>
        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="10" cy="10" r="6.5"/><path d="m15 15 6 6"/></svg>
      </Button></div>
    {sessionSearchOpen&&!navCollapsed&&<TextInput className="session-search" autoFocus type="search" aria-label="按会话标题搜索" placeholder="搜索会话标题" value={sessionQuery} onChange={e=>setSessionQuery(e.target.value)}
      onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();setSessionSearchOpen(false);setSessionQuery('');sessionSearchButton.current?.focus();}}}/>}
    <div className="sessions">{matchingSessions.length===0&&<p role="status">没有匹配的会话</p>}{matchingSessions.map(({chat:c,index:i})=><button disabled={state.busy} key={c.task_id} aria-label={c.title}
      aria-current={conversationOpen && i===state.current || undefined} title={c.title}
      className={conversationOpen && i===state.current?'selected':''}
      onClick={()=>{send({action:'select',task_id:c.task_id});navigate('chat');}}>
      <span className="session-icon" aria-hidden="true">◌</span><span className="nav-label">{c.title}</span>
    </button>)}</div>
    <button className="settings" aria-label="选项" title="选项" aria-haspopup="menu" popoverTarget="horizon-options">
      <span>⚙ <span className="nav-label">选项</span></span>
    </button>
<div id="horizon-options" popover="auto" ref={optionsMenu} role="menu" aria-label="选项"
  onToggle={event=>{
    if(event.newState==='open') event.currentTarget.querySelector('button')?.focus();
  }}
  onKeyDown={event=>{
    const items=Array.from(event.currentTarget.querySelectorAll('button'));
    const index=items.indexOf(document.activeElement as HTMLButtonElement);
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){
      event.preventDefault();
      items[(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length].focus();
    }
  }}>
  <button role="menuitem" onClick={()=>chooseOption(openSettings)}>模型设置</button>
  <button role="menuitem" onClick={()=>chooseOption(()=>navigate('feishu'))}>飞书适配器</button>
  <button role="menuitem" onClick={()=>chooseOption(()=>navigate('test-cleanup'))}>清理测试内容</button>
  <button role="menuitem" onClick={()=>chooseOption(()=>send({action:'export_diagnostics'}))}>导出诊断</button>
  <button role="menuitem" onClick={()=>chooseOption(()=>send({action:'exit'}))}>退出Horizon</button>
</div></aside><div className="workzone" ref={workzone}><main>{conversationOpen && <><header><strong>{chat?.title||'新会话'}</strong></header><div className="tabs"><button className={tab==='chat'?'active':''} onClick={()=>navigate('chat')}>对话</button><button className={tab==='trace'?'active':''} onClick={()=>navigate('trace')}>轨迹</button></div></>}{notice&&<div className="notice" role="alert">{notice}<button onClick={()=>setNotice('')}>×</button></div>}
{state.migration_reviews?.map(review=><MigrationReviewCard key={review.id} review={review} decide={target=>send({action:'hpedia_migration_decide',review_id:review.id,target})}/>)}
{state.gui_term_confirmations?.map(card=><TermConfirmationCard key={card.operation_id} card={card} terms={state.cards||[]} sources={state.archive_sources||[]}
  decide={choice=>send({action:card.entity==='question'?'question_confirm':card.entity==='source'?'source_confirm':card.entity==='frontier'?'frontier_confirm':card.entity==='progress'?'progress_confirm':card.entity==='test_cleanup'?'test_cleanup_confirm':'card_confirm',operation_id:card.operation_id,choice})}/>)}
{tab==='test-cleanup'?<TestCleanupPanel send={send} pending={Boolean(state.gui_term_confirmations?.some(card=>card.entity==='test_cleanup'))}/>:tab==='bulletin'?<><BulletinBoard deleted={state.hpedia_deleted} kind={boardKind} changeKind={setBoardKind} links={state.bulletin_links||[]} terms={state.cards||[]}
  selected={boardLink} onOpen={link=>{closeDetail();setBoardLink(link);setDetail(link.kind==='frontier'?'frontier':'hpedia');}}/>
  {boardLink?.kind==='frontier'?<div key={boardLink.id}>{renderFrontiers(()=>false,0,undefined,state.frontiers?.find(item=>item.frontier_id===boardLink.id))}</div>:
    boardLink?<HpediaPanel deleted={state.hpedia_deleted} key={boardLink.kind+boardLink.id} boardLink={boardLink} renderFrontiers={renderFrontiers} frontierRows={state.frontiers} questions={state.questions||[]} terms={state.cards||[]} send={send} closeDetail={closeDetail} cardEditor={{materials:state.materials||[],selected:cardView,viewEvent:cardViewEvent,commandError:cardError}}
  detailHost={detail==='hpedia'?detailHost:null} openDetail={()=>setDetail('hpedia')}/>:null}</>:tab==='hpedia'?<HpediaPanel deleted={state.hpedia_deleted} renderFrontiers={renderFrontiers} frontierRows={state.frontiers} questions={state.questions||[]} terms={state.cards||[]} send={send} closeDetail={closeDetail} cardEditor={{materials:state.materials||[],selected:cardView,viewEvent:cardViewEvent,commandError:cardError}}
  detailHost={detail==='hpedia'?detailHost:null} openDetail={()=>{closeDetail();setDetail('hpedia');}}/>:tab==='feishu'?<FeishuSetup send={send} close={()=>setTab('chat')}/>:tab==='retriever'?<RetrieverPanel send={send} onOpenHistory={()=>openSearchHistory()}/>:tab==='voyager'?<><SubscriptionPanel rows={state.subscriptions||[]} busy={state.subscription_busy||[]} send={send}/><VoyagerPanel editorFeedback={ownerFeedback?.owner==='voyager'?ownerFeedback:null} closeEditorFeedback={closeOwnerFeedback} requests={state.voyager_requests||[]} busy={state.voyager_busy||[]} selected={voyagerView} history={voyagerHistory} taskId={chat?.task_id} onPrepare={id=>{setText(current=>current||`请调用 delegate 工具，将检索 ${id} 交给历史检索子会话（角色 voyager），返回来源依据和限制。`);setTab('chat');}} onCommand={command=>{if(command.action==='materials_open')requestedMaterial.current=String(command.material_id);send(['voyager_create','materials_open'].includes(command.action)?{...command,task_id:chat?.task_id}:command);}}/></>:['knowledge','themes'].includes(tab)?<KnowledgeWorkbench key="knowledge" editorFeedback={ownerFeedback?.owner==='voyager'?null:ownerFeedback} closeEditorFeedback={closeOwnerFeedback} initialType={tab==='themes'?'theme':'all'} frontiers={state.frontiers||[]} cards={state.cards||[]} themes={state.themes||[]} materials={state.materials||[]} cardSelected={cardView} cardViewEvent={cardViewEvent} cardError={cardError} frontierSelected={frontierView} frontierViewEvent={frontierViewEvent} frontierError={frontierError} themeSelected={themeView} themeViewEvent={themeViewEvent} themeError={themeError} onPendingCount={count=>setKeeperCount(count)} openPendingNonce={openPendingNonce} onRetrieve={prepareFrontierSearch} onCommand={send}/>:tab==='materials'?null:tab==='schedules'?<SchedulePanel schedules={state.schedules||[]} limits={state.schedule_limits||{main:2,children:4}} onCommand={send}/>:tab==='trace'?<section aria-label="轨迹" />:<><div className="messages">{chat && <EditorReports key={chat.task_id} taskId={chat.task_id} detailHost={detail==='report'?detailHost:null} onOpenDetail={()=>{closeDetail();setDetail('report');}}
  materials={state.materials || []} children={state.children} disabled={state.busy || !state.configured}
  frontiers={state.frontiers || []} handoff={(draft:FeedbackHandoff)=>{
    setFeedbackHandoff(draft);
    navigate(draft.owner==='voyager'?'voyager':'knowledge');
  }}
  send={send} prepare={(material_id,options)=>setText(`请调用 delegate，将此 JSON 原样交给 editor 子会话生成报道。 ${JSON.stringify({material_id,...options})}`)}
  prepareRewrite={(report_id,base_version,question,options)=>setText(`请调用 delegate，将此 JSON 原样交给 editor 子会话核对来源并生成新版本。 ${JSON.stringify({report_id,base_version,question,...options})}`)}
  prepareSynthesis={command=>setText(`请调用 delegate，将以下 JSON 原样交给 editor 子会话生成综合报道。
${JSON.stringify(command)}`)}/>}
{!chat?.messages.length&&!state.busy?<div className="welcome"><div className="hero">◈</div><h1>从一个问题，开始研究</h1><p>与 Horizon 一起阅读文献、整理知识、探索新的发现。</p><div className="suggestions">{['总结当前知识库的研究进展','帮我梳理一个研究问题','查看可用的研究工作流'].map(t=><button key={t} onClick={()=>setText(t)}>{t} ↗</button>)}</div></div>:chat?.messages.map((m,i)=><article key={i} className={m.role==='You'?'user':'assistant'}>{m.role!=='You'&&<div className="author">◈ Horizon</div>}<Markdown>{m.text}</Markdown></article>)}{chat&&state.term_confirmations?.map(card=><TermConfirmationCard key={card.operation_id} card={card} terms={state.cards||[]} sources={state.archive_sources||[]}
  decide={choice=>send({action:'agent_term_confirm',task_id:chat.task_id,operation_id:card.operation_id,choice})}/>)}{chat&&state.editor_term_proposals?.map(proposal=>{
  const card:TermConfirmation={operation_id:proposal.proposal_id,expires_at:proposal.term_expires_at,
    entity:proposal.changes.some(change=>change.kind==='frontier')?'frontier':undefined,
    status:proposal.status==='approved'?'confirmed':proposal.status,error:proposal.decision?.error,
    changes:proposal.changes.map(change=>[change.action,{
      ...(change.kind==='frontier'?{frontier_id:change.id}:{card_id:change.id}),...change.after,reason:change.reason,
    }])};
  return <TermConfirmationCard key={proposal.proposal_id+':'+proposal.revision} card={card}
    decide={choice=>send({action:'editor_library_confirm',task_id:chat.task_id,schema_version:1,operation_id:crypto.randomUUID(),
      proposal_id:proposal.proposal_id,revision:proposal.revision,
      expected_versions:proposal.changes.map(change=>change.expected_version??null),
      decision:choice==='confirm'?'approve':'reject'})}/>;
})}{state.busy&&<article className="assistant"><div className="author">◈ Horizon <span className="pulse">●</span></div><Markdown>{stream||'正在思考…'}</Markdown></article>}<div ref={bottom}/></div><div className="compose-wrap">{tools.length>0&&<div className="activity">⌁ {tools[tools.length-1]}</div>}<div className="composer"><MultilineInput aria-label="发送消息" placeholder="发送消息，开始你的研究…" value={text} onChange={e=>setText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();submit();}}}/><div className="compose-footer"><span>◈ 研究助手</span>{state.configured?<><FixedSelect aria-label="选择模型" value={state.model} disabled={state.busy||state.configuring||configuring} onChange={e=>send({action:'select_model',model:e.target.value})}>{Array.from(new Set([state.model,...(state.models||[])])).filter(Boolean).map(m=><option key={m} value={m}>{m}</option>)}</FixedSelect><button disabled={state.busy||state.configuring||configuring} title="刷新模型列表" aria-label="刷新模型列表" onClick={()=>send({action:'refresh_models'})}><svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 7v5h-5"/><path d="M20 12a8 8 0 1 0-2.34 5.66M20 12a8 8 0 0 0-2.34-5.66"/></svg></button></>:<button onClick={openSettings}>配置模型 <span aria-hidden="true" className="disclosure-indicator"/></button>}{state.busy?<button className="send" aria-label="停止" onClick={()=>send({action:'stop'})}>■</button>:<button className="send" aria-label="发送" disabled={!text.trim()||state.configuring} onClick={submit}><svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20V4m-7 7 7-7 7 7"/></svg></button>}</div></div><footer>{state.busy?'Agent 正在运行':'就绪'} · Enter 发送 · Shift + Enter 换行</footer></div></>}
{tab==='materials'&&<ArchiveSourcesPanel sources={state.archive_sources||[]} send={send} onOpen={openArchiveSource}/>}
<MaterialsPanel showList={tab==='materials'} onOpenHtml={openHtml} jobs={state.web_jobs||[]} materials={state.materials||[]} selectedPackage={detail==='material'?selectedMaterial:null} detailHost={detailHost} taskId={chat?.task_id} onCommand={materialsCommand}/></main>
  {detail && <>
    <div className="detail-resize" role="separator" tabIndex={0} aria-label="调整详情宽度" aria-orientation="vertical"
      onPointerDown={event=>{dragStart.current={x:event.clientX,width:event.currentTarget.nextElementSibling!.getBoundingClientRect().width};event.currentTarget.setPointerCapture(event.pointerId);}}
      onPointerMove={event=>{const drag=dragStart.current;if(drag)resizeDetail(drag.width+drag.x-event.clientX);}}
      onLostPointerCapture={()=>{dragStart.current=null;}}
      onKeyDown={event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){
        event.preventDefault();resizeDetail(event.currentTarget.nextElementSibling!.getBoundingClientRect().width+(event.key==='ArrowLeft'?24:-24));
      }}}/>
    <section className="workspace-detail" aria-label="工作区详情" style={{width:detailWidth}}>
      <div className="detail-toolbar"><Button aria-label="关闭详情" onClick={closeDetail}>×</Button></div>
      <div className="detail-body" ref={setDetailHost}>
        {detail==='archive-document'&&archiveDocument&&<ArchiveDocumentView entry={archiveDocument} send={send}/>}
        {detail==='source-url'&&sourceUrl&&<section aria-label="原始来源链接">
          <p>{sourceUrl}</p>
          <button type="button" onClick={()=>send({action:'open_source_url',url:sourceUrl})}>在浏览器打开原文</button>
        </section>}
        {detail==='html'&&localHtml&&<article className="local-html" aria-label={localHtml.name}>{localHtml.content}</article>}
      </div>
    </section>
  </>}
</div>{settings&&<div className="overlay"><form className="modal" onSubmit={e=>{e.preventDefault();const d=new FormData(e.currentTarget);setConfiguring(true);setConfigError('');send({action:'configure',provider,url:d.get('url')||'',key:d.get('key')});}}><h2>模型设置</h2><p>选择提供商并输入 API Key，查询该凭据可见的模型。</p><label>模型提供商<FixedSelect name="provider" value={provider} disabled={configuring} onChange={e=>setProvider(e.target.value)}>{state.providers.map(p=><option key={p.id} value={p.id}>{p.label}</option>)}</FixedSelect></label>{provider==='custom'&&<label>API 地址<TextInput name="url" required type="url" placeholder="https://example.com/v1" disabled={configuring}/></label>}<label>API Key<TextInput name="key" type="password" required autoComplete="off" disabled={configuring}/></label><p className="hint">查询成功后，在对话框右下角选择模型。模型是否可调用还取决于账户权限与额度。</p>{configError&&<p role="alert">{configError}</p>}<div className="modal-actions"><button type="button" disabled={configuring} onClick={()=>setSettings(false)}>取消</button><button disabled={state.busy||state.configuring||configuring}>{configuring?'正在查询…':'确认并查询模型'}</button></div></form></div>}</div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
