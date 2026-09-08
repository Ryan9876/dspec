import type { ProviderDiscovery, Session, SpecStage } from '@/stores/workflowStore';

async function parseError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === 'string') return body.detail;
    if (body.detail?.message) return body.detail.message;
    return JSON.stringify(body.detail ?? body);
  } catch {
    return `${response.status} ${response.statusText}`;
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) throw new Error(await parseError(response));
  return response.json() as Promise<T>;
}

export type StreamHandlers = {
  onToken?: (text: string, seq: number) => void;
  onReview?: (review: unknown) => void;
  onHeartbeat?: () => void;
};

async function consumeSse(response: Response, handlers: StreamHandlers): Promise<void> {
  if (!response.body) throw new Error('Streaming response body is unavailable.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf('\n\n');
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (frame.startsWith(':')) {
        handlers.onHeartbeat?.();
      } else {
        const lines = frame.split('\n');
        const event = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() ?? 'message';
        const data = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n');
        if (data) {
          const payload = JSON.parse(data);
          if (event === 'token') handlers.onToken?.(String(payload.text ?? ''), Number(payload.seq ?? 0));
          if (event === 'review_feedback') handlers.onReview?.(payload);
          if (event === 'error') throw new Error(payload.detail?.message ?? payload.detail ?? 'Generation failed.');
        }
      }
      boundary = buffer.indexOf('\n\n');
    }
  }
}

export const api = {
  health: () => request<Record<string, unknown>>('/api/health'),
  providers: () => request<ProviderDiscovery>('/api/providers'),
  createSession: (bundleName: string, projectType: 'greenfield' | 'existing') =>
    request<Session>('/api/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bundle_name: bundleName, project_type: projectType }) }),
  session: (id: string) => request<Session>(`/api/sessions/${id}`),
  saveAnswers: (id: string, stage: SpecStage, answers: Record<string, unknown>) =>
    request<Session>(`/api/sessions/${id}/answers/${stage}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ answers }) }),
  saveSpec: (id: string, stage: SpecStage, content: string) =>
    request<Session>(`/api/sessions/${id}/specs/${stage}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }) }),
  approveSpec: (id: string, stage: SpecStage) => request<Session>(`/api/sessions/${id}/specs/${stage}/approve`, { method: 'POST' }),
  revise: (id: string, stage: SpecStage, recommendation: string) =>
    request<Session>('/api/spec/revise', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: id, stage, recommendation }) }),
  streamGenerate: async (id: string, stage: SpecStage, handlers: StreamHandlers) => {
    const response = await fetch('/api/spec/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: id, stage })
    });
    if (!response.ok) throw new Error(await parseError(response));
    await consumeSse(response, handlers);
    return request<Session>(`/api/sessions/${id}`);
  },
  selectProvider: (provider: string, model: string, apiKey?: string) =>
    request<{ success: boolean; active_provider: string; active_model: string }>('/api/provider/select', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider, model, api_key: apiKey || null }) }),
  audit: (repoPath: string) => request<AuditResult>('/api/audit/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repo_path: repoPath, use_hash_cache: true }) })
};

export type AuditResult = {
  repo_path: string;
  total_files_scanned: number;
  cached_files_skipped: number;
  scan_duration_ms: number;
  health_score: {
    composite_score: number;
    governance_security_score: number;
    requirements_clarity_score: number;
    architecture_consistency_score: number;
    test_task_coverage_score: number;
  };
  critical_gaps: string[];
  structural_diff_md: string;
  upgrade_spec_md: string;
  limitations: string[];
};
