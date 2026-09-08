'use client';

import { useEffect, useState } from 'react';
import { Header } from '@/components/Header';
import { WizardWorkspace } from '@/components/WizardWorkspace';
import { AuditWorkspace } from '@/components/AuditWorkspace';
import { api } from '@/lib/api';
import { useWorkflowStore } from '@/stores/workflowStore';

const SESSION_KEY = 'dspec.activeSessionId';

export default function Home() {
  const session = useWorkflowStore((s) => s.session);
  const setSession = useWorkflowStore((s) => s.setSession);
  const activeView = useWorkflowStore((s) => s.activeView);
  const setActiveView = useWorkflowStore((s) => s.setActiveView);
  const setProvider = useWorkflowStore((s) => s.setProvider);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState('my-software-project');
  const [projectType, setProjectType] = useState<'greenfield' | 'existing'>('greenfield');
  const [message, setMessage] = useState('');

  useEffect(() => {
    async function bootstrap() {
      try { setProvider(await api.providers()); } catch { setProvider(null); }
      const previous = window.localStorage.getItem(SESSION_KEY);
      if (previous) {
        try { setSession(await api.session(previous)); } catch { window.localStorage.removeItem(SESSION_KEY); }
      }
      setLoading(false);
    }
    void bootstrap();
  }, [setProvider, setSession]);

  useEffect(() => {
    if (session?.id) window.localStorage.setItem(SESSION_KEY, session.id);
  }, [session?.id]);

  async function create() {
    setMessage('Creating local project…');
    try { const next = await api.createSession(name, projectType); setSession(next); setMessage(''); }
    catch (error) { setMessage(error instanceof Error ? error.message : 'Unable to create project.'); }
  }

  if (loading) return <div className="loading-screen"><div className="logo-mark large"><span></span><span></span><span></span></div><p>Loading local DSpec state…</p></div>;

  return <div className="app-frame">
    <Header />
    {!session ? <main className="onboarding">
      <div className="onboarding-copy"><span className="eyebrow">LOCAL SPEC-FIRST WORKSPACE</span><h1>Turn product intent into a buildable contract.</h1><p>DSpec separates governance, requirements, solution architecture, and executable tasks—then checks the handoff before a coding agent receives it.</p><div className="trust-row"><span>Loopback only</span><span>Transactional local state</span><span>Explicit cloud use</span></div></div>
      <section className="create-card"><span className="eyebrow">START A PROJECT</span><h2>What are you defining?</h2><label className="field"><span>Project name</span><input value={name} onChange={(e) => setName(e.target.value)} /></label><div className="segmented"><button className={projectType === 'greenfield' ? 'selected' : ''} onClick={() => setProjectType('greenfield')}>New product</button><button className={projectType === 'existing' ? 'selected' : ''} onClick={() => setProjectType('existing')}>Existing codebase</button></div><button className="primary full" onClick={create}>Create local project</button><p className="muted small">{message || 'Nothing is sent to a cloud provider unless you explicitly select one.'}</p></section>
    </main> : <>
      <nav className="mode-tabs"><button className={activeView === 'wizard' ? 'active' : ''} onClick={() => setActiveView('wizard')}>Specification workspace</button><button className={activeView === 'audit' ? 'active' : ''} onClick={() => setActiveView('audit')}>Repository audit</button><div className="mode-spacer"/><button onClick={() => { window.localStorage.removeItem(SESSION_KEY); setSession(null); }}>New project</button></nav>
      {activeView === 'wizard' ? <WizardWorkspace /> : <AuditWorkspace />}
    </>}
  </div>;
}
