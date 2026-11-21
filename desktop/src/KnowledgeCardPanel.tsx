import React,{useEffect,useRef,useState} from 'react';

export type CardSummary={card_id:string;version:number;title:string;status:string;provenance?:string;source_pending?:boolean;source_removed?:boolean;questionable?:boolean;updated_at:number};
type SourceInput={material_id:string;excerpt:string;locator:string};
type SavedSource=SourceInput&{title:string;material_version:number;content_hash:string;status?:'removed'};
type MaterialSummary={material_id:string;title:string};
export type KnowledgeCard={schema_version:number;operation_id:string;read_operation_id?:string;card_id:string;version:number;title:string;content:string;aliases:string[];recall_terms:string[];sources:SavedSource[];relations:string[];provenance:string;source_pending:boolean;questionable:boolean;known:boolean;status:string;created_at:number;updated_at:number};
export type CardConflictDetails={expected_version?:number;current_version?:number;current?:KnowledgeCard;submitted?:Record<string,unknown>;changed?:string[]};
export type CardViewEvent={source:'view';operation_id?:string;nonce:string};
export type CardCommandError={operation_id?:string;code?:string;message?:string;details?:CardConflictDetails};

type CardDraft={title:string;content:string;aliases:string;recallTerms:string;sources:SourceInput[];relations:string[];questionable:boolean};
export type CardDiffRow={field:string;label:string;mine:string;theirs:string};

const statusLabel:Record<string,string>={active:'有效',archived:'已归档'};
const lines=(s:string)=>s.split('\n').map(v=>v.trim()).filter(Boolean);
const provenanceLabel=(card:{provenance?:string})=>card.provenance==='sourced'?'有外部依据':'用户陈述 · 来源待补充';
const draftFrom=(card:KnowledgeCard):CardDraft=>({
  title:card.title,content:card.content,aliases:card.aliases.join('\n'),recallTerms:card.recall_terms.join('\n'),
  sources:card.sources.map(({material_id,excerpt,locator})=>({material_id,excerpt,locator})),
  relations:card.relations,questionable:card.questionable,
});
const draftFields=(draft:CardDraft,materialName:(id:string)=>string,cardName:(id:string)=>string) => [
  {field:'title',label:'标题',mine:draft.title,raw:draft.title},
  {field:'content',label:'内容',mine:draft.content,raw:draft.content},
  {field:'aliases',label:'别名',mine:lines(draft.aliases).join('\n'),raw:lines(draft.aliases)},
  {field:'recall_terms',label:'检索词',mine:lines(draft.recallTerms).join('\n'),raw:lines(draft.recallTerms)},
  {field:'sources',label:'来源',mine:draft.sources.map(s=>[materialName(s.material_id),s.locator,s.excerpt].join(' · ')).join('、'),raw:draft.sources},
  {field:'relations',label:'关系',mine:draft.relations.map(cardName).join('、'),raw:draft.relations},
  {field:'questionable',label:'存疑',mine:draft.questionable?'是':'否',raw:draft.questionable},
];
const sameValue=(a:unknown,b:unknown)=>JSON.stringify(a)===JSON.stringify(b);
/** Field-by-field difference between a local draft and the latest committed card. */
const cardDiff=(draft:CardDraft,committed:KnowledgeCard,materialName:(id:string)=>string,cardName:(id:string)=>string):CardDiffRow[]=>{
  const latest=draftFields(draftFrom(committed),materialName,cardName);
  return draftFields(draft,materialName,cardName).flatMap((row,index)=>{
    const other=latest[index];
    return sameValue(row.raw,other.raw)?[]:[{field:row.field,label:row.label,mine:row.mine||'（空）',theirs:other.mine||'（空）'}];
  });
};

const isFullCard=(value:KnowledgeCard|null|undefined):value is KnowledgeCard=>!!value&&typeof value.card_id==='string'&&Array.isArray(value.recall_terms)&&Array.isArray(value.sources);

type Pending={kind:'load';card_id:string;operation_id:string}|{kind:'save';operation_id:string;card_id?:string}|{kind:'conflict';card_id:string;operation_id:string};

