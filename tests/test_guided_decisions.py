from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from dspec import db
from dspec.app import app, engine, gateway


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DSPEC_HOME", str(tmp_path / "home"))
    db.init_db()
    with TestClient(app) as c:
        yield c


def create_session(client: TestClient, name: str) -> str:
    response = client.post("/api/sessions", json={"bundle_name": name, "project_type": "greenfield"})
    assert response.status_code == 201
    return response.json()["id"]


def save_tier(client: TestClient, session_id: str, stage: str, content: str) -> None:
    response = client.post(
        "/api/spec/save",
        json={"session_id": session_id, "stage": stage, "content": content},
    )
    assert response.status_code == 200


def architecture_fixture(source_sha: str) -> dict:
    return {
        "recommended_option_id": "best",
        "alternative_objective": "enterprise alignment",
        "decision_summary": "Best Fit balances growth and operating complexity.",
        "source_context_sha256": source_sha,
        "options": [
            {
                "id": "best",
                "role": "best_fit",
                "title": "Best Fit — Recommended",
                "plain_english_summary": "Best overall fit for the requirements.",
                "why_recommended": "It balances capability, maintainability, and growth.",
                "advantages": ["Strong fit"],
                "tradeoffs": ["Moderate complexity"],
                "operational_impact": "Moderate operating burden.",
                "why_engineers_care": "Architecture choices become expensive to change later.",
                "engineering_concept": {"id": "separation-of-concerns", "label": "Separation of concerns", "mental_model": "Use clear responsibilities."},
                "reconsider_when": ["The product becomes permanently single-user."],
                "technical_details": [{"category": "Database", "choice": "PostgreSQL", "consequence": "Run a relational database service."}],
            },
            {
                "id": "simple",
                "role": "simplest",
                "title": "Simplest",
                "plain_english_summary": "Fewer moving parts.",
                "why_recommended": "Choose this when low operating burden dominates.",
                "advantages": ["Simple"],
                "tradeoffs": ["Less specialized"],
                "operational_impact": "Low burden.",
                "why_engineers_care": "Fewer components reduce failure surfaces.",
                "engineering_concept": {"id": "simplicity", "label": "Simplicity", "mental_model": "Prefer fewer moving parts when needs are modest."},
                "reconsider_when": ["Scale or integration needs grow."],
                "technical_details": [{"category": "Backend", "choice": "TypeScript", "consequence": "One primary application language."}],
            },
            {
                "id": "alt",
                "role": "alternative",
                "title": "Enterprise Alignment",
                "plain_english_summary": "Fits an existing Microsoft estate.",
                "why_recommended": "Choose this when organizational alignment dominates.",
                "advantages": ["Environment fit"],
                "tradeoffs": ["Heavier platform"],
                "operational_impact": "Uses established enterprise operations.",
                "why_engineers_care": "Existing skills and support models affect lifetime cost.",
                "engineering_concept": {"id": "platform-alignment", "label": "Platform alignment", "mental_model": "Fit the operating environment unless requirements justify divergence."},
                "reconsider_when": ["The environment is no longer Microsoft-centered."],
                "technical_details": [{"category": "Backend", "choice": ".NET", "consequence": "Aligns with Microsoft tooling."}],
            },
        ],
    }


