import {useState} from 'react';
import {DictionarySearch} from './DictionarySearch';
import type {CardSummary} from './KnowledgeCardPanel';

function searchText(text:string) {
  return text.normalize('NFKC').toLowerCase().replace(/ß/g,'ss').replace(/ς/g,'σ').trim().replace(/\s+/g,' ');
}

export function useModuleSearch(openEntry?:(query:string)=>boolean) {
  const [draft,setDraft]=useState('');
  const [query,setQuery]=useState('');
  const [status,setStatus]=useState('all');
  const [related,setRelated]=useState<string[]>([]);
  const matches=(text:string, state:string, ids:string[], ignoreStatus=false) =>
    (ignoreStatus||status==='all'||state===status) && searchText(text).includes(query) &&
    (!related.length||related.some(id=>ids.includes(id)));
  const controls=(terms:CardSummary[],options:Record<string,string>,placeholder:string,extraFilter?:React.ReactNode, beforeQuery?:React.ReactNode, scopeDisabled=false)=>
    <DictionarySearch query={draft} onPasteQuery={openEntry} setQuery={setDraft} scope={status} changeScope={setStatus}
      cards={terms} selected={related} changeSelected={setRelated} submit={()=>setQuery(openEntry?.(draft)?'':searchText(draft))}
      scopeDisabled={scopeDisabled} beforeQuery={beforeQuery} extraFilter={extraFilter} scopeOptions={{all:'全部',...options}} placeholder={placeholder}/>;
  return {matches, controls};
}