export function KnowledgeCardPanel({cards,materials,selected,viewEvent,commandError,onCommand}:{cards:CardSummary[];materials:MaterialSummary[];selected:KnowledgeCard|null;viewEvent:CardViewEvent|null;commandError:CardCommandError|null;onCommand:(c:unknown)=>void}){
  const [title,setTitle]=useState(''),[content,setContent]=useState(''),[aliases,setAliases]=useState(''),[recallTerms,setRecallTerms]=useState('');
  const [sources,setSources]=useState<SourceInput[]>([]),[relations,setRelations]=useState<string[]>([]),[questionable,setQuestionable]=useState(false);
  const [editing,setEditing]=useState<{card_id:string;version:number}|null>(null);
  const [latest,setLatest]=useState<KnowledgeCard|null>(null);
  const [loading,setLoading]=useState(false),[saving,setSaving]=useState(false),[message,setMessage]=useState('');
  const pending=useRef<Pending|null>(null);
  const archivePending=useRef<{operation_id:string;card_id:string}|null>(null);
  const send=(operation_id:string,action:string,extra:object={})=>onCommand({action,schema_version:1,operation_id,...extra});
  const draft=():CardDraft=>({title,content,aliases,recallTerms,sources,relations,questionable});
  const clearDraft=()=>{setEditing(null);setLatest(null);setTitle('');setContent('');setAliases('');setRecallTerms('');setSources([]);setRelations([]);setQuestionable(false);};
  const populate=(card:KnowledgeCard)=>{const next=draftFrom(card);setEditing({card_id:card.card_id,version:card.version});setTitle(next.title);setContent(next.content);setAliases(next.aliases);setRecallTerms(next.recallTerms);setSources(next.sources);setRelations(next.relations);setQuestionable(next.questionable);setLatest(null);};
  const load=(card:CardSummary)=>{
    const operation_id=crypto.randomUUID();
    pending.current={kind:'load',card_id:card.card_id,operation_id};
    setLoading(true);setSaving(false);setLatest(null);setMessage('正在读取完整已提交版本…');
    send(operation_id,'card_open',{card_id:card.card_id});
  };
  const save=()=>{
    if(loading||saving||latest)return;
    const payload={title,content,aliases:lines(aliases),recall_terms:lines(recallTerms),sources,relations,questionable};
    const operation_id=crypto.randomUUID();
    pending.current={kind:'save',operation_id,card_id:editing?.card_id};
    setSaving(true);setLatest(null);
    setMessage(editing?`正在保存修改（基于版本 ${editing.version}）…`:'正在创建知识卡片…');
    if(editing){send(operation_id,'card_update',{card_id:editing.card_id,expected_version:editing.version,...payload});}
    else{send(operation_id,'card_create',payload);}
  };
  const archive=(card:CardSummary)=>{
    if(archivePending.current)return;
    const operation_id=crypto.randomUUID();
    archivePending.current={operation_id,card_id:card.card_id};
    setMessage('正在归档…');
    send(operation_id,'card_archive',{card_id:card.card_id,expected_version:card.version});
  };
  useEffect(()=>{
    if(!viewEvent||viewEvent.source!=='view'||!isFullCard(selected))return;
    const work=pending.current;
    if(!work)return;
    if(work.kind==='load'){
      if(work.card_id!==selected.card_id||work.operation_id!==selected.read_operation_id)return;
      pending.current=null;setLoading(false);setSaving(false);
      if(selected.status==='archived'){setMessage('已归档的知识卡片为只读。');return;}
      populate(selected);
      setMessage(`已载入版本 ${selected.version}，保存将按该版本校验。`);
      return;
    }
    if(work.kind==='conflict'){
      if(work.card_id!==selected.card_id||work.operation_id!==selected.read_operation_id)return;
      pending.current=null;setLoading(false);setSaving(false);setLatest(selected);
      setMessage(`保存未提交：该卡片已更新到版本 ${selected.version}。下方显示草稿与当前已提交内容的差异；不会自动覆盖，草稿已保留。`);
      return;
    }
    if(viewEvent.operation_id!==work.operation_id||selected.operation_id!==work.operation_id)return;
    if(work.card_id&&work.card_id!==selected.card_id)return;
    pending.current=null;setSaving(false);clearDraft();
    setMessage(`已保存为版本 ${selected.version}。`);
  },[viewEvent,selected]);
  useEffect(()=>{
    if(!viewEvent||viewEvent.source!=='view'||!isFullCard(selected))return;
    const work=archivePending.current;
    if(!work||viewEvent.operation_id!==work.operation_id||selected.operation_id!==work.operation_id||selected.card_id!==work.card_id||selected.status!=='archived')return;
    archivePending.current=null;
    if(editing?.card_id===work.card_id)clearDraft();
    setMessage(`已归档为版本 ${selected.version}；记录、来源与关系保留，归档的卡片在面板中只读。`);
  },[viewEvent,selected,editing]);
  useEffect(()=>{
    if(!commandError?.operation_id)return;
    const work=pending.current;
    if(work&&commandError.operation_id===work.operation_id){
      if(work.kind==='save'&&commandError.code==='version_conflict'){
        const current=commandError.details?.current;
        if(isFullCard(current)){
          pending.current=null;setSaving(false);setLoading(false);setLatest(current);
          setMessage(`保存未提交：该卡片已更新到版本 ${current.version}。下方显示草稿与当前已提交内容的差异；不会自动覆盖，草稿已保留。`);
          return;
        }
        if(work.card_id){
          const operation_id=crypto.randomUUID();
          pending.current={kind:'conflict',card_id:work.card_id,operation_id};
          setSaving(false);setLoading(true);
          setMessage('保存未提交：内容已被更新，正在读取最新版本以显示差异…');
          send(operation_id,'card_open',{card_id:work.card_id});
          return;
        }
      }
      pending.current=null;setLoading(false);setSaving(false);
      setMessage(`${work.kind==='load'?'读取':work.kind==='conflict'?'读取最新版本':'保存'}未完成：${commandError.message||commandError.code||'请检查后重试'}${work.kind==='save'?'；草稿已保留。':'。'}`);
      return;
    }
    const archived=archivePending.current;
    if(archived&&commandError.operation_id===archived.operation_id){
      archivePending.current=null;
      setMessage(`归档未完成：${commandError.message||commandError.code||'请检查后重试'}。`);
    }
  },[commandError]);
  const materialName=(id:string)=>materials.find(m=>m.material_id===id)?.title||selected?.sources.find(s=>s.material_id===id)?.title||id;
  const cardName=(id:string)=>cards.find(c=>c.card_id===id)?.title||id;
  const conflict=latest?cardDiff(draft(),latest,materialName,cardName):[];
  const changeSource=(index:number,fields:Partial<SourceInput>)=>setSources(current=>current.map((source,i)=>i===index?{...source,...fields}:source));
  return <div className="trace"><h2>知识卡片</h2>
  <form onSubmit={e=>{e.preventDefault();save();}}><fieldset disabled={loading||saving}>
  <label>标题<input required disabled={loading} value={title} onChange={e=>setTitle(e.target.value)}/></label>
  <label>内容<textarea required disabled={loading} value={content} onChange={e=>setContent(e.target.value)}/></label>
  <label>别名（每行一个）<textarea disabled={loading} value={aliases} onChange={e=>setAliases(e.target.value)}/></label>
  <label>检索词（每行一个）<textarea value={recallTerms} onChange={e=>setRecallTerms(e.target.value)}/></label>
  <fieldset><legend>来源</legend>
  {sources.map((source,index)=><div key={index}>
    <label>已保存的资料<select required value={source.material_id} onChange={e=>changeSource(index,{material_id:e.target.value})}>
      <option value="">选择资料</option>
      {source.material_id&&!materials.some(m=>m.material_id===source.material_id)&&<option value={source.material_id}>{materialName(source.material_id)}</option>}
      {materials.map(m=><option key={m.material_id} value={m.material_id}>{m.title}</option>)}
    </select></label>
    <label>定位<input required value={source.locator} onChange={e=>changeSource(index,{locator:e.target.value})}/></label>
    <label>原文摘录<textarea required value={source.excerpt} onChange={e=>changeSource(index,{excerpt:e.target.value})}/></label>
    <button type="button" onClick={()=>setSources(current=>current.filter((_,i)=>i!==index))}>移除来源</button>
  </div>)}
  <button type="button" disabled={!materials.length||sources.length>=16} onClick={()=>setSources(current=>[...current,{material_id:'',locator:'',excerpt:''}])}>添加来源</button>
  {!materials.length&&!sources.length&&<p>可在「资料」保存来源，再到这里添加引用。</p>}
  </fieldset>
  <label>关联知识卡片<select multiple value={relations} onChange={e=>setRelations(Array.from(e.target.selectedOptions,option=>option.value))}>
    {cards.filter(card=>card.card_id!==editing?.card_id).map(card=><option key={card.card_id} value={card.card_id}>{card.title}{card.status==='archived'?'（已归档）':''}</option>)}
  </select></label>
  <label><input type="checkbox" disabled={loading} checked={questionable} onChange={e=>setQuestionable(e.target.checked)}/> 标为存疑</label>
  {!sources.length&&<p>未填写来源时，卡片记为「用户陈述 · 来源待补充」。</p>}
  <button type="submit" disabled={loading||saving||!!latest}>{editing?'保存修改':'创建知识卡片'}</button>{editing&&<button type="button" disabled={saving} onClick={()=>{pending.current=null;setSaving(false);clearDraft();setMessage('已取消修改。');}}>取消修改</button>}
  </fieldset></form>
  {message&&<p role="status">{message}</p>}
  {latest&&<section className="child-result" role="alert"><h3>版本冲突：草稿与当前已提交版本 {latest.version} 的差异</h3>
  <p>你的保存没有覆盖他人已提交的内容。逐项核对后再决定：采用最新版本作为基线会保留你的草稿文字，但提交时会写入一个新的版本。</p>
  {conflict.length===0?<p>当前草稿与已提交内容一致；可直接重新保存。</p>:<ul>{conflict.map(row=><li key={row.field}><strong>{row.label}</strong><br/>你的草稿：{row.mine}<br/>已提交（v{latest.version}）：{row.theirs}</li>)}</ul>}
  <button type="button" disabled={loading||saving||latest.status==='archived'} onClick={()=>{setEditing({card_id:latest.card_id,version:latest.version});setLatest(null);setMessage(`已把草稿改为基于最新版本 ${latest.version}；请核对后保存。`);}}>保留草稿，基于最新版本重试</button>
  <button type="button" disabled={loading||saving} onClick={()=>{if(latest.status==='archived'){clearDraft();setMessage('该卡片已归档，已放弃草稿。');}else{populate(latest);setMessage(`已放弃草稿并载入版本 ${latest.version}。`);}}}>放弃草稿</button>
  </section>}
  {isFullCard(selected)&&<section className="child-result"><h3>当前已提交版本</h3>
  <p>{selected.title} · {statusLabel[selected.status]} · 版本 {selected.version}</p><p>{selected.content}</p>
  <p>别名：{selected.aliases.join('、')||'无'} · 检索词：{selected.recall_terms.join('、')||'无'} · 关联：{selected.relations.map(cardName).join('、')||'无'}</p>
  <p>{provenanceLabel(selected)}{selected.questionable?' · 存疑':''} · {selected.known?'已知':'未计入已知'}</p>
  {selected.sources.map(source=><p key={JSON.stringify(source)}>{source.title} · 资料版本 {source.material_version} · {source.locator}{source.status==='removed'?' · 原资料已永久移除':''}<br/>{source.excerpt}</p>)}
  </section>}
  <h3>知识卡片列表</h3><p>列表摘要不用于编辑；编辑会读取完整已提交版本。归档的卡片仍会列出并标明状态。</p>{cards.length===0&&<p>尚无知识卡片。填写上方表单创建第一张卡片。</p>}
  {cards.map(card=><section className="child-result" key={card.card_id}><h3>{card.title}</h3><p>{provenanceLabel(card)}{card.source_removed?' · 引用的资料已移除':''}{card.questionable?' · 存疑':''} · {statusLabel[card.status]||card.status} · 版本 {card.version}</p>
  <button disabled={loading||saving} onClick={()=>load(card)}>{card.status==='archived'?'查看完整版本（只读）':'编辑（读取完整版本）'}</button>
  {card.status!=='archived'&&<button disabled={loading||saving} onClick={()=>archive(card)}>归档</button>}
  </section>)}
  </div>;
}
