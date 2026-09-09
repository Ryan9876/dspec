"""NFR-2.1 failure paths must be as private as successful key storage."""
import os
import stat
from pathlib import Path

import pytest

from dspec import security
from dspec.app import engine
from test_app import DRAFTS, client, create_session


SECRET = "credential-fixture-without-provider-prefix"


@pytest.mark.parametrize("payload", [
    {"provider": "openai", "model": "fixture", "api_key": {"value": SECRET}},
    [{"provider": "openai", "model": "fixture", "api_key": SECRET}],
    {"provider": SECRET, "model": "fixture", "api_key": "sk-irrelevant-fixture"},
])
def test_validation_never_echoes_credential_input(client, payload):
    r = client.post("/api/provider/select", json=payload)
    assert r.status_code == 422
    assert SECRET not in r.text
    assert "input" not in r.json()["detail"][0]


@pytest.mark.parametrize("route", ["generate", "revise", "review", "assist", "stream", "provider"])
def test_provider_exceptions_never_echo_or_persist_credential_text(client, monkeypatch, route):
    sid = create_session(client, "credential-failure-" + route)
    client.post("/api/spec/save", json={"session_id": sid, "stage": "requirements", "content": DRAFTS["requirements"]})
    async def failed(*args, **kwargs):
        raise RuntimeError("Provider rejected key " + SECRET)
    body = {"session_id": sid, "stage": "requirements"}
    if route == "provider":
        def failed_select(*args, **kwargs):
            raise RuntimeError("Storage failed for key " + SECRET)
        monkeypatch.setattr(engine.gateway, "select", failed_select)
        r = client.post("/api/provider/select", json={"provider": "openai", "model": "fixture", "api_key": SECRET})
    else:
        method = {"assist": "discover", "review": "semantic_review", "stream": "generate"}.get(route, route)
        monkeypatch.setattr(engine, method, failed)
        if route == "revise":
            body.update(content=DRAFTS["requirements"], instruction="Add recovery")
        path = "/api/assist/questions" if route == "assist" else "/api/spec/" + route
        r = client.post(path, json=body)
    assert SECRET not in r.text
    assert SECRET not in client.get(f"/api/sessions/{sid}").text


def test_fallback_temp_is_private_before_first_secret_write(client, monkeypatch):
    monkeypatch.setattr(security.platform, "system", lambda: "Linux")
    original_fdopen = os.fdopen
    observed = []
    def private_fdopen(fd, *args, **kwargs):
        observed.append(stat.S_IMODE(os.fstat(fd).st_mode))
        assert observed[-1] == 0o600
        return original_fdopen(fd, *args, **kwargs)
    monkeypatch.setattr(os, "fdopen", private_fdopen)
    security.store_api_key("openai", SECRET)
    assert observed == [0o600]
    assert stat.S_IMODE(security.config_path().stat().st_mode) == 0o600
    assert security.load_api_key("openai") == SECRET


def test_fallback_replace_failure_preserves_prior_key_and_cleans_temp(client, monkeypatch):
    monkeypatch.setattr(security.platform, "system", lambda: "Linux")
    security.store_api_key("openai", "previous-fixture-key")
    original = security.config_path().read_bytes()
    def fail_replace(*args, **kwargs):
        raise OSError("replacement failed")
    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        security.store_api_key("openai", SECRET)
    assert security.config_path().read_bytes() == original
    assert list(security.config_path().parent.glob(".config-*.tmp")) == []
