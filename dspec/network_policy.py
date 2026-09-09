from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

_DEFAULTS = {
    "lm_studio": "http://127.0.0.1:1234",
    "ollama": "http://127.0.0.1:11434",
}
_ENV = {
    "lm_studio": "DSPEC_LM_STUDIO_URL",
    "ollama": "DSPEC_OLLAMA_URL",
}
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def validate_local_endpoint(value: str, provider: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "http":
        raise ValueError(f"{provider} endpoint must use http.")
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK:
        raise ValueError(f"{provider} endpoint must be loopback-only.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{provider} endpoint contains unsupported URL components.")
    if parsed.path.rstrip("/"):
        raise ValueError(f"{provider} endpoint must not contain a path.")
    netloc = f"[{host}]" if host == "::1" else host
    if parsed.port is not None:
        netloc += f":{parsed.port}"
    return urlunsplit(("http", netloc, "", "", ""))


def local_endpoint(provider: str) -> str:
    if provider not in _DEFAULTS:
        raise ValueError(f"Unsupported local provider: {provider}")
    raw = os.environ.get(_ENV[provider], _DEFAULTS[provider])
    return validate_local_endpoint(raw, provider)
