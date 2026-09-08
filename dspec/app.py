from __future__ import annotations

import asyncio
import json
import mimetypes
from contextlib import asynccontextmanager
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from . import db
from .audit import scan_repository
from .config import APP_VERSION, BUILD_HASH, HOST, PORT
from .dspy_signatures import status as dspy_status
from .exporter import build_bundle
from .provider import ProviderGateway, public_discovery
from .quality import evaluate
from .logging_utils import configure_logging
from .spec_engine import SpecEngine


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    db.init_db()
    yield


app = FastAPI(title="DSpec AI", version=APP_VERSION, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
gateway = ProviderGateway()
engine = SpecEngine(gateway)

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


class ReviewRequest(BaseModel):
    session_id: str
    stage: Literal["constitution", "requirements", "solution", "tasks"]


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
            # The worker always records complete/error before returning. Give the
            # ring buffer one scheduling turn to expose that terminal event.
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
    spec = db.save_spec(req.session_id, req.stage, content, review["score"], review, "draft")
    return content, metrics, spec


@app.get("/api/health")
async def health() -> dict[str, Any]:
    db.init_db()
    discovery = await gateway.discover()
    selected = gateway.selected()
    return {
        "status": "healthy",
        "version": APP_VERSION,
        "build_hash": BUILD_HASH,
        "host": HOST,
        "port": PORT,
        "database": "connected",
        "active_provider": selected,
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
    return {"active": gateway.selected(), "providers": public_discovery(found)}


@app.post("/api/provider/select")
async def provider_select(req: ProviderSelect) -> dict[str, Any]:
    try:
        api_key = req.api_key.get_secret_value() if req.api_key else None
        selected = gateway.select(req.provider, req.model, api_key)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"success": True, "active_provider": selected["provider"], "active_model": selected["model"], "credential_storage": selected["credential_storage"]}


@app.post("/api/assist/questions")
async def assist_questions(req: AssistRequest) -> dict[str, Any]:
    session = _session_or_404(req.session_id)
    try:
        return await engine.discover(session, req.stage)
    except Exception as exc:
        raise HTTPException(
            503,
            {
                "error": "assistant_unavailable",
                "message": str(exc),
                "state_preserved": True,
            },
        ) from exc


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
def answer_save(req: AnswerSave) -> dict[str, bool]:
    _session_or_404(req.session_id)
    db.save_answer(req.session_id, req.stage, req.question_id, req.selected_option_id, req.free_text_payload)
    return {"saved": True}


@app.post("/api/spec/draft")
def spec_draft(req: SpecSave) -> dict[str, Any]:
    _session_or_404(req.session_id)
    return db.save_draft_buffer(req.session_id, req.stage, req.content)


@app.post("/api/spec/save")
def spec_save(req: SpecSave) -> dict[str, Any]:
    _session_or_404(req.session_id)
    review = evaluate(req.stage, req.content)
    return db.save_spec(req.session_id, req.stage, req.content, review["score"], review, "draft")


@app.post("/api/spec/review")
async def spec_review(req: ReviewRequest) -> dict[str, Any]:
    session = _session_or_404(req.session_id)
    spec = _latest_spec(session, req.stage)
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
            "semantic_error": str(exc)[:240],
        }
    db.update_spec_review(spec["id"], review["score"], review)
    return review


@app.post("/api/spec/approve")
def spec_approve(req: ApproveRequest) -> dict[str, Any]:
    session = _session_or_404(req.session_id)
    spec = _latest_spec(session, req.stage)
    structural = evaluate(req.stage, spec["content"])
    prior_review = spec.get("review") or {}
    semantic_ok = prior_review.get("semantic_status") == "PASS" and prior_review.get("passed") is True
    if not structural["passed"] or not semantic_ok:
        combined = prior_review if prior_review else {
            "structural": structural,
            "semantic": None,
            "score": structural["score"],
            "passed": False,
            "semantic_status": "NOT TESTED",
        }
        db.update_spec_review(spec["id"], float(combined.get("score", structural["score"])), combined, "draft")
        raise HTTPException(
            409,
            {
                "error": "quality_gate_failed" if not structural["passed"] else "semantic_review_required",
                "review": combined,
            },
        )
    score = float(prior_review.get("score", structural["score"]))
    db.update_spec_review(spec["id"], score, prior_review, "approved")
    return {"approved": True, "stage": req.stage, "quality_score": score, "review": prior_review}


@app.post("/api/spec/generate")
async def generate(req: GenerateRequest) -> dict[str, Any]:
    try:
        text, metrics, spec = await _generate(req)
    except Exception as exc:
        raise HTTPException(503, {"error": "generation_failed", "message": str(exc), "state_preserved": True, "fallback_options": ["lm_studio", "ollama", "openai", "anthropic"]}) from exc
    return {"content": text, "metrics": metrics, "spec": spec}


@app.post("/api/spec/stream")
async def stream(req: GenerateRequest) -> StreamingResponse:
    _session_or_404(req.session_id)
    existing = _ACTIVE_TASKS.get(req.session_id)
    if existing and not existing.done():
        raise HTTPException(409, {"error": "generation_already_running", "state_preserved": True})
    start_seq = _SEQ[req.session_id]

    async def worker() -> None:
        try:
            text, metrics, spec = await _generate(req)
            chunk_size = 140
            for offset in range(0, len(text), chunk_size):
                _record_event(req.session_id, "token", {"text": text[offset:offset + chunk_size]})
                await asyncio.sleep(0)
            _record_event(req.session_id, "metrics", metrics)
            review = spec.get("review", {})
            for check in review.get("checks", []):
                _record_event(req.session_id, "assertion_check", {"assertion": check["id"], "passed": check["passed"]})
            _record_event(req.session_id, "review_feedback", {"mustfix": review.get("must_fix", []), "recommendations": review.get("recommendations", [])})
            _record_event(req.session_id, "complete", {"specType": req.stage, "revision": spec["revision_number"], "version": spec["version_number"]})
        except Exception as exc:
            _record_event(req.session_id, "error", {
                "error": "generation_failed",
                "message": str(exc),
                "state_preserved": True,
                "fallback_options": ["lm_studio", "ollama", "openai", "anthropic"],
            })

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
    fallback = root / "index.html"
    return FileResponse(fallback)


def main() -> None:
    import uvicorn
    uvicorn.run("dspec.app:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
