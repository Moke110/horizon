import {Button} from './gui/elements/Button';
import {FixedSelect} from './gui/elements/ChoiceElements';
import React,{useEffect,useRef,useState} from 'react';
import {MultilineInput, TextInput} from './gui/elements/FormElements';
import {TermLists, type SourceInput} from './TermLists';
import {TestTag} from './TestCleanupPanel';

import {ConvergenceSelect,entryState,stateFields,convergenceLabel,type Convergence} from './ConvergenceSelect';

export type CardSummary={content?:string;aliases?:string[];tags?:string[];card_id:string;version:number;title:string;status:string;provenance?:string;source_pending?:boolean;source_removed?:boolean;questionable?:boolean;validity?:"valid"|"questionable";tracked?:boolean;convergence?:Convergence;updated_at:number};
type SavedSource=SourceInput&{title:string;material_version:number;content_hash:string;status?:'removed'};
type MaterialSummary={material_id:string;title:string};
export type KnowledgeCard={tags?:string[];schema_version:number;operation_id:string;read_operation_id?:string;card_id:string;version:number;title:string;content:string;aliases:string[];sources:SavedSource[];relations:string[];provenance:string;source_pending:boolean;questionable:boolean;validity?:"valid"|"questionable";tracked?:boolean;convergence?:Convergence;known:boolean;status:string;created_at:number;updated_at:number};
export type CardConflictDetails={expected_version?:number;current_version?:number;current?:KnowledgeCard;submitted?:Record<string,unknown>;changed?:string[]};
export type CardViewEvent={source:'view';operation_id?:string;nonce:string};
export type CardCommandError={operation_id?:string;code?:string;message?:string;details?:CardConflictDetails};

type CardDraft={title:string;content:string;aliases:string;sources:SourceInput[];relations:string[];convergence:Convergence};
export type CardDiffRow={field:string;label:string;mine:string;theirs:string};

