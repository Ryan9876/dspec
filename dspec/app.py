from __future__ import annotations

import asyncio
import json
import mimetypes
from contextlib import asynccontextmanager
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from . import db
from .audit import scan_repository
from .config import APP_VERSION, BUILD_HASH, HOST, PORT
from .dspy_signatures import status as dspy_status
from .exporter import build_bundle
from .execution_planning import build_model_candidates, plan_execution, source_snapshot_sha256
from .provider import ProviderGateway, public_discovery
from .provider_selection import reconcile_selected
from .quality import evaluate
from .logging_utils import configure_logging
from .spec_engine import ProductIntentRequired, SpecEngine


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    db.init_db()
    yield


app = FastAPI(title="DSpec AI", version=APP_VERSION, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
gateway = ProviderGateway()
engine = SpecEngine(gateway)


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": error["loc"], "type": error["type"], "msg": "Invalid value for this field."}
        for error in exc.errors()
    ]})


@app.exception_handler(db.StateConflict)
async def state_conflict(_: Request, exc: db.StateConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": {
        "error": "project_state_changed", "message": str(exc), "state_preserved": True,
    }})

_BUFFERS: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=2000))
_SEQ: dict[str, int] = defaultdict(int)
_ACTIVE_TASKS: dict[str, asyncio.Task[None]] = {}


class SessionCreate(BaseModel):
    bundle_name: str = Field(min_length=1, max_length=120)
    project_type: Literal["greenfield", "existing"] = "greenfield"


class AnswerSave(BaseModel):
    session_id: str
    stage: Literal["constitution", "requirements", "solution", "tasks"]
    question_id: str = Field(min_length=1, max_length=160)
    selected_option_id: str | None = None
    free_text_payload: str | None = None


class SpecSave(BaseModel):
    session_id: str
    stage: Literal["constitution", "requirements", "solution", "tasks"]
    content: str = Field(min_length=1)
    expected_intent_sha256: str | None = None


class ReviewRequest(BaseModel):
    session_id: str
    stage: Literal["constitution", "requirements", "solution", "tasks"]


class RevisionApplyRequest(BaseModel):
    session_id: str
    stage: Literal["constitution", "requirements", "solution", "tasks"]
    content: str = Field(min_length=1)
    instruction: str = Field(min_length=1, max_length=4000)


class ApproveRequest(BaseModel):
    session_id: str
    stage: Literal["constitution", "requirements", "solution", "tasks"]


class ProviderSelect(BaseModel):
    provider: Literal["lm_studio", "ollama", "openai", "anthropic"]
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = None


class GenerateRequest(BaseModel):
    session_id: str
    stage: Literal["constitution", "requirements", "solution", "tasks"]
    instructions: str | None = None


class AssistRequest(BaseModel):
    session_id: str
    stage: Literal["constitution", "requirements", "solution", "tasks"]


class ArchitectureOptionsRequest(BaseModel):
    session_id: str
    customization: str | None = Field(default=None, max_length=4000)


class ArchitectureSelectRequest(BaseModel):
    session_id: str
    selected_option_id: str = Field(min_length=1, max_length=160)
    options: dict[str, Any]
    source_context_sha256: str = Field(min_length=64, max_length=64)
    custom: dict[str, Any] = Field(default_factory=dict)


class ExecutionEstimateRequest(BaseModel):
    session_id: str
    budget: float | None = Field(default=None, ge=0)
    escalation: Literal["automatic", "ask_first"] = "automatic"
    budget_behavior: Literal["stop_before_exceeding", "ask_before_overage", "no_enforcement"] = "stop_before_exceeding"


class ExecutionSelectRequest(BaseModel):
    session_id: str
    strategy: Literal["cost_optimized", "balanced", "maximum_capability"]


class AuditRequest(BaseModel):
    repo_path: str
    ignore_patterns: list[str] = []
    use_hash_cache: bool = True


def _session_or_404(session_id: str) -> dict[str, Any]:
    try:
        return db.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(404, "Session not found") from exc


