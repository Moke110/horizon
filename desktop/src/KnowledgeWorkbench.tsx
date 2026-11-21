import React,{useEffect,useMemo,useRef,useState} from 'react';
import {KnowledgeCardPanel,type CardSummary,type KnowledgeCard,type CardViewEvent,type CardCommandError} from './KnowledgeCardPanel';
import {KnowledgePanel,type FrontierSummary,type Frontier,type FrontierViewEvent,type FrontierCommandError} from './KnowledgePanel';
import {ThemePanel,type ThemeSummary,type Theme,type ThemeViewEvent,type ThemeCommandError} from './ThemePanel';
import {KnowledgeHistoryPanel,type HistoryKind,type HistoryRecord} from './KnowledgeHistoryPanel';

type MaterialOption={material_id:string;title:string;version?:number};
type EntityType='card'|'theme'|'frontier';
type TypeFilter='all'|EntityType;
type ScopeFilter='active'|'inactive'|'pending';
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
type SearchValues={query:string;type:TypeFilter;scope:ScopeFilter;relationTarget:string};
type SearchRequest={operation_id:string;values:SearchValues;offset:number};

const PAGE_SIZE=20;
const TYPE_LABEL:Record<EntityType,string>={card:'知识卡片',theme:'命题',frontier:'关注问题'};
const STATUS_LABEL:Record<string,string>={active:'有效',archived:'已归档',following:'关注中',paused:'已暂停',resolved:'已解决',deleted:'已删除'};
const truncate=(value:string,length=56)=>value.length>length?value.slice(0,length)+'…':value;
const targetOf=(value:string):RelationTarget|null=>{
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
  onCommand,
}:{
  initialType?:TypeFilter;
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
}){
  const [query,setQuery]=useState('');
  const [type,setType]=useState<TypeFilter>(initialType);
  const [scope,setScope]=useState<ScopeFilter>('active');
  const [relationTarget,setRelationTarget]=useState('');
  const [page,setPage]=useState(0);
  const [active,setActive]=useState<ActiveSelection|null>(null);
  const [historyOpen,setHistoryOpen]=useState(false);
  const [pageData,setPageData]=useState<SearchPage|null>(null);
  const [loading,setLoading]=useState(true);
  const [searchError,setSearchError]=useState('');
  const results=useRef<HTMLUListElement>(null);
  const detailHeading=useRef<HTMLHeadingElement>(null);
  const historyButton=useRef<HTMLButtonElement>(null);
  const latestSearch=useRef<SearchRequest|null>(null);
  const focusAfterSearch=useRef(false);
  const skipHeadingFocus=useRef(false);

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
      offset,limit:PAGE_SIZE,
    });
  };

  useEffect(()=>{
    const unsubscribe=window.horizon.subscribe(event=>{
      const latest=latestSearch.current;
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
        if(!latest||event.data.operation_id!==latest.operation_id)return;
        focusAfterSearch.current=false;
        setLoading(false);
        setPageData(null);
        setSearchError(event.data.message||event.data.code||'知识库搜索未能完成；可重试。');
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
    if(!active)return;
    if(skipHeadingFocus.current){skipHeadingFocus.current=false;return;}
    detailHeading.current?.focus();
  },[active?.nonce]);

  const pageCount=Math.max(1,Math.ceil((pageData?.total||0)/PAGE_SIZE));
  const currentPage=Math.min(page,pageCount-1);
  const rows=pageData?.results||[];
  const loadingBlocking=loading&&!pageData&&!searchError;
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
  const activeCardSummary=active?.type==='card'?cards.find(card=>card.card_id===active.id):undefined;
  const activeThemeSummary=active?.type==='theme'?themes.find(theme=>theme.theme_id===active.id):undefined;
  const activeFrontierSummary=active?.type==='frontier'?frontiers.find(frontier=>frontier.frontier_id===active.id):undefined;
  const activeTitle=active?.mode==='create'?`新建${TYPE_LABEL[active.type]}`
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
    setHistoryOpen(false);
    setActive({type:row.kind,mode:'open',id:row.id,version:row.version,nonce:crypto.randomUUID()});
  };
  const startCreate=(kind:EntityType)=>{
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
  const changeType=(next:TypeFilter)=>{focusAfterSearch.current=false;setType(next);setPage(0);searchNow({query,type:next,scope,relationTarget},0);};
  const changeScope=(next:ScopeFilter)=>{focusAfterSearch.current=false;setScope(next);setPage(0);searchNow({query,type,scope:next,relationTarget},0);};
  const changeRelation=(next:string)=>{focusAfterSearch.current=false;setRelationTarget(next);setPage(0);searchNow({query,type,scope,relationTarget:next},0);};
  const submit=()=>{
    const values:SearchValues={query,type,scope,relationTarget};
    setPage(0);
    focusAfterSearch.current=true;
    searchNow(values,0);
  };
  const goPage=(next:number)=>{
    const values=latestSearch.current?.values||{query,type,scope,relationTarget};
    setPage(next);
    searchNow(values,next*PAGE_SIZE);
  };
  const retry=()=>{
    const latest=latestSearch.current;
    if(latest)searchNow(latest.values,latest.offset);
  };

  return <div className="workbench">
    <section className="workbench-list" aria-label="知识库搜索">
      <form className="workbench-filters" role="search" onSubmit={event=>{event.preventDefault();submit();}}>
        <label>搜索<input type="search" value={query} onChange={event=>setQuery(event.target.value)} placeholder="标题、判断、问题或范围"/></label>
        <div className="workbench-filter-row">
          <label>类型<select value={type} onChange={event=>changeType(event.target.value as TypeFilter)}>
            <option value="all">全部</option>
            <option value="card">知识卡片</option>
            <option value="theme">命题</option>
            <option value="frontier">关注问题</option>
          </select></label>
          <label>范围<select value={scope} onChange={event=>changeScope(event.target.value as ScopeFilter)}>
            <option value="active">当前有效</option>
            <option value="inactive">归档与停用</option>
            <option value="pending">待确认</option>
          </select></label>
        </div>
        <label>关联记录<select value={relationTarget} onChange={event=>changeRelation(event.target.value)} disabled={!cards.length&&!themes.length}>
          <option value="">全部关联</option>
          {cards.length>0&&<optgroup label="知识卡片">{cards.map(card=><option key={card.card_id} value={`card:${card.card_id}`}>{truncate(card.title)}{card.status==='archived'?'（已归档）':''} · v{card.version}</option>)}</optgroup>}
          {themes.length>0&&<optgroup label="命题">{themes.map(theme=><option key={theme.theme_id} value={`theme:${theme.theme_id}`}>{truncate(theme.judgement)}{theme.status==='archived'?'（已归档）':''} · v{theme.version}</option>)}</optgroup>}
        </select></label>
        <div className="workbench-filter-actions"><span>{target?'只显示与该记录直接关联的记录（双向）':'不筛选关联记录'}</span><button type="submit">搜索</button></div>
      </form>
      <div className="workbench-create" role="group" aria-label="新建知识库记录">
        <span>新建</span>
        <button type="button" onClick={()=>startCreate('card')}>知识卡片</button>
        <button type="button" onClick={()=>startCreate('theme')}>命题</button>
        <button type="button" onClick={()=>startCreate('frontier')}>关注问题</button>
      </div>
      <p className="workbench-count" aria-live="polite">{loading?'正在搜索…':searchError?'搜索未完成':`共 ${pageData?.total||0} 条`}{!loading&&!searchError&&pageCount>1?` · 第 ${currentPage+1}/${pageCount} 页`:''}</p>
      {loadingBlocking?<p className="workbench-status" role="status">正在读取知识卡片、命题与关注问题…</p>
      :searchError?<div className="workbench-error" role="alert"><p>{searchError}</p><button type="button" onClick={retry}>重试</button></div>
      :scope==='pending'&&rows.length===0?<div className="workbench-empty workbench-pending" role="status">
        <h3>待确认：当前为空</h3>
        <p>Keeper 尚未产生待确认记录，因此这里为空；不会显示未经确认的提议，也不提供审批操作。</p>
      </div>
      :rows.length===0?<div className="workbench-empty" role="status"><p>{target?'所选关联记录在当前范围与类型下没有直接关联记录。':query.trim()?`没有匹配「${query.trim()}」的记录。`:'当前范围没有知识卡片、命题或关注问题。可在左侧新建。'}</p></div>
      :<ul className="workbench-results" ref={results} aria-label="搜索结果">
        {rows.map(row=><li key={`${row.kind}:${row.id}`}>
          <button type="button" data-result className={active?.mode==='open'&&active.type===row.kind&&active.id===row.id?'selected':''} aria-current={active?.mode==='open'&&active.type===row.kind&&active.id===row.id?true:undefined} onClick={()=>openResult(row)}>
            <span className="workbench-result-title">{row.title||'（无标题）'}</span>
            {row.summary&&<span className="workbench-result-summary">{row.summary}</span>}
            <span className="workbench-result-meta">
              <span>{TYPE_LABEL[row.kind]}</span>
              <span>{scopeStatus(scope,row)}</span>
              <span>版本 v{row.version}</span>
              {row.questionable&&<span className="workbench-badge-questionable">存疑</span>}
              {row.source_pending&&<span>来源待补充</span>}
              {scope==='pending'&&<span>待确认</span>}
            </span>
          </button>
        </li>)}
      </ul>}
      {!loadingBlocking&&!searchError&&pageCount>1&&<nav className="workbench-paging" aria-label="搜索结果分页">
        <button type="button" disabled={currentPage===0} onClick={()=>goPage(currentPage-1)}>上一页</button>
        <span>第 {currentPage+1} / {pageCount} 页</span>
        <button type="button" disabled={currentPage>=pageCount-1} onClick={()=>goPage(currentPage+1)}>下一页</button>
      </nav>}
    </section>
    <section className="workbench-detail" aria-label="知识库详情">
      {active?<>
        <header className="workbench-detail-head">
          <div>
            <span className="workbench-kind">{TYPE_LABEL[active.type]}</span>
            <h2 ref={detailHeading} tabIndex={-1}>{activeTitle||'知识库记录'}</h2>
            <p>{active.mode==='create'?'保存会直接创建并提交':`${STATUS_LABEL[activeStatus]||activeStatus||'已提交'}${activeVersion?` · 版本 v${activeVersion}`:''}`}</p>
          </div>
          <div className="workbench-detail-actions">
            {active.mode==='open'&&active.id&&<button ref={historyButton} type="button" aria-expanded={historyOpen} aria-controls="knowledge-history" onClick={()=>{if(historyOpen)closeHistory();else setHistoryOpen(true);}}>{historyOpen?'收起版本历史':'版本历史'}</button>}
            <button type="button" onClick={()=>{setHistoryOpen(false);setActive(null);}}>关闭</button>
          </div>
        </header>
        {active.type==='card'&&<KnowledgeCardPanel key={active.nonce} cards={cards} materials={materials} selected={activeCard} viewEvent={cardViewEvent} commandError={cardError} onCommand={onCommand} embedded showList={false} openRequest={requestFor('card')} onCommitted={committed('card')}/>}
        {active.type==='theme'&&<ThemePanel key={active.nonce} themes={themes} materials={materials} cards={cards} selected={activeTheme} viewEvent={themeViewEvent} commandError={themeError} onCommand={onCommand} embedded showList={false} openRequest={requestFor('theme')} onCommitted={committed('theme')}/>}
        {active.type==='frontier'&&<KnowledgePanel key={active.nonce} frontiers={frontiers} selected={activeFrontier} viewEvent={frontierViewEvent} commandError={frontierError} onCommand={onCommand} embedded showList={false} openRequest={requestFor('frontier')} onCommitted={committed('frontier')}/>}
        {historyOpen&&active.mode==='open'&&active.id&&<KnowledgeHistoryPanel key={`${active.type}:${active.id}`} kind={active.type} id={active.id} currentVersion={activeVersion} onCommand={onCommand} onUndoApplied={undoApplied} onClose={closeHistory}/>}
      </>:<div className="workbench-empty">
        <h2>知识库工作台</h2>
        <p>从左侧选择一条已提交记录，在右侧读取完整版本并编辑、保存或检查来源；也可以新建知识卡片、命题或关注问题。</p>
        <p>搜索支持当前有效、归档与停用、待确认范围；待确认视图只显示实际存在的待复核记录。</p>
      </div>}
    </section>
  </div>;
}
