import {CopyEntryId,matchesEntryId} from './HpediaIdentity';
import {ResultCard,ResultCards} from './ResultCards';
import {HpediaTransfer} from './HpediaTransfer';
import {HpediaIcon} from './HpediaIcon';
import {useModuleSearch} from './ModuleSearch';
import {ModuleHeading} from './ModuleHeading';
import {Button} from './gui/elements/Button';
import {MultilineInput} from './gui/elements/FormElements';
import {FixedSelect} from './gui/elements/ChoiceElements';
import {TestTag} from './TestCleanupPanel';
import React, {useEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {KnowledgeCardPanel,type CardSummary,type KnowledgeCard} from './KnowledgeCardPanel';

import {ConvergenceSelect,entryState,stateFields,convergenceLabel,type Convergence} from './ConvergenceSelect';

export type Question = {
  tags?:string[];
  question_id:string;
  version:number;
  prompt:string;
  answer:string;
  term_ids:string[];
  status?:'deleted';
  validity?:"valid"|"questionable";tracked?:boolean;convergence?:Convergence;
};

function referencedText(text:string, terms:CardSummary[], openTerm:(id:string)=>void, deleted:Record<string,string>) {
  return text.split(/(\[\[term:[^\[\]\s]+\]\])/).map((part,index)=>{
    if(!part.startsWith('[[term:'))return part;
    const id=part.slice(7,-2);
    const term=terms.find(candidate=>candidate.card_id===id);
    if(!term&&!deleted[id])return <span key={index}>{id}（待确认）</span>;
    return <Button key={index} type="button" className="hpedia-term-link"
      onClick={()=>openTerm(id)}>{term?.title??(id+'（条目已删除）')}</Button>;
  });
}

export function termLinkLabels(text:string,terms:CardSummary[],deleted:Record<string,string>={}){
  return text.replace(/\[\[term:([^\[\]\s]+)\]\]/g,
    (_,id:string)=>terms.find(term=>term.card_id===id)?.title??(id+(deleted[id]?'（条目已删除）':'（待确认）')));
}

export function QuestionContents({question,terms,openTerm,deleted}:{deleted:Record<string,string>;question:Question;terms:CardSummary[];openTerm:(id:string)=>void}) {
  return <>
    <h2 className="hpedia-text">{referencedText(question.prompt,terms,openTerm,deleted)} <TestTag tags={question.tags}/></h2>
    <CopyEntryId id={question.question_id}/>
    <p className="hpedia-text">{referencedText(question.answer,terms,openTerm,deleted)}</p>
    <p>{convergenceLabel(entryState(question),'question')}</p>
  </>;
}

export function TermContents({term}:{term:KnowledgeCard}) {
  return <>
    <h2>{term.title} <TestTag tags={term.tags}/></h2>
    <CopyEntryId id={term.card_id}/>
    <p className="hpedia-text">{term.content}</p>
    <p>{convergenceLabel(entryState(term),'term')}</p>
    {!!term.aliases.length&&<p>别名 {term.aliases.join('、')}</p>}
  </>;
}

export function HpediaPanel({questions,terms,deleted={},detailHost,openDetail,closeDetail,cardEditor,send,renderFrontiers,frontierRows=[],boardLink}:{
  frontierRows?:{frontier_id:string;version:number}[];
  boardLink?:{kind:string;id:string};
  renderFrontiers:(matches:(text:string,state:string,ids:string[],tracked:boolean)=>boolean, createRequest:number, lookup?:{id:string;version:number;nonce:string})=>React.ReactNode;
  closeDetail:()=>void;
  cardEditor:Pick<React.ComponentProps<typeof KnowledgeCardPanel>,'materials'|'selected'|'viewEvent'|'commandError'>;
  deleted?:Record<string,string>;
  questions:Question[];
  terms:CardSummary[];
  detailHost:HTMLElement|null;
  openDetail:()=>void;
  send:(command:unknown)=>void;
}) {
  const [deletedLookup,setDeletedLookup]=useState('');
  const [frontierCreate,setFrontierCreate]=useState(0);
  const [frontierLookup,setFrontierLookup]=useState<{id:string;version:number;nonce:string}>();
  const [entryType,setEntryType]=useState('question');
  const [termRequest,setTermRequest]=useState<React.ComponentProps<typeof KnowledgeCardPanel>['openRequest']>(null);
  const [question,setQuestion]=useState<Question|null>(null);
  const [editing,setEditing]=useState(false);
  const [prompt,setPrompt]=useState('');
  const [answer,setAnswer]=useState('');
  const [convergence,setConvergence]=useState<Convergence>('resolved');
  const [termChoice,setTermChoice]=useState('');
  const [selectedTerm,setSelectedTerm]=useState<KnowledgeCard|null>(null);
  const [waiting,setWaiting]=useState(false);
  const [error,setError]=useState('');
  const operation=useRef('');
  const expiryTimer=useRef(0);
  const termToRead=useRef('');
  const promptInput=useRef<HTMLTextAreaElement>(null);
  const answerInput=useRef<HTMLTextAreaElement>(null);

  useEffect(()=>{
    const unsubscribe=window.horizon.subscribe(event=>{
    if(event.kind==='question_view'){
      const value=event.data as Question;
      if(value.status==='deleted'){
        setQuestion(null);
        setEditing(false);
        return;
      }
      setQuestion(value);
      setEditing(false);
      setSelectedTerm(null);
    }
    if(event.kind==='card_view'&&event.data?.card_id===termToRead.current){
      termToRead.current='';
      setSelectedTerm(event.data as KnowledgeCard);
    }
    if(event.kind==='question_confirmation'&&event.data.operation_id===operation.current){
      window.clearTimeout(expiryTimer.current);
      if(event.data.status==='pending'){
        expiryTimer.current=window.setTimeout(()=>{
          operation.current='';
          setWaiting(false);
          setError('确认已过期。');
        },Math.max(0,event.data.expires_at*1000-Date.now()));
        return;
      }
      operation.current='';
      setWaiting(false);
      if(event.data.status==='returned'){
        setError(event.data.error?.message||'修改未提交，请重新审核。');
      }else if(event.data.status==='rejected'||event.data.status==='expired'){
        setError(event.data.status==='expired'?'确认已过期。':'已取消修改。');
      }
    }
    if(event.kind==='command_error'&&event.data.operation_id===operation.current){
      operation.current='';
      setWaiting(false);
      setError(event.data.message);
    }
    });
    return ()=>{unsubscribe();window.clearTimeout(expiryTimer.current);};
  },[]);

  const start=(selected:Question|null)=>{
    setTermRequest(null);
    setQuestion(selected);
    setPrompt(selected?.prompt??'');
    setAnswer(selected?.answer??'');
    setConvergence(entryState(selected??undefined));
    setSelectedTerm(null);
    setError('');
    setEditing(true);
    openDetail();
  };

  const show=(id:string)=>{
    setError('');
    setEditing(false);
    setSelectedTerm(null);
    setTermRequest(null);
    setQuestion(null);
    openDetail();
    send({action:'question_open',question_id:id});
  };

  const sendChange=(action:'question_create'|'question_update'|'question_delete')=>{
    if(waiting)return;
    operation.current=crypto.randomUUID();
    setWaiting(true);
    setError('');
    send({action,schema_version:1,operation_id:operation.current,
      ...(question?{question_id:question.question_id,expected_version:question.version}:{}),
      ...(action!=='question_delete'?{prompt,answer,...stateFields(convergence)}:{})});
  };

  const insertReference=(field:'prompt'|'answer')=>{
    const input=(field==='prompt'?promptInput:answerInput).current;
    if(!input||!termChoice)return;
    const value=field==='prompt'?prompt:answer;
    const marker='[[term:'+termChoice+']]';
    const {selectionStart,selectionEnd}=input;
    const next=value.slice(0,selectionStart)+marker+value.slice(selectionEnd);
    if(field==='prompt')setPrompt(next);else setAnswer(next);
    requestAnimationFrame(()=>{
      input.focus();
      input.setSelectionRange(selectionStart+marker.length,selectionStart+marker.length);
    });
  };

  const openTerm=(id:string)=>{
    if(deleted[id]){
      if(boardLink)openDetail();else closeDetail?.();
      setDeletedLookup(id);return;
    }
    setDeletedLookup('');
    termToRead.current=id;
    send({action:'card_open',card_id:id,operation_id:crypto.randomUUID()});
  };

  const filter=useModuleSearch(query=>{
    const removed=Object.keys(deleted).find(id=>matchesEntryId(query,id,id[0]));
    setDeletedLookup(removed??'');
    if(removed){closeDetail?.();return true;}
    const term=terms.find(item=>matchesEntryId(query,item.card_id,'t'));
    if(term){setEntryType('term');editTerm(term);return true;}
    const question=questions.find(item=>matchesEntryId(query,item.question_id,'q'));
    if(question){setEntryType('question');show(question.question_id);return true;}
    const frontier=frontierRows.find(item=>matchesEntryId(query,item.frontier_id,'f'));
    if(frontier){setEntryType('frontier');setFrontierLookup({id:frontier.frontier_id,version:frontier.version,nonce:crypto.randomUUID()});return true;}
    return false;
  });
  const [tracking,setTracking]=useState('all');
  const matchesTracking=(tracked:boolean)=>tracking==='all'||(tracking==='yes')===tracked;
  const matchesEntry=(text:string, item:Question|CardSummary, ids:string[])=>{
    const {validity,tracked}=stateFields(entryState(item));
    return matchesTracking(tracked)&&filter.matches(text,validity,ids);
  };
  const visibleQuestions=questions.filter(item=>item.status!=='deleted' && matchesEntry(termLinkLabels(item.prompt+' '+item.answer,terms,deleted),item,item.term_ids||[]));
  const activeTerms=terms.filter(term=>term.status==='active');
  const editTerm=(term:CardSummary|null)=>{
    const nonce=crypto.randomUUID();
    setTermRequest(term?{kind:'open',id:term.card_id,version:term.version,nonce}:{kind:'create',nonce});
    openDetail();
  };
  const visibleTerms=activeTerms.filter(term=>matchesEntry(
    [term.title,...(term.aliases||[]),term.content||''].join(' '),term,[term.card_id]));
  const detail=termRequest?<KnowledgeCardPanel {...cardEditor} cards={terms} onCommand={send}
    embedded showList={false} termMode openRequest={termRequest}
    onCommitted={(id,version)=>setTermRequest({kind:'open',id,version,nonce:crypto.randomUUID()})}/>:selectedTerm?<article aria-label="术语">
    <button type="button" onClick={()=>setSelectedTerm(null)}>返回问题</button>
    <TermContents term={selectedTerm}/>
  </article>:editing?<form className="hpedia-form" onSubmit={event=>{
    event.preventDefault();
    sendChange(question?'question_update':'question_create');
  }}>
    <h2>{question?'编辑问题':'新增问题'}</h2>
    <label>题干<MultilineInput ref={promptInput} value={prompt} required onChange={event=>setPrompt(event.target.value)}/></label>
    <label>解答<MultilineInput ref={answerInput} value={answer} required onChange={event=>setAnswer(event.target.value)}/></label>
    <ConvergenceSelect value={convergence} onChange={setConvergence}/>
    <div className="hpedia-reference-picker">
      <label>引用术语<FixedSelect value={termChoice} onChange={event=>setTermChoice(event.target.value)}>
        <option value="">选择有效术语</option>
        {activeTerms.map(term=><option key={term.card_id} value={term.card_id}>{term.title}</option>)}
      </FixedSelect></label>
      <button type="button" disabled={!termChoice} onClick={()=>insertReference('prompt')}>引用到题干</button>
      <button type="button" disabled={!termChoice} onClick={()=>insertReference('answer')}>引用到解答</button>
    </div>
    <button type="submit" disabled={waiting}>提交审核</button>
    {question&&<button type="button" disabled={waiting} onClick={()=>setEditing(false)}>返回详情</button>}
  </form>:question?<article aria-label="百科问题">
    <QuestionContents question={question} terms={terms} openTerm={openTerm} deleted={deleted}/>
    <button type="button" disabled={waiting} onClick={()=>start(question)}>编辑问题</button>
    <button type="button" disabled={waiting} onClick={()=>sendChange('question_delete')}>删除问题</button>
  </article>:null;

  useEffect(()=>{
    if(boardLink?.kind==='question'){
      send({action:'question_open',question_id:boardLink.id});
    }else if(boardLink?.kind==='term'){
      const term=terms.find(row=>row.card_id===boardLink.id);
      if(term)setTermRequest({kind:'open',id:term.card_id,version:term.version,nonce:crypto.randomUUID()});
    }
  },[boardLink]);

  if(boardLink)return detailHost?createPortal(<>{deleted[deletedLookup]?<p role="status">{deletedLookup} · 条目已删除</p>:detail}{error&&<p role="alert">{error}</p>}</>,detailHost):null;
  return <section className="hpedia-panel" aria-label="百科">
    <ModuleHeading icon={<HpediaIcon/>} title="百科">
      <Button variant="primary" type="button" disabled={waiting} popoverTarget="hpedia-create">创建</Button>
      <div id="hpedia-create" popover="auto" role="group" aria-label="创建条目">
        <Button value="term" popoverTarget="hpedia-create" popoverTargetAction="hide" onClick={()=>editTerm(null)}>术语</Button>
        <Button value="question" popoverTarget="hpedia-create" popoverTargetAction="hide" onClick={()=>start(null)}>问题</Button>
        <Button value="frontier" popoverTarget="hpedia-create" popoverTargetAction="hide" onClick={()=>setFrontierCreate(value=>value+1)}>前沿</Button>
      </div>
    </ModuleHeading>
    <HpediaTransfer/>
    {deleted[deletedLookup]&&<p role="status">{deletedLookup} · 条目已删除</p>}
    {filter.controls(terms,{valid:'有效',questionable:'存疑'},entryType==='frontier'?'搜索前沿题目和范围':entryType==='term'?'搜索名称、别名和释义':'搜索题干和解答',
      <label>追踪中<FixedSelect aria-label="追踪中" value={tracking} onChange={event=>setTracking(event.target.value)}>
        <option value="all">全部</option><option value="yes">是</option><option value="no">否</option></FixedSelect></label>,
      <FixedSelect aria-label="条目类型" value={entryType} onChange={event=>{setEntryType(event.target.value);closeDetail();}}>
        <option value="question">问题</option><option value="term">术语</option><option value="frontier">前沿</option></FixedSelect>,entryType==='frontier')}
    <div hidden={entryType!=='frontier'}>{renderFrontiers((text,status,ids,tracked)=>matchesTracking(tracked)&&filter.matches(text,status,ids,true),frontierCreate,frontierLookup)}</div>
    {entryType==='frontier'?null:entryType==='term'?<ResultCards label="百科术语结果">{visibleTerms.map(term=><ResultCard key={term.card_id}
      entry={stateFields(entryState(term))} title={term.title} summary={term.content||''} selected={!!detailHost && termRequest?.kind==='open' && termRequest.id===term.card_id}
      onOpen={()=>editTerm(term)}/>)}</ResultCards>:<ResultCards label="百科问题结果">{visibleQuestions.map(row=><ResultCard key={row.question_id}
      entry={stateFields(entryState(row))} title={<>{termLinkLabels(row.prompt,terms,deleted)} <TestTag tags={row.tags}/></>}
      summary={termLinkLabels(row.answer,terms,deleted)} selected={!!detailHost && row.question_id===question?.question_id}
      onOpen={()=>show(row.question_id)}/>)}</ResultCards>}
    {detailHost&&createPortal(<>{detail}{error&&<p role="alert">{error}</p>}</>,detailHost)}
  </section>;
}
