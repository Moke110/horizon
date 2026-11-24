import React from 'react';

export type FeedbackHandoff = {owner:'voyager'|'knowledge'|'frontier'; task_id:string;
  report_id:string; report_version:number; claim_index:number|null; claim_text:string|null;
  note:string; report_frontier_id:string|null; frontier_scope?:{frontier_id:string; version:number; question:string}};

export function EditorFeedbackPreview({draft, onClose, children}:{
  draft:FeedbackHandoff; onClose:()=>void; children?:React.ReactNode;
}) {
  return <section aria-label="Editor 报道反馈">
    <h3>Editor 报道反馈</h3>
    <p>报道 {draft.report_id.slice(0,8)} · v{draft.report_version}
      {' · '}{draft.claim_index==null?'全文':`第 ${draft.claim_index+1} 段`}</p>
    {draft.frontier_scope && <p>Frontier：{draft.frontier_scope.question} · v{draft.frontier_scope.version}</p>}
    {!draft.frontier_scope && draft.report_frontier_id && <p>报道的 Frontier：{draft.report_frontier_id}</p>}
    {draft.claim_text && <blockquote>{draft.claim_text}</blockquote>}
    <p style={{whiteSpace:'pre-wrap'}}>{draft.note}</p>
    {children}
    <button type="button" onClick={onClose}>关闭反馈预览</button>
  </section>;
}
