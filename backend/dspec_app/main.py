from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .audit import scan_repository
from .db import STAGES, approve_spec, create_session, get_session, init_db, save_answers, save_spec
from .exporter import build_bundle
from .providers import LOCAL_ENDPOINTS, discover, load_config, select_provider
from .security import SecretRedactionFilter
from .spec_engine import dspy_ready, generate, revise, validate_candidate


def configure_logging() -> None:
    redactor = SecretRedactionFilter()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "dspec"):
        logger = logging.getLogger(name)
        logger.addFilter(redactor)
        for handler in logger.handlers:
            handler.addFilter(redactor)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    init_db()
    yield


app = FastAPI(title="DSpec AI", version=__version__, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3210", "http://localhost:3210"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)

BUFFERS: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=2000))


class SessionCreate(BaseModel):
    bundle_name: str = Field(min_length=1, max_length=120)
    project_type: str = Field(default="greenfield", pattern="^(greenfield|existing)$")


class AnswersUpdate(BaseModel):
    answers: dict[str, Any]


class SpecUpdate(BaseModel):
    content: str


class ProviderSelect(BaseModel):
    provider: str
    model: str = ""
    api_key: str | None = None


class AuditRequest(BaseModel):
    repo_path: str
    ignore_patterns: list[str] = Field(default_factory=list)
    use_hash_cache: bool = True


class GenerateRequest(BaseModel):
    session_id: str
    stage: str


class ReviseRequest(BaseModel):
    session_id: str
    stage: str
    recommendation: str = Field(min_length=1, max_length=2000)


@app.get("/api/health")
def health() -> dict:
    state = discover()
    cfg = load_config()
    active = cfg["active_provider"]
    provider_data = state["providers"].get(active, {})
    if active in LOCAL_ENDPOINTS:
        provider_ready = bool(provider_data.get("online"))
    else:
        provider_ready = bool(provider_data.get("configured"))
    return {
        "status": "healthy",
        "version": __version__,
        "build_hash": os.environ.get("DSPEC_BUILD_HASH", "development"),
        "port": 3210,
        "database": "connected",
        "active_provider": {"name": active, "model": cfg["active_model"], "ready": provider_ready},
        "detected_local_services": {
            "lm_studio": state["providers"]["lm_studio"],
            "ollama": state["providers"]["ollama"],
        },
        "dspy": {
            "installed": dspy_ready(),
            "optimization": "unoptimized baseline",
            "mipro_v2": "BLOCKED_PENDING_REVIEWED_DATA_AND_AUTHORIZED_MODEL_EXECUTION",
        },
    }


@app.get("/api/providers")
def providers() -> dict:
    return discover()


@app.post("/api/provider/select")
def provider_select(payload: ProviderSelect) -> dict:
    try:
        return select_provider(payload.provider, payload.model, payload.api_key)
    except ValueError as exc:
        raise HTTPException(422, detail={"error": "invalid_provider", "message": str(exc)}) from exc
    except RuntimeError as exc:
        raise HTTPException(
            422, detail={"error": str(exc), "message": "Configure the selected cloud provider key first."}
        ) from exc
    except ConnectionError as exc:
        raise HTTPException(
            503,
            detail={
                "error": str(exc),
                "message": "Selected local provider is not reachable.",
                "fallback_options": ["lm_studio", "ollama", "openai", "anthropic"],
            },
        ) from exc


@app.post("/api/sessions", status_code=201)
def sessions_create(payload: SessionCreate) -> dict:
    return create_session(payload.bundle_name, payload.project_type)


@app.get("/api/sessions/{session_id}")
def sessions_get(session_id: str) -> dict:
    try:
        return get_session(session_id)
    except KeyError as exc:
        raise HTTPException(404, detail="session not found") from exc


@app.put("/api/sessions/{session_id}/answers/{stage}")
def answers_put(session_id: str, stage: str, payload: AnswersUpdate) -> dict:
    try:
        return save_answers(session_id, stage, payload.answers)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, detail="session not found") from exc


@app.put("/api/sessions/{session_id}/specs/{stage}")
def specs_put(session_id: str, stage: str, payload: SpecUpdate) -> dict:
    if stage not in STAGES:
        raise HTTPException(422, detail="invalid stage")
    review = validate_candidate(stage, payload.content)
    try:
        return save_spec(session_id, stage, payload.content, review["score"], review)
    except KeyError as exc:
        raise HTTPException(404, detail="session not found") from exc


