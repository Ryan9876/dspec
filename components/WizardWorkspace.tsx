'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { useWorkflowStore, type SpecStage } from '@/stores/workflowStore';
import { LocalMonaco } from './LocalMonaco';
import { ReviewBoard } from './ReviewBoard';

const stages: Array<{ id: SpecStage; index: string; label: string; subtitle: string }> = [
  { id: 'constitution', index: '01', label: 'Constitution', subtitle: 'Boundaries & governance' },
  { id: 'requirements', index: '02', label: 'Requirements', subtitle: 'Users & required behavior' },
  { id: 'solution', index: '03', label: 'Solution', subtitle: 'Architecture & contracts' },
  { id: 'tasks', index: '04', label: 'Tasks', subtitle: 'Executable work packages' }
];

const questions: Record<SpecStage, Array<{ key: string; label: string; options?: string[]; placeholder?: string }>> = {
  constitution: [
    { key: 'idea', label: 'What are you trying to build?', placeholder: 'Describe the product outcome, users, and the problem it should solve.' },
    { key: 'delivery', label: 'Primary delivery target', options: ['Local desktop/browser', 'Hosted web app', 'Internal enterprise app', 'Other / undecided'] },
    { key: 'data_sensitivity', label: 'Data sensitivity', options: ['Public/non-sensitive', 'Private business data', 'Regulated/sensitive data', 'Unsure — recommend controls'] }
  ],
  requirements: [
    { key: 'primary_user', label: 'Who is the primary user?', placeholder: 'Role, level of expertise, and what they need to accomplish.' },
    { key: 'success', label: 'What does success look like?', placeholder: 'Observable outcomes, not implementation details.' },
    { key: 'failure', label: 'Which failure would be most damaging?', options: ['Wrong result', 'Data loss', 'Security exposure', 'Workflow interruption', 'Unsure'] }
  ],
  solution: [
    { key: 'constraints', label: 'Known architecture constraints', placeholder: 'Existing systems, required platforms, deployment boundaries, integrations, or leave blank for recommendations.' },
    { key: 'reversibility', label: 'Change tolerance', options: ['Prefer reversible/simple', 'Optimize for scale', 'Must preserve legacy compatibility', 'Unsure — recommend approach'] },
    { key: 'observability', label: 'Operational visibility needed', options: ['Basic health/errors', 'Structured logs + metrics', 'Full auditability', 'Unsure'] }
  ],
  tasks: [
    { key: 'handoff', label: 'Primary coding-agent handoff', options: ['ChatGPT / Codex', 'Claude Code', 'Cursor', 'Multiple agents'] },
    { key: 'validation', label: 'Validation expectation', options: ['Automated tests', 'Automated + browser/runtime', 'Automated + deployment verification', 'Recommend based on risk'] },
    { key: 'notes', label: 'Execution notes', placeholder: 'Any sequencing, environment, or review constraints the implementation agent must preserve.' }
  ]
};

