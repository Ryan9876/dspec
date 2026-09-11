from __future__ import annotations

from typing import Any

from .execution_planning import (
    ModelCandidate,
    load_capability_catalog,
    load_pricing_catalog,
    normalize_profile,
    plan_execution,
)


def normalize_profiles_for_routing(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply deterministic safety floors after model-generated classification.

    Unknown local models may only be considered for genuinely low-risk Easy
    work. Medium/hard, ambiguous, cross-system, non-low-blast-radius, or flagged
    work receives at least Standard treatment even when the model suggested
    Local. Forced-advanced rules remain authoritative in normalize_profile().
    """

    result: list[dict[str, Any]] = []
    for raw in profiles:
        item = normalize_profile(raw, str(raw.get("task_text") or ""))
        low_risk_easy = bool(
            item.get("reasoning_complexity") == "easy"
            and item.get("ambiguity") == "low"
            and str(item.get("blast_radius") or "low").lower() == "low"
            and item.get("context_breadth") in {"isolated", "component"}
            and not item.get("risk_flags")
        )
        if item.get("minimum_safe_capability") == "local" and not low_risk_easy:
            item["minimum_safe_capability"] = "standard"
            item["risk_flags"] = sorted(
                set(list(item.get("risk_flags") or []) + ["conservative_local_safety_floor"])
            )
            item["local_eligibility"] = "NOT_ELIGIBLE"
        else:
            item["local_eligibility"] = "ELIGIBLE" if low_risk_easy else "NOT_APPLICABLE"
        result.append(item)
    return result


def safe_routing_candidates(candidates: list[ModelCandidate]) -> list[ModelCandidate]:
    """Exclude cloud models whose capability has no trusted routing profile.

    Unknown local models remain present at Local capability so low-risk Easy
    work can use them. Unknown cloud models are not inferred to be Standard from
    their names; they require a curated capability profile before assignment.
    """

    return [
        item
        for item in candidates
        if item.local or str(item.confidence).lower() != "unknown"
    ]


def _known_priced_subtotal(plan: dict[str, Any]) -> tuple[dict[str, float], list[str]]:
    totals = {"low": 0.0, "expected": 0.0, "high": 0.0}
    unpriced: list[str] = []
    for assignment in plan.get("assignments", []):
        model = assignment.get("assigned_model")
        if not model or bool(model.get("local")):
            continue
        cost = assignment.get("cloud_api_cost_estimate") or {}
        if any(cost.get(band) is None for band in ("low", "expected", "high")):
            unpriced.append(str(assignment.get("task_id") or "UNKNOWN"))
            continue
        for band in totals:
            totals[band] += float(cost.get(band) or 0.0)
    return ({band: round(value, 2) for band, value in totals.items()}, unpriced)


def finalize_plan_policy(plan: dict[str, Any]) -> dict[str, Any]:
    result = dict(plan)
    known_subtotal, unpriced = _known_priced_subtotal(result)
    result["pricing_status"] = "PARTIAL" if unpriced else "COMPLETE"
    result["known_priced_subtotal"] = known_subtotal
    result["unpriced_task_ids"] = unpriced

    capability_catalog = load_capability_catalog()
    pricing_catalog = load_pricing_catalog()
    result["catalogs"] = {
        "capability_schema_version": capability_catalog.get("schema_version"),
        "capability_as_of": capability_catalog.get("as_of"),
        "pricing_schema_version": pricing_catalog.get("schema_version"),
        "pricing_as_of": pricing_catalog.get("as_of"),
    }

    projection = str(result.get("budget_status") or "")
    result["budget_projection_status"] = projection
    budget = result.get("budget")
    behavior = result.get("budget_behavior")
    if (
        budget is not None
        and behavior != "no_enforcement"
        and projection == "EXPECTED_EXCEEDS_BUDGET"
    ):
        result["budget_status"] = "BUDGET_APPROVAL_REQUIRED"
        result["execution_allowed"] = False
    elif result.get("blocked_task_count", 0):
        result["execution_allowed"] = False
    else:
        result["execution_allowed"] = True
    return result


def plan_guided_execution(
    profiles: list[dict[str, Any]],
    candidates: list[ModelCandidate],
    *,
    strategy: str,
    budget: float | None,
    escalation: str,
    budget_behavior: str,
    source_sha256: str,
) -> dict[str, Any]:
    normalized = normalize_profiles_for_routing(profiles)
    safe_candidates = safe_routing_candidates(candidates)
    plan = plan_execution(
        normalized,
        safe_candidates,
        strategy=strategy,
        budget=budget,
        escalation=escalation,
        budget_behavior=budget_behavior,
        source_sha256=source_sha256,
    )
    return finalize_plan_policy(plan)