def test_architecture_selection_persists_and_stale_comparison_is_rejected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    sid = create_session(client, "architecture-choice")
    save_tier(client, sid, "requirements", "# Requirements\n\nREQ-001 shared structured data with reliable updates.")

    async def fake_options(session, customization=None):
        return architecture_fixture(engine.architecture_source_sha256(session, customization))

    monkeypatch.setattr(engine, "architecture_options", fake_options)
    generated = client.post("/api/decisions/architecture/options", json={"session_id": sid})
    assert generated.status_code == 200
    options = generated.json()
    assert [item["role"] for item in options["options"]] == ["best_fit", "simplest", "alternative"]

    selected = client.post(
        "/api/decisions/architecture/select",
        json={
            "session_id": sid,
            "selected_option_id": "best",
            "options": options,
            "source_context_sha256": options["source_context_sha256"],
            "custom": {},
        },
    )
    assert selected.status_code == 200
    session = selected.json()["session"]
    assert session["engineering_decisions"][0]["selected_option_id"] == "best"

    save_tier(client, sid, "requirements", "# Requirements\n\nREQ-001 changed authoritative requirement.")
    stale = client.post(
        "/api/decisions/architecture/select",
        json={
            "session_id": sid,
            "selected_option_id": "best",
            "options": options,
            "source_context_sha256": options["source_context_sha256"],
            "custom": {},
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"] == "architecture_options_stale"


def test_execution_estimate_defaults_cost_optimized_and_forces_security_advanced(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    sid = create_session(client, "routing-estimate")
    save_tier(client, sid, "requirements", "# Requirements\n\nREQ-001 configure UI. REQ-002 protect authorization.")
    save_tier(client, sid, "solution", "# Solution\n\nSOL-001 config path. SOL-002 authorization boundary.")
    save_tier(client, sid, "tasks", "# Tasks\n\nT-001 config update.\nT-002 authorization update.")

    async def fake_profiles(session):
        return {
            "summary": "Two canonical tasks.",
            "profiles": [
                {
                    "task_id": "T-001",
                    "title": "Configuration",
                    "task_text": "Update one isolated configuration path.",
                    "requirement_ids": ["REQ-001"],
                    "solution_refs": ["SOL-001"],
                    "reasoning_complexity": "easy",
                    "context_breadth": "isolated",
                    "ambiguity": "low",
                    "blast_radius": "low",
                    "risk_flags": [],
                    "minimum_safe_capability": "local",
                    "validation": "pytest -q",
                    "bounded_context_refs": ["config.py"],
                },
                {
                    "task_id": "T-002",
                    "title": "Authorization",
                    "task_text": "Modify the authorization boundary for privileged access.",
                    "requirement_ids": ["REQ-002"],
                    "solution_refs": ["SOL-002"],
                    "reasoning_complexity": "medium",
                    "context_breadth": "subsystem",
                    "ambiguity": "medium",
                    "blast_radius": "high",
                    "risk_flags": ["authorization"],
                    "minimum_safe_capability": "standard",
                    "validation": "pytest -q",
                    "bounded_context_refs": ["auth.py", "tests/test_auth.py"],
                },
            ],
        }

    async def fake_discover(max_age_seconds=2.0):
        return {
            "lm_studio": SimpleNamespace(online=True, models=["unknown-local-coder"], configured=True),
            "ollama": SimpleNamespace(online=False, models=[], configured=True),
            "openai": SimpleNamespace(online=True, models=[], configured=True),
            "anthropic": SimpleNamespace(online=False, models=[], configured=False),
        }

    monkeypatch.setattr(engine, "task_execution_profiles", fake_profiles)
    monkeypatch.setattr(gateway, "discover", fake_discover)

    response = client.post(
        "/api/execution/estimate",
        json={
            "session_id": sid,
            "budget": 25,
            "escalation": "automatic",
            "budget_behavior": "stop_before_exceeding",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["recommended_strategy"] == "cost_optimized"
    assert set(body["plans"]) == {"cost_optimized", "balanced", "maximum_capability"}
    cost_plan = body["plans"]["cost_optimized"]
    by_id = {item["task_id"]: item for item in cost_plan["assignments"]}
    assert by_id["T-001"]["assigned_model"]["local"] is True
    assert by_id["T-002"]["minimum_safe_capability"] == "advanced"
    assert by_id["T-002"]["assigned_model"]["capability"] == "advanced"

    selected = client.post(
        "/api/execution/select",
        json={"session_id": sid, "strategy": "cost_optimized"},
    )
    assert selected.status_code == 200
    assert selected.json()["status"] == "SELECTED"
    assert selected.json()["selected_strategy"] == "cost_optimized"

    current = client.get(f"/api/execution/plan/{sid}")
    assert current.status_code == 200
    assert current.json()["selected_plan"]["strategy"] == "cost_optimized"

    save_tier(client, sid, "tasks", "# Tasks\n\nT-001 changed task scope.")
    after_change = client.get(f"/api/execution/plan/{sid}")
    assert after_change.status_code == 404


def test_concept_exposure_is_local_metadata_not_expertise(client: TestClient):
    db.record_concept_exposures([
        {"id": "relational-data", "label": "Relational data"},
        {"id": "relational-data", "label": "Relational data"},
    ])
    rows = db.list_concept_exposures()
    assert rows[0]["concept_id"] == "relational-data"
    assert rows[0]["exposure_count"] == 2
    assert rows[0]["user_marked_understood"] == 0