const statusLabel:Record<string,string>={active:'有效',archived:'已归档'};
const lines=(s:string)=>s.split('\n').map(v=>v.trim()).filter(Boolean);
const provenanceLabel=(card:{provenance?:string})=>card.provenance==='sourced'?'有外部依据':'用户陈述 · 来源待补充';
const draftFrom=(card:KnowledgeCard):CardDraft=>({
  title:card.title,content:card.content,aliases:card.aliases.join('\n'),
  sources:card.sources.map(({material_id,excerpt,locator})=>({material_id,excerpt,locator})),
  relations:card.relations,convergence:entryState(card),
});
const draftFields=(draft:CardDraft,materialName:(id:string)=>string,cardName:(id:string)=>string) => [
  {field:'title',label:'标题',mine:draft.title,raw:draft.title},
  {field:'content',label:'内容',mine:draft.content,raw:draft.content},
  {field:'aliases',label:'别名',mine:lines(draft.aliases).join('\n'),raw:lines(draft.aliases)},
  {field:'sources',label:'来源',mine:draft.sources.map(s=>[materialName(s.material_id),s.locator,s.excerpt].join(' · ')).join('、'),raw:draft.sources},
  {field:'relations',label:'关系',mine:draft.relations.map(cardName).join('、'),raw:draft.relations},
  {field:'convergence',label:'状态与追踪',mine:convergenceLabel(draft.convergence,'term'),raw:draft.convergence},
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

const isFullCard=(value:KnowledgeCard|null|undefined):value is KnowledgeCard=>!!value&&typeof value.card_id==='string'&&Array.isArray(value.aliases)&&Array.isArray(value.sources);

type Pending={kind:'load';card_id:string;operation_id:string}|{kind:'save';operation_id:string;card_id?:string}|{kind:'conflict';card_id:string;operation_id:string};
type OpenRequest={kind:'create';nonce:string}|{kind:'open';nonce:string;id:string;version:number};

export function KnowledgeCardPanel({cards,materials,selected,viewEvent,commandError,onCommand,embedded=false,showList=true,openRequest=null,onCommitted,termMode=false}:{cards:CardSummary[];materials:MaterialSummary[];selected:KnowledgeCard|null;viewEvent:CardViewEvent|null;commandError:CardCommandError|null;onCommand:(c:unknown)=>void;embedded?:boolean;showList?:boolean;openRequest?:OpenRequest|null;termMode?:boolean;onCommitted?:(card_id:string,version:number)=>void}){
  const [title,setTitle]=useState(''),[content,setContent]=useState(''),[aliases,setAliases]=useState('');
  const [sources,setSources]=useState<SourceInput[]>([]),[relations,setRelations]=useState<string[]>([]),[convergence,setConvergence]=useState<Convergence>('resolved');
  const [editing,setEditing]=useState<{card_id:string;version:number}|null>(null);
  const [latest,setLatest]=useState<KnowledgeCard|null>(null);
  const [loading,setLoading]=useState(false),[saving,setSaving]=useState(false),[message,setMessage]=useState('');
  const [reason,setReason]=useState('');
  const pending=useRef<Pending|null>(null);
  const archivePending=useRef<{operation_id:string;card_id:string}|null>(null);
  const committed=useRef(onCommitted);
  committed.current=onCommitted;
  const send=(operation_id:string,action:string,extra:object={})=>onCommand({action,schema_version:1,operation_id,...extra});
  const draft=():CardDraft=>({title,content,aliases,sources,relations,convergence});
  const clearDraft=()=>{setEditing(null);setLatest(null);setTitle('');setContent('');setAliases('');setSources([]);setRelations([]);setConvergence('resolved');setReason('');};
  const populate=(card:KnowledgeCard)=>{const next=draftFrom(card);setEditing({card_id:card.card_id,version:card.version});setTitle(next.title);setContent(next.content);setAliases(next.aliases);setSources(next.sources);setRelations(next.relations);setConvergence(next.convergence);setLatest(null);};
  const load=(card:Pick<CardSummary,'card_id'>)=>{
    const operation_id=crypto.randomUUID();
    pending.current={kind:'load',card_id:card.card_id,operation_id};
    setLoading(true);setSaving(false);setLatest(null);setMessage('正在读取完整已提交版本…');
    send(operation_id,'card_open',{card_id:card.card_id});
  };
  useEffect(()=>window.horizon.subscribe(event=>{
    if(event.kind!=='term_confirmation')return;
    const card=event.data as {operation_id:string;status:string};
    if(card.operation_id!==pending.current?.operation_id&&card.operation_id!==archivePending.current?.operation_id)return;
    if(card.status!=='rejected'&&card.status!=='expired')return;
    pending.current=null;
    archivePending.current=null;
    setSaving(false);
    setMessage(card.status==='expired'?'确认已过期。':'已取消修改。');
  }),[]);
  const openId=openRequest?.kind==='open'?openRequest.id:'';
  const creating=openRequest?.kind==='create';
  const staleDraft=!!openId&&editing?.card_id!==openId;
  const save=()=>{
    if(loading||saving||latest)return;
    if(staleDraft){setMessage('当前草稿属于另一条知识卡片；请重新载入本记录后再保存。');return;}
    if(termMode&&sources.some(source=>!source.locator.trim()||!source.excerpt.trim())){setMessage('请为每项来源填写定位和原文摘录。');return;}
    const payload={title,content,aliases:lines(aliases),sources,relations,...stateFields(convergence),...(reason.trim()?{reason:reason.trim()}:{})};
    const operation_id=crypto.randomUUID();
    pending.current={kind:'save',operation_id,card_id:editing?.card_id};
    setSaving(true);setLatest(null);
    setMessage(editing?`正在审核修改（基于版本 ${editing.version}）…`:'正在审核新增术语…');
    if(editing){send(operation_id,'card_update',{card_id:editing.card_id,expected_version:editing.version,...payload});}
    else{send(operation_id,'card_create',payload);}
  };
  const archive=(card:CardSummary)=>{
    if(archivePending.current)return;
    const operation_id=crypto.randomUUID();
    archivePending.current={operation_id,card_id:card.card_id};
    setMessage('正在审核归档…');
    send(operation_id,'card_archive',{card_id:card.card_id,expected_version:card.version});
  };
  useEffect(()=>{
    if(!viewEvent||viewEvent.source!=='view'||!isFullCard(selected))return;
    const work=pending.current;
    if(!work)return;
    if(work.kind==='load'){
      if(work.card_id!==selected.card_id||work.operation_id!==selected.read_operation_id)return;
      pending.current=null;setLoading(false);setSaving(false);
      if(selected.status==='archived'){setEditing(null);setLatest(null);setMessage('已归档的知识卡片为只读。');return;}
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
    pending.current=null;setSaving(false);setLatest(null);
    setEditing({card_id:selected.card_id,version:selected.version});
    setReason('');
    setMessage(work.card_id?`已保存为版本 ${selected.version}。`:`已创建为版本 ${selected.version}。`);
    committed.current?.(selected.card_id,selected.version);
  },[viewEvent,selected]);
  useEffect(()=>{
    if(!viewEvent||viewEvent.source!=='view'||!isFullCard(selected))return;
    const work=archivePending.current;
    if(!work||viewEvent.operation_id!==work.operation_id||selected.operation_id!==work.operation_id||selected.card_id!==work.card_id||selected.status!=='archived')return;
    archivePending.current=null;
    if(editing?.card_id===work.card_id)clearDraft();
    setMessage(`已归档为版本 ${selected.version}；记录、来源与关系保留，归档的卡片在面板中只读。`);
    committed.current?.(selected.card_id,selected.version);
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
  useEffect(()=>{
    if(!openRequest)return;
    if(openRequest.kind==='create'){
      pending.current=null;archivePending.current=null;setLoading(false);setSaving(false);setLatest(null);clearDraft();
      setMessage('填写标题和内容后创建知识卡片；提交后需要确认。');
      return;
    }
    load({card_id:openRequest.id});
  },[openRequest?.nonce]);
  const materialName=(id:string)=>materials.find(m=>m.material_id===id)?.title||selected?.sources.find(s=>s.material_id===id)?.title||id;
  const cardName=(id:string)=>cards.find(c=>c.card_id===id)?.title||id;
  const conflict=latest?cardDiff(draft(),latest,materialName,cardName):[];
  const changeSource=(index:number,fields:Partial<SourceInput>)=>setSources(current=>current.map((source,i)=>i===index?{...source,...fields}:source));
  const locked=!creating&&isFullCard(selected)&&selected.status==='archived'&&!editing;
  return <div className={`trace${embedded?' workbench-panel':''}`}>{!embedded&&<h2>知识卡片</h2>}


  {!locked&&!staleDraft&&<form onSubmit={e=>{e.preventDefault();save();}}><fieldset disabled={loading||saving}>
  {termMode?<><label>标题<TextInput required value={title} onChange={e=>setTitle(e.target.value)}/></label>
  <label>释义<MultilineInput required value={content} onChange={e=>setContent(e.target.value)}/></label>
  <TermLists aliases={lines(aliases)} sources={sources} relations={relations} materials={materials}
    terms={cards.filter(card=>card.card_id!==editing?.card_id)} materialName={materialName} termName={cardName}
    onAliases={values=>setAliases(values.join('\n'))} onSources={setSources} onRelations={setRelations}/>
  </>:<>
  <label>标题<TextInput required disabled={loading} value={title} onChange={e=>setTitle(e.target.value)}/></label>
  <label>{termMode?'释义':'内容'}<MultilineInput required disabled={loading} value={content} onChange={e=>setContent(e.target.value)}/></label>
  <label>别名（每行一个）<MultilineInput disabled={loading} value={aliases} onChange={e=>setAliases(e.target.value)}/></label>
  <fieldset><legend>来源</legend>
  {sources.map((source,index)=><div key={index}>
    <label>已保存的资料<FixedSelect required value={source.material_id} onChange={e=>changeSource(index,{material_id:e.target.value})}>
      <option value="">选择资料</option>
      {source.material_id&&!materials.some(m=>m.material_id===source.material_id)&&<option value={source.material_id}>{materialName(source.material_id)}</option>}
      {materials.map(m=><option key={m.material_id} value={m.material_id}>{m.title}</option>)}
    </FixedSelect></label>
    <label>定位<TextInput required value={source.locator} onChange={e=>changeSource(index,{locator:e.target.value})}/></label>
    <label>原文摘录<MultilineInput required value={source.excerpt} onChange={e=>changeSource(index,{excerpt:e.target.value})}/></label>
    <Button type="button" onClick={()=>setSources(current=>current.filter((_,i)=>i!==index))}>移除来源</Button>
  </div>)}
  <Button type="button" disabled={!materials.length||sources.length>=16} onClick={()=>setSources(current=>[...current,{material_id:'',locator:'',excerpt:''}])}>添加来源</Button>
  {!materials.length&&!sources.length&&<p>可在「资料」保存来源，再到这里添加引用。</p>}
  </fieldset>
  <label>{termMode?'关联术语':'关联知识卡片'}<FixedSelect multiple value={relations} onChange={e=>setRelations(Array.from(e.target.selectedOptions,option=>option.value))}>
    {cards.filter(card=>card.card_id!==editing?.card_id).map(card=><option key={card.card_id} value={card.card_id}>{card.title}{card.status==='archived'?'（已归档）':''}</option>)}
  </FixedSelect></label>
  </>}
  <ConvergenceSelect value={convergence} onChange={setConvergence}/>
  {!sources.length&&<p>未填写来源时，卡片记为「用户陈述 · 来源待补充」。</p>}
  <label>本次保存理由（可选）<TextInput maxLength={500} value={reason} onChange={e=>setReason(e.target.value)} placeholder="例如：补充来源与别名"/></label>
  <p>理由会写入版本历史；保存成功后清空，失败时与草稿一起保留。</p>
  <Button variant={termMode?'primary':'standard'} type="submit" disabled={loading||saving||!!latest}>{editing?'保存修改':termMode?'创建术语':'创建知识卡片'}</Button>{editing&&<Button type="button" disabled={saving} onClick={()=>load(editing)}>取消修改并重新读取</Button>}
  </fieldset></form>}
  {locked&&<p>已归档的知识卡片以只读方式显示；不会创建或覆盖任何记录。</p>}
  {message&&<p role="status">{message}</p>}
  {latest&&<section className="child-result" role="alert"><h3>版本冲突：草稿与当前已提交版本 {latest.version} 的差异</h3>
  <p>你的保存没有覆盖他人已提交的内容。逐项核对后再决定：采用最新版本作为基线会保留你的草稿文字，但提交时会写入一个新的版本。</p>
  {conflict.length===0?<p>当前草稿与已提交内容一致；可直接重新保存。</p>:<ul>{conflict.map(row=><li key={row.field}><strong>{row.label}</strong><br/>你的草稿：{row.mine}<br/>已提交（v{latest.version}）：{row.theirs}</li>)}</ul>}
  <Button type="button" disabled={loading||saving||latest.status==='archived'} onClick={()=>{setEditing({card_id:latest.card_id,version:latest.version});setLatest(null);setMessage(`已把草稿改为基于最新版本 ${latest.version}；请核对后保存。`);}}>保留草稿，基于最新版本重试</Button>
  <Button type="button" disabled={loading||saving} onClick={()=>{if(latest.status==='archived'){clearDraft();setMessage('该卡片已归档，已放弃草稿。');}else{populate(latest);setMessage(`已放弃草稿并载入版本 ${latest.version}。`);}}}>放弃草稿</Button>
  </section>}
  {!creating&&isFullCard(selected)&&<section className="child-result"><h3>当前已提交版本</h3>
  <p>{selected.title} <TestTag tags={selected.tags}/> · {statusLabel[selected.status]} · 版本 {selected.version}</p><p>{selected.content}</p>
  <p>别名：{selected.aliases.join('、')||'无'} · 关联：{selected.relations.map(cardName).join('、')||'无'}</p>
  <p>{provenanceLabel(selected)}{selected.convergence?(' · '+convergenceLabel(selected.convergence,'term')):selected.questionable?' · 存疑':''} · {selected.known?'已知':'未计入已知'}</p>
  {selected.sources.map(source=><p key={JSON.stringify(source)}>{source.title} · 资料版本 {source.material_version} · {source.locator}{source.status==='removed'?' · 原资料已永久移除':''}<br/>{source.excerpt}</p>)}
  {selected.status!=='archived'&&<div className="workbench-actions"><Button variant={termMode?'archive':'standard'} type="button" disabled={loading||saving} onClick={()=>archive(selected)}>{termMode?'归档术语':'归档知识卡片'}</Button></div>}
  </section>}
  {showList&&<><h3>知识卡片列表</h3><p>列表摘要不用于编辑；编辑会读取完整已提交版本。归档的卡片仍会列出并标明状态。</p>{cards.length===0&&<p>尚无知识卡片。填写上方表单创建第一张卡片。</p>}
  {cards.map(card=><section className="child-result" key={card.card_id}><h3>{card.title}</h3><p>{provenanceLabel(card)}{card.source_removed?' · 引用的资料已移除':''}{card.convergence?(' · '+convergenceLabel(card.convergence,'term')):card.questionable?' · 存疑':''} · {statusLabel[card.status]||card.status} · 版本 {card.version}</p>
  <Button disabled={loading||saving} onClick={()=>load(card)}>{card.status==='archived'?'查看完整版本（只读）':'编辑（读取完整版本）'}</Button>
  {card.status!=='archived'&&<Button disabled={loading||saving} onClick={()=>archive(card)}>归档</Button>}
  </section>)}</>}
  </div>;
}