export function WizardWorkspace() {
  const session = useWorkflowStore((s) => s.session);
  const setSession = useWorkflowStore((s) => s.setSession);
  const stage = useWorkflowStore((s) => s.stage);
  const setStage = useWorkflowStore((s) => s.setStage);
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [draft, setDraft] = useState('');
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [message, setMessage] = useState('');
  const [generating, setGenerating] = useState(false);
  const answerTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!session) return;
    setAnswers(session.answers[stage] ?? {});
    setDraft(session.specs[stage]?.content ?? '');
    setSaveState('idle');
    setMessage('');
  }, [session?.id, stage]);

  const saveAnswers = useCallback((next: Record<string, unknown>) => {
    if (!session) return;
    if (answerTimer.current) clearTimeout(answerTimer.current);
    setSaveState('saving');
    answerTimer.current = setTimeout(async () => {
      try { setSession(await api.saveAnswers(session.id, stage, next)); setSaveState('saved'); }
      catch { setSaveState('error'); }
    }, 450);
  }, [session, stage, setSession]);

  function updateAnswer(key: string, value: string) {
    const next = { ...answers, [key]: value };
    setAnswers(next); saveAnswers(next);
  }

  function updateDraft(next: string) {
    setDraft(next);
    if (!session) return;
    if (draftTimer.current) clearTimeout(draftTimer.current);
    setSaveState('saving');
    draftTimer.current = setTimeout(async () => {
      try { setSession(await api.saveSpec(session.id, stage, next)); setSaveState('saved'); }
      catch { setSaveState('error'); }
    }, 650);
  }

  async function generate() {
    if (!session) return;
    setGenerating(true); setMessage('Generating with the selected provider…');
    try { const next = await api.generate(session.id, stage); setSession(next); setDraft(next.specs[stage].content); setMessage('Generated and reviewed. Inspect before approval.'); }
    catch (error) { setMessage(error instanceof Error ? error.message : 'Generation unavailable.'); }
    finally { setGenerating(false); }
  }

  async function approve() {
    if (!session) return;
    setMessage('');
    try { setSession(await api.approveSpec(session.id, stage)); setMessage(`${stage} approved for this project state.`); }
    catch (error) { setMessage(error instanceof Error ? error.message : 'Approval blocked.'); }
  }

  async function copy() {
    await navigator.clipboard.writeText(draft); setMessage('Copied this stage to the clipboard.');
  }

  async function download(draftBundle: boolean) {
    if (!session) return;
    const response = await fetch(`/api/export/${session.id}?draft=${draftBundle ? 'true' : 'false'}`);
    if (!response.ok) { setMessage(await response.text()); return; }
    const blob = await response.blob(); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${session.bundle_name}-${draftBundle ? 'draft-' : ''}bundle.zip`; anchor.click(); URL.revokeObjectURL(url);
    setMessage(draftBundle ? 'Draft bundle downloaded and explicitly labeled draft.' : 'Approved agent bundle downloaded.');
  }

  if (!session) return null;
  const spec = session.specs[stage];
  const stagePosition = stages.findIndex((s) => s.id === stage);
  return <div className="wizard-shell">
    <aside className="stepper">
      <div className="stepper-label">SPEC PIPELINE</div>
      {stages.map((item, index) => {
        const approved = session.specs[item.id].approved;
        const active = item.id === stage;
        return <button key={item.id} className={`step ${active ? 'active' : ''} ${approved ? 'complete' : ''}`} onClick={() => setStage(item.id)}>
          <span className="step-index">{approved ? '✓' : item.index}</span><span><strong>{item.label}</strong><small>{item.subtitle}</small></span>{index < stages.length - 1 && <i />}
        </button>;
      })}
      <div className="stepper-foot"><span>Progress</span><strong>{Math.round(((stagePosition + (spec.approved ? 1 : 0.45)) / 4) * 100)}%</strong><div className="mini-progress"><i style={{ width: `${((stagePosition + (spec.approved ? 1 : 0.45)) / 4) * 100}%` }} /></div></div>
    </aside>

    <main className="workspace">
      <div className="workspace-title"><div><span className="eyebrow">TIER {stagePosition + 1} OF 4</span><h1>{stages[stagePosition].label}</h1><p>{stages[stagePosition].subtitle}. Capture product intent first; technical assumptions remain reviewable.</p></div><div className="save-indicator"><span className={`runtime-light ${saveState === 'saved' ? 'online' : ''}`}></span>{saveState === 'saving' ? 'Saving…' : saveState === 'error' ? 'Save failed' : saveState === 'saved' ? 'Saved locally' : 'Local persistence'}</div></div>

      <section className="assistant-card">
        <div className="assistant-head"><div className="assistant-symbol">✦</div><div><span className="eyebrow">GUIDED DISCOVERY</span><h2>Answer what you know. DSpec should expose the gaps.</h2></div></div>
        <div className="question-list">
          {questions[stage].map((question) => <div className="question" key={question.key}><label>{question.label}</label>
            {question.options ? <div className="option-grid">{question.options.map((option) => <button key={option} className={answers[question.key] === option ? 'selected' : ''} onClick={() => updateAnswer(question.key, option)}><span className="radio"></span>{option}</button>)}</div> : <textarea rows={3} value={String(answers[question.key] ?? '')} onChange={(e) => updateAnswer(question.key, e.target.value)} placeholder={question.placeholder} />}
          </div>)}
        </div>
        <div className="assistant-actions"><span className="muted small">Provider use is explicit. A failed model call never discards these answers.</span><button className="primary glow" onClick={generate} disabled={generating}>{generating ? 'Generating…' : 'Generate stage draft'}</button></div>
      </section>

      <section className="draft-area">
        <div className="draft-toolbar"><div><span className="eyebrow">DRAFT REVIEW</span><h2>{stage}.md</h2></div><div className="toolbar-actions"><button className="secondary" onClick={copy} disabled={!draft}>Copy</button><button className="secondary" onClick={() => download(true)}>Draft bundle</button><button className="primary" onClick={approve} disabled={!draft || spec.approved}>Approve stage</button></div></div>
        <div className="draft-grid"><div className="editor-wrap"><LocalMonaco value={draft} onChange={updateDraft} /></div><ReviewBoard review={spec.review} approved={spec.approved} /></div>
        <div className="stage-footer"><p className="muted small">{message}</p><button className="secondary" onClick={() => download(false)}>Export approved agent bundle</button></div>
      </section>
    </main>
  </div>;
}
