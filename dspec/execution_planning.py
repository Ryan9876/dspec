from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

CAPABILITY_RANK = {"local": 0, "standard": 1, "advanced": 2}
CAPABILITY_LABELS = {0: "Local", 1: "Standard Cloud", 2: "Advanced Cloud"}
VALID_STRATEGIES = {"cost_optimized", "balanced", "maximum_capability"}
VALID_ESCALATION = {"automatic", "ask_first"}
VALID_BUDGET_BEHAVIOR = {"stop_before_exceeding", "ask_before_overage", "no_enforcement"}

FORCED_ADVANCED_PATTERNS = (
    "authentication",
    "authorization",
    "credential",
    "secret",
    "permission boundary",
    "security boundary",
    "destructive data",
    "data deletion",
    "database migration",
    "schema migration",
    "irreversible",
    "external write",
    "production data",
)

_CONTEXT_TOKENS = {
    "isolated": 12_000,
    "component": 24_000,
    "subsystem": 36_000,
    "cross_system": 70_000,
}
_OUTPUT_TOKENS = {"easy": 4_000, "medium": 9_000, "hard": 18_000}
_RETRY_FACTORS = {"easy": 1.10, "medium": 1.30, "hard": 1.60}


@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    model: str
    capability: str
    confidence: str
    local: bool
    input_per_million: float | None
    output_per_million: float | None
    pricing_as_of: str | None
    pricing_source: str | None

    @property
    def rank(self) -> int:
        return CAPABILITY_RANK[self.capability]


def _data_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "data" / name


def _load_json(name: str) -> dict[str, Any]:
    return json.loads(_data_path(name).read_text(encoding="utf-8"))


def load_capability_catalog() -> dict[str, Any]:
    return _load_json("model-capabilities.json")


def load_pricing_catalog() -> dict[str, Any]:
    return _load_json("model-pricing.json")


