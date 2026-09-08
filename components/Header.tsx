'use client';
import { useState } from 'react';
import { useWorkflowStore } from '@/stores/workflowStore';
import { ProviderModal } from './ProviderModal';

export function Header() {
  const provider = useWorkflowStore((s) => s.provider);
  const session = useWorkflowStore((s) => s.session);
  const [open, setOpen] = useState(false);
  const active = provider?.active_provider ?? 'detecting';
  const info = provider?.providers[active];
  const ready = info?.kind === 'local' ? info?.online : info?.configured;
  return <>
    <header className="topbar">
      <div className="brand"><div className="logo-mark"><span></span><span></span><span></span></div><div><strong>DSpec</strong><small>SPEC-FIRST DELIVERY</small></div></div>
      <div className="project-chip"><span className="muted small">PROJECT</span><strong>{session?.bundle_name ?? 'No project loaded'}</strong></div>
      <button className="runtime-chip" onClick={() => setOpen(true)}><span className={`runtime-light ${ready ? 'online' : ''}`}></span><span><small>127.0.0.1:3210</small><strong>{active.replace('_', ' ')}{provider?.active_model ? ` · ${provider.active_model}` : ''}</strong></span><span className="chevron">⌄</span></button>
    </header>
    {open && <ProviderModal onClose={() => setOpen(false)} />}
  </>;
}
