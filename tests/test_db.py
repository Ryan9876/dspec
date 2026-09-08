from backend.dspec_app import db
from tests.helpers import valid_spec

def test_session_answers_and_spec_survive_new_connection():
    created = db.create_session("Persistent Project")
    sid = created["id"]
    db.save_answers(sid, "constitution", {"idea": "Build a governed local app", "delivery": "Local desktop/browser"})
    content = valid_spec("constitution")
    db.save_spec(sid, "constitution", content, 0.95, {"score": 0.95, "must_fix": [], "passing": ["ok"], "recommendations": []})
    loaded = db.get_session(sid)
    assert loaded["answers"]["constitution"]["idea"] == "Build a governed local app"
    assert loaded["specs"]["constitution"]["content"] == content
    assert loaded["specs"]["constitution"]["revision"] == 2

def test_edit_resets_approval():
    session = db.create_session("Approval Reset")
    sid = session["id"]
    content = valid_spec("requirements")
    db.save_spec(sid, "requirements", content, 0.96, {"score": 0.96, "must_fix": [], "passing": [], "recommendations": []})
    db.approve_spec(sid, "requirements")
    assert db.get_session(sid)["specs"]["requirements"]["approved"] is True
    db.save_spec(sid, "requirements", content + "\nAdditional explicit requirement.", 0.96, {"score": 0.96, "must_fix": [], "passing": [], "recommendations": []})
    assert db.get_session(sid)["specs"]["requirements"]["approved"] is False

def test_bundle_names_are_unique():
    a = db.create_session("same project")
    b = db.create_session("same project")
    assert a["bundle_name"] == "same-project"
    assert b["bundle_name"] == "same-project-2"
