import io, json, zipfile
import pytest
from backend.dspec_app import db
from backend.dspec_app.exporter import build_bundle
from tests.helpers import valid_spec

def seed(approved: bool):
    session = db.create_session("Export Project")
    for stage in db.STAGES:
        content = valid_spec(stage)
        db.save_spec(session["id"], stage, content, 0.96, {"score": 0.96, "must_fix": [], "passing": [], "recommendations": []})
        if approved: db.approve_spec(session["id"], stage)
    return session["id"]

def test_final_export_is_approval_gated():
    sid = seed(False)
    with pytest.raises(ValueError, match="not approved"):
        build_bundle(sid)

def test_draft_export_is_labeled_and_complete():
    sid = seed(False)
    data, manifest = build_bundle(sid, allow_draft=True)
    assert manifest["state"] == "draft"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        required = {
            "specs/export-project/constitution.md", "specs/export-project/requirements.md", "specs/export-project/solution.md", "specs/export-project/tasks.md",
            "CLAUDE.md", ".cursorrules", "CODEX_PROMPT.md", "CHATGPT_PROMPT.md", "bundle-manifest.json"
        }
        assert required <= names
        bundle_manifest = json.loads(archive.read("bundle-manifest.json"))
        assert bundle_manifest["generated_by"]["optimization"] == "unoptimized baseline"

def test_approved_export_state():
    sid = seed(True)
    data, manifest = build_bundle(sid)
    assert manifest["state"] == "approved"
    assert len(data) > 1000