@app.post("/api/sessions/{session_id}/specs/{stage}/approve")
def specs_approve(session_id: str, stage: str) -> dict:
    try:
        return approve_spec(session_id, stage)
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, detail="session not found") from exc


@app.post("/api/spec/generate")
def spec_generate(payload: GenerateRequest) -> dict:
    if payload.stage not in STAGES:
        raise HTTPException(422, detail="invalid stage")
    try:
        session = get_session(payload.session_id)
    except KeyError as exc:
        raise HTTPException(404, detail="session not found") from exc
    context = {stage: session["specs"][stage]["content"] for stage in STAGES}
    context["idea"] = str(session["answers"]["constitution"].get("idea", ""))
    try:
        result = generate(payload.stage, context, json.dumps(session["answers"][payload.stage]))
    except Exception as exc:
        raise HTTPException(503, detail={"error": "generation_unavailable", "message": str(exc)}) from exc
    return save_spec(
        payload.session_id, payload.stage, result["content"], result["review"]["score"], result["review"]
    )


@app.post("/api/spec/revise")
def spec_revise(payload: ReviseRequest) -> dict:
    if payload.stage not in STAGES:
        raise HTTPException(422, detail="invalid stage")
    try:
        session = get_session(payload.session_id)
    except KeyError as exc:
        raise HTTPException(404, detail="session not found") from exc
    current = session["specs"][payload.stage]["content"]
    if not current.strip():
        raise HTTPException(409, detail="draft is empty")
    try:
        result = revise(payload.stage, current, payload.recommendation)
    except Exception as exc:
        raise HTTPException(503, detail={"error": "revision_unavailable", "message": str(exc)}) from exc
    return save_spec(
        payload.session_id, payload.stage, result["content"], result["review"]["score"], result["review"]
    )


@app.post("/api/spec/stream")
async def spec_stream(payload: GenerateRequest) -> StreamingResponse:
    stream_id = payload.session_id + ":" + payload.stage

    async def event_source():
        try:
            result = spec_generate(payload)
            content = result["specs"][payload.stage]["content"]
            for seq, start in enumerate(range(0, len(content), 120), 1):
                event = {"seq": seq, "text": content[start : start + 120]}
                BUFFERS[stream_id].append(event)
                yield f"event: token\ndata: {json.dumps(event)}\n\n"
                await asyncio.sleep(0)
            review = result["specs"][payload.stage]["review"]
            yield f"event: review_feedback\ndata: {json.dumps(review)}\n\n"
            yield (
                f"event: complete\ndata: "
                f"{json.dumps({'stage': payload.stage, 'revision': result['specs'][payload.stage]['revision']})}\n\n"
            )
        except HTTPException as exc:
            yield f"event: error\ndata: {json.dumps({'status': exc.status_code, 'detail': exc.detail})}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/spec/stream/resume")
def stream_resume(session_id: str, stage: str, last_seq: int = 0) -> StreamingResponse:
    stream_id = session_id + ":" + stage

    async def replay():
        for event in list(BUFFERS[stream_id]):
            if int(event["seq"]) > last_seq:
                yield f"event: token\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(replay(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/audit/scan")
def audit_scan(payload: AuditRequest) -> dict:
    try:
        return scan_repository(payload.repo_path, payload.ignore_patterns, payload.use_hash_cache)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@app.get("/api/export/{session_id}")
def export_bundle(session_id: str, draft: bool = False):
    try:
        data, manifest = build_bundle(session_id, allow_draft=draft)
        session = get_session(session_id)
    except KeyError as exc:
        raise HTTPException(404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    filename = f"{session['bundle_name']}-{'draft-' if draft else ''}bundle.zip"
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-DSpec-Bundle-State": manifest["state"],
        },
    )


frontend = Path(os.environ.get("DSPEC_FRONTEND_DIR", Path(__file__).resolve().parents[2] / "out"))
if frontend.exists():
    assets = frontend / "_next"
    if assets.exists():
        app.mount("/_next", StaticFiles(directory=assets), name="next-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend_files(path: str):
        candidate = frontend / path
        if candidate.is_file():
            return FileResponse(candidate)
        html = frontend / path / "index.html"
        if html.is_file():
            return FileResponse(html)
        return FileResponse(frontend / "index.html")
else:

    @app.get("/", include_in_schema=False)
    def no_frontend():
        return JSONResponse({"status": "backend_only", "message": "Build the Next.js static export with npm run build."})
