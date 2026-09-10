"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { create } from "zustand";
import { Activity, Archive, Check, ChevronRight, CircleAlert, Clipboard, Cpu, FolderSearch, LoaderCircle, Plus, RefreshCw, Save, Settings2, ShieldCheck, Sparkles } from "lucide-react";
import { loader } from "@monaco-editor/react";

loader.config({ paths: { vs: "/monaco/vs" } });
const Editor = dynamic(() => import("@monaco-editor/react"), { ssr: false });

type Stage = "constitution" | "requirements" | "solution" | "tasks";
type Session = {
  id: string;
  bundle_name: string;
  project_type: string;
  specs: Partial<Record<Stage, {
    id: string; content: string; revision_number: number; version_number: number;
    quality_score: number; approval_status: string; review: Review;
  }>>;
  drafts?: Partial<Record<Stage,{content:string;updated_at:string}>>;
  answers: Array<{stage: Stage; question_id: string; selected_option_id?: string; free_text_payload?: string; updated_at?: string}>;
  engineering_decisions?: EngineeringDecision[];
  execution_plan?: ExecutionEstimateBundle | null;
};
type Review = {
  score?: number; threshold?: number; passed?: boolean;
  passing?: Array<{id:string;label:string;detail:string}>;
  must_fix?: Array<{id:string;label:string;detail:string;recommendation?:string}>;
  recommendations?: string[];
  semantic_status?: "PASS"|"FAIL"|"NOT TESTED"|string;
  semantic_error?: string;
};
type DiscoveryOption = {
  id:string; label:string; rationale:string; plain_english_consequence?:string|null;
  advantages?:string[]; tradeoffs?:string[]; engineering_concept?:EngineeringConcept|null;
  technical_details?:TechnicalDetail[];
};
type DiscoveryQuestion = {
  id:string; question:string; why_it_matters:string; options:DiscoveryOption[];
  recommended_option_id:string; allow_free_text:boolean;
};
type DiscoveryResult = { questions:DiscoveryQuestion[]; gaps_found:string[] };
type EngineeringConcept = { id:string; label:string; mental_model:string };
type TechnicalDetail = { category:string; choice:string; consequence:string };
type ArchitectureOption = {
  id:string; role:"best_fit"|"simplest"|"alternative"; title:string;
  plain_english_summary:string; why_recommended:string; advantages:string[]; tradeoffs:string[];
  operational_impact:string; why_engineers_care:string; engineering_concept:EngineeringConcept;
  reconsider_when:string[]; technical_details:TechnicalDetail[];
};
type ArchitectureOptionsResult = {
  recommended_option_id:string; alternative_objective:string; decision_summary:string;
  source_context_sha256:string; customization?:string; options:ArchitectureOption[];
};
type EngineeringDecision = {
  decision_type:string; decision_id:string; selected_option_id?:string;
  options:ArchitectureOptionsResult; custom?:Record<string,unknown>; source_context_sha256:string; updated_at:string;
};
type StrategyName = "cost_optimized"|"balanced"|"maximum_capability";
type StrategyPlan = {
  strategy:StrategyName; strategy_label:string; budget_status:string; blocked_task_count:number;
  model_mix:Record<"local"|"standard"|"advanced",{tasks:number;percent:number}>;
  cloud_api_cost_estimate:{low:number|null;expected:number|null;high:number|null};
  cost_confidence:string; estimate_assumptions:string[];
};
type ExecutionEstimateBundle = {
  status:"ESTIMATE"|"SELECTED"|string; source_snapshot_sha256:string;
  recommended_strategy:StrategyName; selected_strategy?:StrategyName|null;
  selected_plan?:StrategyPlan; budget?:number|null; escalation?:"automatic"|"ask_first";
  budget_behavior?:"stop_before_exceeding"|"ask_before_overage"|"no_enforcement";
  plans:Record<StrategyName,StrategyPlan>;
};
type Health = {
  status: string; version: string; port: number; active_provider: {provider:string;model:string};
  active_provider_ready?: boolean;
  detected_local_services?: Record<string,{online:boolean;models:string[]}>;
  cloud_provider_readiness?: Record<string,boolean>;
  dspy?: {available:boolean;optimization:string;mipro_v2:string};
};
type RecoveryPrompt = {
  message: string;
  statePreserved: boolean;
  fallbackOptions: string[];
};

const STAGES: Stage[] = ["constitution","requirements","solution","tasks"];
const LABELS: Record<Stage,string> = {
  constitution:"Constitution", requirements:"Requirements", solution:"Solution", tasks:"Tasks"
};
const ASSIST: Record<Stage,{title:string;prompt:string;choices:string[]}> = {
  constitution:{title:"Project intent",prompt:"What do you want to build or change? Describe the user, problem, and desired outcome.",choices:["Local-first / private by default","Cloud-capable with explicit consent","Recommend a safe default"]},
  requirements:{title:"Outcome clarity",prompt:"What should a user be able to accomplish, including failure and recovery paths?",choices:["Define the core happy path","Identify edge cases first","Recommend complete coverage"]},
  solution:{title:"Architecture decisions",prompt:"Which technical decisions are constraints versus preferences?",choices:["Preserve stated stack","Optimize for lowest complexity","Recommend based on requirements"]},
  tasks:{title:"Execution strategy",prompt:"How should implementation be sequenced and verified?",choices:["Small reversible slices","Capability milestones","Recommend lowest-risk sequence"]},
};

type Store = {
  session: Session | null; stage: Stage; health: Health | null; busy: string | null;
  setSession:(s:Session|null)=>void; setStage:(s:Stage)=>void; setHealth:(h:Health|null)=>void; setBusy:(v:string|null)=>void;
};
const useWorkflow = create<Store>((set)=>({
  session:null, stage:"constitution", health:null, busy:null,
  setSession:(session)=>set({session}), setStage:(stage)=>set({stage}), setHealth:(health)=>set({health}), setBusy:(busy)=>set({busy}),
}));

async function api<T>(path:string, init?:RequestInit):Promise<T>{
  const r=await fetch(path,{...init,headers:{"Content-Type":"application/json",...(init?.headers||{})}});
  if(!r.ok){
    let detail:string;
    try{ const j=await r.json(); detail=typeof j.detail==="string"?j.detail:JSON.stringify(j.detail??j); }
    catch{ detail=await r.text(); }
    throw new Error(detail||`HTTP ${r.status}`);
  }
  return r.json();
}

function scoreTone(score?:number){ if((score??0)>=.9)return "text-emerald-300"; if((score??0)>=.7)return "text-amber-300"; return "text-rose-300"; }

