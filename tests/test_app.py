from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from dspec import db
from dspec.app import app, engine
import dspec.spec_engine as spec_engine_module
from dspec.quality import evaluate


DRAFTS = {
    "constitution": """# Constitution

## Security
Credentials and secrets must remain isolated from browser responses and authorization boundaries must be explicit.

## Runtime
The application must bind only to the governed local runtime and must fail predictably when required dependencies are unavailable.

## Quality and validation
Every material change must define tests, acceptance evidence, rollback considerations, and explicit NOT TESTED states where evidence does not exist.
""",
    "requirements": """# Requirements

## User workflow
The user can create a project, define all four specification tiers, review each draft, correct failures, and approve only complete work.

## Acceptance criteria
FR-1 must preserve saved state across refresh. Verification must confirm the stored revision after reload.

## Error and edge cases
Invalid input must return a clear error response without data loss. Provider failure must preserve current state and offer recovery.
""",
    "solution": """# Solution

## Components and API
A FastAPI component exposes POST /api/spec/save and returns an explicit 422 error response for invalid input.

## Typed schema
project_sessions.id TEXT PRIMARY KEY. spec_documents.revision_number INTEGER NOT NULL with a relationship to project_sessions.id.

## State and failure contracts
The client persists revisions transactionally. Status code 503 represents provider unavailability and must preserve the saved state.
""",
    "tasks": """# Tasks

1. Implement the governed session persistence required by FR-1.
   - Verification: `pytest -q`
2. Implement the provider failure recovery behavior required by FR-5.
   - Verification: `pytest -q`
3. Verify acceptance behavior and preserve existing security boundaries.
   - Verification: `python -m compileall -q dspec`
""",
}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DSPEC_HOME", str(tmp_path / "home"))
    db.init_db()
    with TestClient(app) as c:
        yield c


def create_session(client: TestClient, name: str = "test-project") -> str:
    r = client.post("/api/sessions", json={"bundle_name": name, "project_type": "greenfield"})
    assert r.status_code == 201
    return r.json()["id"]


