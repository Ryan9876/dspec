from __future__ import annotations

from pathlib import Path

import pytest

from dspec import db


def test_intent_context_hash_is_stable_until_root_intent_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("DSPEC_HOME", str(tmp_path / "home"))
    db.init_db()
    session = db.create_session("intent-hash")
    sid = session["id"]
    empty_hash = session["intent_context_sha256"]

    first = db.save_answer(
        sid,
        "constitution",
        "assistant-constitution",
        "Recommend a safe default",
        "Create a simple number generator",
    )
    assert first["intent_invalidated"] is False
    first_hash = db.get_session(sid)["intent_context_sha256"]
    assert first_hash != empty_hash

    same = db.save_answer(
        sid,
        "constitution",
        "assistant-constitution",
        "Recommend a safe default",
        "Create a simple number generator",
    )
    assert same["intent_invalidated"] is False
    assert db.get_session(sid)["intent_context_sha256"] == first_hash

    changed = db.save_answer(
        sid,
        "constitution",
        "assistant-constitution",
        "Local-first / private by default",
        "Create a simple number generator",
    )
    assert changed["intent_invalidated"] is True
    assert db.get_session(sid)["intent_context_sha256"] != first_hash