def _latest_spec(session: dict[str, Any], stage: str) -> dict[str, Any]:
    spec = session.get("specs", {}).get(stage)
    if not spec:
        raise HTTPException(404, f"No {stage} draft exists")
    return spec


def _record_event(session_id: str, event: str, payload: dict[str, Any]) -> dict[str, Any]:
    _SEQ[session_id] += 1
    item = {"seq": _SEQ[session_id], "event": event, "data": payload}
    _BUFFERS[session_id].append(item)
    return item


def _sse(item: dict[str, Any]) -> str:
    data = {"seq": item["seq"], **item["data"]}
    return f"id: {item['seq']}\nevent: {item['event']}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def _events_since(session_id: str, last_seq: int) -> AsyncIterator[str]:
    current = last_seq
    last_heartbeat = asyncio.get_running_loop().time()
    while True:
        emitted = False
        for item in list(_BUFFERS[session_id]):
            if item["seq"] <= current:
                continue
            current = item["seq"]
            emitted = True
            yield _sse(item)
            if item["event"] in {"complete", "error"}:
                return
        task = _ACTIVE_TASKS.get(session_id)
        if task and task.done():
            await asyncio.sleep(0)
            terminal = [i for i in _BUFFERS[session_id] if i["seq"] > current and i["event"] in {"complete", "error"}]
            if not terminal:
                return
            continue
        now = asyncio.get_running_loop().time()
        if now - last_heartbeat >= 15.0:
            last_heartbeat = now
            yield ": ping\n\n"
        await asyncio.sleep(0.10 if emitted else 0.25)


async def _generate(req: GenerateRequest) -> tuple[str, dict[str, Any], dict[str, Any]]:
    session = _session_or_404(req.session_id)
    content, metrics, review = await engine.generate(session, req.stage, req.instructions)
    spec = db.save_spec(req.session_id, req.stage, content, review["score"], review, "draft", expected_context=db.review_context(session, req.stage))
    return content, metrics, spec


@app.get("/api/health")
async def health() -> dict[str, Any]:
    db.init_db()
    discovery = await gateway.discover(max_age_seconds=15.0)
    selected, selected_ready = reconcile_selected(gateway, discovery)
    return {
        "status": "healthy",
        "version": APP_VERSION,
        "build_hash": BUILD_HASH,
        "host": HOST,
        "port": PORT,
        "database": "connected",
        "active_provider": selected,
        "active_provider_ready": selected_ready,
        "detected_local_services": {
            "lm_studio": public_discovery(discovery)["lm_studio"],
            "ollama": public_discovery(discovery)["ollama"],
        },
        "cloud_provider_readiness": {
            "openai": discovery["openai"].configured,
            "anthropic": discovery["anthropic"].configured,
        },
        "dspy": dspy_status(),
    }


@app.get("/api/providers")
async def providers() -> dict[str, Any]:
    found = await gateway.discover()
    selected, selected_ready = reconcile_selected(gateway, found)
    return {"active": selected, "active_ready": selected_ready, "providers": public_discovery(found)}


@app.post("/api/provider/select")
async def provider_select(req: ProviderSelect) -> dict[str, Any]:
    try:
        api_key = req.api_key.get_secret_value() if req.api_key else None
        selected = gateway.select(req.provider, req.model, api_key)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, "Provider settings could not be saved. Check the model, credential format, and local credential-store access.") from exc
    return {"success": True, "active_provider": selected["provider"], "active_model": selected["model"], "credential_storage": selected["credential_storage"]}


@app.post("/api/assist/questions")
async def assist_questions(req: AssistRequest) -> dict[str, Any]:
    session = _session_or_404(req.session_id)
    try:
        result = await engine.discover(session, req.stage)
    except ProductIntentRequired as exc:
        raise HTTPException(422, {"error": "product_intent_required", "message": str(exc), "state_preserved": True}) from exc
    except Exception as exc:
        raise HTTPException(
            503,
            {
                "error": "assistant_unavailable",
                "message": "The assistant could not reach the selected model. Check the provider connection and credentials, then retry.",
                "state_preserved": True,
            },
        ) from exc
    db.save_answer(
        req.session_id,
        req.stage,
        f"discovery-context-{req.stage}",
        None,
        json.dumps(result, sort_keys=True, separators=(",", ":")),
    )
    return result


