from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import db
from .execution_planning import build_model_candidates, plan_execution, source_snapshot_sha256
from .guided_engine import architecture_options, architecture_source_sha256, task_execution_profiles
from .guided_store import (
    augment_session,
    get_execution_plan,
    save_engineering_decision,
    save_execution_plan,
)
from .provider import ProviderGateway


class ArchitectureOptionsRequest(BaseModel):
    session_id: str
    customization: str | None = Field(default=None, max_length=4000)


class ArchitectureSelectRequest(BaseModel):
    session_id: str
    selected_option_id: str = Field(min_length=1, max_length=160)
    options: dict[str, Any]
    source_context_sha256: str = Field(min_length=64, max_length=64)
    customization: str | None = Field(default=None, max_length=4000)


class ExecutionEstimateRequest(BaseModel):
    session_id: str
    budget: float | None = Field(default=None, ge=0)
    escalation: Literal["automatic", "ask_first"] = "automatic"
    budget_behavior: Literal[
        "stop_before_exceeding", "ask_before_overage", "no_enforcement"
    ] = "stop_before_exceeding"


class ExecutionSelectRequest(BaseModel):
    session_id: str
    strategy: Literal["cost_optimized", "balanced", "maximum_capability"]


def _session_or_404(session_id: str) -> dict[str, Any]:
    try:
        return augment_session(db.get_session(session_id))
    except KeyError as exc:
        raise HTTPException(404, "Session not found") from exc


def build_guided_router(gateway: ProviderGateway) -> APIRouter:
    router = APIRouter()

    @router.post("/api/decisions/architecture/options")
    async def compare_architecture(req: ArchitectureOptionsRequest) -> dict[str, Any]:
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
            return await architecture_options(session, gateway, req.customization)
        except ValueError as exc:
            raise HTTPException(
                409,
                {
                    "error": "requirements_required",
                    "message": str(exc),
                    "state_preserved": True,
                },
            ) from exc
        except Exception as exc:
            raise HTTPException(
                503,
                {
                    "error": "architecture_options_unavailable",
                    "message": "Implementation approaches could not be generated. Your specification state was preserved.",
                    "state_preserved": True,
                },
            ) from exc

    @router.post("/api/decisions/architecture/select")
    def select_architecture(req: ArchitectureSelectRequest) -> dict[str, Any]:
        session = _session_or_404(req.session_id)
        current_source = architecture_source_sha256(session, req.customization)
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
        roles = sorted(
            str(item.get("role") or "") for item in rows if isinstance(item, dict)
        )
        if roles != ["alternative", "best_fit", "simplest"]:
            raise HTTPException(422, "Architecture comparison roles are invalid.")
        chosen = next(
            (
                item
                for item in rows
                if isinstance(item, dict) and item.get("id") == req.selected_option_id
            ),
            None,
        )
        if chosen is None:
            raise HTTPException(
                422,
                "Selected architecture option was not present in the current comparison.",
            )

        decision = save_engineering_decision(
            req.session_id,
            "architecture",
            "primary-stack",
            req.selected_option_id,
            req.options,
            {"instruction": (req.customization or "").strip()},
            req.source_context_sha256,
        )
        return {
            "saved": True,
            "decision": decision,
            "session": augment_session(db.get_session(req.session_id)),
        }

    @router.post("/api/execution/estimate")
    async def estimate_execution(req: ExecutionEstimateRequest) -> dict[str, Any]:
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
            profile_result = await task_execution_profiles(session, gateway)
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
                for strategy in (
                    "cost_optimized",
                    "balanced",
                    "maximum_capability",
                )
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
            save_execution_plan(req.session_id, source_sha, bundle)
            return bundle
        except ValueError as exc:
            raise HTTPException(
                409,
                {
                    "error": "execution_context_incomplete",
                    "message": str(exc),
                    "state_preserved": True,
                },
            ) from exc
        except Exception as exc:
            raise HTTPException(
                503,
                {
                    "error": "execution_estimate_unavailable",
                    "message": "Implementation cost/routing could not be estimated. Your specification state was preserved.",
                    "state_preserved": True,
                },
            ) from exc

    @router.post("/api/execution/select")
    def select_execution(req: ExecutionSelectRequest) -> dict[str, Any]:
        session = _session_or_404(req.session_id)
        bundle = get_execution_plan(req.session_id)
        if not bundle:
            raise HTTPException(
                409, "Generate an implementation estimate before selecting a strategy."
            )
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
        return save_execution_plan(req.session_id, current_source, updated)

    @router.get("/api/execution/plan/{session_id}")
    def current_execution(session_id: str) -> dict[str, Any]:
        session = _session_or_404(session_id)
        bundle = get_execution_plan(session_id)
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

    return router
