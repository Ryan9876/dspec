"""Exercise the real DSPy path with deterministic responses; no live inference."""
import json

import pytest
from dspy.utils import DummyLM

from dspec import db
from dspec.app import engine
from test_app import DRAFTS, client, create_session


QUALITY = {
    "has_no_ambiguous_todos": True, "has_typed_schemas": True,
    "has_error_contracts": True, "agent_executable": True, "rubric_score": 0.97,
}


@pytest.fixture(autouse=True)
def isolate_live_provider_readiness(monkeypatch):
    async def selected(gateway):
        return gateway.selected()
    monkeypatch.setattr("dspec.spec_engine.selected_for_inference", selected)


def use_review(monkeypatch, **changes):
    result = {"score": 0.97, "must_fix": [], "recommendations": [], "consistency_issues": [], **changes}
    lm = DummyLM([{"review": json.dumps(result)}] * 20)
    monkeypatch.setattr(engine, "_lm", lambda selected: lm)
    return lm


def save(client, sid, stage, content=None):
    response = client.post("/api/spec/save", json={"session_id": sid, "stage": stage, "content": content or DRAFTS[stage]})
    assert response.status_code == 200
    return response.json()


def review(client, sid, stage):
    return client.post("/api/spec/review", json={"session_id": sid, "stage": stage})


def approve(client, sid, stage):
    return client.post("/api/spec/approve", json={"session_id": sid, "stage": stage})


def test_revision_runs_actual_dspy_signature_and_refine(client, monkeypatch):
    sid = create_session(client, "real-dspy-revise")
    save(client, sid, "constitution")
    original = save(client, sid, "requirements")
    revised = DRAFTS["requirements"] + "\n\n## Recovery\nRetry preserves the saved input."
    calls = []
    class TrackedLM(DummyLM):
        def forward(self, prompt=None, messages=None, **kwargs):
            # Refine copies the LM per attempt, so record calls across copies.
            calls.append(messages)
            return super().forward(prompt=prompt, messages=messages, **kwargs)
    lm = TrackedLM([{"reasoning": "Preserve the governing constraints.", "revised_spec": revised, "quality_assessment": json.dumps(QUALITY)}])
    monkeypatch.setattr(engine, "_lm", lambda selected: lm)
    result = client.post("/api/spec/revise", json={"session_id": sid, "stage": "requirements", "content": original["content"], "instruction": "Add retry recovery."})
    assert result.status_code == 200, result.text
    assert len(calls) == 1
    assert "Add retry recovery." in calls[0][-1]["content"]
    assert DRAFTS["constitution"].strip() in calls[0][-1]["content"]
    assert result.json()["content"] == revised
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["drafts"]["requirements"]["content"] == revised
    assert session["specs"]["requirements"]["id"] == original["id"]
    assert session["specs"]["requirements"]["approval_status"] == "draft"


def test_cross_tier_contradiction_blocks_approval_even_with_high_score(client, monkeypatch):
    use_review(monkeypatch, consistency_issues=["Solution exposes the private local data remotely."])
    sid = create_session(client, "contradiction")
    save(client, sid, "solution")
    result = review(client, sid, "solution")
    assert result.status_code == 200
    assert result.json()["passed"] is False
    assert result.json()["semantic_status"] == "FAIL"
    assert any("private local data" in item["detail"] for item in result.json()["must_fix"])
    assert approve(client, sid, "solution").status_code == 409


@pytest.mark.parametrize("mutation", ["formal", "draft", "answers"])
def test_upstream_changes_invalidate_downstream_review_and_approved_export(client, monkeypatch, mutation):
    use_review(monkeypatch)
    sid = create_session(client, "context-" + mutation)
    for stage in db.STAGES:
        save(client, sid, stage)
        assert review(client, sid, stage).json()["passed"] is True
        assert approve(client, sid, stage).status_code == 200
    assert client.get(f"/api/export/{sid}").status_code == 200
    if mutation == "answers":
        response = client.post("/api/answers", json={"session_id": sid, "stage": "constitution", "question_id": "runtime", "free_text_payload": "Change the required host."})
    else:
        route = "/api/spec/save" if mutation == "formal" else "/api/spec/draft"
        response = client.post(route, json={"session_id": sid, "stage": "constitution", "content": DRAFTS["constitution"] + "\nRequire a new runtime boundary."})
    assert response.status_code == 200
    state = client.get(f"/api/sessions/{sid}").json()
    assert state["specs"]["tasks"]["review"]["semantic_status"] == "STALE"
    assert state["specs"]["tasks"]["approval_status"] == "draft"
    assert approve(client, sid, "tasks").status_code == 409
    assert client.get(f"/api/export/{sid}").status_code == 409
    assert client.get(f"/api/export/{sid}?allow_draft=true").status_code == 200


