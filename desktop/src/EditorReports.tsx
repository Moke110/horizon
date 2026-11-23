import React, {useEffect, useState} from 'react';
import type {MaterialSummary} from './MaterialsPanel';

type Child = {child_id:string; role:string; status:string; result?:string};
type Claim = {kind:'source'|'inference'|'unknown'; text:string; citations:{locator:string; excerpt:string; status?:string}[]};
type Report = {report_id:string; version:number; task_id:string; title:string; claims:Claim[]; limitation?:string};
type Citation = {segment?:string; status:string; segment_truncated?:boolean; next_offset?:number|null};

export function EditorReports({taskId, materials, children, disabled, prepare, send}:{
  taskId:string; materials:MaterialSummary[]; children:Child[]; disabled:boolean;
  prepare:(id:string)=>void; send:(command:unknown)=>void;
}) {
  const [material, setMaterial] = useState('');
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
    {children.filter(child=>child.role==='editor' && child.status==='succeeded').map(child=>{
      let saved: {report_id?:string; version?:number};
      try { saved = JSON.parse(child.result || '{}'); } catch { return null; }
      return saved.report_id && <button key={child.child_id}
        onClick={()=>send({action:'report_open', report_id:saved.report_id})}>
        打开报道 {saved.report_id.slice(0, 8)} · v{saved.version}
      </button>;
    })}
    {report && <article aria-label="报道正文">
      <h2>{report.title}</h2>
      {report.limitation && <p>{report.limitation}</p>}
      {report.claims.map((claim, claimIndex)=><section key={claimIndex}>
        <p><strong>{{source:'来源结论', inference:'综合推断', unknown:'未知或争议'}[claim.kind]}</strong> {claim.text}</p>
        {claim.citations.map((ref, citationIndex)=><React.Fragment key={citationIndex}>
          <button onClick={()=>{
            setSnapshot(null);
            send({action:'report_citation', report_id:report.report_id, claim_index:claimIndex, citation_index:citationIndex});
          }}>{ref.locator} · {ref.excerpt}</button>
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
