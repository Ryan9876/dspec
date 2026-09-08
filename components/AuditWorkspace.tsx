'use client';
import { useState } from 'react';
import { api, type AuditResult } from '@/lib/api';

export function AuditWorkspace() {
  const [path, setPath] = useState('');
  const [report, setReport] = useState<AuditResult | null>(null);
  const [message, setMessage] = useState('');
  async function scan() {
    setMessage('Scanning read-only evidence…'); setReport(null);
    try { const next = await api.audit(path); setReport(next); setMessage(`Scanned ${next.total_files_scanned} files in ${next.scan_duration_ms} ms.`); }
    catch (error) { setMessage(error instanceof Error ? error.message : 'Scan failed.'); }
  }
  const scores = report ? [
    ['Governance & security', report.health_score.governance_security_score],
    ['Requirements clarity', report.health_score.requirements_clarity_score],
    ['Architecture consistency', report.health_score.architecture_consistency_score],
    ['Test & task coverage', report.health_score.test_task_coverage_score]
  ] : [];
  return <main className="audit-workspace">
    <div className="hero-copy"><span className="eyebrow">EXISTING PROJECT</span><h1>Audit a local codebase without modifying it.</h1><p>DSpec reads bounded static evidence, ignores generated/vendor directories, rejects external symlink traversal, and reports what it can actually observe.</p></div>
    <div className="audit-input-card"><label className="field"><span>Repository path</span><div className="inline-field"><input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/Users/ryan/Workspace/project" /><button className="primary" disabled={!path.trim()} onClick={scan}>Run audit</button></div></label><p className="muted small">{message}</p></div>
    {report && <div className="audit-results">
      <section className="score-hero"><span className="eyebrow">COMPOSITE STATIC EVIDENCE</span><strong>{report.health_score.composite_score}%</strong><p className="muted">Not a security certification.</p></section>
      <section className="score-list">{scores.map(([label, score]) => <div className="score-row" key={label as string}><span>{label}</span><div className="score-bar"><i style={{ width: `${score}%` }} /></div><strong>{score}%</strong></div>)}</section>
      <section className="gap-card"><h2>Structural gaps</h2>{report.critical_gaps.length ? report.critical_gaps.map((gap) => <div className="gap" key={gap}>{gap}</div>) : <div className="finding pass">No high-level evidence gap crossed the prototype threshold.</div>}</section>
      <section className="limitations"><h3>Limits of this score</h3>{report.limitations.map((item) => <p key={item}>{item}</p>)}</section>
    </div>}
  </main>;
}
