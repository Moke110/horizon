import React, {useEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {convergenceLabel,type Convergence} from './ConvergenceSelect';
import {QuestionContents,TermContents,termLinkLabels,type Question} from './HpediaPanel';
import type {CardSummary,KnowledgeCard} from './KnowledgeCardPanel';

export type BulletinLink = {
  kind:'term'|'question';
  id:string;
  title:string;
  convergence:Exclude<Convergence,'resolved'>;
};

export function BulletinBoard({links,terms,detailHost,openDetail,send}:{
  links:BulletinLink[];
  terms:CardSummary[];
  detailHost:HTMLElement|null;
  openDetail:()=>void;
  send:(command:unknown)=>void;
}) {
  const [selection,setSelection]=useState<{kind:'term';value:KnowledgeCard}|{kind:'question';value:Question}|null>(null);
  const requested=useRef<{kind:BulletinLink['kind'];id:string}|null>(null);
  useEffect(()=>window.horizon.subscribe(event=>{
    if(event.kind==='bulletin_term'&&requested.current?.kind==='term'&&requested.current.id===event.data?.card_id){
      setSelection({kind:'term',value:event.data});
    }
    if(event.kind==='bulletin_question'&&requested.current?.kind==='question'&&requested.current.id===event.data?.question_id){
      setSelection({kind:'question',value:event.data});
    }
  }),[]);
  const open=(kind:BulletinLink['kind'],id:string)=>{
    requested.current={kind,id};
    setSelection(null);
    openDetail();
    send({action:'bulletin_open',kind,id});
  };
  return <section className="bulletin-panel" aria-label="白板">
    <h1>白板</h1>
    {links.length?<ul>{links.map(link=><li key={link.kind+':'+link.id}>
      <button type="button" onClick={()=>open(link.kind,link.id)}>
        {termLinkLabels(link.title,terms)} <small>{link.kind==='term'?'术语':'百科问题'} · {convergenceLabel(link.convergence,link.kind)}</small>
      </button>
    </li>)}</ul>:<p>暂无待解决的术语和问题。</p>}
    {detailHost&&selection&&createPortal(
      selection.kind==='term'?<article aria-label="词典术语"><TermContents term={selection.value}/></article>:
        <article aria-label="百科问题"><QuestionContents question={selection.value} terms={terms}
          openTerm={id=>open('term',id)}/></article>,detailHost)}
  </section>;
}
