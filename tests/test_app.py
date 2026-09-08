from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dspec import db
from dspec.app import app, engine
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
    assert 0 <= body["health_score"]["composite_score"] <= 100
    assert body["assessment_scope"].startswith("Bounded static file evidence")


def test_stream_generation_preserves_contract(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    sid = create_session(client, "stream-test")

    async def fake_generate(session, stage, instructions=None):
        review = evaluate(stage, DRAFTS[stage])
        return DRAFTS[stage], {
            "provider": "fixture", "model": "fixture", "inference_latency_ms": 1,
            "tokens_generated": None, "tokens_per_second": None,
        }, review

    monkeypatch.setattr(engine, "generate", fake_generate)
    with client.stream("POST", "/api/spec/stream", json={"session_id": sid, "stage": "requirements"}) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "event: token" in text
    assert "event: complete" in text
    assert "event: assertion_check" in text
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["specs"]["requirements"]["content"] == DRAFTS["requirements"]


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