@app.post("/api/decisions/architecture/options")
async def architecture_options(req: ArchitectureOptionsRequest) -> dict[str, Any]:
    session = _session_or_404(req.session_id)
    pending = db.pending_drafts(session, "requirements")
    if pending:
        raise HTTPException(
            409,
            {
                "error": "unsaved_requirements_context",
                "message": "Save changed Constitution/Requirements drafts before comparing implementation approaches.",
                "state_preserved": True,
            },
        )
    try:
        return await engine.architecture_options(session, req.customization)
    except ValueError as exc:
        raise HTTPException(409, {"error": "requirements_required", "message": str(exc), "state_preserved": True}) from exc
    except Exception as exc:
        raise HTTPException(
            503,
            {
                "error": "architecture_options_unavailable",
                "message": "Implementation approaches could not be generated. Your specification state was preserved.",
                "state_preserved": True,
            },
        ) from exc


@app.post("/api/decisions/architecture/select")
def architecture_select(req: ArchitectureSelectRequest) -> dict[str, Any]:
    session = _session_or_404(req.session_id)
    current_source = engine.architecture_source_sha256(
        session,
        str(req.custom.get("instruction") or "") if isinstance(req.custom, dict) else None,
    )
    if current_source != req.source_context_sha256:
        raise HTTPException(
            409,
            {
                "error": "architecture_options_stale",
                "message": "Requirements or architecture inputs changed. Refresh the comparison before selecting an option.",
                "state_preserved": True,
            },
        )
    rows = req.options.get("options") if isinstance(req.options, dict) else None
    if not isinstance(rows, list) or len(rows) != 3:
        raise HTTPException(422, "Architecture comparison must contain exactly three options.")
    chosen = next((item for item in rows if isinstance(item, dict) and item.get("id") == req.selected_option_id), None)
    if not chosen:
        raise HTTPException(422, "Selected architecture option was not present in the current comparison.")
    roles = sorted(str(item.get("role") or "") for item in rows if isinstance(item, dict))
    if roles != ["alternative", "best_fit", "simplest"]:
        raise HTTPException(422, "Architecture comparison roles are invalid.")
    decision = db.save_engineering_decision(
        req.session_id,
        "architecture",
        "primary-stack",
        req.selected_option_id,
        req.options,
        req.custom,
        req.source_context_sha256,
    )
    return {"saved": True, "decision": decision, "session": db.get_session(req.session_id)}


@app.post("/api/execution/estimate")
async def execution_estimate(req: ExecutionEstimateRequest) -> dict[str, Any]:
    session = _session_or_404(req.session_id)
    pending = db.pending_drafts(session, "tasks")
    if pending:
        raise HTTPException(
            409,
            {
                "error": "unsaved_execution_context",
                "message": "Save changed Requirements, Solution, or Tasks drafts before estimating implementation routing.",
                "state_preserved": True,
            },
        )
    try:
        profile_result = await engine.task_execution_profiles(session)
        discovered = await gateway.discover()
        candidates = build_model_candidates(discovered, gateway.selected())
        source_sha = source_snapshot_sha256(session)
        plans = {
            strategy: plan_execution(
                profile_result["profiles"],
                candidates,
                strategy=strategy,
                budget=req.budget,
                escalation=req.escalation,
                budget_behavior=req.budget_behavior,
                source_sha256=source_sha,
            )
            for strategy in ("cost_optimized", "balanced", "maximum_capability")
        }
        bundle = {
            "status": "ESTIMATE",
            "source_snapshot_sha256": source_sha,
            "recommended_strategy": "cost_optimized",
            "selected_strategy": None,
            "budget": req.budget,
            "escalation": req.escalation,
            "budget_behavior": req.budget_behavior,
            "profile_summary": profile_result.get("summary"),
            "plans": plans,
        }
        db.save_execution_plan(req.session_id, source_sha, bundle)
        return bundle
    except ValueError as exc:
        raise HTTPException(409, {"error": "execution_context_incomplete", "message": str(exc), "state_preserved": True}) from exc
    except Exception as exc:
        raise HTTPException(
            503,
            {
                "error": "execution_estimate_unavailable",
                "message": "Implementation cost/routing could not be estimated. Your specification state was preserved.",
                "state_preserved": True,
            },
        ) from exc


