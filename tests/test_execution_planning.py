from __future__ import annotations

from dspec.execution_planning import (
    ModelCandidate,
    normalize_profile,
    plan_execution,
    render_execution_views,
)


def candidate(
    provider: str,
    model: str,
    capability: str,
    *,
    local: bool,
    input_price: float | None = None,
    output_price: float | None = None,
) -> ModelCandidate:
    return ModelCandidate(
        provider=provider,
        model=model,
        capability=capability,
        confidence="test",
        local=local,
        input_per_million=0.0 if local else input_price,
        output_per_million=0.0 if local else output_price,
        pricing_as_of=None if local else "2026-09-09",
        pricing_source=None if local else "fixture",
    )


def profile(task_id: str = "T-001", **overrides):
    base = {
        "task_id": task_id,
        "title": "Implement a bounded change",
        "task_text": "Update one isolated configuration path and run its existing tests.",
        "requirement_ids": ["REQ-001"],
        "solution_refs": ["SOL-001"],
        "reasoning_complexity": "easy",
        "context_breadth": "isolated",
        "ambiguity": "low",
        "blast_radius": "low",
        "risk_flags": [],
        "minimum_safe_capability": "local",
        "validation": "pytest -q",
        "bounded_context_refs": ["config.py", "tests/test_config.py"],
    }
    base.update(overrides)
    return base


def test_security_sensitive_work_forces_advanced_capability():
    item = normalize_profile(
        profile(
            task_text="Change the authorization boundary for administrator access.",
            minimum_safe_capability="local",
        )
    )
    assert item["minimum_safe_capability"] == "advanced"
    assert item["forced_advanced"] is True
    assert "forced_advanced_safety_floor" in item["risk_flags"]


def test_cost_optimized_prefers_eligible_local_model():
    local = candidate("ollama", "local-coder", "local", local=True)
    cloud = candidate("openai", "frontier", "advanced", local=False, input_price=4, output_price=20)
    plan = plan_execution([profile()], [local, cloud], strategy="cost_optimized")
    assigned = plan["assignments"][0]["assigned_model"]
    assert assigned["provider"] == "ollama"
    assert plan["cloud_api_cost_estimate"]["expected"] == 0.0
    assert plan["model_mix"]["local"]["tasks"] == 1


def test_budget_never_downgrades_minimum_safe_capability():
    local = candidate("ollama", "local-coder", "local", local=True)
    advanced = candidate("openai", "frontier", "advanced", local=False, input_price=4, output_price=20)
    security = profile(
        task_id="T-SEC",
        task_text="Implement credential storage and authorization checks.",
        minimum_safe_capability="local",
        reasoning_complexity="hard",
        context_breadth="cross_system",
    )
    plan = plan_execution(
        [security],
        [local, advanced],
        strategy="cost_optimized",
        budget=0.01,
        budget_behavior="stop_before_exceeding",
    )
    assignment = plan["assignments"][0]
    assert assignment["assigned_model"]["capability"] == "advanced"
    assert plan["budget_status"] in {"EXPECTED_EXCEEDS_BUDGET", "HIGH_ESTIMATE_EXCEEDS_BUDGET"}


def test_missing_safe_model_blocks_instead_of_unsafe_fallback():
    local = candidate("ollama", "local-coder", "local", local=True)
    security = profile(
        task_id="T-SEC",
        task_text="Perform an irreversible production database migration.",
        minimum_safe_capability="local",
    )
    plan = plan_execution([security], [local], strategy="cost_optimized")
    assert plan["assignments"][0]["status"] == "BLOCKED_NO_SAFE_MODEL"
    assert plan["assignments"][0]["assigned_model"] is None


def test_unknown_cloud_price_remains_unknown():
    cloud = candidate("openai", "unpriced", "standard", local=False)
    medium = profile(
        task_id="T-MED",
        reasoning_complexity="medium",
        context_breadth="subsystem",
        minimum_safe_capability="standard",
    )
    plan = plan_execution([medium], [cloud], strategy="balanced", budget=25)
    assert plan["cloud_api_cost_estimate"]["expected"] is None
    assert plan["budget_status"] == "UNKNOWN_PRICING"


def test_execution_views_are_derived_and_traceable():
    local = candidate("ollama", "local-coder", "local", local=True)
    plan = plan_execution(
        [profile()],
        [local],
        strategy="cost_optimized",
        source_sha256="abc123",
    )
    views = render_execution_views(plan)
    text = views["execution/local.md"]
    assert "Derived execution view" in text
    assert "canonical DSpec requirements, solution, and tasks remain authoritative" in text
    assert "T-001" in text
    assert "abc123" in text


def test_non_convergence_contract_requires_rediagnosis_and_escalation():
    local = candidate("ollama", "local-coder", "local", local=True)
    plan = plan_execution([profile()], [local], strategy="cost_optimized")
    policy = plan["escalation_policy"]
    assert policy["same_tier_failed_attempt_limit"] == 2
    assert policy["on_nonconvergence"] == "ESCALATION_REQUIRED"
    assert policy["re_diagnose_before_retry"] is True
    assert plan["result_contract"]["statuses"] == ["COMPLETE", "BLOCKED", "ESCALATION_REQUIRED"]
