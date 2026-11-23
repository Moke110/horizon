import React, {useEffect, useState} from 'react';
import type {MaterialSummary} from './MaterialsPanel';

type Child = {child_id:string; role:string; status:string; result?:string};
type Claim = {kind:'source'|'inference'|'unknown'; text:string; citations:{locator:string; excerpt:string; status?:string}[]};
type Report = {report_id:string; version:number; task_id:string; title:string; claims:Claim[]; limitation?:string; feedback?:{revision:number; score:number|null; reason?:string; claim_index?:number}};
type Citation = {segment?:string; status:string; segment_truncated?:boolean; next_offset?:number|null};

export function EditorReports({taskId, materials, children, disabled, prepare, prepareRewrite, send}:{
  taskId:string; materials:MaterialSummary[]; children:Child[]; disabled:boolean;
  prepare:(id:string)=>void; prepareRewrite:(id:string,version:number,question:string)=>void; send:(command:unknown)=>void;
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
    if (event.kind === 'report_citation' && event.data.report_id === report?.report_id && event.data.version === report?.version) {
      setCitation(event.data.citation);
    }
  }), [taskId, report?.report_id, report?.version]);

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
        onClick={()=>send({action:'report_open', report_id:saved.report_id, version:saved.version})}>
        打开报道 {saved.report_id.slice(0, 8)} · v{saved.version}
      </button>;
    })}
    {report && <article aria-label="报道正文">
      <h2>{report.title} · v{report.version}</h2>
      <form key={JSON.stringify([report.report_id, report.version, report.feedback?.revision])} onSubmit={event=>{
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        const reason = String(data.get('reason') || '').trim();
        const claim = data.get('claim');
        send({action:'report_feedback', report_id:report.report_id, version:report.version,
              operation_id:crypto.randomUUID(), expected_revision:report.feedback?.revision ?? 0,
              score:Number(data.get('score')), reason:reason || null, claim_index:claim ? Number(claim) : null});
      }}>
        <label>版本评分 <select name="score" required defaultValue={report.feedback?.score ?? ''}>
          <option value="">请选择</option>
          {[1,2,3,4,5].map(score=><option key={score} value={score}>{score}</option>)}
        </select></label>
        <label>原因 <input name="reason" maxLength={2000} defaultValue={report.feedback?.reason ?? ''}/></label>
        <label>段落 <select name="claim" defaultValue={report.feedback?.claim_index ?? ''}>
          <option value="">全文</option>
          {report.claims.map((_,index)=><option key={index} value={index}>第 {index+1} 段</option>)}
        </select></label>
        <button type="submit">保存评分</button>
        {report.feedback?.score != null && <button type="button" onClick={()=>send({
          action:'report_feedback', report_id:report.report_id, version:report.version, score:null,
          operation_id:crypto.randomUUID(), expected_revision:report.feedback?.revision,
        })}>撤回评分</button>}
      </form>
      <form key={JSON.stringify([report.report_id, report.version])} onSubmit={event=>{
        event.preventDefault();
        const question = String(new FormData(event.currentTarget).get('question') || '').trim();
        if(question) prepareRewrite(report.report_id, report.version, question);
      }}>
        <label>追问或纠错 <input name="question" required maxLength={2000}/></label>
        <button type="submit" disabled={disabled}>准备改写</button>
      </form>
      {report.limitation && <p>{report.limitation}</p>}
      {report.claims.map((claim, claimIndex)=><section key={claimIndex}>
        <p><strong>{{source:'来源结论', inference:'综合推断', unknown:'未知或争议'}[claim.kind]}</strong> {claim.text}</p>
        {claim.citations.map((ref, citationIndex)=><React.Fragment key={citationIndex}>
          <button onClick={()=>{
            setSnapshot(null);
            send({action:'report_citation', report_id:report.report_id, version:report.version, claim_index:claimIndex, citation_index:citationIndex});
          }}>{ref.locator} · {ref.excerpt}</button>
          {ref.status!=='abstract' && <button onClick={()=>{
            setSnapshot({claimIndex,citationIndex});
            send({action:'report_citation', report_id:report.report_id, version:report.version, claim_index:claimIndex,
                  citation_index:citationIndex, snapshot_offset:0});
          }}>打开原文快照</button>}
        </React.Fragment>)}
      </section>)}
      {citation && <blockquote>{citation.status==='removed'?'原快照已永久移除。':citation.status==='unavailable'?'该摘要授权已不可用。':citation.segment}
        {citation.segment_truncated && '…'}
      </blockquote>}
      {snapshot && citation?.next_offset != null && <button onClick={()=>send({action:'report_citation',
        report_id:report.report_id, version:report.version, claim_index:snapshot.claimIndex, citation_index:snapshot.citationIndex,
        snapshot_offset:citation.next_offset,
      })}>下一段原文</button>}
    </article>}
  </details>;
}