def test_health_is_truthful_and_loopback_contract(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert body["host"] == "127.0.0.1"
    assert body["port"] == 3210
    assert body["database"] == "connected"
    assert body["dspy"]["optimization"] == "UNOPTIMIZED"


@pytest.mark.parametrize("stage", ["constitution", "requirements", "solution", "tasks"])
def test_quality_fixture_passes(stage: str):
    review = evaluate(stage, DRAFTS[stage])
    assert review["score"] >= 0.90
    assert review["passed"] is True


def test_transactional_four_tier_review_approval_and_export(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    async def semantic_pass(session, stage, content):
        structural = evaluate(stage, content)
        return {
            "structural": structural,
            "semantic": {"score": 0.97, "must_fix": [], "recommendations": [], "consistency_issues": []},
            "score": min(structural["score"], 0.97),
            "threshold": 0.90,
            "passed": structural["passed"],
            "semantic_status": "PASS",
            "provider": {"provider": "fixture", "model": "fixture"},
        }
    monkeypatch.setattr(engine, "semantic_review", semantic_pass)
    sid = create_session(client)
    for stage, content in DRAFTS.items():
        saved = client.post("/api/spec/save", json={"session_id": sid, "stage": stage, "content": content})
        assert saved.status_code == 200
        review = client.post("/api/spec/review", json={"session_id": sid, "stage": stage})
        assert review.status_code == 200
        assert review.json()["passed"] is True
        approved = client.post("/api/spec/approve", json={"session_id": sid, "stage": stage})
        assert approved.status_code == 200

    result = client.get(f"/api/export/{sid}")
    assert result.status_code == 200
    with zipfile.ZipFile(io.BytesIO(result.content)) as zf:
        names = set(zf.namelist())
        for stage in DRAFTS:
            assert f"specs/test-project/{stage}.md" in names
        assert {"CLAUDE.md", ".cursorrules", "CODEX_PROMPT.md", "CHATGPT_PROMPT.md", "bundle-manifest.json"} <= names


def test_approval_rejects_placeholder(client: TestClient):
    sid = create_session(client, "placeholder-test")
    bad = "# Solution\n\n## API\nTODO: define this later. " + ("incomplete " * 30)
    assert client.post("/api/spec/save", json={"session_id": sid, "stage": "solution", "content": bad}).status_code == 200
    result = client.post("/api/spec/approve", json={"session_id": sid, "stage": "solution"})
    assert result.status_code == 409


def test_ac2_solution_missing_error_contract_fails_quality_gate(client: TestClient):
    sid = create_session(client, "missing-error-contract")
    missing_errors = """# Solution

## Components and API
The FastAPI service exposes POST /api/projects and GET /api/projects/{project_id}. Requests use typed Pydantic interfaces.

## Typed schema
projects.id UUID PRIMARY KEY. projects.name TEXT NOT NULL. project_events.project_id UUID references projects.id with an index on project_id.

## State
Project writes are transactional and persisted in SQLite. Components have explicit interfaces and schemas.
"""
    saved = client.post(
        "/api/spec/save",
        json={"session_id": sid, "stage": "solution", "content": missing_errors},
    )
    assert saved.status_code == 200
    review = saved.json()["review"]
    assert review["passed"] is False
    assert any(item["id"] == "errors" for item in review["must_fix"])

    approval = client.post("/api/spec/approve", json={"session_id": sid, "stage": "solution"})
    assert approval.status_code == 409
    body = approval.json()["detail"]
    assert body["error"] == "quality_gate_failed"
    assert any(item["id"] == "errors" for item in body["review"]["must_fix"])


def test_answers_persist_on_readback(client: TestClient):
    sid = create_session(client, "answer-persist")
    saved = client.post("/api/answers", json={
        "session_id": sid, "stage": "requirements", "question_id": "q1",
        "selected_option_id": "recommended", "free_text_payload": "preserve this input"
    })
    assert saved.status_code == 200
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["answers"][0]["free_text_payload"] == "preserve this input"


def test_repository_audit_is_bounded_and_actionable(client: TestClient, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.md").write_text("# Requirements\nAcceptance criteria and verification.", encoding="utf-8")
    (repo / "solution.md").write_text("# Architecture\nAPI schema with error response.", encoding="utf-8")
    (repo / "tasks.md").write_text("# Tasks\n1. test verification", encoding="utf-8")
    (repo / "app.py").write_text("def typed(x: str) -> str:\n    return x\n", encoding="utf-8")
    (repo / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    hidden = repo / ".git"
    hidden.mkdir()
    (hidden / "secret").write_text("ignore", encoding="utf-8")
    r = client.post("/api/audit/scan", json={"repo_path": str(repo), "use_hash_cache": True})
    assert r.status_code == 200
    body = r.json()
    assert body["total_files_scanned"] == 5
    expected_dimensions = {
        "governance_security_score",
        "requirements_clarity_score",
        "architecture_consistency_score",
        "test_task_coverage_score",
    }
    assert expected_dimensions <= set(body["health_score"])
    assert all(0 <= body["health_score"][key] <= 100 for key in expected_dimensions)
    assert 0 <= body["health_score"]["composite_score"] <= 100
    assert body["scan_duration_ms"] <= 10_000
    assert body["structural_diff_md"]
    assert body["upgrade_spec_md"]
    assert body["assessment_scope"].startswith("Bounded static file evidence")


def test_stream_generation_preserves_contract_and_persists_only_final_selection(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    sid = create_session(client, "stream-test")
    provisional = "# Provisional requirements\n\nThis content must never become the formal draft."

    async def fake_generate_stream(session, stage, instructions=None):
        review = evaluate(stage, DRAFTS[stage])
        yield {"event": "candidate_start", "attempt": 1, "provisional": True}
        yield {"event": "token", "attempt": 1, "provisional": True, "text": provisional}
        yield {"event": "candidate_end", "attempt": 1, "provisional": True}
        yield {
            "event": "final",
            "content": DRAFTS[stage],
            "metrics": {
                "provider": "fixture",
                "model": "fixture",
                "inference_latency_ms": 12,
                "first_provisional_chunk_ms": 3,
                "tokens_generated": None,
                "tokens_per_second": None,
            },
            "review": review,
        }

    monkeypatch.setattr(engine, "generate_stream", fake_generate_stream)
    with client.stream("POST", "/api/spec/stream", json={"session_id": sid, "stage": "requirements"}) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())

    assert "event: candidate_start" in text
    assert "event: token" in text
    assert '"provisional":true' in text
    assert "event: candidate_selected" in text
    assert '"provisional":false' in text
    assert "event: complete" in text
    assert "event: assertion_check" in text
    assert '"first_provisional_chunk_ms":3' in text

    session = client.get(f"/api/sessions/{sid}").json()
    assert session["specs"]["requirements"]["content"] == DRAFTS["requirements"].strip()
    assert provisional not in session["specs"]["requirements"]["content"]


def test_stream_generation_failure_preserves_saved_state_and_offers_fallback(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    sid = create_session(client, "stream-failure")
    draft = DRAFTS["constitution"]
    assert client.post("/api/spec/draft", json={"session_id": sid, "stage": "constitution", "content": draft}).status_code == 200
    assert client.post("/api/answers", json={
        "session_id": sid,
        "stage": "constitution",
        "question_id": "assistant-constitution",
        "selected_option_id": "Recommend a safe default",
        "free_text_payload": "preserve this recovery context",
    }).status_code == 200

    async def failed_generate_stream(session, stage, instructions=None):
        if False:
            yield {}
        raise RuntimeError("LM Studio unavailable")

    monkeypatch.setattr(engine, "generate_stream", failed_generate_stream)
    with client.stream("POST", "/api/spec/stream", json={"session_id": sid, "stage": "constitution"}) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())

    assert "event: error" in text
    assert '"state_preserved":true' in text
    assert '"fallback_options":["lm_studio","ollama","openai","anthropic"]' in text
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["drafts"]["constitution"]["content"] == draft
    answer = next(item for item in session["answers"] if item["question_id"] == "assistant-constitution")
    assert answer["free_text_payload"] == "preserve this recovery context"


def test_stream_context_change_rejects_final_persistence_after_provisional_output(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    sid = create_session(client, "stream-conflict")

    async def fake_generate_stream(session, stage, instructions=None):
        review = evaluate(stage, DRAFTS[stage])
        yield {"event": "candidate_start", "attempt": 1, "provisional": True}
        yield {"event": "token", "attempt": 1, "provisional": True, "text": "# provisional"}
        db.save_answer(
            sid,
            stage,
            "changed-during-generation",
            "changed",
            "This changes the review/generation context.",
        )
        yield {
            "event": "final",
            "content": DRAFTS[stage],
            "metrics": {
                "provider": "fixture",
                "model": "fixture",
                "inference_latency_ms": 20,
                "first_provisional_chunk_ms": 2,
            },
            "review": review,
        }

    monkeypatch.setattr(engine, "generate_stream", fake_generate_stream)
    with client.stream("POST", "/api/spec/stream", json={"session_id": sid, "stage": "requirements"}) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())

    assert "event: token" in text
    assert "event: error" in text
    assert "event: candidate_selected" not in text
    session = client.get(f"/api/sessions/{sid}").json()
    assert "requirements" not in session["specs"]


def test_dspy_discovery_endpoint_returns_structured_mcq(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    sid = create_session(client, "assist-test")

    async def fake_discover(session, stage):
        return {
            "gaps_found": ["Authentication ownership is not explicit."],
            "questions": [{
                "id": "auth-owner",
                "question": "Who owns authentication?",
                "why_it_matters": "It changes trust boundaries.",
                "options": [
                    {"id": "local", "label": "Local only", "rationale": "Smallest trust surface."},
                    {"id": "external", "label": "External IdP", "rationale": "Centralized enterprise identity."},
                ],
                "recommended_option_id": "local",
                "allow_free_text": True,
            }],
        }

    monkeypatch.setattr(engine, "discover", fake_discover)
    r = client.post("/api/assist/questions", json={"session_id": sid, "stage": "constitution"})
    assert r.status_code == 200
    body = r.json()
    assert body["questions"][0]["recommended_option_id"] == "local"
    assert body["questions"][0]["allow_free_text"] is True


def test_trusted_host_rejects_external_host(client: TestClient):
    r = client.get("/api/health", headers={"host": "example.com"})
    assert r.status_code == 400


def test_review_unavailable_remains_not_tested(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    sid = create_session(client, "review-unavailable")
    client.post("/api/spec/save", json={"session_id": sid, "stage": "requirements", "content": DRAFTS["requirements"]})

    async def unavailable(session, stage, content):
        raise RuntimeError("provider offline")

    monkeypatch.setattr(engine, "semantic_review", unavailable)
    review = client.post("/api/spec/review", json={"session_id": sid, "stage": "requirements"})
    assert review.status_code == 200
    assert review.json()["semantic_status"] == "NOT TESTED"
    assert review.json()["passed"] is False
    approval = client.post("/api/spec/approve", json={"session_id": sid, "stage": "requirements"})
    assert approval.status_code == 409


def test_review_revision_applies_to_draft_without_creating_formal_revision(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    sid = create_session(client, "review-revision-apply")
    original = DRAFTS["requirements"]
    saved = client.post(
        "/api/spec/save",
        json={"session_id": sid, "stage": "requirements", "content": original},
    )
    assert saved.status_code == 200
    assert saved.json()["revision_number"] == 1

    revised = original + "\n\n## Applied review correction\nRecovery behavior is explicit."

    async def fake_revise(session, stage, content, instruction):
        assert session["id"] == sid
        assert stage == "requirements"
        assert content == original
        assert instruction == "Add explicit recovery behavior."
        return revised, {
            "provider": "lm_studio",
            "model": "fixture-model",
            "inference_latency_ms": 1,
            "tokens_generated": None,
            "tokens_per_second": None,
        }, {
            "score": 0.95,
            "threshold": 0.90,
            "passed": True,
            "passing": [{"id": "fixture", "label": "Recovery", "detail": "Recovery is explicit."}],
            "must_fix": [],
            "recommendations": [],
        }

    monkeypatch.setattr(engine, "revise", fake_revise)
    response = client.post(
        "/api/spec/revise",
        json={
            "session_id": sid,
            "stage": "requirements",
            "content": original,
            "instruction": "Add explicit recovery behavior.",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == revised
    assert body["formal_revision_created"] is False

    session = client.get(f"/api/sessions/{sid}").json()
    assert session["drafts"]["requirements"]["content"] == revised
    assert session["specs"]["requirements"]["revision_number"] == 1
    assert session["specs"]["requirements"]["content"] == original


def test_review_revision_failure_preserves_existing_state(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    sid = create_session(client, "review-revision-failure")
    original = DRAFTS["requirements"]
    client.post(
        "/api/spec/save",
        json={"session_id": sid, "stage": "requirements", "content": original},
    )
    client.post(
        "/api/spec/draft",
        json={"session_id": sid, "stage": "requirements", "content": "unsaved local draft"},
    )

    async def unavailable(session, stage, content, instruction):
        raise RuntimeError("provider offline")

    monkeypatch.setattr(engine, "revise", unavailable)
    response = client.post(
        "/api/spec/revise",
        json={
            "session_id": sid,
            "stage": "requirements",
            "content": "unsaved local draft",
            "instruction": "Apply review fix.",
        },
    )
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "revision_unavailable"
    assert detail["state_preserved"] is True

    session = client.get(f"/api/sessions/{sid}").json()
    assert session["drafts"]["requirements"]["content"] == "unsaved local draft"
    assert session["specs"]["requirements"]["revision_number"] == 1
    assert session["specs"]["requirements"]["content"] == original


def test_draft_buffer_survives_readback_without_revision_noise(client: TestClient):
    sid = create_session(client, "draft-buffer")
    first = client.post("/api/spec/draft", json={"session_id": sid, "stage": "solution", "content": "first live edit"})
    second = client.post("/api/spec/draft", json={"session_id": sid, "stage": "solution", "content": "second live edit"})
    assert first.status_code == 200 and second.status_code == 200
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["drafts"]["solution"]["content"] == "second live edit"
    assert "solution" not in session["specs"]

    formal = client.post("/api/spec/save", json={"session_id": sid, "stage": "solution", "content": DRAFTS["solution"]})
    assert formal.status_code == 200
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["specs"]["solution"]["revision_number"] == 1
    assert session["drafts"]["solution"]["content"] == DRAFTS["solution"]


def test_1000_file_audit_index_target(client: TestClient, tmp_path: Path):
    repo = tmp_path / "repo-1000"
    repo.mkdir()
    for index in range(1000):
        (repo / f"module_{index:04d}.py").write_text(
            f"def value_{index}(x: int) -> int:\n    return x + {index}\n",
            encoding="utf-8",
        )
    result = client.post("/api/audit/scan", json={"repo_path": str(repo), "use_hash_cache": False})
    assert result.status_code == 200
    body = result.json()
    assert body["total_files_scanned"] == 1000
    assert body["scan_duration_ms"] <= 3000
    assert body["limits"]["truncated"] is False
    assert "upgrade_spec_md" in body


def test_ac5_provider_switch_routes_subsequent_generation_without_restart(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    sid = create_session(client, "provider-switch")
    client.post(
        "/api/answers",
        json={
            "session_id": sid,
            "stage": "requirements",
            "question_id": "preserve",
            "selected_option_id": "yes",
            "free_text_payload": "keep this state across provider switches",
        },
    )

    routed_calls: list[tuple[str, str]] = []

    async def routed_generate(session, stage, instructions=None):
        selected = engine.gateway.selected()
        routed_calls.append((selected["provider"], selected["model"]))
        review = evaluate(stage, DRAFTS[stage])
        return DRAFTS[stage], {
            "provider": selected["provider"],
            "model": selected["model"],
            "inference_latency_ms": 1,
            "tokens_generated": None,
            "tokens_per_second": None,
        }, review

    monkeypatch.setattr(engine, "generate", routed_generate)

    local_select = client.post(
        "/api/provider/select",
        json={"provider": "lm_studio", "model": "local-validation-model"},
    )
    assert local_select.status_code == 200
    first = client.post(
        "/api/spec/generate",
        json={"session_id": sid, "stage": "requirements"},
    )
    assert first.status_code == 200
    assert first.json()["metrics"]["provider"] == "lm_studio"
    assert first.json()["metrics"]["model"] == "local-validation-model"

    cloud_select = client.post(
        "/api/provider/select",
        json={"provider": "openai", "model": "cloud-validation-model"},
    )
    assert cloud_select.status_code == 200
    second = client.post(
        "/api/spec/generate",
        json={"session_id": sid, "stage": "requirements"},
    )
    assert second.status_code == 200
    assert second.json()["metrics"]["provider"] == "openai"
    assert second.json()["metrics"]["model"] == "cloud-validation-model"

    assert routed_calls == [
        ("lm_studio", "local-validation-model"),
        ("openai", "cloud-validation-model"),
    ]

    persisted = client.get(f"/api/sessions/{sid}")
    assert persisted.status_code == 200
    session = persisted.json()
    assert session["answers"][0]["free_text_payload"] == "keep this state across provider switches"
    assert session["specs"]["requirements"]["revision_number"] == 2


def test_provider_secret_is_not_echoed_in_validation_or_response(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    secret = "sk-super-secret-value-1234567890"

    def fake_select(provider, model, api_key=None):
        assert api_key == secret
        return {"provider": provider, "model": model, "credential_storage": "fixture"}

    monkeypatch.setattr("dspec.app.gateway.select", fake_select)
    result = client.post("/api/provider/select", json={"provider": "openai", "model": "gpt-5.6", "api_key": secret})
    assert result.status_code == 200
    assert secret not in result.text
    assert "api_key" not in result.text.lower()


def test_sanitizer_redacts_common_api_key_shapes():
    from dspec.security import sanitize

    samples = [
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "api_key=sk-abcdefghijklmnopqrstuvwxyz123456",
        "provider key sk-ant-abcdefghijklmnopqrstuvwxyz123456",
    ]
    for sample in samples:
        cleaned = sanitize(sample)
        assert "[REDACTED_API_KEY]" in cleaned
        assert "abcdefghijklmnopqrstuvwxyz123456" not in cleaned


def test_constitution_generation_requires_saved_product_intent(client: TestClient):
    sid = create_session(client, "missing-product-intent")
    response = client.post("/api/spec/stream", json={"session_id": sid, "stage": "constitution"})
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "product_intent_required"


def test_assist_persists_generated_question_context(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    sid = create_session(client, "discovery-context")
    client.post("/api/answers", json={
        "session_id": sid,
        "stage": "constitution",
        "question_id": "assistant-constitution",
        "selected_option_id": "Local-first / private by default",
        "free_text_payload": "Build a simple local inventory tracker.",
    })

    async def fake_discover(session, stage):
        return {
            "gaps_found": ["Backup behavior is not defined."],
            "questions": [{
                "id": "backup",
                "question": "Should the user be able to export a backup?",
                "why_it_matters": "It determines recovery behavior.",
                "options": [
                    {"id": "yes", "label": "Yes, manual export", "rationale": "Keeps recovery user-controlled."},
                    {"id": "no", "label": "No export", "rationale": "Smallest feature surface."},
                ],
                "recommended_option_id": "yes",
                "allow_free_text": True,
            }],
        }

    monkeypatch.setattr(engine, "discover", fake_discover)
    response = client.post("/api/assist/questions", json={"session_id": sid, "stage": "constitution"})
    assert response.status_code == 200
    session = client.get(f"/api/sessions/{sid}").json()
    context = next(item for item in session["answers"] if item["question_id"] == "discovery-context-constitution")
    payload = json.loads(context["free_text_payload"])
    assert payload["questions"][0]["options"][0]["label"] == "Yes, manual export"



def _save_root_intent(
    client: TestClient,
    sid: str,
    *,
    choice: str = "Recommend a safe default",
    text: str = "Create a simple number generator",
):
    return client.post(
        "/api/answers",
        json={
            "session_id": sid,
            "stage": "constitution",
            "question_id": "assistant-constitution",
            "selected_option_id": choice,
            "free_text_payload": text,
        },
    )


def test_root_intent_change_retires_active_context_preserves_history_and_blocks_stale_writes(
    client: TestClient,
):
    sid = create_session(client, "intent-reset")

    first = _save_root_intent(client, sid)
    assert first.status_code == 200
    assert first.json()["intent_invalidated"] is False
    session = client.get(f"/api/sessions/{sid}").json()
    old_intent_hash = session["intent_context_sha256"]

    assert client.post(
        "/api/answers",
        json={
            "session_id": sid,
            "stage": "constitution",
            "question_id": "old-choice",
            "selected_option_id": "board",
            "free_text_payload": "Use the stale board model.",
        },
    ).status_code == 200
    assert client.post(
        "/api/spec/save",
        json={
            "session_id": sid,
            "stage": "constitution",
            "content": DRAFTS["constitution"],
            "expected_intent_sha256": old_intent_hash,
        },
    ).status_code == 200
    assert client.post(
        "/api/spec/save",
        json={
            "session_id": sid,
            "stage": "requirements",
            "content": DRAFTS["requirements"],
            "expected_intent_sha256": old_intent_hash,
        },
    ).status_code == 200

    same = _save_root_intent(client, sid)
    assert same.status_code == 200
    assert same.json()["intent_invalidated"] is False
    same_session = client.get(f"/api/sessions/{sid}").json()
    assert "constitution" in same_session["specs"]
    assert "requirements" in same_session["specs"]

    changed = _save_root_intent(
        client,
        sid,
        text="Create a simple dice roller for one local user",
    )
    assert changed.status_code == 200
    assert changed.json()["intent_invalidated"] is True
    assert changed.json()["invalidated_stages"] == [
        "constitution",
        "requirements",
        "solution",
        "tasks",
    ]

    session = client.get(f"/api/sessions/{sid}").json()
    new_intent_hash = session["intent_context_sha256"]
    assert new_intent_hash != old_intent_hash
    assert session["specs"] == {}
    assert session["drafts"] == {}
    assert [answer["question_id"] for answer in session["answers"]] == [
        "assistant-constitution"
    ]
    assert session["answers"][0]["free_text_payload"] == "Create a simple dice roller for one local user"

    with db.connect() as conn:
        historical = conn.execute(
            "SELECT spec_type,content FROM spec_documents WHERE session_id=? ORDER BY spec_type",
            (sid,),
        ).fetchall()
    assert {row["spec_type"] for row in historical} == {"constitution", "requirements"}

    stale = client.post(
        "/api/spec/draft",
        json={
            "session_id": sid,
            "stage": "constitution",
            "content": "stale in-flight editor write",
            "expected_intent_sha256": old_intent_hash,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"] == "project_state_changed"

    fresh = client.post(
        "/api/spec/draft",
        json={
            "session_id": sid,
            "stage": "constitution",
            "content": "fresh post-reset draft",
            "expected_intent_sha256": new_intent_hash,
        },
    )
    assert fresh.status_code == 200

    fresh_spec = client.post(
        "/api/spec/save",
        json={
            "session_id": sid,
            "stage": "constitution",
            "content": DRAFTS["constitution"],
            "expected_intent_sha256": new_intent_hash,
        },
    )
    assert fresh_spec.status_code == 200
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["specs"]["constitution"]["revision_number"] == 2


def test_operating_boundary_change_invalidates_active_state(client: TestClient):
    sid = create_session(client, "intent-boundary-reset")
    first = _save_root_intent(
        client,
        sid,
        choice="Local-first / private by default",
        text="Create a simple number generator",
    )
    assert first.status_code == 200
    session = client.get(f"/api/sessions/{sid}").json()
    intent_hash = session["intent_context_sha256"]

    assert client.post(
        "/api/spec/save",
        json={
            "session_id": sid,
            "stage": "constitution",
            "content": DRAFTS["constitution"],
            "expected_intent_sha256": intent_hash,
        },
    ).status_code == 200

    changed = _save_root_intent(
        client,
        sid,
        choice="Cloud-capable with explicit consent",
        text="Create a simple number generator",
    )
    assert changed.status_code == 200
    assert changed.json()["intent_invalidated"] is True
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["specs"] == {}


def test_discovery_inputs_after_intent_reset_exclude_stale_answers_and_drafts(
    client: TestClient,
):
    sid = create_session(client, "discovery-clean-context")
    _save_root_intent(client, sid, text="Build the old product")
    session = client.get(f"/api/sessions/{sid}").json()
    old_hash = session["intent_context_sha256"]

    assert client.post(
        "/api/answers",
        json={
            "session_id": sid,
            "stage": "constitution",
            "question_id": "old-choice",
            "selected_option_id": "opt_gov_board",
            "free_text_payload": "STALE GOVERNING BOARD",
        },
    ).status_code == 200
    assert client.post(
        "/api/spec/draft",
        json={
            "session_id": sid,
            "stage": "constitution",
            "content": "STALE DRAFT " * 600,
            "expected_intent_sha256": old_hash,
        },
    ).status_code == 200

    _save_root_intent(client, sid, text="Create a simple number generator")
    session = client.get(f"/api/sessions/{sid}").json()
    inputs = engine._discovery_inputs(session, "constitution")

    assert "Project intent:\nCreate a simple number generator" in inputs["current_answers"]
    assert "assistant-constitution" not in inputs["current_answers"]
    assert "opt_gov_board" not in inputs["current_answers"]
    assert "STALE GOVERNING BOARD" not in inputs["current_answers"]
    assert "STALE DRAFT" not in inputs["current_answers"]


def test_discovery_uses_bounded_budget_and_reports_diagnostics(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    import dspy

    sid = create_session(client, "discovery-budget")
    _save_root_intent(client, sid, text="Create a simple number generator")
    session = client.get(f"/api/sessions/{sid}").json()

    selected = {"provider": "lm_studio", "model": "fixture-model"}
    lm_calls: list[dict[str, object]] = []

    async def fake_selected(_gateway):
        return selected

    def fake_make_lm(provider, model, **kwargs):
        lm_calls.append({"provider": provider, "model": model, **kwargs})
        return dspy.LM("openai/fixture-model", api_key="fixture")

    async def fake_run(**inputs):
        assert len(inputs["prior_tiers"]) <= spec_engine_module.DISCOVERY_MAX_PRIOR_CHARS + 80
        assert len(inputs["current_answers"]) <= (
            spec_engine_module.DISCOVERY_MAX_INTENT_CHARS
            + spec_engine_module.DISCOVERY_MAX_ANSWERS_CHARS
            + spec_engine_module.DISCOVERY_MAX_DRAFT_CHARS
            + 260
        )
        return SimpleNamespace(
            discovery={
                "gaps_found": ["Range is not specified."],
                "questions": [
                    {
                        "id": "range",
                        "question": "What number range should be used?",
                        "why_it_matters": "It defines the generator output.",
                        "options": [
                            {"id": "1-10", "label": "1 to 10", "rationale": "Simple default."},
                            {"id": "1-100", "label": "1 to 100", "rationale": "Broader range."},
                        ],
                        "recommended_option_id": "1-10",
                        "allow_free_text": True,
                    }
                ],
            }
        )

    monkeypatch.setattr(spec_engine_module, "selected_for_inference", fake_selected)
    monkeypatch.setattr(spec_engine_module, "make_lm", fake_make_lm)
    monkeypatch.setattr(dspy, "asyncify", lambda _program: fake_run)

    result = __import__("asyncio").run(engine.discover(session, "constitution"))

    assert lm_calls == [{
        "provider": "lm_studio",
        "model": "fixture-model",
        "max_tokens": spec_engine_module.DISCOVERY_MAX_OUTPUT_TOKENS,
        "temperature": 0.0,
        "num_retries": 1,
    }]
    assert result["diagnostics"]["provider"] == "lm_studio"
    assert result["diagnostics"]["model"] == "fixture-model"
    assert result["diagnostics"]["max_output_tokens"] == spec_engine_module.DISCOVERY_MAX_OUTPUT_TOKENS
    assert result["diagnostics"]["request_latency_ms"] >= 0



def test_discovery_schema_bounds_questions_gaps_and_options():
    from pydantic import ValidationError
    from dspec.dspy_signatures import DiscoveryResult

    option = {"id": "a", "label": "A", "rationale": "Short rationale."}
    question = {
        "id": "q",
        "question": "Choose one?",
        "why_it_matters": "It affects the result.",
        "options": [
            option,
            {"id": "b", "label": "B", "rationale": "Alternative."},
        ],
        "recommended_option_id": "a",
        "allow_free_text": True,
    }

    with pytest.raises(ValidationError):
        DiscoveryResult(questions=[question] * 4, gaps_found=[])

    with pytest.raises(ValidationError):
        DiscoveryResult(questions=[], gaps_found=["g1", "g2", "g3", "g4"])

    too_many_options = {
        **question,
        "options": [
            option,
            {"id": "b", "label": "B", "rationale": "Alternative."},
            {"id": "c", "label": "C", "rationale": "Alternative."},
            {"id": "d", "label": "D", "rationale": "Alternative."},
        ],
    }
    with pytest.raises(ValidationError):
        DiscoveryResult(questions=[too_many_options], gaps_found=[])
