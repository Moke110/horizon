import React,{useEffect,useRef,useState} from 'react';

export type ThemeSummary={theme_id:string;version:number;judgement:string;status:string;evidence_sufficiency:string;evidence_count:number;related_card_count:number;updated_at:number};
type MaterialOption={material_id:string;title:string;version?:number};
type CardOption={card_id:string;title:string;version:number;status:string};
type SourceInput={material_id:string;excerpt:string;locator:string;material_version?:number};
type PinnedSource=SourceInput&{material_version?:number;content_hash?:string;source_kind?:string;title?:string;snapshot_id?:string;source_label?:string;status?:'removed'};
type EvidenceInput={evidence_id?:string;stance:'supports'|'opposes';reason:string;source:SourceInput};
type SavedEvidence=EvidenceInput&{evidence_id:string;source:PinnedSource};
type RelatedCard={card_id:string;version:number};
export type Theme={schema_version:number;operation_id:string;read_operation_id?:string;theme_id:string;version:number;judgement:string;conditions:string[];related_cards:RelatedCard[];evidence:SavedEvidence[];evidence_sufficiency:string;known:boolean;origin:string;status:string;created_at:number;updated_at:number};
export type ThemeConflictDetails={kind?:string;expected_version?:number;current_version?:number;current?:Theme;submitted?:Record<string,unknown>;changed?:string[];card_id?:string};
export type ThemeViewEvent={source:'view';operation_id?:string;nonce:string};
export type ThemeCommandError={operation_id?:string;code?:string;message?:string;details?:ThemeConflictDetails};
type EvidenceView={operation_id?:string;schema_version:number;status:string;material_id:string;material_version?:number;content_hash?:string;title:string;source:{kind?:string;label?:string};locator:string;excerpt:string;anchor:{id?:string;page?:number;start:number;end:number}|null;segment:string|null;segment_truncated:boolean};

type ThemeDraft={judgement:string;conditions:string;relatedCards:RelatedCard[];evidence:EvidenceInput[]};
type ThemeDiffRow={field:string;label:string;mine:string;theirs:string};
type Pending={kind:'load';theme_id:string;operation_id:string}|{kind:'save';operation_id:string;theme_id?:string}|{kind:'conflict';theme_id:string;operation_id:string};

const statusLabel:Record<string,string>={active:'有效',archived:'已归档'};
const sufficiencyLabel:Record<string,string>={balanced:'支持与反对证据并存',single_sided:'证据不足（仅单向）',unsupported:'证据不足（暂无证据）'};
const stanceLabel:Record<string,string>={supports:'支持',opposes:'反对'};
const lines=(s:string)=>s.split('\n').map(v=>v.trim()).filter(Boolean);
const sameValue=(a:unknown,b:unknown)=>JSON.stringify(a)===JSON.stringify(b);
const isFullTheme=(value:Theme|null|undefined):value is Theme=>!!value&&typeof value.theme_id==='string'&&Array.isArray(value.conditions)&&Array.isArray(value.related_cards)&&Array.isArray(value.evidence);
const draftFrom=(theme:Theme):ThemeDraft=>({
  judgement:theme.judgement,conditions:theme.conditions.join('\n'),
  relatedCards:theme.related_cards.map(({card_id,version})=>({card_id,version})),
  evidence:theme.evidence.map(({evidence_id,stance,reason,source})=>({evidence_id,stance,reason,source:{material_id:source.material_id,excerpt:source.excerpt,locator:source.locator,material_version:source.material_version}})),
});
const evidenceText=(entry:{stance:string;reason:string;source:SourceInput},materialName:(id:string)=>string)=>[stanceLabel[entry.stance]||entry.stance,entry.reason,materialName(entry.source.material_id),entry.source.locator,entry.source.excerpt].join(' · ');

