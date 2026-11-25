import React,{useEffect,useState,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import Markdown from 'react-markdown';
import './style.css';
import './window-caption.css';
import {SubscriptionPanel,type Subscription} from './SubscriptionPanel';
import {VoyagerPanel,type VoyagerSummary} from './VoyagerPanel';
import {SchedulePanel,type Schedule} from './SchedulePanel';
import type {MaterialSummary} from './MaterialsPanel';
import {KnowledgePanel,type FrontierSummary,type Frontier,type FrontierViewEvent,type FrontierCommandError} from './KnowledgePanel';
import {KnowledgeCardPanel,type CardSummary,type KnowledgeCard,type CardViewEvent,type CardCommandError} from './KnowledgeCardPanel';
import {ThemePanel,type ThemeSummary,type Theme,type ThemeViewEvent,type ThemeCommandError} from './ThemePanel';
import {KnowledgeWorkbench} from './KnowledgeWorkbench';
import {EditorReports} from './EditorReports';
import {FeishuSetup} from './FeishuSetup';
import type {FeedbackHandoff} from './EditorFeedbackPreview';
type Child={child_id:string;session_id:string;parent_session_id:string;role:string;input_version:number;status:string;artifact_ref:string;result?:string;error?:string};
type TermConfirmation={operation_id:string;status:string;expires_at:number;changes:[string,Record<string,unknown>][]};

function TermConfirmationCard({card,decide}:{card:TermConfirmation;decide:(choice:'confirm'|'reject')=>void}){
  const [elapsed,setElapsed]=useState(false);
  useEffect(()=>{
    if(card.status!=='pending')return;
    const delay=card.expires_at*1000-Date.now();
    if(delay<=0){setElapsed(true);return;}
    const timer=window.setTimeout(()=>setElapsed(true),delay);
    return ()=>window.clearTimeout(timer);
  },[card.status,card.expires_at]);
  const status=elapsed&&card.status==='pending'?'expired':card.status;
  const labels:Record<string,string>={aliases:'别名',recall_terms:'检索词',sources:'来源',relations:'关系',
    questionable:'存疑',reason:'修改理由'};
  return <article className="assistant" aria-label="术语变更确认">
    <div className="author">图书管理员</div>
    {card.changes.map(([action,change],index)=><div key={index}>
      <strong>{({create:'新增',update:'修改',archive:'归档',delete:'删除'} as Record<string,string>)[action]} {String(change.title??change.card_id??'')}</strong>
      {typeof change.content==='string'&&<p>{change.content}</p>}
      <dl>{Object.entries(change).filter(([field])=>field in labels).map(([field,value])=><React.Fragment key={field}>
        <dt>{labels[field]}</dt><dd>{typeof value==='string'?value:JSON.stringify(value)}</dd>
      </React.Fragment>)}</dl>
    </div>)}
    {status==='pending'?<>
      <button onClick={()=>decide('confirm')}>确认</button>
      <button onClick={()=>decide('reject')}>取消</button>
    </>:<p>{({confirmed:'已确认',rejected:'已取消',expired:'已过期'} as Record<string,string>)[status]}</p>}
  </article>;
}
type Chat={task_id:string;version:number;title:string;messages:{role:string;text:string}[]};
declare global {interface Window {horizon:{send:(c:unknown)=>void;subscribe:(f:(e:any)=>void)=>()=>void}}}
const childRoleLabel=(role:string)=>role==='voyager'?'Voyager 子会话':role==='keeper'?'Keeper 知识确认子会话':`开发工具 · ${role}`;
function App(){
const [state,setState]=useState<{web_jobs?:string[];subscriptions?:Subscription[];subscription_busy?:string[];voyager_requests?:VoyagerSummary[];voyager_busy?:string[];materials?:(MaterialSummary&{version?:number})[];frontiers?:FrontierSummary[];cards?:CardSummary[];term_confirmations?:TermConfirmation[];gui_term_confirmations?:TermConfirmation[];themes?:ThemeSummary[];deliveries?:{delivery_id:string;version:number;status:string;attempts:unknown[];receipts:unknown[];checked?:boolean}[];delivery_enabled?:boolean;schedules?:Schedule[];schedule_limits?:{main:number;children:number};chats:Chat[];current:number;busy:boolean;model:string;provider:string;turns:{turn_id:string;status:string;version:number;error?:string;failure?:{category:string;retryable:boolean;attempts:number;message:string}}[];children:Child[];configured:boolean;configuring:boolean;providers:{id:string;label:string}[];models:string[]}>({chats:[],current:0,busy:false,model:'',provider:'',turns:[],children:[],configured:false,configuring:false,providers:[],models:[]});
const [text,setText]=useState(''),[stream,setStream]=useState(''),[tab,setTab]=useState('chat'),[tools,setTools]=useState<string[]>([]),[settings,setSettings]=useState(false),[notice,setNotice]=useState('');
const [feedbackHandoff,setFeedbackHandoff]=useState<FeedbackHandoff|null>(null);
const [provider,setProvider]=useState(''),[configuring,setConfiguring]=useState(false),[configError,setConfigError]=useState('');
const [voyagerView,setVoyagerView]=useState<any>(null),[voyagerHistory,setVoyagerHistory]=useState<any>(null),[frontierView,setFrontierView]=useState<Frontier|null>(null),[frontierViewEvent,setFrontierViewEvent]=useState<FrontierViewEvent|null>(null),[frontierError,setFrontierError]=useState<FrontierCommandError|null>(null);
const [cardView,setCardView]=useState<KnowledgeCard|null>(null),[cardViewEvent,setCardViewEvent]=useState<CardViewEvent|null>(null),[cardError,setCardError]=useState<CardCommandError|null>(null);
const [themeView,setThemeView]=useState<Theme|null>(null),[themeViewEvent,setThemeViewEvent]=useState<ThemeViewEvent|null>(null),[themeError,setThemeError]=useState<ThemeCommandError|null>(null);
const [keeperCount,setKeeperCount]=useState<number|null>(null),[keeperReview,setKeeperReview]=useState(0),[openPendingNonce,setOpenPendingNonce]=useState(0);
const pending=useRef<Record<string,string>>({});
const createPending=useRef<string|null>(null);
const bottom=useRef<HTMLDivElement>(null);
const send=window.horizon.send;
useEffect(()=>{const unsubscribe=window.horizon.subscribe(e=>{if(e.kind==='voyager_view'){setVoyagerView(e.data);setVoyagerHistory(null);if(createPending.current&&e.data?.operation_id===createPending.current){createPending.current=null;setTab(current=>current==='knowledge'||current==='themes'?'voyager':current);}}if(e.kind==='voyager_history')setVoyagerHistory(e.data);if(e.kind==='frontier_view'){setFrontierView(e.data);setFrontierViewEvent({source:'view',operation_id:e.data?.operation_id,nonce:crypto.randomUUID()});setFrontierError(null);}if(e.kind==='card_view'){setCardView(e.data);setCardViewEvent({source:'view',operation_id:e.data?.operation_id,nonce:crypto.randomUUID()});setCardError(null);}if(e.kind==='theme_view'){setThemeView(e.data);setThemeViewEvent({source:'view',operation_id:e.data?.operation_id,nonce:crypto.randomUUID()});setThemeError(null);}if(e.kind==='keeper_review'){setKeeperCount(null);setKeeperReview(n=>n+1);}if(e.kind==='configuration_saved'){setConfiguring(false);setSettings(false);setConfigError('');}if(e.kind==='configuration_error'){setConfiguring(false);setConfigError(e.data);}if(e.kind==='state'){setState(e.data);if(e.data.busy)pending.current={};if(!e.data.busy)setStream('');} if(e.kind==='agent'){if(e.data.type==='retry')setNotice(`请求暂时失败，正在重试 ${e.data.attempt}/3，等待 ${Math.ceil(e.data.delayMs/1000)} 秒`);if(e.data.type==='text')setStream(s=>s+e.data.text);if(e.data.type==='error')setStream(s=>s+'\n错误：'+e.data.message);if(e.data.type==='tool_call'||e.data.type==='tool_result')setTools(s=>[...s,`${e.data.type==='tool_call'?'调用':'完成'} · ${e.data.name}${e.data.isError?' · 失败':''}`]);}if(e.kind==='command_error'){setNotice(e.data.code==='unfinished_turn'?'请先继续或放弃未完成的轮次，再发送新消息。':e.data.message);setFrontierError({operation_id:e.data.operation_id,code:e.data.code,message:e.data.message});setCardError({operation_id:e.data.operation_id,code:e.data.code,message:e.data.message,details:e.data.details});setThemeError({operation_id:e.data.operation_id,code:e.data.code,message:e.data.message,details:e.data.details});if(createPending.current&&e.data.operation_id===createPending.current)createPending.current=null;const saved=pending.current[e.data.operation_id];if(saved!==undefined){setText(current=>current||saved);delete pending.current[e.data.operation_id];}}if(e.kind==='notice')setNotice(e.data);});send({action:'ready'});return unsubscribe;},[]);
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
const openKeeperPending=()=>{setOpenPendingNonce(value=>value+1);setTab('knowledge');};
const chat=state.chats[state.current];
const ownerFeedback=feedbackHandoff?.task_id===chat?.task_id?feedbackHandoff:null;
const closeOwnerFeedback=()=>setFeedbackHandoff(null);
const conversationOpen=Boolean(chat) && ['chat','trace'].includes(tab);
const submit=()=>{if(!text.trim()||state.busy||state.configuring)return;setTools([]);const operation_id=crypto.randomUUID();pending.current[operation_id]=text;send({action:'send',text,operation_id,expected_version:state.chats[state.current]?.version||1});setText('');};
return <div className="shell"><div className="window-bar" aria-hidden="true" /><aside><div className="brand"><span className="mark">◈</span> horizon <small>RESEARCH</small></div><button className="new" disabled={state.busy} onClick={()=>{send({action:'new',operation_id:crypto.randomUUID()});setTab('chat');}}>⊕ 新会话</button><div className="nav"><button aria-current={(tab==='knowledge') || undefined} onClick={()=>setTab('knowledge')}>知识库</button><button aria-current={(tab==='themes') || undefined} onClick={()=>setTab('themes')}>命题</button><button aria-current={(tab==='voyager') || undefined} onClick={()=>setTab('voyager')}>范围检索</button><button aria-current={(tab==='materials') || undefined} onClick={()=>setTab('materials')}>资料</button><button aria-current={(tab==='schedules') || undefined} onClick={()=>setTab('schedules')}>自动任务</button></div><div className="section">工作区 <span>⌕</span></div><div className="workspace">▾ &nbsp; Horizon 研究工作区</div><div className="sessions">{state.chats.map((c,i)=><button disabled={state.busy} key={c.task_id} aria-current={conversationOpen && i===state.current || undefined} title={c.title} className={conversationOpen && i===state.current?'selected':''} onClick={()=>{send({action:'select',task_id:c.task_id});setTab('chat');}}>{c.title}</button>)}</div><button className="settings" aria-haspopup="menu" popoverTarget="horizon-options">⚙ 选项 <span className={state.configured?'online':'offline'}>●</span></button>
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
  <button role="menuitem" onClick={()=>chooseOption(()=>setTab('feishu'))}>飞书适配器</button>
  <button role="menuitem" onClick={()=>chooseOption(()=>send({action:'export_diagnostics'}))}>导出诊断</button>
  <button role="menuitem" onClick={()=>chooseOption(()=>send({action:'exit'}))}>退出Horizon</button>
</div></aside><main>{conversationOpen && <><header><strong>{chat?.title||'新会话'}</strong><span>研究助手</span></header><div className="tabs"><button className={tab==='chat'?'active':''} onClick={()=>setTab('chat')}>对话</button><button className={tab==='trace'?'active':''} onClick={()=>setTab('trace')}>轨迹</button></div></>}{notice&&<div className="notice" role="alert">{notice}<button onClick={()=>setNotice('')}>×</button></div>}
{state.gui_term_confirmations?.map(card=><TermConfirmationCard key={card.operation_id} card={card}
  decide={choice=>send({action:'card_confirm',operation_id:card.operation_id,choice})}/>)}

{tab==='feishu'?<FeishuSetup send={send} close={()=>setTab('chat')}/>:tab==='voyager'?<><SubscriptionPanel rows={state.subscriptions||[]} busy={state.subscription_busy||[]} send={send}/><VoyagerPanel editorFeedback={ownerFeedback?.owner==='voyager'?ownerFeedback:null} closeEditorFeedback={closeOwnerFeedback} requests={state.voyager_requests||[]} busy={state.voyager_busy||[]} selected={voyagerView} history={voyagerHistory} taskId={chat?.task_id} onPrepare={id=>{setText(current=>current||`请调用 delegate 工具，将检索 ${id} 交给 voyager 子会话，返回来源依据和限制。`);setTab('chat');}} onCommand={command=>{if(command.action==='materials_open')setTab('materials');send(['voyager_create','materials_open'].includes(command.action)?{...command,task_id:chat?.task_id}:command);}}/></>:tab==='knowledge'||tab==='themes'?<KnowledgeWorkbench editorFeedback={ownerFeedback?.owner==='voyager'?null:ownerFeedback} closeEditorFeedback={closeOwnerFeedback} initialType={tab==='themes'?'theme':'all'} frontiers={state.frontiers||[]} cards={state.cards||[]} themes={state.themes||[]} materials={state.materials||[]} cardSelected={cardView} cardViewEvent={cardViewEvent} cardError={cardError} frontierSelected={frontierView} frontierViewEvent={frontierViewEvent} frontierError={frontierError} themeSelected={themeView} themeViewEvent={themeViewEvent} themeError={themeError} onPendingCount={count=>setKeeperCount(count)} openPendingNonce={openPendingNonce} onCommand={command=>{const value=command as {action?:string;operation_id?:string};if(value.action==='voyager_create'){createPending.current=value.operation_id||null;send(chat?.task_id?{...value,task_id:chat.task_id}:command);}else send(command);}}/>:tab==='materials'?<section aria-label="资料" />:tab==='schedules'?<SchedulePanel schedules={state.schedules||[]} limits={state.schedule_limits||{main:2,children:4}} onCommand={send}/>:tab==='trace'?<section aria-label="轨迹" />:<><div className="messages">{chat && <EditorReports key={chat.task_id} taskId={chat.task_id}
  materials={state.materials || []} children={state.children} disabled={state.busy || !state.configured}
  frontiers={state.frontiers || []} handoff={(draft:FeedbackHandoff)=>{
    setFeedbackHandoff(draft);
    setTab(draft.owner==='voyager'?'voyager':'knowledge');
  }}
  send={send} prepare={(material_id,options)=>setText(`请调用 delegate，将此 JSON 原样交给 editor 子会话生成报道。 ${JSON.stringify({material_id,...options})}`)}
  prepareRewrite={(report_id,base_version,question,options)=>setText(`请调用 delegate，将此 JSON 原样交给 editor 子会话核对来源并生成新版本。 ${JSON.stringify({report_id,base_version,question,...options})}`)}
  prepareSynthesis={command=>setText(`请调用 delegate，将以下 JSON 原样交给 editor 子会话生成综合报道。
${JSON.stringify(command)}`)}/>}
{!chat?.messages.length&&!state.busy?<div className="welcome"><div className="hero">◈</div><h1>从一个问题，开始研究</h1><p>与 Horizon 一起阅读文献、整理知识、探索新的发现。</p><div className="suggestions">{['总结当前知识库的研究进展','帮我梳理一个研究问题','查看可用的研究工作流'].map(t=><button key={t} onClick={()=>setText(t)}>{t} ↗</button>)}</div></div>:chat?.messages.map((m,i)=><article key={i} className={m.role==='You'?'user':'assistant'}><div className="author">{m.role==='You'?'探索者':'◈ Horizon'}</div><Markdown>{m.text}</Markdown></article>)}{chat&&state.term_confirmations?.map(card=><TermConfirmationCard key={card.operation_id} card={card}
  decide={choice=>send({action:'agent_term_confirm',task_id:chat.task_id,operation_id:card.operation_id,choice})}/>)}{state.busy&&<article className="assistant"><div className="author">◈ Horizon <span className="pulse">●</span></div><Markdown>{stream||'正在思考…'}</Markdown></article>}<div ref={bottom}/></div><div className="compose-wrap">{tools.length>0&&<div className="activity">⌁ {tools[tools.length-1]}</div>}<div className="composer"><textarea aria-label="发送消息" placeholder="发送消息，开始你的研究…" value={text} onChange={e=>setText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();submit();}}}/><div className="compose-footer"><span>◈ 研究助手</span>{state.configured?<><select aria-label="选择模型" value={state.model} disabled={state.busy||state.configuring||configuring} onChange={e=>send({action:'select_model',model:e.target.value})}>{Array.from(new Set([state.model,...(state.models||[])])).filter(Boolean).map(m=><option key={m} value={m}>{m}</option>)}</select><button disabled={state.busy||state.configuring||configuring} title="刷新模型列表" aria-label="刷新模型列表" onClick={()=>send({action:'refresh_models'})}>↻</button></>:<button onClick={openSettings}>配置模型⌄</button>}{state.busy?<button className="send" aria-label="停止" onClick={()=>send({action:'stop'})}>■</button>:<button className="send" aria-label="发送" disabled={!text.trim()||state.configuring} onClick={submit}><svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20V4m-7 7 7-7 7 7"/></svg></button>}</div></div><footer>{state.busy?'Agent 正在运行':'就绪'} · Enter 发送 · Shift + Enter 换行</footer></div></>}
</main>{settings&&<div className="overlay"><form className="modal" onSubmit={e=>{e.preventDefault();const d=new FormData(e.currentTarget);setConfiguring(true);setConfigError('');send({action:'configure',provider,url:d.get('url')||'',key:d.get('key')});}}><h2>模型设置</h2><p>选择提供商并输入 API Key，查询该凭据可见的模型。</p><label>模型提供商<select name="provider" value={provider} disabled={configuring} onChange={e=>setProvider(e.target.value)}>{state.providers.map(p=><option key={p.id} value={p.id}>{p.label}</option>)}</select></label>{provider==='custom'&&<label>API 地址<input name="url" required type="url" placeholder="https://example.com/v1" disabled={configuring}/></label>}<label>API Key<input name="key" type="password" required autoComplete="off" disabled={configuring}/></label><p className="hint">查询成功后，在对话框右下角选择模型。模型是否可调用还取决于账户权限与额度。</p>{configError&&<p role="alert">{configError}</p>}<div className="modal-actions"><button type="button" disabled={configuring} onClick={()=>setSettings(false)}>取消</button><button disabled={state.busy||state.configuring||configuring}>{configuring?'正在查询…':'确认并查询模型'}</button></div></form></div>}</div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
