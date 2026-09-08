'use client';

import { useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { useWorkflowStore } from '@/stores/workflowStore';

const defaults: Record<string, string> = {
  lm_studio: '',
  ollama: '',
  openai: 'gpt-5.6',
  anthropic: 'claude-opus-4-1'
};

export function ProviderModal({ onClose }: { onClose: () => void }) {
  const providerState = useWorkflowStore((s) => s.provider);
  const setProviderState = useWorkflowStore((s) => s.setProvider);
  const [provider, setProvider] = useState(providerState?.active_provider ?? 'lm_studio');
  const detectedModels = providerState?.providers[provider]?.models ?? [];
  const [model, setModel] = useState(providerState?.active_model || detectedModels[0] || defaults[provider] || '');
  const [apiKey, setApiKey] = useState('');
  const [message, setMessage] = useState('');
  const cloud = provider === 'openai' || provider === 'anthropic';
  const choices = useMemo(() => ['lm_studio', 'ollama', 'openai', 'anthropic'], []);

  async function save() {
    setMessage('Saving…');
    try {
      await api.selectProvider(provider, model, cloud ? apiKey : undefined);
      setApiKey('');
      setProviderState(await api.providers());
      setMessage('Provider selected.');
      window.setTimeout(onClose, 450);
    } catch (error) {
      setApiKey('');
      setMessage(error instanceof Error ? error.message : 'Unable to select provider.');
    }
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="modal" role="dialog" aria-modal="true" aria-label="Provider switcher" onMouseDown={(e) => e.stopPropagation()}>
      <div className="section-heading"><div><span className="eyebrow">Runtime</span><h2>Choose inference provider</h2></div><button className="icon-button" onClick={onClose}>×</button></div>
      <p className="muted">Local providers remain on this workstation. Cloud content is sent only after you explicitly select a cloud provider.</p>
      <div className="provider-grid">
        {choices.map((item) => {
          const info = providerState?.providers[item];
          const ready = info?.kind === 'local' ? info.online : info?.configured;
          return <button key={item} className={`provider-card ${provider === item ? 'selected' : ''}`} onClick={() => { setProvider(item); setModel((providerState?.providers[item]?.models ?? [])[0] || defaults[item] || ''); }}>
            <strong>{item.replace('_', ' ')}</strong><span className={`status-dot ${ready ? 'online' : ''}`}>{ready ? 'ready' : info?.kind === 'local' ? 'offline' : 'not configured'}</span>
          </button>;
        })}
      </div>
      <label className="field"><span>Model identifier</span><input value={model} onChange={(e) => setModel(e.target.value)} list="provider-models" placeholder="Select or enter a model" /></label>
      <datalist id="provider-models">{detectedModels.map((item) => <option key={item} value={item} />)}</datalist>
      {cloud && <label className="field"><span>API key {providerState?.providers[provider]?.configured ? '(leave blank to keep existing)' : ''}</span><input type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="Write-only; never returned to the browser" /></label>}
      <div className="modal-footer"><span className="muted small">{message}</span><button className="primary" onClick={save}>Use provider</button></div>
    </section>
  </div>;
}