/** Field-by-field difference between a local draft and the latest committed Theme. */
const themeDiff=(draft:ThemeDraft,committed:Theme,materialName:(id:string)=>string,cardName:(id:string)=>string):ThemeDiffRow[]=>{
  const latest=draftFrom(committed);
  const rows=(value:ThemeDraft)=>[
    {field:'judgement',label:'判断',mine:value.judgement||'（空）',raw:value.judgement},
    {field:'conditions',label:'适用条件',mine:lines(value.conditions).join('、')||'（空）',raw:lines(value.conditions)},
    {field:'related_cards',label:'关联知识卡片',mine:value.relatedCards.map(r=>`${cardName(r.card_id)}（v${r.version}）`).join('、')||'（空）',raw:value.relatedCards.map(r=>[r.card_id,r.version])},
    {field:'evidence',label:'证据',mine:value.evidence.map(e=>evidenceText(e,materialName)).join('；')||'（空）',raw:value.evidence.map(e=>[e.stance,e.reason,e.source.material_id,e.source.locator,e.source.excerpt])},
  ];
  return rows(draft).flatMap((row,index)=>sameValue(row.raw,rows(latest)[index].raw)?[]:[{field:row.field,label:row.label,mine:row.mine,theirs:rows(latest)[index].mine}]);
};

