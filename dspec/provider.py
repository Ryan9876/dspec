from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, AsyncIterator

import httpx

from . import db
from .security import load_api_key, store_api_key

PROVIDERS = ("lm_studio", "ollama", "openai", "anthropic")
DEFAULT_MODELS = {
    "lm_studio": "local-model",
    "ollama": "llama3.3:latest",
    "openai": "gpt-5.6",
    "anthropic": "claude-sonnet-4-6",
}

@dataclass
class ProviderInfo:
    name: str
    online: bool
    models: list[str]
    configured: bool
    endpoint: str | None = None
    error: str | None = None


class ProviderGateway:
    def __init__(self) -> None:
        # Health and provider discovery are UI-critical loopback operations.
        # A missing local runtime must never stall the app health endpoint.
        self.timeout = httpx.Timeout(0.75, connect=0.25)
        self._discovery_cache: tuple[float, dict[str, ProviderInfo]] | None = None
        self._discovery_lock = asyncio.Lock()

    async def discover(self, max_age_seconds: float = 2.0) -> dict[str, ProviderInfo]:
        now = time.monotonic()
        cached = self._discovery_cache
        if cached and now - cached[0] <= max_age_seconds:
            return cached[1]
        async with self._discovery_lock:
            now = time.monotonic()
            cached = self._discovery_cache
            if cached and now - cached[0] <= max_age_seconds:
                return cached[1]
            lm, ollama = await asyncio.gather(self._probe_lm_studio(), self._probe_ollama())
            openai_key = load_api_key("openai")
            anthropic_key = load_api_key("anthropic")
            result = {
                "lm_studio": lm,
                "ollama": ollama,
                "openai": ProviderInfo("openai", bool(openai_key), [], bool(openai_key), "https://api.openai.com"),
                "anthropic": ProviderInfo("anthropic", bool(anthropic_key), [], bool(anthropic_key), "https://api.anthropic.com"),
            }
            self._discovery_cache = (time.monotonic(), result)
            return result

    def invalidate_discovery_cache(self) -> None:
        self._discovery_cache = None

    async def _probe_lm_studio(self) -> ProviderInfo:
        endpoint = os.environ.get("DSPEC_LM_STUDIO_URL", "http://127.0.0.1:1234")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(endpoint + "/v1/models")
                r.raise_for_status()
                models = [str(x.get("id")) for x in r.json().get("data", []) if x.get("id")]
                return ProviderInfo("lm_studio", True, models, True, endpoint)
        except Exception as exc:
            return ProviderInfo("lm_studio", False, [], True, endpoint, str(exc)[:160])

    async def _probe_ollama(self) -> ProviderInfo:
        endpoint = os.environ.get("DSPEC_OLLAMA_URL", "http://127.0.0.1:11434")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(endpoint + "/api/tags")
                r.raise_for_status()
                models = [str(x.get("name")) for x in r.json().get("models", []) if x.get("name")]
                return ProviderInfo("ollama", True, models, True, endpoint)
        except Exception as exc:
            return ProviderInfo("ollama", False, [], True, endpoint, str(exc)[:160])

    def selected(self) -> dict[str, str]:
        return db.get_setting("active_provider", {"provider": "lm_studio", "model": DEFAULT_MODELS["lm_studio"]})

    def select(self, provider: str, model: str, api_key: str | None = None) -> dict[str, str]:
        if provider not in PROVIDERS:
            raise ValueError("Unsupported provider")
        if not model.strip():
            raise ValueError("Model is required")
        storage = None
        if api_key:
            storage = store_api_key(provider, api_key)
        db.set_setting("active_provider", {"provider": provider, "model": model})
        self.invalidate_discovery_cache()
        return {"provider": provider, "model": model, "credential_storage": storage or "unchanged"}

    async def complete(self, messages: list[dict[str, str]], provider: str | None = None, model: str | None = None) -> tuple[str, dict[str, Any]]:
        selected = self.selected()
        provider = provider or selected["provider"]
        model = model or selected["model"]
        if os.environ.get("DSPEC_TEST_MODE") == "1" and provider == "fixture":
            text = os.environ.get("DSPEC_FIXTURE_RESPONSE", "# Fixture Spec\n\n## API Error Contracts\n- 422 invalid input.\n\n## Schema\n- id TEXT PRIMARY KEY\n\n## Verification\n`pytest -q`")
            return text, {"provider": "fixture", "model": "fixture", "latency_ms": 1}
        start = time.perf_counter()
        if provider == "lm_studio":
            base = os.environ.get("DSPEC_LM_STUDIO_URL", "http://127.0.0.1:1234")
            payload = {"model": model, "messages": messages, "stream": False, "temperature": 0.2}
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post(base + "/v1/chat/completions", json=payload)
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"]
        elif provider == "ollama":
            base = os.environ.get("DSPEC_OLLAMA_URL", "http://127.0.0.1:11434")
            payload = {"model": model, "messages": messages, "stream": False, "options": {"temperature": 0.2}}
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post(base + "/api/chat", json=payload)
                r.raise_for_status()
                text = r.json()["message"]["content"]
        elif provider == "openai":
            key = load_api_key("openai")
            if not key:
                raise RuntimeError("OpenAI API key is not configured")
            payload = {"model": model, "messages": messages, "stream": False, "temperature": 0.2}
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=payload)
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"]
        elif provider == "anthropic":
            key = load_api_key("anthropic")
            if not key:
                raise RuntimeError("Anthropic API key is not configured")
            system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
            anth = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in {"user", "assistant"}]
            payload = {"model": model, "system": system, "messages": anth, "max_tokens": 8192, "temperature": 0.2}
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post("https://api.anthropic.com/v1/messages", headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, json=payload)
                r.raise_for_status()
                text = "".join(x.get("text", "") for x in r.json().get("content", []) if x.get("type") == "text")
        else:
            raise ValueError("Unsupported provider")
        latency = int((time.perf_counter() - start) * 1000)
        return text, {"provider": provider, "model": model, "latency_ms": latency}


def public_discovery(items: dict[str, ProviderInfo]) -> dict[str, Any]:
    return {k: asdict(v) for k, v in items.items()}