@app.post("/api/execution/select")
def execution_select(req: ExecutionSelectRequest) -> dict[str, Any]:
    session = _session_or_404(req.session_id)
    bundle = db.get_execution_plan(req.session_id)
    if not bundle:
        raise HTTPException(409, "Generate an implementation estimate before selecting a strategy.")
    current_source = source_snapshot_sha256(session)
    if bundle.get("source_snapshot_sha256") != current_source:
        raise HTTPException(
            409,
            {
                "error": "execution_plan_stale",
                "message": "Requirements, Solution, Tasks, or the architecture decision changed. Recalculate the implementation estimate.",
                "state_preserved": True,
            },
        )
    plans = bundle.get("plans") or {}
    if req.strategy not in plans:
        raise HTTPException(422, "Selected implementation strategy is unavailable.")
    updated = {
        **bundle,
        "status": "SELECTED",
        "selected_strategy": req.strategy,
        "selected_plan": plans[req.strategy],
    }
    return db.save_execution_plan(req.session_id, current_source, updated)


@app.get("/api/execution/plan/{session_id}")
def execution_plan(session_id: str) -> dict[str, Any]:
    session = _session_or_404(session_id)
    bundle = db.get_execution_plan(session_id)
    if not bundle:
        raise HTTPException(404, "No implementation execution plan exists.")
    if bundle.get("source_snapshot_sha256") != source_snapshot_sha256(session):
        raise HTTPException(
            409,
            {
                "error": "execution_plan_stale",
                "message": "The implementation plan no longer matches the current specification.",
                "state_preserved": True,
            },
        )
    return bundle


@app.get("/api/sessions")
def sessions() -> list[dict[str, Any]]:
    return db.list_sessions()


@app.post("/api/sessions", status_code=201)
def session_create(req: SessionCreate) -> dict[str, Any]:
    try:
        return db.create_session(req.bundle_name.strip(), req.project_type)
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise HTTPException(409, "Bundle name already exists") from exc
        raise


@app.get("/api/sessions/{session_id}")
def session_get(session_id: str) -> dict[str, Any]:
    return _session_or_404(session_id)


@app.post("/api/answers")
def answer_save(req: AnswerSave) -> dict[str, Any]:
    _session_or_404(req.session_id)
    return db.save_answer(req.session_id, req.stage, req.question_id, req.selected_option_id, req.free_text_payload)


@app.post("/api/spec/draft")
def spec_draft(req: SpecSave) -> dict[str, Any]:
    _session_or_404(req.session_id)
    return db.save_draft_buffer(
        req.session_id,
        req.stage,
        req.content,
        expected_intent_sha256=req.expected_intent_sha256,
    )


@app.post("/api/spec/save")
def spec_save(req: SpecSave) -> dict[str, Any]:
    _session_or_404(req.session_id)
    review = evaluate(req.stage, req.content)
    return db.save_spec(
        req.session_id,
        req.stage,
        req.content,
        review["score"],
        review,
        "draft",
        expected_intent_sha256=req.expected_intent_sha256,
    )


@app.post("/api/spec/review")
async def spec_review(req: ReviewRequest) -> dict[str, Any]:
    session = _session_or_404(req.session_id)
    spec = _latest_spec(session, req.stage)
    pending = db.pending_drafts(session, req.stage)
    if pending:
        raise HTTPException(409, {"error": "unsaved_draft_changes", "message": "Save changed drafts before review: " + ", ".join(pending)})
    context = db.review_context(session, req.stage)
    try:
        review = await engine.semantic_review(session, req.stage, spec["content"])
    except Exception as exc:
        structural = evaluate(req.stage, spec["content"])
        review = {
            "structural": structural,
            "semantic": None,
            "score": structural["score"],
            "threshold": 0.90,
            "passed": False,
            "passing": structural.get("passing", []),
            "must_fix": structural.get("must_fix", []),
            "recommendations": structural.get("recommendations", []),
            "semantic_status": "NOT TESTED",
            "semantic_error": "Semantic review could not complete. Check the selected provider connection and credentials, then retry.",
        }
    review["context_sha256"] = context
    with db.tx() as conn:
        db.require_context(conn, req.session_id, req.stage, context)
        db.update_spec_review(spec["id"], review["score"], review, "draft", connection=conn)
    return review


