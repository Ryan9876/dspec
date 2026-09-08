'use client';
import type { Review } from '@/stores/workflowStore';

export function ReviewBoard({ review, approved }: { review: Review | Record<string, never>; approved: boolean }) {
  const score = 'score' in review ? Number(review.score) : 0;
  const passing = 'passing' in review ? review.passing : [];
  const mustFix = 'must_fix' in review ? review.must_fix : [];
  const recommendations = 'recommendations' in review ? review.recommendations : [];
  return <aside className="review-panel">
    <div className="review-score"><div><span className="eyebrow">QUALITY GATE</span><strong>{Math.round(score * 100)}%</strong></div><span className={`pill ${approved ? 'approved' : ''}`}>{approved ? 'approved' : 'draft'}</span></div>
    <div className="meter"><span style={{ width: `${Math.min(100, score * 100)}%` }} /></div>
    <section><h3>Passing</h3>{passing.length ? passing.map((item) => <div className="finding pass" key={item}>✓ <span>{item}</span></div>) : <p className="muted small">Save a substantive draft to evaluate it.</p>}</section>
    <section><h3>Must fix</h3>{mustFix.length ? mustFix.map((item) => <div className="finding fail" key={item.id}>! <span>{item.message}</span></div>) : <div className="finding pass">✓ <span>No must-fix findings reported.</span></div>}</section>
    {recommendations.length > 0 && <section><h3>Recommendations</h3>{recommendations.map((item) => <div className="finding recommendation" key={item.id}>→ <span>{item.message}</span></div>)}</section>}
  </aside>;
}
