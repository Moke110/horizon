import {ResultCard,ResultCards} from './ResultCards';
import {ModuleHeading} from './ModuleHeading';
import {useModuleSearch} from './ModuleSearch';
import {TestTag} from './TestCleanupPanel';
import {termLinkLabels} from './HpediaPanel';
import type {CardSummary} from './KnowledgeCardPanel';
import {FixedSelect} from './gui/elements/ChoiceElements';

export type BoardKind = 'term'|'question'|'frontier';
export type BulletinLink = {
  kind:BoardKind; id:string; title:string; summary:string; term_ids:string[];
  validity?:'valid'|'questionable'; tags?:string[];
};

export function BulletinBoard({kind,changeKind,links,terms,selected,onOpen}:{
  kind:BoardKind; changeKind:(kind:BoardKind)=>void; links:BulletinLink[]; terms:CardSummary[];
  selected:BulletinLink|null; onOpen:(link:BulletinLink)=>void;
}) {
  const filter=useModuleSearch();
  const visible=links.filter(link=>{
    if(link.kind!==kind)return false;
    const aliases=link.kind==='term'?terms.find(item=>item.card_id===link.id)?.aliases||[]:[];
    const text=termLinkLabels([link.title,link.summary,...aliases].join(' '),terms);
    return filter.matches(text,link.validity||'frontier',link.term_ids);
  });
  return <section className="bulletin-panel" aria-label="白板">
    <ModuleHeading icon={<span className="bulletin-icon"/>} title="白板"/>
    {filter.controls(terms,{valid:'有效',questionable:'存疑',frontier:'不适用'},'搜索白板内容',
      <label>类型<FixedSelect aria-label="类型" value={kind} onChange={event=>changeKind(event.target.value as BoardKind)}>
        <option value="term">术语</option><option value="question">问题</option><option value="frontier">前沿</option>
      </FixedSelect></label>)}
    <ResultCards label="白板结果">{visible.map(link=><ResultCard key={link.kind+':'+link.id}
      title={<>{termLinkLabels(link.title,terms)} <TestTag tags={link.tags}/></>}
      summary={termLinkLabels(link.summary,terms)} status="追踪中"
      selected={selected?.kind===link.kind&&selected.id===link.id} onOpen={()=>onOpen(link)}/>)}</ResultCards>
  </section>;
}