@app.post("/api/spec/revise")
async def spec_revise(req: RevisionApplyRequest) -> dict[str, Any]:
    session = _session_or_404(req.session_id)
    try:
        content, metrics, review = await engine.revise(session, req.stage, req.content, req.instruction)
    except Exception as exc:
        raise HTTPException(
            503,
            {
                "error": "revision_unavailable",
                "message": "The revision could not complete. Check the selected provider connection and credentials, then retry.",
                "state_preserved": True,
            },
        ) from exc
    draft = db.save_draft_buffer(req.session_id, req.stage, content, expected_context=db.review_context(session, req.stage))
    return {
        "content": content,
        "review": review,
        "metrics": metrics,
        "draft": draft,
        "session": db.get_session(req.session_id),
        "formal_revision_created": False,
    }


@app.post("/api/spec/approve")
def spec_approve(req: ApproveRequest) -> dict[str, Any]:
    _session_or_404(req.session_id)
    with db.tx() as conn:
        return _approve_current(req, conn)


def _approve_current(req: ApproveRequest, conn) -> dict[str, Any]:
    session = db.get_session(req.session_id, conn)
    spec = _latest_spec(session, req.stage)
    structural = evaluate(req.stage, spec["content"])
    prior_review = spec.get("review") or {}
    semantic_ok = db.review_is_current(session, req.stage)
    if not structural["passed"] or not semantic_ok:
        combined = prior_review if prior_review else {
            "structural": structural,
            "semantic": None,
            "score": structural["score"],
            "passed": False,
            "semantic_status": "NOT TESTED",
        }
        raise HTTPException(
            409,
            {
                "error": "quality_gate_failed" if not structural["passed"] else "semantic_review_required",
                "review": combined,
            },
        )
    score = float(prior_review.get("score", structural["score"]))
    db.update_spec_review(spec["id"], score, prior_review, "approved", connection=conn)
    return {"approved": True, "stage": req.stage, "quality_score": score, "review": prior_review}


@app.post("/api/spec/generate")
async def generate(req: GenerateRequest) -> dict[str, Any]:
    try:
        text, metrics, spec = await _generate(req)
    except ProductIntentRequired as exc:
        raise HTTPException(422, {"error": "product_intent_required", "message": str(exc), "state_preserved": True}) from exc
    except db.StateConflict:
        raise
    except Exception as exc:
        raise HTTPException(503, {"error": "generation_failed", "message": "Generation could not complete. Check the selected provider connection and credentials, then retry.", "state_preserved": True, "fallback_options": ["lm_studio", "ollama", "openai", "anthropic"]}) from exc
    return {"content": text, "metrics": metrics, "spec": spec}


