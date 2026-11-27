import React from 'react';
import {renderLocalHtml} from './LocalHtml';

export type ArchiveReport = {
  report_id: string;
  title: string;
  summary: string;
  sha256: string;
  file_path: string;
  covered_docs: string[];
  status: 'completed';
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
        <span>已完成</span>
      </li>)}</ul>}
  </section>;
}

export function ArchiveReportView({entry}: {entry: ArchiveReportOriginal}) {
  const {report, html} = entry;
  return <section aria-label="报道条目">
    <h3>{report.title}</h3>
    <p>{report.summary}</p>
    <dl style={{overflowWrap: 'anywhere'}}>
      <dt>报道 ID</dt><dd>{report.report_id}</dd>
      <dt>状态</dt><dd>已完成</dd>
      <dt>SHA-256</dt><dd>{report.sha256}</dd>
      <dt>文件地址</dt><dd>{report.file_path}</dd>
      <dt>被报道文档</dt><dd><ul>{report.covered_docs.map(identity => <li key={identity}>{identity}</li>)}</ul></dd>
    </dl>
    {html === null ? <p role="status">原文不可用</p>
      : <article aria-label="报道原文" className="local-html">{renderLocalHtml(html)}</article>}
  </section>;
}
