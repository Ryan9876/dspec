import { create } from 'zustand';

export type SpecStage = 'constitution' | 'requirements' | 'solution' | 'tasks';
export type ProjectType = 'greenfield' | 'existing';
export type Review = {
  score: number;
  passing: string[];
  must_fix: Array<{ id: string; message: string }>;
  recommendations: Array<{ id: string; message: string }>;
};
export type SpecDocument = {
  content: string;
  revision: number;
  version: number;
  quality_score: number;
  approved: boolean;
  review: Review | Record<string, never>;
  updated_at: string;
};
export type Session = {
  id: string;
  bundle_name: string;
  project_type: ProjectType;
  status: string;
  active_stage: SpecStage;
  answers: Record<SpecStage, Record<string, unknown>>;
  specs: Record<SpecStage, SpecDocument>;
};
export type ProviderDiscovery = {
  active_provider: string;
  active_model: string;
  providers: Record<string, { kind: string; online?: boolean; configured?: boolean; models?: string[] }>;
};

type WorkflowState = {
  session: Session | null;
  stage: SpecStage;
  provider: ProviderDiscovery | null;
  activeView: 'wizard' | 'audit';
  setSession: (session: Session | null) => void;
  setStage: (stage: SpecStage) => void;
  setProvider: (provider: ProviderDiscovery | null) => void;
  setActiveView: (view: 'wizard' | 'audit') => void;
};

export const useWorkflowStore = create<WorkflowState>((set) => ({
  session: null,
  stage: 'constitution',
  provider: null,
  activeView: 'wizard',
  setSession: (session) => set({ session, stage: session?.active_stage ?? 'constitution' }),
  setStage: (stage) => set({ stage }),
  setProvider: (provider) => set({ provider }),
  setActiveView: (activeView) => set({ activeView })
}));
