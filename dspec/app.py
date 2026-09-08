from __future__ import annotations

import asyncio
import json
import mimetypes
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .audit import scan_repository
from .config import APP_VERSION, BUILD_HASH, HOST, PORT
from .dspy_signatures import status as dspy_status
from .exporter import build_bundle
from .provider import ProviderGateway, public_discovery
from .quality import evaluate
from .spec_engine import SpecEngine

app = FastAPI(title="DSpec AI", version=APP_VERSION, docs_url="/api/docs", redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
gateway = ProviderGateway()
engine = SpecEngine(gateway)

_BUFFERS: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=2000))
_SEQ: dict[str, int] = defaultdict(int)


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
    api_key: str | None = Field(default=None, min_length=8)


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


async def _generate(req: GenerateRequest) -> tuple[str, dict[str, Any], dict[str, Any]]:
    session = _session_or_404(req.session_id)
    content, metrics, review = await engine.generate(session, req.stage, req.instructions)
    spec = db.save_spec(req.session_id, req.stage, content, review["score"], review, "draft")
    return content, metrics, spec


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


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
        selected = gateway.select(req.provider, req.model, req.api_key)
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


@app.post("/api/spec/save")
def spec_save(req: SpecSave) -> dict[str, Any]:
    _session_or_404(req.session_id)
    review = evaluate(req.stage, req.content)
    return db.save_spec(req.session_id, req.stage, req.content, review["score"], review, "draft")


@app.post("/api/spec/review")
def spec_review(req: ReviewRequest) -> dict[str, Any]:
    session = _session_or_404(req.session_id)
    spec = _latest_spec(session, req.stage)
    review = evaluate(req.stage, spec["content"])
    db.update_spec_review(spec["id"], review["score"], review)
    return review


@app.post("/api/spec/approve")
def spec_approve(req: ApproveRequest) -> dict[str, Any]:
    session = _session_or_404(req.session_id)
    spec = _latest_spec(session, req.stage)
    review = evaluate(req.stage, spec["content"])
    if not review["passed"]:
        db.update_spec_review(spec["id"], review["score"], review, "draft")
        raise HTTPException(409, {"error": "quality_gate_failed", "review": review})
    db.update_spec_review(spec["id"], review["score"], review, "approved")
    return {"approved": True, "stage": req.stage, "quality_score": review["score"], "review": review}


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
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def worker() -> None:
        try:
            text, metrics, spec = await _generate(req)
            chunk_size = 140
            for offset in range(0, len(text), chunk_size):
                await queue.put(_record_event(req.session_id, "token", {"text": text[offset:offset + chunk_size]}))
            await queue.put(_record_event(req.session_id, "metrics", metrics))
            review = spec.get("review", {})
            for check in review.get("checks", []):
                await queue.put(_record_event(req.session_id, "assertion_check", {"assertion": check["id"], "passed": check["passed"]}))
            await queue.put(_record_event(req.session_id, "review_feedback", {"mustfix": review.get("must_fix", []), "recommendations": review.get("recommendations", [])}))
            await queue.put(_record_event(req.session_id, "complete", {"specType": req.stage, "revision": spec["revision_number"], "version": spec["version_number"]}))
        except Exception as exc:
            await queue.put(_record_event(req.session_id, "error", {"error": "generation_failed", "message": str(exc), "state_preserved": True, "fallback_options": ["lm_studio", "ollama", "openai", "anthropic"]}))
        finally:
            await queue.put(None)

    async def events() -> AsyncIterator[str]:
        task = asyncio.create_task(worker())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if item is None:
                    break
                yield _sse(item)
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/spec/stream/resume")
def stream_resume(session_id: str, last_seq: int = 0) -> Response:
    _session_or_404(session_id)
    body = "".join(_sse(item) for item in _BUFFERS[session_id] if item["seq"] > last_seq)
    return Response(body, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


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