export default function Home(){
  const {session,stage,health,busy,setSession,setStage,setHealth,setBusy}=useWorkflow();
  const [sessions,setSessions]=useState<Array<{id:string;bundle_name:string}>>([]);
  const [providerOpen,setProviderOpen]=useState(false);
  const [recovery,setRecovery]=useState<RecoveryPrompt|null>(null);
  const [auditOpen,setAuditOpen]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [draft,setDraft]=useState("");
  const [streamText,setStreamText]=useState("");
  const [streamAttempt,setStreamAttempt]=useState<number|null>(null);
  const [streamProvisional,setStreamProvisional]=useState(false);
  const [review,setReview]=useState<Review>({});
  const saveTimer=useRef<ReturnType<typeof setTimeout>|null>(null);

  const refreshHealth=useCallback(async()=>{
    try{ setHealth(await api<Health>("/api/health")); }catch(e){ setHealth(null); setError(String(e)); }
  },[setHealth]);

  const loadSession=useCallback(async(id:string)=>{
    const s=await api<Session>(`/api/sessions/${id}`);
    setSession(s); setDraft(s.drafts?.[stage]?.content??s.specs[stage]?.content??""); setReview(s.specs[stage]?.review??{});
  },[setSession,stage]);

  const refreshSessions=useCallback(async()=>{
    const list=await api<Array<{id:string;bundle_name:string}>>("/api/sessions");
    setSessions(list);
    if(!session && list.length) await loadSession(list[0].id);
  },[loadSession,session]);

  useEffect(()=>{ void refreshHealth(); void refreshSessions(); },[]); // intentional bootstrap
  useEffect(()=>{ const timer=window.setInterval(()=>{void refreshHealth()},5000); return ()=>window.clearInterval(timer); },[refreshHealth]);
  useEffect(()=>{ if(session){ setDraft(session.drafts?.[stage]?.content??session.specs[stage]?.content??""); setReview(session.specs[stage]?.review??{}); } },[stage,session?.id]);

  useEffect(()=>{
    if(!session||!draft.trim())return;
    const persist=()=>{
      const body=JSON.stringify({session_id:session.id,stage,content:draft});
      const blob=new Blob([body],{type:"application/json"});
      if(!navigator.sendBeacon("/api/spec/draft",blob)){
        void fetch("/api/spec/draft",{method:"POST",headers:{"Content-Type":"application/json"},body,keepalive:true});
      }
    };
    const onVisibility=()=>{if(document.visibilityState==="hidden")persist();};
    window.addEventListener("pagehide",persist);
    document.addEventListener("visibilitychange",onVisibility);
    return ()=>{window.removeEventListener("pagehide",persist);document.removeEventListener("visibilitychange",onVisibility);};
  },[session?.id,stage,draft]);

  const current=session?.specs[stage];

  async function newProject(){
    const name=window.prompt("Project / bundle name");
    if(!name?.trim())return;
    setBusy("Creating project");
    try{
      const s=await api<Session>("/api/sessions",{method:"POST",body:JSON.stringify({bundle_name:name.trim(),project_type:"greenfield"})});
      setSession(s); setStage("constitution"); setDraft(""); setReview({}); await refreshSessions();
    }catch(e){setError(String(e));}finally{setBusy(null);}
  }

  async function saveDraft(content=draft){
    if(!session||!content.trim())return false;
    try{
      const saved=await api<{content:string;review:Review}>("/api/spec/save",{method:"POST",body:JSON.stringify({session_id:session.id,stage,content})});
      setReview(saved.review??{}); await loadSession(session.id);
      return true;
    }catch(e){
      setError(String(e));
      return false;
    }
  }

  async function saveDraftBuffer(content:string){
    if(!session)return;
    await api("/api/spec/draft",{method:"POST",body:JSON.stringify({session_id:session.id,stage,content})});
  }

  function draftChanged(value:string|undefined){
    const next=value??""; setDraft(next); setReview({});
    if(saveTimer.current)clearTimeout(saveTimer.current);
    saveTimer.current=setTimeout(()=>{void saveDraftBuffer(next).catch(e=>setError(String(e)))},250);
  }

  async function saveAssistant(questionId:string, choice:string, text:string){
    if(!session)return;
    await api("/api/answers",{method:"POST",body:JSON.stringify({session_id:session.id,stage,question_id:questionId,selected_option_id:choice,free_text_payload:text})});
    await loadSession(session.id);
  }

  async function generate(){
    if(!session)return;
    setBusy("Generating"); setError(null); setRecovery(null); setStreamText(""); setStreamAttempt(null); setStreamProvisional(false);
    let output=""; let lastSeq=0; let reconnectAttempt=0; let initial=true; let terminal=false; let providerFailure=false;
    const sleep=(ms:number)=>new Promise(resolve=>setTimeout(resolve,ms));
    try{
      while(!terminal){
        try{
          const url=initial?"/api/spec/stream":`/api/spec/stream/resume?session_id=${encodeURIComponent(session.id)}&last_seq=${lastSeq}`;
          const response=await fetch(url,initial?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({session_id:session.id,stage})}:undefined);
          if(!response.ok||!response.body){
            const message=await response.text();
            if(response.status>=400&&response.status<500)terminal=true;
            throw new Error(message);
          }
          if(initial){
            const baseline=Number(response.headers.get("X-DSpec-Start-Seq")??"0");
            if(Number.isFinite(baseline))lastSeq=Math.max(lastSeq,baseline);
          }
          const reader=response.body.getReader(); const decoder=new TextDecoder(); let buffer="";
          while(true){
            const {done,value}=await reader.read();
            if(done)break;
            buffer+=decoder.decode(value,{stream:true});
            const frames=buffer.split("\n\n"); buffer=frames.pop()??"";
            for(const frame of frames){
              const event=frame.split("\n").find(x=>x.startsWith("event:"))?.slice(6).trim();
              const dataLine=frame.split("\n").find(x=>x.startsWith("data:"));
              if(!dataLine)continue;
              const data=JSON.parse(dataLine.slice(5));
              if(typeof data.seq==="number")lastSeq=Math.max(lastSeq,data.seq);

              if(event==="candidate_start"){
                const candidate=typeof data.attempt==="number"?data.attempt:null;
                output="";
                setStreamText("");
                setStreamAttempt(candidate);
                setStreamProvisional(true);
                setBusy(candidate?`Generating candidate ${candidate}`:"Generating candidate");
              }
              if(event==="token"){
                output+=data.text??"";
                setStreamText(output);
              }
              if(event==="candidate_end"){
                const candidate=typeof data.attempt==="number"?data.attempt:streamAttempt;
                setBusy(candidate?`Evaluating candidate ${candidate}`:"Evaluating candidate");
              }
              if(event==="candidate_selected"){
                output=String(data.content??"");
                setStreamText(output);
                setStreamProvisional(false);
                setBusy("Finalizing selected draft");
              }
              if(event==="complete"){terminal=true;break;}
              if(event==="error"){
                providerFailure=true;terminal=true;
                const fallbacks=Array.isArray(data.fallback_options)
                  ? Array.from(new Set(data.fallback_options.filter((item:unknown)=>typeof item==="string"&&item!==health?.active_provider.provider))) as string[]
                  : [];
                setRecovery({
                  message:data.message||"The selected provider became unavailable during generation.",
                  statePreserved:data.state_preserved===true,
                  fallbackOptions:fallbacks,
                });
                try{setHealth(await api<Health>("/api/health"));}catch{}
                setProviderOpen(true);
                throw new Error(data.message||"Generation failed; saved input state was preserved.");
              }
            }
            if(terminal)break;
          }
          if(!terminal)throw new Error("Generation stream disconnected before completion.");
        }catch(e){
          if(providerFailure||terminal)throw e;
          if(reconnectAttempt>=5)throw e;
          reconnectAttempt+=1;
          setBusy(`Reconnecting stream (${reconnectAttempt}/5)`);
          await sleep(Math.min(500*Math.pow(1.5,reconnectAttempt-1),5000));
          initial=false;
          continue;
        }
      }
      await loadSession(session.id);
      setStreamText("");
      setStreamAttempt(null);
      setStreamProvisional(false);
    }catch(e){
      setStreamText("");
      setStreamAttempt(null);
      setStreamProvisional(false);
      setError(String(e));
    }finally{setBusy(null);}
  }

  async function reviewNow(){
    if(!session)return; setBusy("Reviewing");
    try{
      if(draft.trim()){
        const saved=await saveDraft(draft);
        if(!saved)throw new Error("Formal revision could not be saved; semantic review was not started.");
      }
      const r=await api<Review>("/api/spec/review",{method:"POST",body:JSON.stringify({session_id:session.id,stage})});
      setReview(r);await loadSession(session.id);
    }
    catch(e){setError(String(e));}finally{setBusy(null);}
  }

  async function applyReviewInstruction(instruction:string){
    if(!session||!draft.trim()||!instruction.trim())return;
    setBusy("Applying review fix"); setError(null);
    try{
      const r=await api<{content:string;review:Review;draft:{content:string;updated_at:string};session?:Session;formal_revision_created:boolean}>("/api/spec/revise",{
        method:"POST",
        body:JSON.stringify({session_id:session.id,stage,content:draft,instruction}),
      });
      setDraft(r.content);
      setReview(r.review??{});
      setSession(r.session??{...session,drafts:{...(session.drafts??{}),[stage]:r.draft}});
    }catch(e){setError(String(e));}finally{setBusy(null);}
  }

  async function approve(){
    if(!session)return; setBusy("Approving");
    try{await api("/api/spec/approve",{method:"POST",body:JSON.stringify({session_id:session.id,stage})});await loadSession(session.id);}
    catch(e){setError(String(e));}finally{setBusy(null);}
  }

  async function copyDraft(){
    await navigator.clipboard.writeText(draft);
  }

  async function copyAll(){
    if(!session)return;
    const text=STAGES.map(s=>{
      const content=session.specs[s]?.content??"NOT DRAFTED";
      return `<dspec-tier name="${s}">\n${content}\n</dspec-tier>`;
    }).join("\n\n");
    await navigator.clipboard.writeText(`# DSpec consolidated instruction set: ${session.bundle_name}\n\n${text}`);
  }

  const approvedCount=useMemo(()=>session?STAGES.filter(s=>session.specs[s]?.approval_status==="approved").length:0,[session]);
  const draftDirty=draft!==(current?.content??"");
  const activeProviderReady=useMemo(()=>{
    if(!health)return false;
    if(typeof health.active_provider_ready==="boolean")return health.active_provider_ready;
    const provider=health.active_provider.provider;
    if(provider==="lm_studio"||provider==="ollama"){
      const service=health.detected_local_services?.[provider];
      return service?.online===true && service.models.includes(health.active_provider.model);
    }
    return health.cloud_provider_readiness?.[provider]===true;
  },[health]);
  const activeProviderStatus=useMemo(()=>{
    if(!health)return "offline";
    const provider=health.active_provider.provider;
    if(provider==="lm_studio"||provider==="ollama"){
      const service=health.detected_local_services?.[provider];
      if(!service?.online)return "offline";
      if(!service.models.length)return "no model";
      if(!activeProviderReady)return "select model";
    }
    return activeProviderReady?"ready":"not configured";
  },[health,activeProviderReady]);

  return <main className="min-h-screen">
    <header className="sticky top-0 z-30 border-b border-slate-800/90 bg-[#0B0F17]/90 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1540px] items-center gap-4 px-5 py-3">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-xl border border-indigo-400/40 bg-indigo-500/15 shadow-[0_0_22px_rgba(99,102,241,.2)]"><ShieldCheck className="h-5 w-5 text-indigo-300"/></div>
          <div><div className="flex items-center gap-2"><div className="font-semibold tracking-wide">DSpec AI</div>{health&&<span className="text-[10px] font-medium text-slate-500">v{health.version}</span>}</div><div className="text-xs text-slate-500">spec-first delivery workspace</div></div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button className="badge bg-slate-950/60" aria-label="LLM provider switcher" onClick={()=>setProviderOpen(true)}>
            <span className={`dot ${activeProviderReady?"ok":"bad"}`}/><Cpu className="h-3.5 w-3.5"/>
            {health?`${health.active_provider.provider} · ${health.active_provider.model} · ${activeProviderStatus}`:"backend offline"}
            <Settings2 className="h-3.5 w-3.5"/>
          </button>
          <span className="badge"><Activity className="h-3.5 w-3.5"/>127.0.0.1:3210</span>
          <button className="btn" onClick={()=>void refreshHealth()} title="Refresh health"><RefreshCw className="h-4 w-4"/></button>
        </div>
      </div>
    </header>

    <div className="mx-auto grid max-w-[1540px] grid-cols-[250px_minmax(0,1fr)] gap-5 px-5 py-5">
      <aside className="space-y-4">
        <section className="panel p-3">
          <div className="mb-2 flex items-center justify-between"><span className="text-xs font-semibold uppercase tracking-widest text-slate-500">Projects</span><button className="btn !p-1.5" aria-label="New project" onClick={newProject}><Plus className="h-4 w-4"/></button></div>
          <div className="space-y-1">
            {sessions.map(s=><button key={s.id} onClick={()=>void loadSession(s.id)} className={`w-full rounded-lg px-3 py-2 text-left text-sm ${session?.id===s.id?"bg-indigo-500/15 text-indigo-200 ring-1 ring-indigo-400/30":"text-slate-400 hover:bg-slate-800/60"}`}>{s.bundle_name}</button>)}
            {!sessions.length&&<div className="px-2 py-5 text-center text-xs text-slate-600">Create the first governed project.</div>}
          </div>
        </section>
        <section className="panel p-3">
          <div className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-500">Workspace</div>
          <button className={`mb-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm ${!auditOpen?"bg-slate-800 text-white":"text-slate-400"}`} onClick={()=>setAuditOpen(false)}><Sparkles className="h-4 w-4"/>Spec Builder</button>
          <button className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm ${auditOpen?"bg-slate-800 text-white":"text-slate-400"}`} onClick={()=>setAuditOpen(true)}><FolderSearch className="h-4 w-4"/>Repository Audit</button>
        </section>
        {session&&<section className="panel p-3 text-xs text-slate-400">
          <div className="mb-2 flex justify-between"><span>Approval progress</span><span>{approvedCount}/4</span></div>
          <div className="h-1.5 overflow-hidden rounded bg-slate-800"><div className="h-full bg-gradient-to-r from-indigo-500 to-cyan-400" style={{width:`${approvedCount*25}%`}}/></div>
        </section>}
      </aside>

      <section className="min-w-0">
        {error&&<div className="mb-4 flex items-start gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-200"><CircleAlert className="mt-0.5 h-4 w-4 shrink-0"/><div className="flex-1 break-words">{error}</div><button onClick={()=>setError(null)}>×</button></div>}
        {recovery&&!providerOpen&&<div className="mb-4 flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-100"><CircleAlert className="mt-0.5 h-4 w-4 shrink-0"/><div className="flex-1"><div className="font-semibold">Generation interrupted</div><div className="mt-1 text-xs leading-5 text-amber-100/75">{recovery.statePreserved?"Saved project state is preserved. ":""}Choose a healthy local provider or configure/select a cloud fallback, then generate again.</div></div><button className="btn" onClick={()=>setProviderOpen(true)}>Switch provider</button></div>}
        {auditOpen?<AuditPanel/>:<>
          <div className="panel mb-4 flex items-center gap-1 p-2">
            {STAGES.map((s,i)=>{
              const spec=session?.specs[s]; const active=s===stage;
              return <button key={s} onClick={()=>setStage(s)} className={`flex flex-1 items-center gap-2 rounded-lg px-3 py-2.5 text-left ${active?"bg-indigo-500/15 ring-1 ring-indigo-400/30":"hover:bg-slate-800/60"}`}>
                <span className={`grid h-6 w-6 place-items-center rounded-full text-xs font-bold ${spec?.approval_status==="approved"?"bg-emerald-500/15 text-emerald-300":active?"bg-indigo-500 text-white":"bg-slate-800 text-slate-400"}`}>{spec?.approval_status==="approved"?<Check className="h-3.5 w-3.5"/>:i+1}</span>
                <span><span className="block text-sm font-semibold">{LABELS[s]}</span><span className="block text-[11px] text-slate-500">{spec?`r${spec.revision_number} · ${spec.approval_status}`:"not started"}</span></span>
                {i<3&&<ChevronRight className="ml-auto h-4 w-4 text-slate-700"/>}
              </button>
            })}
          </div>

          {!session?<EmptyState onCreate={newProject}/>:<>
            <StageDecisionPanel stage={stage} session={session} onChanged={()=>loadSession(session.id)}/>
            <div className="grid grid-cols-[minmax(0,1fr)_340px] gap-4">
            <div className="space-y-4">
              <section className="panel overflow-hidden">
                <div className="flex items-center gap-3 border-b border-slate-800 px-4 py-3">
                  <div><div className="font-semibold">{LABELS[stage]} draft</div><div className="text-xs text-slate-500">Edits auto-save to local SQLite. Explicit Save remains available.</div></div>
                  <div className="ml-auto flex gap-2">
                    <button className="btn flex items-center gap-1.5" onClick={()=>void copyDraft()} disabled={!draft}><Clipboard className="h-4 w-4"/>Copy</button>
                    <button className="btn flex items-center gap-1.5" onClick={()=>void saveDraft()} disabled={!draft}><Save className="h-4 w-4"/>Save</button>
                    <button className="btn btn-primary flex items-center gap-1.5" onClick={()=>void generate()} disabled={!!busy}><Sparkles className="h-4 w-4"/>{current?"Regenerate":"Generate"}</button>
                  </div>
                </div>
                {busy&&streamText&&(busy.startsWith("Generating")||busy.startsWith("Evaluating")||busy.startsWith("Reconnecting")||busy.startsWith("Finalizing"))?<div>
                  <div className={`border-b px-4 py-2 text-xs ${streamProvisional?"border-amber-500/20 bg-amber-500/10 text-amber-200":"border-emerald-500/20 bg-emerald-500/10 text-emerald-200"}`}>
                    {streamProvisional?`Provisional candidate${streamAttempt?` ${streamAttempt}`:""} — DSpec is still evaluating and refining this text. It is not saved or approved yet.`:"Final Refine-selected draft — saving authoritative result."}
                  </div>
                  <pre className="max-h-[580px] min-h-[480px] overflow-auto whitespace-pre-wrap p-5 font-mono text-sm leading-6 text-slate-200">{streamText}</pre>
                </div>:
                <Editor height="620px" language="markdown" theme="vs-dark" value={draft} onChange={draftChanged} options={{minimap:{enabled:false},wordWrap:"on",fontSize:14,lineHeight:22,padding:{top:18},scrollBeyondLastLine:false,automaticLayout:true}}/>}
              </section>
            </div>

            <div className="space-y-4">
              <AssistantCard stage={stage} session={session} onSave={saveAssistant}/>
              <ReviewBoard review={review} status={current?.approval_status} dirty={draftDirty} busy={busy} onReview={reviewNow} onApply={applyReviewInstruction} onApprove={approve}/>
              <section className="panel p-4">
                <div className="mb-3 flex items-center gap-2 font-semibold"><Archive className="h-4 w-4 text-cyan-300"/>Handoff</div>
                <p className="mb-3 text-xs leading-5 text-slate-500">Approved export includes all four tiers plus Claude, Cursor, Codex and ChatGPT agent instructions. Draft exports are explicitly labeled.</p>
                <button className="btn mb-2 w-full" onClick={()=>void copyAll()}><Clipboard className="mr-1 inline h-3.5 w-3.5"/>Copy all tiers</button>
                <a className="btn block text-center" href={session?`/api/export/${session.id}`:"#"}>Export approved bundle</a>
                <a className="mt-2 block text-center text-xs text-slate-500 hover:text-slate-300" href={session?`/api/export/${session.id}?allow_draft=true`:"#"}>Export labeled draft</a>
              </section>
              <section className="panel p-4 text-xs text-slate-500">
                <div className="mb-2 font-semibold text-slate-300">Engine state</div>
                <div className="flex justify-between py-1"><span>DSPy</span><span>{health?.dspy?.available?"available":"not installed"}</span></div>
                <div className="flex justify-between py-1"><span>Optimization</span><span>{health?.dspy?.optimization??"unknown"}</span></div>
                <div className="mt-2 rounded-lg bg-amber-500/8 p-2 text-amber-200/75">MIPROv2 remains BLOCKED until reviewed training data and authorized model execution exist.</div>
              </section>
            </div>
          </div></>}
        </>}
      </section>
    </div>

    {providerOpen&&<ProviderModal health={health} recovery={recovery} onClose={()=>setProviderOpen(false)} onChanged={async()=>{await refreshHealth();setRecovery(null);setProviderOpen(false)}}/>}
    {busy&&<div className="fixed bottom-5 right-5 z-50 flex items-center gap-2 rounded-xl border border-slate-700 bg-slate-900 px-4 py-3 text-sm shadow-2xl"><LoaderCircle className="h-4 w-4 animate-spin text-cyan-300"/>{busy}</div>}
  </main>
}

function StageDecisionPanel({stage,session,onChanged}:{stage:Stage;session:Session;onChanged:()=>Promise<void>}){
  if(stage==="solution")return <ArchitectureDecisionPanel session={session} onChanged={onChanged}/>;
  if(stage==="tasks")return <ExecutionStrategyPanel session={session} onChanged={onChanged}/>;
  return null;
}

function ArchitectureDecisionPanel({session,onChanged}:{session:Session;onChanged:()=>Promise<void>}){
  const saved=session.engineering_decisions?.find(item=>item.decision_type==="architecture"&&item.decision_id==="primary-stack");
  const [comparison,setComparison]=useState<ArchitectureOptionsResult|null>(saved?.options??null);
  const [customizing,setCustomizing]=useState(false);
  const [customization,setCustomization]=useState(String(saved?.custom?.instruction??saved?.options?.customization??""));
  const [working,setWorking]=useState(false);
  const [err,setErr]=useState("");

  useEffect(()=>{
    setComparison(saved?.options??null);
    setCustomization(String(saved?.custom?.instruction??saved?.options?.customization??""));
    setCustomizing(false);
    setErr("");
  },[session.id,saved?.updated_at]);

  if(!session.specs.requirements){
    return <section className="panel mb-4 p-4">
      <div className="font-semibold">Choose the implementation approach</div>
      <p className="mt-1 text-sm leading-6 text-slate-500">Finish and save the Requirements first. DSpec will use those requirements to compare complete technology approaches instead of asking you to pick languages or databases without context.</p>
    </section>;
  }

  async function generateOptions(instruction=""){
    setWorking(true);setErr("");
    try{
      const result=await api<ArchitectureOptionsResult>("/api/decisions/architecture/options",{
        method:"POST",
        body:JSON.stringify({session_id:session.id,customization:instruction.trim()||null}),
      });
      setComparison(result);
      setCustomization(result.customization??instruction);
      setCustomizing(false);
    }catch(e){setErr(String(e));}
    finally{setWorking(false);}
  }

  async function selectOption(optionId:string){
    if(!comparison)return;
    setWorking(true);setErr("");
    try{
      await api("/api/decisions/architecture/select",{
        method:"POST",
        body:JSON.stringify({
          session_id:session.id,
          selected_option_id:optionId,
          options:comparison,
          source_context_sha256:comparison.source_context_sha256,
          custom:{instruction:comparison.customization??customization.trim()},
        }),
      });
      await onChanged();
    }catch(e){setErr(String(e));}
    finally{setWorking(false);}
  }

  const selectedId=saved?.selected_option_id;
  return <section className="panel mb-4 overflow-hidden">
    <div className="flex items-start gap-4 border-b border-slate-800 p-4">
      <div className="min-w-0 flex-1">
        <div className="font-semibold">Choose the implementation approach</div>
        <p className="mt-1 max-w-4xl text-sm leading-6 text-slate-500">DSpec translates the requirements into three coherent engineering options. The consequences come first; exact languages, databases, frameworks, and infrastructure stay available under Technical details.</p>
      </div>
      <button className="btn btn-primary shrink-0" disabled={working} onClick={()=>void generateOptions(customization)}>
        {working?<LoaderCircle className="mr-1 inline h-4 w-4 animate-spin"/>:<Sparkles className="mr-1 inline h-4 w-4"/>}
        {comparison?"Refresh comparison":"Compare options"}
      </button>
    </div>
    {err&&<div className="mx-4 mt-4 rounded-lg border border-rose-500/25 bg-rose-500/8 p-3 text-xs text-rose-200">{err}</div>}
    {!comparison?<div className="p-5 text-sm text-slate-500">The recommendation will be based on the saved Constitution, Requirements, operating constraints, and any architecture preferences you have already provided.</div>:<>
      <div className="border-b border-slate-800 px-4 py-3">
        <div className="text-sm text-slate-300">{comparison.decision_summary}</div>
        {!!comparison.customization&&<div className="mt-2 rounded-lg bg-cyan-500/8 px-3 py-2 text-xs text-cyan-200">Re-evaluated with your constraint: {comparison.customization}</div>}
      </div>
      <div className="grid grid-cols-1 gap-3 p-4 xl:grid-cols-3">
        {comparison.options.map(option=>{
          const recommended=option.id===comparison.recommended_option_id;
          const selected=option.id===selectedId;
          return <article key={option.id} className={`flex min-w-0 flex-col rounded-xl border p-4 ${recommended?"border-indigo-400/40 bg-indigo-500/7":"border-slate-800 bg-slate-950/25"}`}>
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-base font-semibold text-slate-100">{option.title}</div>
                <div className="mt-1 text-[11px] uppercase tracking-wider text-slate-600">{option.role==="best_fit"?"Best overall fit":option.role==="simplest"?"Lowest complexity":comparison.alternative_objective}</div>
              </div>
              {recommended&&<span className="rounded-full bg-cyan-500/10 px-2 py-1 text-[10px] font-semibold text-cyan-300">Recommended</span>}
            </div>

            <div className="mt-4">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">What this means for you</div>
              <p className="mt-1 text-sm leading-6 text-slate-300">{option.plain_english_summary}</p>
            </div>

            <div className="mt-4">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{recommended?"Why DSpec recommends it":"Why you might choose it"}</div>
              <p className="mt-1 text-xs leading-5 text-slate-400">{option.why_recommended}</p>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-3">
              <div><div className="text-[11px] font-semibold text-emerald-300/80">Advantages</div><div className="mt-1 space-y-1">{option.advantages.slice(0,4).map(item=><div key={item} className="text-xs leading-5 text-slate-400">• {item}</div>)}</div></div>
              <div><div className="text-[11px] font-semibold text-amber-300/80">Tradeoffs</div><div className="mt-1 space-y-1">{option.tradeoffs.slice(0,4).map(item=><div key={item} className="text-xs leading-5 text-slate-400">• {item}</div>)}</div></div>
            </div>

            <div className="mt-4 rounded-lg bg-slate-900/70 p-3">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Operational impact</div>
              <p className="mt-1 text-xs leading-5 text-slate-400">{option.operational_impact}</p>
            </div>

            <div className="mt-3 rounded-lg border border-cyan-500/15 bg-cyan-500/5 p-3">
              <div className="text-[11px] font-semibold text-cyan-300">Why engineers care</div>
              <p className="mt-1 text-xs leading-5 text-slate-400">{option.why_engineers_care}</p>
              <div className="mt-2 text-[11px] text-slate-500"><span className="font-semibold text-slate-400">{option.engineering_concept.label}:</span> {option.engineering_concept.mental_model}</div>
            </div>

            {!!option.reconsider_when.length&&<div className="mt-3">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">What would change this recommendation</div>
              <div className="mt-1 space-y-1">{option.reconsider_when.slice(0,3).map(item=><div key={item} className="text-[11px] leading-5 text-slate-500">• {item}</div>)}</div>
            </div>}

            <details className="mt-4 rounded-lg border border-slate-800 bg-slate-950/40">
              <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-300">Technical details</summary>
              <div className="border-t border-slate-800 p-3">
                {option.technical_details.map((item,index)=><div key={`${index}-${item.category}`} className="mb-2 last:mb-0">
                  <div className="flex items-baseline justify-between gap-3 text-xs"><span className="text-slate-500">{item.category}</span><span className="text-right font-medium text-slate-200">{item.choice}</span></div>
                  <div className="mt-0.5 text-[11px] leading-4 text-slate-600">{item.consequence}</div>
                </div>)}
              </div>
            </details>

            <button className={`btn mt-4 w-full ${recommended?"btn-primary":""}`} disabled={working||selected} onClick={()=>void selectOption(option.id)}>
              {selected?"Selected":"Choose this approach"}
            </button>
          </article>;
        })}
      </div>
      <div className="border-t border-slate-800 p-4">
        {!customizing?<button className="btn" onClick={()=>setCustomizing(true)}>Customize technology choices</button>:<div className="grid gap-2 md:grid-cols-[1fr_auto]">
          <textarea className="input min-h-20 resize-y text-xs" value={customization} onChange={e=>setCustomization(e.target.value)} placeholder="Describe the change in plain English, for example: Use SQL Server instead of PostgreSQL because our company already operates SQL Server."/>
          <div className="flex gap-2 md:flex-col"><button className="btn btn-primary" disabled={working||!customization.trim()} onClick={()=>void generateOptions(customization)}>Re-evaluate options</button><button className="btn" onClick={()=>setCustomizing(false)}>Cancel</button></div>
        </div>}
        <p className="mt-2 text-[11px] leading-5 text-slate-600">Customization is treated as an engineering constraint. DSpec regenerates the complete approaches so compatibility and tradeoffs are reconsidered rather than silently swapping one component.</p>
      </div>
    </>}
  </section>;
}

function ExecutionStrategyPanel({session,onChanged}:{session:Session;onChanged:()=>Promise<void>}){
  const existing=session.execution_plan??null;
  const [bundle,setBundle]=useState<ExecutionEstimateBundle|null>(existing?.plans?existing:null);
  const initialBudget=existing?.budget;
  const initialChoice=initialBudget===10?"10":initialBudget===25?"25":initialBudget===50?"50":typeof initialBudget==="number"?"custom":"none";
  const [budgetChoice,setBudgetChoice]=useState(initialChoice);
  const [customBudget,setCustomBudget]=useState(typeof initialBudget==="number"&&![10,25,50].includes(initialBudget)?String(initialBudget):"");
  const [escalation,setEscalation]=useState<"automatic"|"ask_first">(existing?.escalation??"automatic");
  const [budgetBehavior,setBudgetBehavior]=useState<"stop_before_exceeding"|"ask_before_overage"|"no_enforcement">(existing?.budget_behavior??"stop_before_exceeding");
  const [settingsDirty,setSettingsDirty]=useState(false);
  const [working,setWorking]=useState(false);
  const [err,setErr]=useState("");

  useEffect(()=>{
    const next=session.execution_plan;
    if(next?.plans)setBundle(next);
  },[session.id,session.execution_plan?.source_snapshot_sha256,session.execution_plan?.status]);

  if(!session.specs.tasks){
    return <section className="panel mb-4 p-4">
      <div className="font-semibold">Plan AI implementation cost</div>
      <p className="mt-1 text-sm leading-6 text-slate-500">After the Tasks are saved, DSpec can classify the work and compare cost-optimized, balanced, and maximum-capability model strategies at the project level.</p>
    </section>;
  }

  const effectiveBudget=budgetChoice==="none"?null:budgetChoice==="custom"?(customBudget.trim()?Number(customBudget):null):Number(budgetChoice);
  const validBudget=effectiveBudget===null||(Number.isFinite(effectiveBudget)&&effectiveBudget>=0);
  const strategies:StrategyName[]=["cost_optimized","balanced","maximum_capability"];
  const descriptions:Record<StrategyName,{summary:string;tradeoff:string}>={
    cost_optimized:{summary:"Uses local or lower-cost models wherever they meet the task's minimum safe capability, and reserves stronger cloud models for work that actually needs them.",tradeoff:"Lowest expected API spend, with escalation when cheaper models are not sufficient."},
    balanced:{summary:"Applies stronger models earlier to moderately difficult or ambiguous work while still using local models where the fit is clear.",tradeoff:"Higher API cost in exchange for more reasoning headroom and potentially fewer retries."},
    maximum_capability:{summary:"Uses the strongest configured eligible model for most substantive work.",tradeoff:"Highest reasoning capability, but expensive models may perform work that cheaper models could safely handle."},
  };

  function markDirty(){setSettingsDirty(true);}

  async function estimate(){
    if(!validBudget)return;
    setWorking(true);setErr("");
    try{
      const result=await api<ExecutionEstimateBundle>("/api/execution/estimate",{
        method:"POST",
        body:JSON.stringify({
          session_id:session.id,
          budget:effectiveBudget,
          escalation,
          budget_behavior:budgetBehavior,
        }),
      });
      setBundle(result);setSettingsDirty(false);
      await onChanged();
    }catch(e){setErr(String(e));}
    finally{setWorking(false);}
  }

  async function choose(strategy:StrategyName){
    if(settingsDirty)return;
    setWorking(true);setErr("");
    try{
      const result=await api<ExecutionEstimateBundle>("/api/execution/select",{
        method:"POST",
        body:JSON.stringify({session_id:session.id,strategy}),
      });
      setBundle(result);
      await onChanged();
    }catch(e){setErr(String(e));}
    finally{setWorking(false);}
  }

  function money(value:number|null|undefined){return value===null||value===undefined?"UNKNOWN":`${value.toFixed(2)}`;}

  return <section className="panel mb-4 overflow-hidden">
    <div className="border-b border-slate-800 p-4">
      <div className="font-semibold">Implementation strategy & cloud budget</div>
      <p className="mt-1 max-w-5xl text-sm leading-6 text-slate-500">DSpec estimates the project-level model mix and cloud API cost before you choose how implementation work should be routed. Cost Optimized is the default recommendation.</p>
    </div>

    <div className="grid gap-4 border-b border-slate-800 p-4 lg:grid-cols-3">
      <div>
        <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Cloud budget</div>
        <select className="input" value={budgetChoice} onChange={e=>{setBudgetChoice(e.target.value);markDirty();}}>
          <option value="none">No limit</option><option value="10">$10</option><option value="25">$25</option><option value="50">$50</option><option value="custom">Custom</option>
        </select>
        {budgetChoice==="custom"&&<input className="input mt-2" type="number" min="0" step="1" value={customBudget} onChange={e=>{setCustomBudget(e.target.value);markDirty();}} placeholder="Maximum cloud API spend"/>}
        {!validBudget&&<div className="mt-1 text-[11px] text-rose-300">Enter a valid non-negative budget.</div>}
      </div>
      <div>
        <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">If a cheaper model is not sufficient</div>
        <label className="mb-1.5 flex cursor-pointer gap-2 rounded-lg border border-slate-800 p-2 text-xs text-slate-400"><input type="radio" checked={escalation==="automatic"} onChange={()=>{setEscalation("automatic");markDirty();}}/><span><span className="text-slate-200">Automatically escalate — Recommended</span><span className="mt-0.5 block text-[11px] text-slate-600">Move the task to the next safe capability tier.</span></span></label>
        <label className="flex cursor-pointer gap-2 rounded-lg border border-slate-800 p-2 text-xs text-slate-400"><input type="radio" checked={escalation==="ask_first"} onChange={()=>{setEscalation("ask_first");markDirty();}}/><span><span className="text-slate-200">Ask me first</span><span className="mt-0.5 block text-[11px] text-slate-600">Stop before switching to a more expensive model.</span></span></label>
      </div>
      <div>
        <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Budget behavior</div>
        <select className="input" value={budgetBehavior} onChange={e=>{setBudgetBehavior(e.target.value as typeof budgetBehavior);markDirty();}}>
          <option value="stop_before_exceeding">Stop before exceeding — Recommended</option>
          <option value="ask_before_overage">Ask before any overage</option>
          <option value="no_enforcement">No budget enforcement</option>
        </select>
        <div className="mt-2 rounded-lg border border-amber-500/15 bg-amber-500/5 p-2 text-[11px] leading-5 text-amber-100/75">Budget controls may pause paid work, but they never route a task below its minimum safe model capability.</div>
      </div>
    </div>

    <div className="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
      <div className="text-xs text-slate-500">{bundle&&!settingsDirty?"Estimate reflects the controls above.":bundle?"Controls changed — recalculate before choosing a strategy.":"Generate one project-level estimate for all three strategies."}</div>
      <button className="btn btn-primary shrink-0" disabled={working||!validBudget} onClick={()=>void estimate()}>{working?<LoaderCircle className="mr-1 inline h-4 w-4 animate-spin"/>:<Sparkles className="mr-1 inline h-4 w-4"/>}{bundle?"Recalculate":"Estimate implementation"}</button>
    </div>

    {err&&<div className="mx-4 mt-4 rounded-lg border border-rose-500/25 bg-rose-500/8 p-3 text-xs text-rose-200">{err}</div>}
    {bundle&&<div className="grid grid-cols-1 gap-3 p-4 xl:grid-cols-3">
      {strategies.map(strategy=>{
        const plan=bundle.plans[strategy];
        if(!plan)return null;
        const recommended=strategy===bundle.recommended_strategy;
        const selected=strategy===bundle.selected_strategy;
        const cost=plan.cloud_api_cost_estimate;
        return <article key={strategy} className={`flex flex-col rounded-xl border p-4 ${recommended?"border-indigo-400/40 bg-indigo-500/7":"border-slate-800 bg-slate-950/25"}`}>
          <div className="flex items-start justify-between gap-2">
            <div className="text-base font-semibold text-slate-100">{plan.strategy_label}</div>
            {recommended&&<span className="rounded-full bg-cyan-500/10 px-2 py-1 text-[10px] font-semibold text-cyan-300">Recommended</span>}
          </div>
          <div className="mt-4">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">What this means for you</div>
            <p className="mt-1 text-sm leading-6 text-slate-300">{descriptions[strategy].summary}</p>
          </div>
          <div className="mt-3 text-xs leading-5 text-slate-500">{descriptions[strategy].tradeoff}</div>

          <div className="mt-4 rounded-lg bg-slate-900/70 p-3">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Estimated model mix</div>
            <div className="mt-2 grid grid-cols-3 gap-2 text-center">
              <div><div className="text-lg font-semibold text-slate-200">{plan.model_mix.local.percent}%</div><div className="text-[10px] text-slate-600">Local</div></div>
              <div><div className="text-lg font-semibold text-slate-200">{plan.model_mix.standard.percent}%</div><div className="text-[10px] text-slate-600">Standard</div></div>
              <div><div className="text-lg font-semibold text-slate-200">{plan.model_mix.advanced.percent}%</div><div className="text-[10px] text-slate-600">Advanced</div></div>
            </div>
          </div>

          <div className="mt-3 rounded-lg border border-slate-800 p-3">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Estimated cloud API cost</div>
            <div className="mt-1 text-2xl font-semibold text-slate-100">{money(cost.expected)}</div>
            <div className="mt-1 text-[11px] text-slate-600">Range {money(cost.low)} – {money(cost.high)}</div>
            {plan.budget_status!=="NO_LIMIT"&&plan.budget_status!=="WITHIN_EXPECTED"&&<div className="mt-2 text-[11px] leading-4 text-amber-300">Budget status: {plan.budget_status.replaceAll("_"," ").toLowerCase()}</div>}
            {!!plan.blocked_task_count&&<div className="mt-2 text-[11px] leading-4 text-rose-300">{plan.blocked_task_count} task(s) have no configured model meeting the minimum safe capability.</div>}
          </div>

          <details className="mt-3 rounded-lg border border-slate-800">
            <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-300">How this estimate works</summary>
            <div className="border-t border-slate-800 p-3">{plan.estimate_assumptions.map(item=><div key={item} className="mb-1 text-[11px] leading-5 text-slate-500">• {item}</div>)}</div>
          </details>

          <button className={`btn mt-4 w-full ${recommended?"btn-primary":""}`} disabled={working||settingsDirty||selected} onClick={()=>void choose(strategy)}>{selected?"Selected":"Use this strategy"}</button>
        </article>;
      })}
    </div>}
  </section>;
}

function EmptyState({onCreate}:{onCreate:()=>void}){
  return <div className="panel grid min-h-[540px] place-items-center p-10 text-center"><div><ShieldCheck className="mx-auto mb-4 h-10 w-10 text-indigo-300"/><h2 className="text-xl font-semibold">Create a governed DSpec project</h2><p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-slate-500">Move from product intent through Constitution, Requirements, Solution and executable Tasks while preserving explicit review and approval state.</p><button className="btn btn-primary mt-5" onClick={onCreate}>Create project</button></div></div>
}

function AssistantCard({stage,session,onSave}:{stage:Stage;session:Session;onSave:(questionId:string,choice:string,text:string)=>Promise<void>}){
  const saved=session.answers.find(a=>a.stage===stage&&a.question_id===`assistant-${stage}`);
  const [choice,setChoice]=useState(saved?.selected_option_id??ASSIST[stage].choices[2]);
  const [text,setText]=useState(saved?.free_text_payload??"");
  const [discovery,setDiscovery]=useState<DiscoveryResult|null>(null);
  const [loading,setLoading]=useState(false);
  const [err,setErr]=useState("");
  useEffect(()=>{
    setChoice(saved?.selected_option_id??ASSIST[stage].choices[2]);
    setText(saved?.free_text_payload??"");
    setDiscovery(null);
    setErr("");
  },[stage,session.id,saved?.updated_at]);

  async function analyze(){
    if(stage==="constitution"&&!text.trim()){
      setErr("Describe what you want to build before analyzing gaps.");
      return;
    }
    setLoading(true);setErr("");
    try{
      await onSave(`assistant-${stage}`,choice,text);
      const result=await api<DiscoveryResult>("/api/assist/questions",{method:"POST",body:JSON.stringify({session_id:session.id,stage})});
      setDiscovery(result);
    }catch(e){setErr(String(e));}
    finally{setLoading(false);}
  }

  return <section className="panel p-4">
    <div className="mb-1 flex items-center gap-2 font-semibold"><Sparkles className="h-4 w-4 text-indigo-300"/>Field assistant</div>
    <div className="mb-3 text-xs text-slate-500">{ASSIST[stage].title}</div>
    <p className="mb-3 text-sm leading-5 text-slate-300">{ASSIST[stage].prompt}</p>
    {stage==="constitution"?<>
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-slate-500">Project brief</div>
      <textarea className="input min-h-28 resize-y text-xs" value={text} onChange={e=>setText(e.target.value)} placeholder="Describe what you want to build, who it is for, and the outcome you want…"/>
      <div className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-widest text-slate-500">Operating boundary</div>
      <div className="space-y-1.5">{ASSIST[stage].choices.map(c=><label key={c} className="flex cursor-pointer items-start gap-2 rounded-lg border border-slate-800 p-2 text-xs text-slate-400 hover:border-slate-700"><input type="radio" checked={choice===c} onChange={()=>setChoice(c)} className="mt-0.5"/><span>{c}</span></label>)}</div>
    </>:<>
      <div className="space-y-1.5">{ASSIST[stage].choices.map(c=><label key={c} className="flex cursor-pointer items-start gap-2 rounded-lg border border-slate-800 p-2 text-xs text-slate-400 hover:border-slate-700"><input type="radio" checked={choice===c} onChange={()=>setChoice(c)} className="mt-0.5"/><span>{c}</span></label>)}</div>
      <textarea className="input mt-3 min-h-24 resize-y text-xs" value={text} onChange={e=>setText(e.target.value)} placeholder="Add product-level context or constraints…"/>
    </>}
    <div className="mt-2 grid grid-cols-2 gap-2">
      <button className="btn" onClick={()=>void onSave(`assistant-${stage}`,choice,text)}>{stage==="constitution"?"Save project brief":"Save input"}</button>
      <button className="btn btn-primary flex items-center justify-center gap-1.5" disabled={loading} onClick={()=>void analyze()}>{loading?<LoaderCircle className="h-4 w-4 animate-spin"/>:<Sparkles className="h-4 w-4"/>}Analyze gaps</button>
    </div>
    {err&&<div className="mt-3 text-xs text-rose-300">{err}</div>}
    {discovery&&<div className="mt-4 border-t border-slate-800 pt-4">
      {!!discovery.gaps_found?.length&&<div className="mb-3"><div className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-slate-500">Gaps detected</div>{discovery.gaps_found.slice(0,4).map(g=><div key={g} className="mt-1 text-xs leading-5 text-slate-400">• {g}</div>)}</div>}
      <div className="space-y-3">{discovery.questions.slice(0,3).map(q=><DynamicQuestion key={q.id} question={q} saved={session.answers.find(a=>a.stage===stage&&a.question_id===q.id)} onSave={onSave}/>)}</div>
    </div>}
  </section>
}

function DynamicQuestion({question,saved,onSave}:{question:DiscoveryQuestion;saved?:Session["answers"][number];onSave:(questionId:string,choice:string,text:string)=>Promise<void>}){
  const [choice,setChoice]=useState(saved?.selected_option_id??question.recommended_option_id);
  const [text,setText]=useState(saved?.free_text_payload??"");
  const [savedState,setSavedState]=useState(false);
  return <div className="rounded-xl border border-indigo-400/15 bg-indigo-500/5 p-3">
    <div className="text-sm font-semibold text-slate-200">{question.question}</div>
    <div className="mt-1 text-[11px] leading-4 text-slate-500">{question.why_it_matters}</div>
    <div className="mt-2 space-y-1.5">{question.options.map(o=><label key={o.id} className="block cursor-pointer rounded-lg border border-slate-800 p-2 text-xs hover:border-slate-700"><span className="flex items-start gap-2"><input className="mt-0.5" type="radio" checked={choice===o.id} onChange={()=>{setChoice(o.id);setSavedState(false)}}/><span><span className="text-slate-300">{o.label}{o.id===question.recommended_option_id&&<span className="ml-1 text-cyan-300">Recommended</span>}</span><span className="mt-0.5 block text-[11px] leading-4 text-slate-600">{o.plain_english_consequence??o.rationale}</span>{o.plain_english_consequence&&<span className="mt-1 block text-[11px] leading-4 text-slate-500">{o.rationale}</span>}{(o.engineering_concept||o.technical_details?.length)?<details className="mt-2 rounded border border-slate-800 bg-slate-950/40 p-2"><summary className="cursor-pointer text-[11px] font-medium text-slate-400">Engineering principle & technical details</summary>{o.engineering_concept&&<span className="mt-2 block text-[11px] leading-4 text-cyan-200/80"><b>{o.engineering_concept.label}:</b> {o.engineering_concept.mental_model}</span>}{o.technical_details?.map((detail,index)=><span key={`${index}-${detail.category}`} className="mt-2 block text-[11px] leading-4 text-slate-500"><b className="text-slate-400">{detail.category} — {detail.choice}:</b> {detail.consequence}</span>)}</details>:null}</span></span></label>)}</div>
    {question.allow_free_text&&<textarea className="input mt-2 min-h-16 text-xs" value={text} onChange={e=>{setText(e.target.value);setSavedState(false)}} placeholder="Optional context or alternative…"/>}
    <button className="btn mt-2 w-full" onClick={async()=>{await onSave(question.id,choice??"",text);setSavedState(true)}}>{savedState?"Saved":"Save answer"}</button>
  </div>
}

function ReviewBoard({review,status,dirty,busy,onReview,onApply,onApprove}:{review:Review;status?:string;dirty:boolean;busy:string|null;onReview:()=>Promise<void>;onApply:(instruction:string)=>Promise<void>;onApprove:()=>Promise<void>}){
  const score=review.score??0;
  const fixes=review.must_fix??[];
  const passing=review.passing??[];
  const recommendations=review.recommendations??[];
  return <section className="panel p-4">
    <div className="mb-3 flex items-center justify-between"><div className="font-semibold">Review board</div><span className={`text-sm font-bold ${scoreTone(score)}`}>{Math.round(score*100)}%</span></div>
    {review.semantic_status&&<div className={`mb-3 rounded-lg px-2 py-1.5 text-xs ${review.semantic_status==="PASS"?"bg-emerald-500/10 text-emerald-300":review.semantic_status==="NOT TESTED"?"bg-amber-500/10 text-amber-200":"bg-rose-500/10 text-rose-200"}`}>Semantic review: {review.semantic_status}{review.semantic_error?` — ${review.semantic_error}`:""}</div>}
    <div className="mb-3 h-1.5 overflow-hidden rounded bg-slate-800"><div className={`h-full ${score>=.9?"bg-emerald-400":score>=.7?"bg-amber-400":"bg-rose-400"}`} style={{width:`${Math.round(score*100)}%`}}/></div>
    {status==="approved"&&!dirty&&<div className="mb-3 flex items-center gap-2 rounded-lg bg-emerald-500/10 p-2 text-xs text-emerald-300"><Check className="h-4 w-4"/>Approved revision</div>}
    {dirty&&<div className="mb-3 rounded-lg bg-amber-500/10 p-2 text-xs text-amber-200">Draft buffer has changes newer than the formal revision. Review will save a new revision first.</div>}

    {!!passing.length&&<div className="mb-3">
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-emerald-300/80">Passing assertions</div>
      <div className="space-y-1.5">{passing.slice(0,4).map(x=><div key={x.id} className="flex gap-2 rounded-lg border border-emerald-500/15 bg-emerald-500/5 p-2"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-300"/><div><div className="text-xs font-medium text-emerald-200">{x.label}</div><div className="mt-0.5 text-[11px] leading-4 text-slate-500">{x.detail}</div></div></div>)}</div>
    </div>}

    {!!fixes.length?<div>
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-rose-300/80">Must fix</div>
      <div className="space-y-2">{fixes.slice(0,4).map(x=>{
        const instruction=x.recommendation??x.detail;
        return <div key={x.id} className="rounded-lg border border-rose-500/20 bg-rose-500/5 p-2"><div className="text-xs font-semibold text-rose-200">{x.label}</div><div className="mt-1 text-[11px] leading-4 text-slate-500">{instruction}</div><button className="btn mt-2 w-full !py-1.5 text-xs" disabled={!!busy||!instruction} onClick={()=>void onApply(instruction)}>Apply fix</button></div>;
      })}</div>
    </div>:<div className="mb-3 text-xs text-slate-500">{review.score!==undefined?"No deterministic must-fix items. Approval still represents explicit user/reviewer promotion.":"Run review after creating a draft."}</div>}

    {!!recommendations.length&&<div className="mt-3">
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-cyan-300/80">Actionable recommendations</div>
      <div className="space-y-2">{recommendations.slice(0,4).map((instruction,index)=><div key={`${index}-${instruction}`} className="rounded-lg border border-cyan-500/15 bg-cyan-500/5 p-2"><div className="text-[11px] leading-4 text-slate-400">{instruction}</div><button className="btn mt-2 w-full !py-1.5 text-xs" disabled={!!busy} onClick={()=>void onApply(instruction)}>Apply recommendation</button></div>)}</div>
    </div>}

    <div className="mt-3 grid grid-cols-2 gap-2"><button className="btn" disabled={!!busy} onClick={()=>void onReview()}>Review</button><button className="btn btn-primary" disabled={!!busy||dirty||score<.9||review.passed!==true} onClick={()=>void onApprove()}>Approve</button></div>
  </section>
}

function ProviderModal({health,recovery,onClose,onChanged}:{health:Health|null;recovery:RecoveryPrompt|null;onClose:()=>void;onChanged:()=>Promise<void>}){
  const [provider,setProvider]=useState(health?.active_provider.provider??"lm_studio");
  const [model,setModel]=useState(health?.active_provider.model??"");
  const [key,setKey]=useState(""); const [err,setErr]=useState("");
  const localProvider=provider==="lm_studio"||provider==="ollama";
  const localService=localProvider?health?.detected_local_services?.[provider]:undefined;
  const localModels=localService?.models??[];
  useEffect(()=>{ if(localProvider&&localModels.length&&!localModels.includes(model))setModel(localModels[0]); },[provider,health,model,localProvider,localModels.join("|")]);
  async function save(){
    setErr("");
    try{await api("/api/provider/select",{method:"POST",body:JSON.stringify({provider,model,api_key:key||null})});await onChanged();}
    catch(e){setErr(String(e));}
    finally{setKey("");}
  }
  return <div className="fixed inset-0 z-50 grid place-items-center bg-black/75 p-4" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}>
    <div className="panel w-full max-w-lg p-5 shadow-2xl">
      <div className="mb-4"><div className="text-lg font-semibold">LLM provider</div><div className="text-xs text-slate-500">Changes apply to subsequent generation calls without restarting DSpec.</div></div>
      {recovery&&<div className="mb-4 rounded-lg border border-amber-500/25 bg-amber-500/8 p-3 text-xs text-amber-100"><div className="font-semibold">Generation interrupted</div><div className="mt-1 leading-5 text-amber-100/75">{recovery.message}</div><div className="mt-1 leading-5">{recovery.statePreserved?"Saved project state is preserved. ":""}Choose a healthy local provider or configure/select a cloud fallback.</div>{!!recovery.fallbackOptions.length&&<div className="mt-2 flex flex-wrap gap-1.5">{recovery.fallbackOptions.map(item=><span key={item} className="rounded bg-slate-950/50 px-2 py-1">{item==="lm_studio"?"LM Studio":item==="ollama"?"Ollama":item==="openai"?"OpenAI":"Anthropic"} · {(item==="lm_studio"||item==="ollama")?(health?.detected_local_services?.[item]?.online?"ready":"offline"):(health?.cloud_provider_readiness?.[item]?"configured":"needs key")}</span>)}</div>}</div>}
      <label className="mb-3 block text-xs text-slate-400">Provider<select className="input mt-1" value={provider} onChange={e=>setProvider(e.target.value)}><option value="lm_studio">LM Studio</option><option value="ollama">Ollama</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option></select></label>
      <label className="mb-1 block text-xs text-slate-400">Model{(localProvider&&localModels.length>0)?<select className="input mt-1" value={model} onChange={e=>setModel(e.target.value)}>{localModels.map(m=><option key={m} value={m}>{m}</option>)}</select>:<input className="input mt-1" value={model} onChange={e=>setModel(e.target.value)} placeholder="model identifier"/>}</label>
      {localProvider&&<div className={`mb-3 text-[11px] ${localService?.online?(localModels.length?"text-emerald-300":"text-amber-200"):"text-rose-300"}`}>{!localService?.online?"Local server not detected.":localModels.length?`${localModels.length} model${localModels.length===1?"":"s"} detected automatically.`:"Local server is online, but it is not exposing a loaded model yet."}</div>}
      {(provider==="openai"||provider==="anthropic")&&<label className="block text-xs text-slate-400">API key <span className="text-slate-600">(write-only)</span><input className="input mt-1" type="password" value={key} onChange={e=>setKey(e.target.value)} autoComplete="off" placeholder="Leave blank to keep existing key"/></label>}
      {err&&<div className="mt-3 text-xs text-rose-300">{err}</div>}
      <div className="mt-5 flex justify-end gap-2"><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={()=>void save()}>Apply provider</button></div>
    </div>
  </div>
}

function AuditPanel(){
  const [path,setPath]=useState(""); const [report,setReport]=useState<any>(null); const [busy,setBusy]=useState(false); const [error,setError]=useState("");
  async function scan(){setBusy(true);setError("");try{setReport(await api("/api/audit/scan",{method:"POST",body:JSON.stringify({repo_path:path,use_hash_cache:true})}));}catch(e){setError(String(e));}finally{setBusy(false)}}
  const scores=report?.health_score;
  return <div className="space-y-4">
    <section className="panel p-5"><div className="mb-1 text-lg font-semibold">Local repository audit</div><div className="mb-4 text-sm text-slate-500">Read-only bounded static analysis. Symlinks outside the selected root and common generated/vendor paths are excluded.</div><div className="flex gap-2"><input className="input" value={path} onChange={e=>setPath(e.target.value)} placeholder="/Users/you/Workspace/project"/><button className="btn btn-primary min-w-28" disabled={!path||busy} onClick={()=>void scan()}>{busy?"Scanning…":"Scan"}</button></div>{error&&<div className="mt-3 text-sm text-rose-300">{error}</div>}</section>
    {report&&<><div className="grid grid-cols-5 gap-3">{[["Composite",scores.composite_score],["Governance",scores.governance_security_score],["Requirements",scores.requirements_clarity_score],["Architecture",scores.architecture_consistency_score],["Tests / Tasks",scores.test_task_coverage_score]].map(([label,value])=><div key={String(label)} className="panel p-4"><div className="text-xs text-slate-500">{label}</div><div className="mt-2 text-2xl font-bold">{value}%</div></div>)}</div>
      <section className="panel p-5"><div className="mb-3 font-semibold">Evidence & gaps</div><div className="mb-4 grid grid-cols-3 gap-3 text-xs text-slate-400"><div>Files scanned <b className="text-slate-200">{report.total_files_scanned}</b></div><div>Cache hits <b className="text-slate-200">{report.cached_files_skipped}</b></div><div>Duration <b className="text-slate-200">{report.scan_duration_ms} ms</b></div></div><div className="space-y-2">{report.critical_gaps.length?report.critical_gaps.map((g:string)=><div key={g} className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-sm text-amber-100/80">{g}</div>):<div className="text-sm text-emerald-300">No high-confidence structural gaps detected by this bounded scan.</div>}</div><div className="mt-5"><div className="mb-2 text-sm font-semibold text-slate-300">Upgrade specification</div><pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-xl border border-slate-800 bg-slate-950/60 p-4 text-xs leading-5 text-slate-400">{report.upgrade_spec_md}</pre></div><div className="mt-4 text-xs text-slate-600">{report.assessment_scope}</div></section></>}
  </div>
}
