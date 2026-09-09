from __future__ import annotations

import asyncio

import pytest

from dspec.dspy_runtime import make_lm
from dspec.network_policy import local_endpoint, validate_local_endpoint
from dspec.provider import ProviderGateway


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://127.0.0.1:1234", "http://127.0.0.1:1234"),
        ("http://localhost:11434/", "http://localhost:11434"),
        ("http://[::1]:1234", "http://[::1]:1234"),
    ],
)
def test_local_endpoint_accepts_only_normalized_loopback(raw: str, expected: str):
    assert validate_local_endpoint(raw, "local-test") == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://127.0.0.1:1234",
        "http://example.com:1234",
        "http://10.0.0.5:1234",
        "http://127.0.0.1:1234/v1",
        "http://127.0.0.1:1234?target=remote",
        "http://user@127.0.0.1:1234",
    ],
)
def test_local_endpoint_rejects_non_loopback_or_ambiguous_urls(raw: str):
    with pytest.raises(ValueError):
        validate_local_endpoint(raw, "local-test")


def test_environment_override_cannot_turn_local_provider_remote(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DSPEC_LM_STUDIO_URL", "http://example.com:1234")
    with pytest.raises(ValueError, match="loopback-only"):
        local_endpoint("lm_studio")


def test_provider_probe_fails_closed_before_http_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DSPEC_LM_STUDIO_URL", "http://example.com:1234")

    class UnexpectedClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("HTTP client must not be created for a rejected local endpoint")

    monkeypatch.setattr("dspec.provider.httpx.AsyncClient", UnexpectedClient)
    info = asyncio.run(ProviderGateway()._probe_lm_studio())
    assert info.online is False
    assert info.endpoint is None
    assert info.error and "loopback-only" in info.error


def test_provider_completion_rejects_remote_local_override_before_network(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DSPEC_OLLAMA_URL", "http://remote.example:11434")
    gateway = ProviderGateway()
    gateway.selected = lambda: {"provider": "ollama", "model": "fixture-model"}  # type: ignore[method-assign]

    class UnexpectedClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("HTTP client must not be created for a rejected local endpoint")

    monkeypatch.setattr("dspec.provider.httpx.AsyncClient", UnexpectedClient)
    with pytest.raises(ValueError, match="loopback-only"):
        asyncio.run(
            gateway.complete(
                [{"role": "user", "content": "private specification text"}],
                provider="ollama",
                model="fixture-model",
            )
        )


def test_dspy_runtime_rejects_remote_local_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DSPEC_LM_STUDIO_URL", "http://remote.example:1234")
    with pytest.raises(ValueError, match="loopback-only"):
        make_lm("lm_studio", "fixture-model")
