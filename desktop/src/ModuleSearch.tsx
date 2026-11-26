import {useState} from 'react';
import {DictionarySearch} from './DictionarySearch';
import type {CardSummary} from './KnowledgeCardPanel';

export function useModuleSearch() {
  const [draft,setDraft]=useState('');
  const [query,setQuery]=useState('');
  const [status,setStatus]=useState('all');
  const [related,setRelated]=useState<string[]>([]);
  const matches=(text:string, state:string, ids:string[]) =>
    (status==='all'||state===status) && text.toLocaleLowerCase().includes(query) &&
    (!related.length||related.some(id=>ids.includes(id)));
  const controls=(terms:CardSummary[],options:Record<string,string>,placeholder:string,extraFilter?:React.ReactNode, beforeQuery?:React.ReactNode)=>
    <DictionarySearch query={draft} setQuery={setDraft} scope={status} changeScope={setStatus}
      cards={terms} selected={related} changeSelected={setRelated} submit={()=>setQuery(draft.trim().toLocaleLowerCase())}
      beforeQuery={beforeQuery} extraFilter={extraFilter} scopeOptions={{all:'全部',...options}} placeholder={placeholder}/>;
  return {matches, controls};
}
