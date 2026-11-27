import {TextInput, MultilineInput} from './gui/elements/FormElements';
import {FixedSelect} from './gui/elements/ChoiceElements';
import {Collapsible} from './gui/elements/Collapsible';
import {createPortal} from 'react-dom';
import React, {useEffect, useState} from 'react';
import type {FeedbackHandoff} from './EditorFeedbackPreview';
import {EditorPreferences, UsedWritingPreferences,
  type FrontierChoice, type WritingPreference} from './EditorPreferences';

type Child = {child_id:string; role:string; status:string; result?:string};
type Claim = {kind:'source'|'inference'|'unknown'|'disagreement'|'known'; text:string; independent_source_count?:number; citations:{locator:string; excerpt:string; status?:string; title?:string}[]};
type Report = {report_id:string; version:number; task_id:string; title:string; claims:Claim[]; limitation?:string; sources?:{material_id:string; title:string; limitation?:string; repost?:boolean}[];
  context_versions?:{preferences?:WritingPreference[]; writing_frontier_id?:string};
  feedback?:{revision:number; score:number|null; reason?:string; claim_index?:number}; gaps?:{material_id?:string; reason:string; source?:string; detail?:string}[]};
type Citation = {segment?:string; status:string; segment_truncated?:boolean; next_offset?:number|null};

export function EditorReports({taskId, children, frontiers, handoff, send, detailHost, onOpenDetail}:{
  detailHost:HTMLDivElement|null; onOpenDetail:()=>void;
  taskId:string; children:Child[];
  frontiers:FrontierChoice[]; handoff:(draft:FeedbackHandoff)=>void;
  send:(command:unknown)=>void;
}) {
  const [feedbackOwner, setFeedbackOwner] = useState<FeedbackHandoff['owner']>('voyager');
  const [report, setReport] = useState<Report|null>(null);
  const [citation, setCitation] = useState<Citation|null>(null);
  const [snapshot, setSnapshot] = useState<{claimIndex:number; citationIndex:number}|null>(null);
  useEffect(() => window.horizon.subscribe(event => {
    if (event.kind === 'editor_feedback_handoff' && event.data.task_id === taskId) {
      handoff(event.data);
    }
    if (event.kind === 'report_view' && event.data.task_id === taskId) {
      setReport(event.data);
      setCitation(null);
      setSnapshot(null);
    }
    if (event.kind === 'report_citation' && event.data.report_id === report?.report_id && event.data.version === report?.version) {
      setCitation(event.data.citation);
    }
  }), [taskId, report?.report_id, report?.version, handoff]);

  return <Collapsible title="历史报道">
    <EditorPreferences frontiers={frontiers} taskId={taskId} send={send}/>
    {children.filter(child=>child.role==='editor' && child.status==='succeeded').map(child=>{
      let saved: {report_id?:string; version?:number; outcome?:string; gaps?:unknown[]};
      try { saved = JSON.parse(child.result || '{}'); } catch { return null; }
      if (saved.outcome==='no_new') return <p key={child.child_id}>当前可读资料没有发现新增内容。
        {Boolean(saved.gaps?.length) && '部分来源未覆盖。'}
      </p>;
      return saved.report_id && <button key={child.child_id}
        onClick={()=>{onOpenDetail();send({action:'report_open', report_id:saved.report_id, version:saved.version});}}>
        打开报道 {saved.report_id.slice(0, 8)} · v{saved.version}
      </button>;
    })}
    {report && detailHost && createPortal(<article aria-label="报道正文">
      <h2>{report.title} · v{report.version}</h2>
      <UsedWritingPreferences rows={report.context_versions?.preferences || []} frontiers={frontiers}/>
      <form key={JSON.stringify([report.report_id, report.version, report.feedback?.revision])} onSubmit={event=>{
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        const reason = String(data.get('reason') || '').trim();
        const claim = data.get('claim');
        send({action:'report_feedback', report_id:report.report_id, version:report.version,
              operation_id:crypto.randomUUID(), expected_revision:report.feedback?.revision ?? 0,
              score:Number(data.get('score')), reason:reason || null, claim_index:claim ? Number(claim) : null});
      }}>
        <label>版本评分 <FixedSelect name="score" required defaultValue={report.feedback?.score ?? ''}>
          <option value="">请选择</option>
          {[1,2,3,4,5].map(score=><option key={score} value={score}>{score}</option>)}
        </FixedSelect></label>
        <label>原因 <TextInput name="reason" maxLength={2000} defaultValue={report.feedback?.reason ?? ''}/></label>
        <label>段落 <FixedSelect name="claim" defaultValue={report.feedback?.claim_index ?? ''}>
          <option value="">全文</option>
          {report.claims.map((_,index)=><option key={index} value={index}>第 {index+1} 段</option>)}
        </FixedSelect></label>
        <button type="submit">保存评分</button>
        {report.feedback?.score != null && <button type="button" onClick={()=>send({
          action:'report_feedback', report_id:report.report_id, version:report.version, score:null,
          operation_id:crypto.randomUUID(), expected_revision:report.feedback?.revision,
        })}>撤回评分</button>}
      </form>
      <Collapsible title="交给其他流程的反馈">
        <form key={`${report.report_id}:${report.version}`} onSubmit={event=>{
          event.preventDefault();
          const values=new FormData(event.currentTarget);
          const claim=values.get('claim');
          send({action:'editor_feedback_handoff', owner:feedbackOwner, report_id:report.report_id,
            version:report.version, note:values.get('note'), claim_index:claim ? Number(claim) : null,
            frontier_id:values.get('frontier') || null});
        }}>
          <label>反馈类型 <FixedSelect value={feedbackOwner} onChange={event=>setFeedbackOwner(event.target.value as FeedbackHandoff['owner'])}>
            <option value="voyager">来源与检索</option><option value="knowledge">知识库</option><option value="frontier">Frontier 范围</option>
          </FixedSelect></label>
          <label>反馈内容 <MultilineInput name="note" required maxLength={2000}/></label>
          <label>段落 <FixedSelect name="claim"><option value="">全文</option>
            {report.claims.map((_,index)=><option key={index} value={index}>第 {index+1} 段</option>)}
          </FixedSelect></label>
          {feedbackOwner==='frontier' && <label>要调整的 Frontier <FixedSelect name="frontier" required defaultValue={report.context_versions?.writing_frontier_id || ''}>
            <option value="">请选择</option>
            {frontiers.filter(row=>row.status==='following').map(row=><option key={row.frontier_id} value={row.frontier_id}>{row.question}</option>)}
          </FixedSelect></label>}
          <button type="submit">交给对应流程</button>
        </form>
      </Collapsible>
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
            send({action:'report_citation', report_id:report.report_id, version:report.version, claim_index:claimIndex, citation_index:citationIndex});
          }}>{report.sources && ref.title ? ref.title + ' · ' : ''}{ref.locator} · {ref.excerpt}</button>
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
    </article>,detailHost)}
  </Collapsible>;
}
