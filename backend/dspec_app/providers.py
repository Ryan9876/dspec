from __future__ import annotations

import json
import os
import platform
import stat
import subprocess
from pathlib import Path

import httpx

PROVIDERS = ("lm_studio", "ollama", "openai", "anthropic")
LOCAL_ENDPOINTS = {"lm_studio": "http://127.0.0.1:1234", "ollama": "http://127.0.0.1:11434"}
CLOUD_ENDPOINTS = {"openai": "https://api.openai.com", "anthropic": "https://api.anthropic.com"}


def config_path() -> Path:
    path = Path(os.environ.get("DSPEC_CONFIG_PATH", "~/.dspec/config.json")).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def credential_path() -> Path:
    path = Path(os.environ.get("DSPEC_CREDENTIAL_PATH", "~/.dspec/credentials.json")).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_private(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        return {"active_provider": "lm_studio", "active_model": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"active_provider": "lm_studio", "active_model": ""}
    provider = data.get("active_provider")
    if provider not in PROVIDERS:
        provider = "lm_studio"
    return {"active_provider": provider, "active_model": str(data.get("active_model", ""))}


def save_config(provider: str, model: str) -> None:
    _write_private(config_path(), {"active_provider": provider, "active_model": model})


class CredentialStore:
    service = "dspec.ai"

    def set(self, provider: str, api_key: str) -> None:
        if platform.system() == "Darwin" and os.environ.get("DSPEC_DISABLE_KEYCHAIN") != "1":
            subprocess.run(
                ["security", "add-generic-password", "-U", "-s", self.service, "-a", provider, "-w", api_key],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        path = credential_path()
        data: dict[str, str] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        data[provider] = api_key
        _write_private(path, data)

    def get(self, provider: str) -> str | None:
        env_name = f"DSPEC_{provider.upper()}_API_KEY"
        if os.environ.get(env_name):
            return os.environ[env_name]
        if platform.system() == "Darwin" and os.environ.get("DSPEC_DISABLE_KEYCHAIN") != "1":
            proc = subprocess.run(
                ["security", "find-generic-password", "-s", self.service, "-a", provider, "-w"],
                check=False,
                capture_output=True,
                text=True,
            )
            return proc.stdout.strip() if proc.returncode == 0 else None
        path = credential_path()
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8")).get(provider)
        except (OSError, json.JSONDecodeError):
            return None


def _probe_lm_studio(timeout: float = 0.7) -> dict:
    try:
        response = httpx.get(f"{LOCAL_ENDPOINTS['lm_studio']}/v1/models", timeout=timeout)
        response.raise_for_status()
        models = [str(item.get("id")) for item in response.json().get("data", []) if item.get("id")]
        return {"online": True, "models": models}
    except Exception:
        return {"online": False, "models": []}


def _probe_ollama(timeout: float = 0.7) -> dict:
    try:
        response = httpx.get(f"{LOCAL_ENDPOINTS['ollama']}/api/tags", timeout=timeout)
        response.raise_for_status()
        models = [str(item.get("name")) for item in response.json().get("models", []) if item.get("name")]
        return {"online": True, "models": models}
    except Exception:
        return {"online": False, "models": []}


def discover() -> dict:
    cfg = load_config()
    store = CredentialStore()
    return {
        "active_provider": cfg["active_provider"],
        "active_model": cfg["active_model"],
        "providers": {
            "lm_studio": {"kind": "local", **_probe_lm_studio()},
            "ollama": {"kind": "local", **_probe_ollama()},
            "openai": {"kind": "cloud", "configured": bool(store.get("openai"))},
            "anthropic": {"kind": "cloud", "configured": bool(store.get("anthropic"))},
        },
    }


def select_provider(provider: str, model: str, api_key: str | None = None, *, verify_local: bool = True) -> dict:
    if provider not in PROVIDERS:
        raise ValueError("unsupported provider")
    store = CredentialStore()
    if api_key:
        if provider not in CLOUD_ENDPOINTS:
            raise ValueError("API keys are accepted only for cloud providers")
        store.set(provider, api_key)
    if provider in CLOUD_ENDPOINTS and not store.get(provider):
        raise RuntimeError("provider_not_configured")
    if verify_local and provider == "lm_studio" and not _probe_lm_studio()["online"]:
        raise ConnectionError("provider_unreachable")
    if verify_local and provider == "ollama" and not _probe_ollama()["online"]:
        raise ConnectionError("provider_unreachable")
    save_config(provider, model)
    return {"success": True, "active_provider": provider, "active_model": model}