@app.post("/api/spec/stream")
async def stream(req: GenerateRequest) -> StreamingResponse:
    session = _session_or_404(req.session_id)
    try:
        engine.validate_input(session, req.stage)
    except ProductIntentRequired as exc:
        raise HTTPException(422, {"error": "product_intent_required", "message": str(exc), "state_preserved": True}) from exc
    existing = _ACTIVE_TASKS.get(req.session_id)
    if existing and not existing.done():
        raise HTTPException(409, {"error": "generation_already_running", "state_preserved": True})

    start_seq = _SEQ[req.session_id]
    expected_context = db.review_context(session, req.stage)

    async def worker() -> None:
        try:
            final: dict[str, Any] | None = None
            async for item in engine.generate_stream(session, req.stage, req.instructions):
                event = str(item.get("event") or "")
                if event == "final":
                    final = item
                    continue
                if event not in {"candidate_start", "token", "candidate_end"}:
                    raise RuntimeError(f"Unexpected generation stream event: {event!r}")
                payload = {key: value for key, value in item.items() if key != "event"}
                _record_event(req.session_id, event, payload)

            if final is None:
                raise RuntimeError("Generation stream completed without an authoritative final result.")

            content = str(final.get("content") or "").strip()
            metrics = dict(final.get("metrics") or {})
            review = dict(final.get("review") or {})
            if not content:
                raise RuntimeError("Generation stream returned an empty authoritative specification.")

            spec = db.save_spec(
                req.session_id,
                req.stage,
                content,
                float(review.get("score", 0.0)),
                review,
                "draft",
                expected_context=expected_context,
            )

            _record_event(
                req.session_id,
                "candidate_selected",
                {
                    "content": content,
                    "provisional": False,
                    "quality_score": review.get("score"),
                    "quality_passed": review.get("passed"),
                },
            )
            _record_event(req.session_id, "metrics", metrics)
            for check in review.get("checks", []):
                _record_event(req.session_id, "assertion_check", {"assertion": check["id"], "passed": check["passed"]})
            _record_event(
                req.session_id,
                "review_feedback",
                {"mustfix": review.get("must_fix", []), "recommendations": review.get("recommendations", [])},
            )
            _record_event(
                req.session_id,
                "complete",
                {"specType": req.stage, "revision": spec["revision_number"], "version": spec["version_number"]},
            )
        except Exception as exc:
            _record_event(
                req.session_id,
                "error",
                {
                    "error": "generation_failed",
                    "message": str(exc) if isinstance(exc, db.StateConflict) else "Generation could not complete. Check the selected provider connection and credentials, then retry.",
                    "state_preserved": True,
                    "fallback_options": ["lm_studio", "ollama", "openai", "anthropic"],
                },
            )

    _ACTIVE_TASKS[req.session_id] = asyncio.create_task(worker())
    return StreamingResponse(
        _events_since(req.session_id, start_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-DSpec-Start-Seq": str(start_seq)},
    )


@app.get("/api/spec/stream/resume")
async def stream_resume(session_id: str, last_seq: int = 0) -> StreamingResponse:
    _session_or_404(session_id)
    return StreamingResponse(
        _events_since(session_id, last_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/audit/scan")
def audit_scan(req: AuditRequest) -> dict[str, Any]:
    try:
        return scan_repository(req.repo_path, req.ignore_patterns, req.use_hash_cache)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/export/{session_id}")
def export(session_id: str, allow_draft: bool = False) -> Response:
    _session_or_404(session_id)
    try:
        payload, manifest = build_bundle(session_id, allow_draft)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    filename = f"{manifest['bundle_name']}-dspec-{manifest['status']}.zip"
    return Response(payload, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _frontend_root() -> Path | None:
    repo = Path(__file__).resolve().parents[1]
    for candidate in [repo / "frontend" / "out", repo / "static"]:
        if (candidate / "index.html").exists():
            return candidate
    return None


_FRONTEND = _frontend_root()
if _FRONTEND:
    assets = _FRONTEND / "_next"
    if assets.exists():
        app.mount("/_next", StaticFiles(directory=assets), name="next")


@app.get("/{full_path:path}", include_in_schema=False)
def frontend(full_path: str) -> Response:
    if full_path.startswith("api/"):
        raise HTTPException(404)
    root = _FRONTEND
    if not root:
        return JSONResponse({"status": "healthy", "message": "Frontend build not present. Run the frontend export build."})
    requested = (root / full_path).resolve() if full_path else root / "index.html"
    if root.resolve() not in requested.parents and requested != root.resolve():
        raise HTTPException(404)
    if requested.is_dir():
        requested = requested / "index.html"
    if requested.exists() and requested.is_file():
        return FileResponse(requested, media_type=mimetypes.guess_type(str(requested))[0])
    return FileResponse(root / "index.html")


def main() -> None:
    import uvicorn
    uvicorn.run("dspec.app:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