def test_unsaved_edit_cannot_approve_a_previous_revision(client, monkeypatch):
    use_review(monkeypatch)
    sid = create_session(client, "edited-after-review")
    save(client, sid, "constitution")
    assert review(client, sid, "constitution").json()["passed"]
    client.post("/api/spec/draft", json={"session_id": sid, "stage": "constitution", "content": DRAFTS["constitution"] + "\nNew constraints."})
    assert approve(client, sid, "constitution").status_code == 409
    assert review(client, sid, "constitution").status_code == 409


def test_review_result_cannot_commit_after_concurrent_edit(client, monkeypatch):
    sid = create_session(client, "edit-during-review")
    original = save(client, sid, "requirements")
    async def delayed_review(session, stage, content):
        db.save_spec(sid, stage, content + "\nConcurrent replacement.")
        return {"score": 0.97, "passed": True, "semantic_status": "PASS"}
    monkeypatch.setattr(engine, "semantic_review", delayed_review)
    result = review(client, sid, "requirements")
    assert result.status_code == 409
    state = client.get(f"/api/sessions/{sid}").json()
    assert state["specs"]["requirements"]["id"] != original["id"]
    assert state["specs"]["requirements"]["approval_status"] == "draft"
    assert approve(client, sid, "requirements").status_code == 409


def test_revision_result_cannot_overwrite_a_concurrent_draft(client, monkeypatch):
    sid = create_session(client, "edit-during-revise")
    save(client, sid, "requirements")
    async def delayed_revise(session, stage, content, instruction):
        db.save_draft_buffer(sid, stage, "Newer user work")
        return content + "\nStale AI correction.", {}, {"score": 0.95}
    monkeypatch.setattr(engine, "revise", delayed_revise)
    result = client.post("/api/spec/revise", json={"session_id": sid, "stage": "requirements", "content": DRAFTS["requirements"], "instruction": "Add retry recovery."})
    assert result.status_code == 409
    assert client.get(f"/api/sessions/{sid}").json()["drafts"]["requirements"]["content"] == "Newer user work"


def test_failed_rereview_revokes_existing_approval(client, monkeypatch):
    sid = create_session(client, "failed-rereview")
    use_review(monkeypatch)
    save(client, sid, "constitution")
    assert review(client, sid, "constitution").json()["passed"]
    assert approve(client, sid, "constitution").status_code == 200
    use_review(monkeypatch, must_fix=["Security boundary is insufficient."])
    assert review(client, sid, "constitution").json()["passed"] is False
    assert client.get(f"/api/sessions/{sid}").json()["specs"]["constitution"]["approval_status"] == "draft"


def test_same_content_autosave_and_later_tier_edits_preserve_valid_review(client, monkeypatch):
    use_review(monkeypatch)
    sid = create_session(client, "preserve-current-evidence")
    save(client, sid, "constitution")
    assert review(client, sid, "constitution").json()["passed"]
    assert approve(client, sid, "constitution").status_code == 200
    client.post("/api/spec/draft", json={"session_id": sid, "stage": "constitution", "content": DRAFTS["constitution"]})
    save(client, sid, "tasks")
    state = client.get(f"/api/sessions/{sid}").json()
    assert state["specs"]["constitution"]["review"]["semantic_status"] == "PASS"
    assert state["specs"]["constitution"]["approval_status"] == "approved"


def test_legacy_unbound_reviews_require_new_review_without_losing_content(client):
    sid = create_session(client, "legacy-evidence")
    spec = save(client, sid, "constitution")
    db.update_spec_review(spec["id"], 0.97, {"passed": True, "semantic_status": "PASS", "score": 0.97}, "approved")
    db.init_db()
    state = client.get(f"/api/sessions/{sid}").json()
    assert state["specs"]["constitution"]["content"] == DRAFTS["constitution"]
    assert state["specs"]["constitution"]["id"] == spec["id"]
    assert state["specs"]["constitution"]["approval_status"] == "draft"
    assert state["specs"]["constitution"]["review"]["semantic_status"] == "STALE"
    assert approve(client, sid, "constitution").status_code == 409


def test_generation_result_cannot_overwrite_concurrent_user_work(client, monkeypatch):
    sid = create_session(client, "generation-conflict")
    async def delayed_generate(session, stage, instructions):
        db.save_draft_buffer(sid, stage, "Concurrent user edit")
        return DRAFTS[stage], {}, {"score": 0.97}
    monkeypatch.setattr(engine, "generate", delayed_generate)
    result = client.post("/api/spec/generate", json={"session_id": sid, "stage": "requirements"})
    assert result.status_code == 409
    state = client.get(f"/api/sessions/{sid}").json()
    assert state["drafts"]["requirements"]["content"] == "Concurrent user edit"
    assert "requirements" not in state["specs"]
