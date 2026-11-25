import {ResultCard,ResultCards} from './ResultCards';
import {ModuleHeading} from './ModuleHeading';
import {Button} from './gui/elements/Button';
import {useModuleSearch} from './ModuleSearch';
import {TestTag} from './TestCleanupPanel';
import React, {useEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {convergenceLabel,type Convergence} from './ConvergenceSelect';
import {QuestionContents,TermContents,termLinkLabels,type Question} from './HpediaPanel';
import type {CardSummary,KnowledgeCard} from './KnowledgeCardPanel';

import {FixedSelect} from './gui/elements/ChoiceElements';
export type BoardKind = 'term'|'question'|'frontier';
export type BulletinLink = {
  tags?:string[]; summary:string; term_ids:string[];
  kind:'term'|'question';
  id:string;
  title:string;
  convergence:Exclude<Convergence,'resolved'>;
};

export function BulletinBoard({kind,changeKind,links,terms,detailHost,openDetail,send,children}:{
  kind:BoardKind; changeKind:(kind:BoardKind)=>void;
  links:BulletinLink[];
  children:(filter:(text:string,status:string,ids:string[])=>boolean,createRequest:number)=>React.ReactNode;
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
  const filter=useModuleSearch();
  const [createRequest,setCreateRequest]=useState(0);
  const visible=links.filter(link=>{
    if(link.kind!==kind)return false;
    const term=terms.find(item=>item.card_id===link.id);
    const ids=link.term_ids;
    return filter.matches(termLinkLabels([link.title,link.summary,...(term?.aliases||[])].join(' '),terms),link.convergence,ids);
  });
  return <section className="bulletin-panel" aria-label="白板">
    <ModuleHeading icon={<span className="bulletin-icon"/>} title="白板">
      <Button type="button" variant="primary" onClick={()=>{changeKind('frontier');setCreateRequest(n=>n+1);}}>新增前沿</Button>
    </ModuleHeading>
    {filter.controls(terms,{unresolved:'尚未收敛',conflicted:'存在矛盾',following:'关注中',paused:'已暂停',resolved:'已解决'},'搜索白板内容和前沿',
      <label>类型<FixedSelect aria-label="类型" value={kind} onChange={event=>changeKind(event.target.value as BoardKind)}>
        <option value="term">术语</option><option value="question">问题</option><option value="frontier">前沿</option>
      </FixedSelect></label>)}
    {kind!=='frontier'&&<ResultCards label="白板结果">{visible.map(link=>{
      const summary=link.summary;
      return <ResultCard key={link.kind+':'+link.id} title={<>{termLinkLabels(link.title,terms)} <TestTag tags={link.tags}/></>}
        summary={termLinkLabels(summary||'',terms)} status={convergenceLabel(link.convergence,link.kind)}
        selected={!!detailHost&&requested.current?.kind===link.kind&&requested.current.id===link.id} onOpen={()=>open(link.kind,link.id)}/>;
    })}</ResultCards>}
    <div hidden={kind!=='frontier'}>{children(filter.matches,createRequest)}</div>
    {detailHost&&selection&&createPortal(
      selection.kind==='term'?<article aria-label="词典术语"><TermContents term={selection.value}/></article>:
        <article aria-label="百科问题"><QuestionContents question={selection.value} terms={terms}
          openTerm={id=>open('term',id)}/></article>,detailHost)}
  </section>;
}