def source_snapshot_sha256(session: dict[str, Any]) -> str:
    architecture = [
        item for item in session.get("engineering_decisions", [])
        if item.get("decision_type") == "architecture"
    ]
    payload = {
        "session_id": session.get("id"),
        "requirements": session.get("specs", {}).get("requirements", {}).get("content"),
        "solution": session.get("specs", {}).get("solution", {}).get("content"),
        "tasks": session.get("specs", {}).get("tasks", {}).get("content"),
        "architecture_decisions": architecture,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _capability_entry(provider: str, model: str, catalog: dict[str, Any]) -> dict[str, Any] | None:
    exact = catalog.get("models", {}).get(f"{provider}:{model}")
    if exact:
        return exact
    aliases = catalog.get("aliases", {})
    target = aliases.get(f"{provider}:{model}")
    return catalog.get("models", {}).get(target) if target else None


def _pricing_entry(provider: str, model: str, catalog: dict[str, Any]) -> dict[str, Any] | None:
    exact = catalog.get("models", {}).get(f"{provider}:{model}")
    if exact:
        return exact
    aliases = catalog.get("aliases", {})
    target = aliases.get(f"{provider}:{model}")
    return catalog.get("models", {}).get(target) if target else None


def build_model_candidates(
    discovered: dict[str, Any],
    selected: dict[str, str] | None = None,
) -> list[ModelCandidate]:
    capability_catalog = load_capability_catalog()
    pricing_catalog = load_pricing_catalog()
    result: list[ModelCandidate] = []
    seen: set[tuple[str, str]] = set()

    def add(provider: str, model: str, local: bool) -> None:
        key = (provider, model)
        if key in seen:
            return
        seen.add(key)
        cap = _capability_entry(provider, model, capability_catalog)
        price = _pricing_entry(provider, model, pricing_catalog)
        capability = str((cap or {}).get("max_safe_tier") or ("local" if local else "standard"))
        confidence = str((cap or {}).get("confidence") or "unknown")
        if capability not in CAPABILITY_RANK:
            capability = "local" if local else "standard"
            confidence = "unknown"
        result.append(
            ModelCandidate(
                provider=provider,
                model=model,
                capability=capability,
                confidence=confidence,
                local=local,
                input_per_million=0.0 if local else _number_or_none((price or {}).get("input_per_million")),
                output_per_million=0.0 if local else _number_or_none((price or {}).get("output_per_million")),
                pricing_as_of=None if local else (price or {}).get("as_of"),
                pricing_source=None if local else (price or {}).get("source"),
            )
        )

    for provider in ("lm_studio", "ollama"):
        info = discovered.get(provider)
        online = bool(getattr(info, "online", None) if info is not None else False)
        models = list(getattr(info, "models", []) if info is not None else [])
        if isinstance(info, dict):
            online = bool(info.get("online"))
            models = list(info.get("models") or [])
        if online:
            for model in models:
                add(provider, str(model), True)

    for provider in ("openai", "anthropic"):
        info = discovered.get(provider)
        configured = bool(getattr(info, "configured", None) if info is not None else False)
        if isinstance(info, dict):
            configured = bool(info.get("configured"))
        if not configured:
            continue
        for key, entry in capability_catalog.get("models", {}).items():
            if not key.startswith(provider + ":"):
                continue
            model = key.split(":", 1)[1]
            if bool(entry.get("routing_candidate", True)):
                add(provider, model, False)

    if selected:
        provider = str(selected.get("provider") or "")
        model = str(selected.get("model") or "")
        if provider and model:
            local = provider in {"lm_studio", "ollama"}
            info = discovered.get(provider)
            ready = bool(
                (getattr(info, "online", False) if local else getattr(info, "configured", False))
                if info is not None else False
            )
            if isinstance(info, dict):
                ready = bool(info.get("online") if local else info.get("configured"))
            if ready:
                add(provider, model, local)

    return result


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def normalize_profile(profile: dict[str, Any], task_text: str = "") -> dict[str, Any]:
    result = dict(profile)
    complexity = str(result.get("reasoning_complexity") or "medium").lower()
    if complexity not in {"easy", "medium", "hard"}:
        complexity = "medium"
    context = str(result.get("context_breadth") or "subsystem").lower()
    if context not in _CONTEXT_TOKENS:
        context = "subsystem"
    ambiguity = str(result.get("ambiguity") or "medium").lower()
    if ambiguity not in {"low", "medium", "high"}:
        ambiguity = "medium"
    risk_flags = [str(x).lower() for x in result.get("risk_flags", [])]
    minimum = str(result.get("minimum_safe_capability") or "standard").lower()
    if minimum not in CAPABILITY_RANK:
        minimum = "standard"

    combined = " ".join([task_text.lower(), *risk_flags])
    forced = any(pattern in combined for pattern in FORCED_ADVANCED_PATTERNS)
    if forced:
        minimum = "advanced"
        risk_flags = sorted(set(risk_flags + ["forced_advanced_safety_floor"]))

    result.update(
        {
            "reasoning_complexity": complexity,
            "context_breadth": context,
            "ambiguity": ambiguity,
            "risk_flags": risk_flags,
            "minimum_safe_capability": minimum,
            "forced_advanced": forced,
        }
    )
    return result


def estimate_tokens(profile: dict[str, Any]) -> dict[str, int]:
    complexity = str(profile.get("reasoning_complexity") or "medium")
    context = str(profile.get("context_breadth") or "subsystem")
    base_input = _CONTEXT_TOKENS.get(context, _CONTEXT_TOKENS["subsystem"])
    base_output = _OUTPUT_TOKENS.get(complexity, _OUTPUT_TOKENS["medium"])
    retry = _RETRY_FACTORS.get(complexity, _RETRY_FACTORS["medium"])
    return {
        "input_low": int(base_input * 0.65),
        "input_expected": int(base_input * retry),
        "input_high": int(base_input * retry * 1.8),
        "output_low": int(base_output * 0.60),
        "output_expected": int(base_output * retry),
        "output_high": int(base_output * retry * 1.8),
    }


def _cost(candidate: ModelCandidate, tokens: dict[str, int], band: str) -> float | None:
    if candidate.local:
        return 0.0
    if candidate.input_per_million is None or candidate.output_per_million is None:
        return None
    return round(
        tokens[f"input_{band}"] / 1_000_000 * candidate.input_per_million
        + tokens[f"output_{band}"] / 1_000_000 * candidate.output_per_million,
        4,
    )


def _eligible(
    profile: dict[str, Any],
    candidates: Iterable[ModelCandidate],
) -> list[ModelCandidate]:
    minimum = CAPABILITY_RANK[str(profile["minimum_safe_capability"])]
    return [item for item in candidates if item.rank >= minimum]


def _select_candidate(
    profile: dict[str, Any],
    candidates: list[ModelCandidate],
    strategy: str,
    tokens: dict[str, int],
) -> ModelCandidate | None:
    eligible = _eligible(profile, candidates)
    if not eligible:
        return None
    minimum = CAPABILITY_RANK[str(profile["minimum_safe_capability"])]
    target = minimum
    if strategy == "balanced" and (
        profile.get("ambiguity") in {"medium", "high"}
        or profile.get("reasoning_complexity") == "hard"
    ):
        target = min(2, minimum + 1)
    elif strategy == "maximum_capability":
        target = max(item.rank for item in eligible)

    pool = [item for item in eligible if item.rank >= target]
    if not pool:
        pool = eligible

    def key(item: ModelCandidate) -> tuple[int, float, int, str]:
        expected = _cost(item, tokens, "expected")
        unknown = 1 if expected is None else 0
        cost = expected if expected is not None else float("inf")
        if strategy == "maximum_capability":
            return (-item.rank, cost, unknown, item.model)
        return (0 if item.local else 1, cost, item.rank, item.model)

    return sorted(pool, key=key)[0]


def plan_execution(
    profiles: list[dict[str, Any]],
    candidates: list[ModelCandidate],
    *,
    strategy: str = "cost_optimized",
    budget: float | None = None,
    escalation: str = "automatic",
    budget_behavior: str = "stop_before_exceeding",
    source_sha256: str | None = None,
) -> dict[str, Any]:
    if strategy not in VALID_STRATEGIES:
        raise ValueError("Unsupported implementation strategy.")
    if escalation not in VALID_ESCALATION:
        raise ValueError("Unsupported escalation policy.")
    if budget_behavior not in VALID_BUDGET_BEHAVIOR:
        raise ValueError("Unsupported budget behavior.")
    if budget is not None and budget < 0:
        raise ValueError("Budget cannot be negative.")

    assignments: list[dict[str, Any]] = []
    totals: dict[str, float] = {"low": 0.0, "expected": 0.0, "high": 0.0}
    unknown_pricing = False
    tier_counts = {"local": 0, "standard": 0, "advanced": 0}

    for raw in profiles:
        profile = normalize_profile(raw, str(raw.get("task_text") or ""))
        tokens = estimate_tokens(profile)
        chosen = _select_candidate(profile, candidates, strategy, tokens)
        assignment: dict[str, Any] = {
            **profile,
            "token_estimate": tokens,
            "status": "PLANNED" if chosen else "BLOCKED_NO_SAFE_MODEL",
        }
        if chosen is None:
            assignment["assigned_model"] = None
            assignments.append(assignment)
            continue

        costs = {band: _cost(chosen, tokens, band) for band in ("low", "expected", "high")}
        if any(value is None for value in costs.values()):
            unknown_pricing = True
        else:
            for band, value in costs.items():
                totals[band] += float(value or 0.0)
        assignment["assigned_model"] = {
            "provider": chosen.provider,
            "model": chosen.model,
            "capability": chosen.capability,
            "capability_confidence": chosen.confidence,
            "local": chosen.local,
            "pricing_as_of": chosen.pricing_as_of,
            "pricing_source": chosen.pricing_source,
        }
        assignment["cloud_api_cost_estimate"] = costs
        tier_counts[chosen.capability] += 1
        assignments.append(assignment)

    count = max(1, sum(tier_counts.values()))
    mix = {
        tier: {
            "tasks": value,
            "percent": round(value / count * 100, 1) if sum(tier_counts.values()) else 0.0,
        }
        for tier, value in tier_counts.items()
    }
    budget_status = "NO_LIMIT" if budget is None else "WITHIN_EXPECTED"
    if budget is not None:
        if unknown_pricing:
            budget_status = "UNKNOWN_PRICING"
        elif totals["expected"] > budget:
            budget_status = "EXPECTED_EXCEEDS_BUDGET"
        elif totals["high"] > budget:
            budget_status = "HIGH_ESTIMATE_EXCEEDS_BUDGET"

    blocked = sum(1 for item in assignments if item["status"] != "PLANNED")
    return {
        "schema_version": 1,
        "source_snapshot_sha256": source_sha256,
        "strategy": strategy,
        "strategy_label": {
            "cost_optimized": "Cost Optimized — Recommended",
            "balanced": "Balanced",
            "maximum_capability": "Maximum Capability",
        }[strategy],
        "escalation": escalation,
        "budget": budget,
        "budget_behavior": budget_behavior,
        "budget_status": budget_status,
        "model_mix": mix,
        "cloud_api_cost_estimate": (
            {"low": round(totals["low"], 2), "expected": round(totals["expected"], 2), "high": round(totals["high"], 2)}
            if not unknown_pricing
            else {"low": None, "expected": None, "high": None}
        ),
        "cost_confidence": "LOW" if unknown_pricing else "ESTIMATE",
        "estimate_assumptions": [
            "Cloud API cost only; local compute/electricity is not priced.",
            "Token estimates are planning ranges based on task reasoning complexity and context breadth.",
            "Actual debugging, retries, provider caching, and repository size can move cost outside this range.",
            "A finite budget may pause or require authorization; it never lowers a task below minimum safe capability.",
        ],
        "blocked_task_count": blocked,
        "assignments": assignments,
    }


def render_execution_views(plan: dict[str, Any]) -> dict[str, str]:
    buckets: dict[str, list[dict[str, Any]]] = {"local": [], "standard": [], "advanced": []}
    for item in plan.get("assignments", []):
        model = item.get("assigned_model")
        if not model:
            continue
        capability = str(model.get("capability") or "")
        if capability in buckets:
            buckets[capability].append(item)

    result: dict[str, str] = {}
    for capability, items in buckets.items():
        lines = [
            f"# {CAPABILITY_LABELS[CAPABILITY_RANK[capability]]} execution view",
            "",
            "> Derived execution view. The canonical DSpec requirements, solution, and tasks remain authoritative.",
            "",
            f"Source snapshot: `{plan.get('source_snapshot_sha256') or 'UNKNOWN'}`",
            "",
        ]
        if not items:
            lines.append("No tasks are currently routed to this tier.")
        for item in items:
            task_id = str(item.get("task_id") or "UNIDENTIFIED")
            title = str(item.get("title") or "")
            model = item.get("assigned_model") or {}
            lines.extend(
                [
                    f"## {task_id} {title}".rstrip(),
                    "",
                    f"- Assigned: {model.get('provider')} / {model.get('model')}",
                    f"- Minimum safe capability: {item.get('minimum_safe_capability')}",
                    f"- Reasoning complexity: {item.get('reasoning_complexity')}",
                    f"- Risk flags: {', '.join(item.get('risk_flags') or []) or 'none identified'}",
                    f"- Requirements: {', '.join(item.get('requirement_ids') or []) or 'UNKNOWN'}",
                    f"- Validation: {item.get('validation') or 'Use canonical task verification.'}",
                    "",
                    str(item.get("task_text") or "").strip(),
                    "",
                ]
            )
        result[f"execution/{capability}.md"] = "\n".join(lines).rstrip() + "\n"
    return result
