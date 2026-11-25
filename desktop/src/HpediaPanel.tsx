import React, {useEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import type {CardSummary, KnowledgeCard} from './KnowledgeCardPanel';

export type Question = {
  question_id:string;
  version:number;
  prompt:string;
  answer:string;
  term_ids:string[];
  status?:'deleted';
};

function referencedText(text:string, terms:CardSummary[], openTerm:(id:string)=>void) {
  return text.split(/(\[\[term:[^\[\]\s]+\]\])/).map((part,index)=>{
    if(!part.startsWith('[[term:'))return part;
    const id=part.slice(7,-2);
    const term=terms.find(candidate=>candidate.card_id===id);
    return <button key={index} type="button" className="hpedia-term-link"
      onClick={()=>openTerm(id)}>{term?.title??'词典术语'}</button>;
  });
}

export function HpediaPanel({questions,terms,detailHost,openDetail,send}:{
  questions:Question[];
  terms:CardSummary[];
  detailHost:HTMLElement|null;
  openDetail:()=>void;
  send:(command:unknown)=>void;
}) {
  const [question,setQuestion]=useState<Question|null>(null);
  const [editing,setEditing]=useState(false);
  const [prompt,setPrompt]=useState('');
  const [answer,setAnswer]=useState('');
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
      if(event.data.status==='rejected'||event.data.status==='expired'){
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
    setQuestion(selected);
    setPrompt(selected?.prompt??'');
    setAnswer(selected?.answer??'');
    setSelectedTerm(null);
    setError('');
    setEditing(true);
    openDetail();
  };

  const show=(id:string)=>{
    setError('');
    setEditing(false);
    setSelectedTerm(null);
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
      ...(action!=='question_delete'?{prompt,answer}:{})});
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
    termToRead.current=id;
    send({action:'card_open',card_id:id,operation_id:crypto.randomUUID()});
  };

  const activeTerms=terms.filter(term=>term.status==='active');
  const detail=selectedTerm?<article aria-label="词典术语">
    <button type="button" onClick={()=>setSelectedTerm(null)}>返回问题</button>
    <h2>{selectedTerm.title}</h2>
    <p className="hpedia-text">{selectedTerm.content}</p>
    {!!selectedTerm.aliases.length&&<p>别名 {selectedTerm.aliases.join('、')}</p>}
  </article>:editing?<form className="hpedia-form" onSubmit={event=>{
    event.preventDefault();
    sendChange(question?'question_update':'question_create');
  }}>
    <h2>{question?'编辑问题':'新增问题'}</h2>
    <label>题干<textarea ref={promptInput} value={prompt} required onChange={event=>setPrompt(event.target.value)}/></label>
    <label>解答<textarea ref={answerInput} value={answer} required onChange={event=>setAnswer(event.target.value)}/></label>
    <div className="hpedia-reference-picker">
      <label>引用术语<select value={termChoice} onChange={event=>setTermChoice(event.target.value)}>
        <option value="">选择有效术语</option>
        {activeTerms.map(term=><option key={term.card_id} value={term.card_id}>{term.title}</option>)}
      </select></label>
      <button type="button" disabled={!termChoice} onClick={()=>insertReference('prompt')}>引用到题干</button>
      <button type="button" disabled={!termChoice} onClick={()=>insertReference('answer')}>引用到解答</button>
    </div>
    <button type="submit" disabled={waiting}>提交审核</button>
    {question&&<button type="button" disabled={waiting} onClick={()=>setEditing(false)}>返回详情</button>}
  </form>:question?<article aria-label="百科问题">
    <h2 className="hpedia-text">{referencedText(question.prompt,terms,openTerm)}</h2>
    <p className="hpedia-text">{referencedText(question.answer,terms,openTerm)}</p>
    <button type="button" disabled={waiting} onClick={()=>start(question)}>编辑问题</button>
    <button type="button" disabled={waiting} onClick={()=>sendChange('question_delete')}>删除问题</button>
  </article>:null;

  return <section className="hpedia-panel" aria-label="百科">
    <h1>百科</h1>
    <button type="button" disabled={waiting} onClick={()=>start(null)}>新增问题</button>
    {questions.length?<ul>{questions.map(row=><li key={row.question_id}>
      <button type="button" onClick={()=>show(row.question_id)}>{row.prompt.replace(
        /\[\[term:([^\[\]\s]+)\]\]/g,(_,id:string)=>terms.find(term=>term.card_id===id)?.title??'词典术语')}</button>
    </li>)}</ul>:<p>暂无百科问题。</p>}
    {detailHost&&createPortal(<>{detail}{error&&<p role="alert">{error}</p>}</>,detailHost)}
  </section>;
}
