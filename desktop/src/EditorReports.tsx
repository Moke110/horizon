import React, {useEffect, useState} from 'react';
import type {MaterialSummary} from './MaterialsPanel';

type Child = {child_id:string; role:string; status:string; result?:string};
type Claim = {kind:'source'|'inference'|'unknown'|'disagreement'|'known'; text:string; independent_source_count?:number; citations:{locator:string; excerpt:string; status?:string; title?:string}[]};
type Report = {report_id:string; version:number; task_id:string; title:string; claims:Claim[]; limitation?:string; sources?:{material_id:string; title:string; limitation?:string; repost?:boolean}[];
  gaps?:{material_id?:string; reason:string; source?:string; detail?:string}[]};
type Citation = {segment?:string; status:string; segment_truncated?:boolean; next_offset?:number|null};

export function EditorReports({taskId, materials, children, disabled, prepare, prepareSynthesis, send}:{
  taskId:string; materials:MaterialSummary[]; children:Child[]; disabled:boolean;
  prepare:(id:string)=>void; prepareSynthesis:(command:{question:string; material_ids:string[]; expand_known:boolean})=>void; send:(command:unknown)=>void;
}) {
  const [material, setMaterial] = useState('');
  const [question, setQuestion] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [expandKnown, setExpandKnown] = useState(false);
  const materialIds = selected.filter(id=>materials.some(row=>row.material_id===id));
  const [report, setReport] = useState<Report|null>(null);
  const [citation, setCitation] = useState<Citation|null>(null);
  const [snapshot, setSnapshot] = useState<{claimIndex:number; citationIndex:number}|null>(null);
  useEffect(() => window.horizon.subscribe(event => {
    if (event.kind === 'report_view' && event.data.task_id === taskId) {
      setReport(event.data);
      setCitation(null);
      setSnapshot(null);
    }
    if (event.kind === 'report_citation' && event.data.report_id === report?.report_id) {
      setCitation(event.data.citation);
    }
  }), [taskId, report?.report_id]);

  return <details>
    <summary>Editor 报道</summary>
    <label>选择资料 <select value={material} onChange={event=>setMaterial(event.target.value)}>
      <option value="">请选择</option>
      {materials.map(row=><option key={row.material_id} value={row.material_id}>{row.title}</option>)}
    </select></label>
    <button disabled={disabled || !materials.some(row=>row.material_id===material)} onClick={()=>prepare(material)}>准备生成报道</button>
    <details>
      <summary>综合多份资料</summary>
      <label>综合问题 <input value={question} onChange={event=>setQuestion(event.target.value)} maxLength={500}/></label>
      <label>选择两到四份资料 <select multiple value={materialIds} onChange={event=>
        setSelected(Array.from(event.target.selectedOptions, option=>option.value))}>
        {materials.map(row=><option key={row.material_id} value={row.material_id}>{row.title}</option>)}
      </select></label>
      <label><input type="checkbox" checked={expandKnown} onChange={event=>setExpandKnown(event.target.checked)}/> 展开已知背景</label>
      <button disabled={disabled || !question.trim() || materialIds.length<2 || materialIds.length>4}
        onClick={()=>prepareSynthesis({question:question.trim(), material_ids:materialIds, expand_known:expandKnown})}>
        准备综合报道
      </button>
    </details>
    {children.filter(child=>child.role==='editor' && child.status==='succeeded').map(child=>{
      let saved: {report_id?:string; version?:number; outcome?:string; gaps?:unknown[]};
      try { saved = JSON.parse(child.result || '{}'); } catch { return null; }
      if (saved.outcome==='no_new') return <p key={child.child_id}>当前可读资料没有发现新增内容。
        {Boolean(saved.gaps?.length) && '部分来源未覆盖。'}
      </p>;
      return saved.report_id && <button key={child.child_id}
        onClick={()=>send({action:'report_open', report_id:saved.report_id})}>
        打开报道 {saved.report_id.slice(0, 8)} · v{saved.version}
      </button>;
    })}
    {report && <article aria-label="报道正文">
      <h2>{report.title}</h2>
      {report.limitation && <p>{report.limitation}</p>}
      {report.sources && <ul aria-label="资料范围">{report.sources.map(source=>
        <li key={source.material_id}>{source.title}{source.repost && '（转载，不计独立证据）'} {source.limitation}</li>)}</ul>}
      {Boolean(report.gaps?.length) && <ul aria-label="覆盖缺口">{report.gaps?.map((gap,index)=>
        <li key={index}>{gap.source ? gap.source + ' · ' : ''}{gap.detail ||
          (gap.reason==='unavailable' ? '选中的资料无可读正文或摘要授权' : '还有检索范围未覆盖')}</li>)}</ul>}
      {report.claims.map((claim, claimIndex)=><section key={claimIndex}>
        <p><strong>{{source:'来源结论', inference:'综合推断', unknown:'未知或争议', disagreement:'来源分歧', known:'已知背景'}[claim.kind]}</strong> {claim.text}{claim.independent_source_count!=null && claim.citations.length>0 &&
          <small> · {claim.independent_source_count} 份独立证据</small>}</p>
        {claim.citations.map((ref, citationIndex)=><React.Fragment key={citationIndex}>
          <button onClick={()=>{
            setSnapshot(null);
            send({action:'report_citation', report_id:report.report_id, claim_index:claimIndex, citation_index:citationIndex});
          }}>{report.sources && ref.title ? ref.title + ' · ' : ''}{ref.locator} · {ref.excerpt}</button>
          {ref.status!=='abstract' && <button onClick={()=>{
            setSnapshot({claimIndex,citationIndex});
            send({action:'report_citation', report_id:report.report_id, claim_index:claimIndex,
                  citation_index:citationIndex, snapshot_offset:0});
          }}>打开原文快照</button>}
        </React.Fragment>)}
      </section>)}
      {citation && <blockquote>{citation.status==='removed'?'原快照已永久移除。':citation.status==='unavailable'?'该摘要授权已不可用。':citation.segment}
        {citation.segment_truncated && '…'}
      </blockquote>}
      {snapshot && citation?.next_offset != null && <button onClick={()=>send({action:'report_citation',
        report_id:report.report_id, claim_index:snapshot.claimIndex, citation_index:snapshot.citationIndex,
        snapshot_offset:citation.next_offset,
      })}>下一段原文</button>}
    </article>}
  </details>;
}