export function ThemePanel({themes,materials,cards,selected,viewEvent,commandError,onCommand}:{themes:ThemeSummary[];materials:MaterialOption[];cards:CardOption[];selected:Theme|null;viewEvent:ThemeViewEvent|null;commandError:ThemeCommandError|null;onCommand:(c:unknown)=>void}){
  const [judgement,setJudgement]=useState(''),[conditions,setConditions]=useState('');
  const [relatedCards,setRelatedCards]=useState<RelatedCard[]>([]),[evidence,setEvidence]=useState<EvidenceInput[]>([]);
  const [editing,setEditing]=useState<{theme_id:string;version:number}|null>(null);
  const [latest,setLatest]=useState<Theme|null>(null);
  const [loading,setLoading]=useState(false),[saving,setSaving]=useState(false),[message,setMessage]=useState('');
  const [citation,setCitation]=useState<EvidenceView|null>(null);
  const citationReader=useRef<HTMLElement>(null);
  const pending=useRef<Pending|null>(null);
  const citationPending=useRef<string|null>(null);
  const archivePending=useRef<{operation_id:string;theme_id:string}|null>(null);
  const send=(action:string,extra:object={},operation_id=crypto.randomUUID())=>onCommand({action,schema_version:1,operation_id,...extra});
  const draft=():ThemeDraft=>({judgement,conditions,relatedCards,evidence});
  const clearDraft=()=>{setEditing(null);setLatest(null);setJudgement('');setConditions('');setRelatedCards([]);setEvidence([]);};
  const populate=(theme:Theme)=>{
    const next=draftFrom(theme);
    setEditing({theme_id:theme.theme_id,version:theme.version});
    setJudgement(next.judgement);setConditions(next.conditions);setRelatedCards(next.relatedCards);setEvidence(next.evidence);setLatest(null);
  };
  const load=(theme:ThemeSummary)=>{
    const operation_id=crypto.randomUUID();
    pending.current={kind:'load',theme_id:theme.theme_id,operation_id};
    citationPending.current=null;
    setLoading(true);setSaving(false);setLatest(null);setCitation(null);setMessage('正在读取完整已提交版本…');
    send('theme_open',{theme_id:theme.theme_id},operation_id);
  };
  const save=()=>{
    if(loading||saving||latest)return;
    if(!judgement.trim()){setMessage('请填写判断。');return;}
    if(evidence.some(entry=>!entry.reason.trim()||!entry.source.material_id||!entry.source.locator.trim()||!entry.source.excerpt.trim())){
      setMessage('每条证据都需要支持或反对的方向、理由，以及已保存资料的定位和摘录。');return;
    }
    const payload={
      judgement,
      conditions:lines(conditions),
      related_cards:relatedCards,
      evidence:evidence.map(({evidence_id,stance,reason,source})=>({...(evidence_id?{evidence_id}:{}) ,stance,reason,source})),
    };
    const operation_id=crypto.randomUUID();
    pending.current={kind:'save',operation_id,theme_id:editing?.theme_id};
    citationPending.current=null;
    setSaving(true);setLatest(null);setCitation(null);
    setMessage(editing?`正在保存修改（基于版本 ${editing.version}）…`:'正在创建命题…');
    if(editing)send('theme_update',{theme_id:editing.theme_id,expected_version:editing.version,...payload},operation_id);
    else send('theme_create',payload,operation_id);
  };
  const archive=(theme:{theme_id:string;version:number})=>{
    if(archivePending.current)return;
    const operation_id=crypto.randomUUID();
    archivePending.current={operation_id,theme_id:theme.theme_id};
    setMessage('正在归档…');
    send('theme_archive',{theme_id:theme.theme_id,expected_version:theme.version},operation_id);
  };
  const openEvidence=(theme:Theme,entry:SavedEvidence)=>{
    const operation_id=crypto.randomUUID();
    citationPending.current=operation_id;
    setCitation(null);
    setMessage('正在打开证据引用的资料快照…');
    send('theme_source',{theme_id:theme.theme_id,expected_version:theme.version,evidence_id:entry.evidence_id},operation_id);
  };
  useEffect(()=>{
    const unsubscribe=window.horizon.subscribe(e=>{
      if(e.kind==='theme_source'&&citationPending.current===e.data.operation_id){
        citationPending.current=null;
        setCitation(e.data);
        setMessage('');
      }
    });
    return unsubscribe;
  },[]);
  useEffect(()=>{
    if(citation){
      citationReader.current?.scrollIntoView({block:'nearest'});
      citationReader.current?.focus({preventScroll:true});
    }
  },[citation]);
  useEffect(()=>{
    if(!viewEvent||viewEvent.source!=='view'||!isFullTheme(selected))return;
    const work=pending.current;
    if(!work)return;
    if(work.kind==='load'||work.kind==='conflict'){
      if(work.theme_id!==selected.theme_id||work.operation_id!==selected.read_operation_id)return;
      pending.current=null;setLoading(false);setSaving(false);
      if(work.kind==='conflict'){setLatest(selected);setMessage(`保存未提交：该命题已更新到版本 ${selected.version}。下方显示草稿与当前已提交内容的差异；不会自动覆盖，草稿已保留。`);return;}
      if(selected.status==='archived'){setMessage('已归档的命题为只读。');return;}
      populate(selected);
      setMessage(`已载入版本 ${selected.version}，保存将按该版本校验。`);
      return;
    }
    if(viewEvent.operation_id!==work.operation_id||selected.operation_id!==work.operation_id)return;
    if(work.theme_id&&work.theme_id!==selected.theme_id)return;
    pending.current=null;setSaving(false);clearDraft();
    setMessage(`已保存为版本 ${selected.version}。`);
  },[viewEvent,selected]);
  useEffect(()=>{
    if(!viewEvent||viewEvent.source!=='view'||!isFullTheme(selected))return;
    const work=archivePending.current;
    if(!work||viewEvent.operation_id!==work.operation_id||selected.operation_id!==work.operation_id||selected.theme_id!==work.theme_id||selected.status!=='archived')return;
    archivePending.current=null;
    if(editing?.theme_id===work.theme_id)clearDraft();
    setMessage(`已归档为版本 ${selected.version}；判断、条件、来源与关系保留，归档的命题在面板中只读。`);
  },[viewEvent,selected,editing]);
  useEffect(()=>{
    if(!commandError?.operation_id)return;
    const work=pending.current;
    if(work&&commandError.operation_id===work.operation_id){
      if(work.kind==='save'&&commandError.code==='version_conflict'){
        const details=commandError.details;
        if(details?.kind==='related_card'){
          pending.current=null;setSaving(false);setLoading(false);
          setMessage(`保存未提交：关联知识卡片「${details.card_id||''}」已更新到版本 ${details.current_version}，请重新选择后再保存；草稿已保留。`);
          return;
        }
        const current=details?.current;
        if(isFullTheme(current)){
          pending.current=null;setSaving(false);setLoading(false);setLatest(current);
          setMessage(`保存未提交：该命题已更新到版本 ${current.version}。下方显示草稿与当前已提交内容的差异；不会自动覆盖，草稿已保留。`);
          return;
        }
        if(work.theme_id){
          const operation_id=crypto.randomUUID();
          pending.current={kind:'conflict',theme_id:work.theme_id,operation_id};
          setSaving(false);setLoading(true);
          setMessage('保存未提交：内容已被更新，正在读取最新版本以显示差异…');
          send('theme_open',{theme_id:work.theme_id},operation_id);
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
      return;
    }
    if(commandError.operation_id===citationPending.current){
      citationPending.current=null;
      setMessage(`来源未打开：${commandError.message||commandError.code}。`);
    }
  },[commandError]);
  const materialName=(id:string)=>materials.find(m=>m.material_id===id)?.title||selected?.evidence.find(e=>e.source.material_id===id)?.source.title||id;
  const cardName=(id:string)=>cards.find(c=>c.card_id===id)?.title||id;
  const conflict=latest?themeDiff(draft(),latest,materialName,cardName):[];
  const changeEvidence=(index:number,fields:Partial<EvidenceInput>)=>setEvidence(current=>current.map((entry,i)=>i===index?{...entry,...fields}:entry));
  const changeSource=(index:number,fields:Partial<SourceInput>)=>setEvidence(current=>current.map((entry,i)=>i===index?{...entry,source:{...entry.source,...fields}}:entry));
  const toggleRelated=(values:string[])=>setRelatedCards(values.map(id=>{
    const existing=relatedCards.find(relation=>relation.card_id===id);
    const card=cards.find(candidate=>candidate.card_id===id);
    return {card_id:id,version:existing?.version??card?.version??1};
  }));
  return <div className="trace"><h2>命题</h2><p>命题是跨来源的判断：写明适用条件，用支持或反对的证据记录立场；证据只引用已保存资料的不可变快照。</p>
  <form onSubmit={e=>{e.preventDefault();save();}}><fieldset disabled={loading||saving}>
  <label>判断<textarea required maxLength={4000} value={judgement} onChange={e=>setJudgement(e.target.value)}/></label>
  <label>适用条件（每行一个）<textarea maxLength={2000} value={conditions} onChange={e=>setConditions(e.target.value)}/></label>
  <label>关联知识卡片<select multiple value={relatedCards.map(relation=>relation.card_id)} onChange={e=>toggleRelated(Array.from(e.target.selectedOptions,option=>option.value))}>
    {cards.map(card=><option key={card.card_id} value={card.card_id} disabled={card.status==='archived'&&!relatedCards.some(relation=>relation.card_id===card.card_id)}>{card.title}{card.status==='archived'?'（已归档）':''}（v{card.version}）</option>)}
  </select></label>
  {!cards.length&&<p>暂无知识卡片；仍可保存不含关联的命题。</p>}
  <fieldset><legend>证据</legend>
  {evidence.map((entry,index)=><div key={index}>
    <label>方向<select value={entry.stance} onChange={e=>changeEvidence(index,{stance:e.target.value as 'supports'|'opposes'})}><option value="supports">支持</option><option value="opposes">反对</option></select></label>
    <label>理由<textarea required maxLength={4000} value={entry.reason} onChange={e=>changeEvidence(index,{reason:e.target.value})}/></label>
    <label>已保存的资料<select required value={entry.source.material_id} onChange={e=>{
      const material=materials.find(item=>item.material_id===e.target.value);
      changeSource(index,{material_id:e.target.value,material_version:material?.version,locator:'',excerpt:''});
    }}>
      <option value="">选择资料</option>
      {entry.source.material_id&&!materials.some(m=>m.material_id===entry.source.material_id)&&<option value={entry.source.material_id}>{materialName(entry.source.material_id)}</option>}
      {materials.map(m=><option key={m.material_id} value={m.material_id}>{m.title}{m.version?`（v${m.version}）`:''}</option>)}
    </select></label>
    <label>定位<input required maxLength={2000} placeholder="例如第 2 页或 p6" value={entry.source.locator} onChange={e=>changeSource(index,{locator:e.target.value})}/></label>
    <label>原文摘录<textarea required maxLength={4000} value={entry.source.excerpt} onChange={e=>changeSource(index,{excerpt:e.target.value})}/></label>
    <button type="button" onClick={()=>setEvidence(current=>current.filter((_,i)=>i!==index))}>移除证据</button>
  </div>)}
  <button type="button" disabled={evidence.length>=64} onClick={()=>setEvidence(current=>[...current,{stance:'supports',reason:'',source:{material_id:'',locator:'',excerpt:''}}])}>添加证据</button>
  {!materials.length&&!evidence.length&&<p>可在「资料」保存来源，再到这里引用证据。</p>}
  </fieldset>
  <p>证据为空或只有单一方向也可保存，命题会标记为「证据不足」。</p>
  <button type="submit" disabled={loading||saving||!!latest}>{editing?'保存修改':'创建命题'}</button>{editing&&<button type="button" disabled={saving} onClick={()=>{pending.current=null;setSaving(false);clearDraft();setMessage('已取消修改。');}}>取消修改</button>}
  </fieldset></form>
  {message&&<p role="status">{message}</p>}
  {citation&&<section ref={citationReader} tabIndex={-1} className="child-result" aria-label="证据来源"><h3>{citation.title}</h3>
    <p>{citation.status==='removed'?'原资料已永久移除':citation.source.label||'已保存资料'} · 资料版本 {citation.material_version} · 定位：{citation.locator}</p>
    <p>摘录：{citation.excerpt}</p>
    {citation.status!=='removed'&&(citation.segment?<><p>{citation.anchor?.page?`第 ${citation.anchor.page} 页`:citation.anchor?.id?`段落 ${citation.anchor.id}`:''}{citation.segment_truncated?'（显示摘录附近的原文）':''}</p><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{citation.segment}</pre></>:<p>该资料没有可显示的正文段落。</p>)}
    <button type="button" onClick={()=>setCitation(null)}>关闭来源</button>
  </section>}
  {latest&&<section className="child-result" role="alert"><h3>版本冲突：草稿与当前已提交版本 {latest.version} 的差异</h3>
  <p>你的保存没有覆盖他人已提交的内容。逐项核对后再决定：采用最新版本作为基线会保留你的草稿文字，但提交时会写入一个新的版本。</p>
  {conflict.length===0?<p>当前草稿与已提交内容一致；可直接重新保存。</p>:<ul>{conflict.map(row=><li key={row.field}><strong>{row.label}</strong><br/>你的草稿：{row.mine}<br/>已提交（v{latest.version}）：{row.theirs}</li>)}</ul>}
  <button type="button" disabled={loading||saving||latest.status==='archived'} onClick={()=>{setEditing({theme_id:latest.theme_id,version:latest.version});setLatest(null);setMessage(`已把草稿改为基于最新版本 ${latest.version}；请核对后保存。`);}}>保留草稿，基于最新版本重试</button>
  <button type="button" disabled={loading||saving} onClick={()=>{if(latest.status==='archived'){clearDraft();setMessage('该命题已归档，已放弃草稿。');}else{populate(latest);setMessage(`已放弃草稿并载入版本 ${latest.version}。`);}}}>放弃草稿</button>
  </section>}
  {isFullTheme(selected)&&<section className="child-result"><h3>当前已提交版本</h3>
  <p>{statusLabel[selected.status]||selected.status} · 版本 {selected.version} · {sufficiencyLabel[selected.evidence_sufficiency]||selected.evidence_sufficiency}</p>
  <p>{selected.judgement}</p>
  <p>适用条件：{selected.conditions.join('、')||'无'} · 关联知识卡片：{selected.related_cards.map(relation=>`${cardName(relation.card_id)}（v${relation.version}）`).join('、')||'无'}</p>
  {selected.evidence.map(entry=><div key={entry.evidence_id}><p><strong>{stanceLabel[entry.stance]||entry.stance}</strong> · {entry.reason}</p>
    <p>{entry.source.title||materialName(entry.source.material_id)} · 资料版本 {entry.source.material_version??'未知'} · {entry.source.locator}{entry.source.status==='removed'?' · 原资料已永久移除':''}<br/>{entry.source.excerpt}</p>
    <button type="button" disabled={loading||saving} onClick={()=>openEvidence(selected,entry)}>打开来源</button></div>)}
  {!selected.evidence.length&&<p>暂无证据。</p>}
  {selected.status!=='archived'&&<button type="button" disabled={loading||saving} onClick={()=>archive(selected)}>归档命题</button>}
  </section>}
  <h3>命题列表</h3><p>列表摘要不用于编辑；编辑会读取完整已提交版本。归档的命题仍会列出并标明状态。</p>{!themes.length&&<p>尚无命题。填写上方表单创建第一个命题。</p>}
  {themes.map(theme=><section className="child-result" key={theme.theme_id}><h3>{theme.judgement}</h3>
  <p>{sufficiencyLabel[theme.evidence_sufficiency]||theme.evidence_sufficiency} · {theme.evidence_count} 条证据 · {theme.related_card_count} 张关联卡片 · {statusLabel[theme.status]||theme.status} · 版本 {theme.version}</p>
  <button disabled={loading||saving} onClick={()=>load(theme)}>{theme.status==='archived'?'查看完整版本（只读）':'编辑（读取完整版本）'}</button>
  {theme.status!=='archived'&&<button disabled={loading||saving} onClick={()=>archive(theme)}>归档</button>}
  </section>)}
  </div>;
}
