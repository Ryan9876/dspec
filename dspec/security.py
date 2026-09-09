from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .config import config_path

SERVICE_PREFIX = "dspec.ai"
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"Bearer\s+[^\s]+", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[=:]\s*)[^\s,;]+", re.IGNORECASE),
]


def sanitize(text: str) -> str:
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED_API_KEY]", out)
    return out


def _read_fallback() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        # Never follow a credential-file symlink, and repair permissions before
        # reading legacy fallback files created with broader access.
        if path.is_symlink() or not path.is_file():
            return {}
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            os.chmod(path, 0o600)
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_fallback(data: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp creates mode 0600 atomically, before any credentials are written.
    # A unique name also avoids following a pre-existing temporary-file symlink.
    fd, temporary = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(data, indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def store_api_key(provider: str, api_key: str) -> str:
    if not api_key or len(api_key.strip()) < 8:
        raise ValueError("API key is empty or too short")
    account = f"{SERVICE_PREFIX}.{provider}"
    if platform.system() == "Darwin":
        proc = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", account, "-a", provider, "-w", api_key],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return "macos_keychain"
        raise RuntimeError(f"macOS Keychain write failed: {sanitize(proc.stderr.strip())}")
    data = _read_fallback()
    secrets = data.setdefault("secrets", {})
    secrets[provider] = api_key
    _write_fallback(data)
    return "protected_file_0600"


def load_api_key(provider: str) -> str | None:
    account = f"{SERVICE_PREFIX}.{provider}"
    if platform.system() == "Darwin":
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", account, "-a", provider, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else None
    return _read_fallback().get("secrets", {}).get(provider)


def delete_api_key(provider: str) -> None:
    account = f"{SERVICE_PREFIX}.{provider}"
    if platform.system() == "Darwin":
        subprocess.run(["security", "delete-generic-password", "-s", account, "-a", provider], capture_output=True, check=False)
        return
    data = _read_fallback()
    data.get("secrets", {}).pop(provider, None)
    _write_fallback(data)
