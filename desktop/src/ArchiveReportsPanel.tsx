import React from 'react';
import {renderLocalHtml} from './LocalHtml';

export const assessmentDecisions: Record<string, string> = {
  completed: '已完成', questionable: '存疑', invalid: '失效',
};
export const assessmentBases: Record<string, string> = {
  methodology: '重大研究方法错位', integrity: '诚信缺陷', interpretation: 'Agent 解读错误', correction: '误判更正',
};

export type ArchiveReport = {
  report_id: string;
  title: string;
  summary: string;
  sha256: string;
  file_path: string | null;
  covered_docs: string[];
  status: 'completed';
  assessment?: {decision: string; basis: string; reason: string; evidence: string};
};

export type ArchiveReportOriginal = {report: ArchiveReport; html: string | null};

export function ArchiveReportsPanel({reports, onOpen}: {
  reports: ArchiveReport[];
  onOpen: (reportId: string) => void;
}) {
  return <section aria-label="资料库报道">
    <h2>报道</h2>
    {reports.length === 0 ? <p>尚无报道。</p> : <ul>{reports.map(report =>
      <li key={report.report_id}>
        <button type="button" onClick={() => onOpen(report.report_id)}>{report.title} · 打开报道</button>
        <span>{assessmentDecisions[report.assessment?.decision ?? report.status]}</span>
      </li>)}</ul>}
  </section>;
}

export function ArchiveReportView({entry, assessment}: {
  entry: ArchiveReportOriginal; assessment?: ArchiveReport['assessment'];
}) {
  const {report, html} = entry;
  return <section aria-label="报道条目">
    <h3>{report.title}</h3>
    <p>{report.summary}</p>
    <dl style={{overflowWrap: 'anywhere'}}>
      <dt>报道 ID</dt><dd>{report.report_id}</dd>
      <dt>状态</dt><dd>{assessmentDecisions[assessment?.decision ?? report.status]}</dd>
      {assessment && <>
        <dt>依据类别</dt><dd>{assessmentBases[assessment.basis]}</dd>
        <dt>重评理由</dt><dd>{assessment.reason}</dd>
        <dt>重评证据</dt><dd>{assessment.evidence}</dd>
      </>}
      <dt>SHA-256</dt><dd>{report.sha256}</dd>
      {report.file_path && <><dt>文件地址</dt><dd>{report.file_path}</dd></>}
      <dt>被报道文档</dt><dd><ol>{report.covered_docs.map((identity, index) =>
        <li key={index}>{identity}</li>)}</ol></dd>
    </dl>
    {html === null ? <p role="status">原文不可用</p>
      : <article aria-label="报道原文" className="local-html">{renderLocalHtml(html)}</article>}
  </section>;
}
