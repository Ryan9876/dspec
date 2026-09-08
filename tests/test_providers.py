import json, stat
import pytest
from backend.dspec_app import providers

def test_cloud_key_is_write_only_and_private(monkeypatch):
    result = providers.select_provider("openai", "gpt-test", "sk-example-key-1234567890", verify_local=False)
    assert "api_key" not in result
    path = providers.credential_path()
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert providers.CredentialStore().get("openai") == "sk-example-key-1234567890"
    assert "sk-example" not in json.dumps(providers.load_config())

def test_cloud_provider_without_key_is_blocked():
    with pytest.raises(RuntimeError, match="provider_not_configured"):
        providers.select_provider("anthropic", "claude-test", verify_local=False)

def test_local_selection_requires_reachable_provider(monkeypatch):
    monkeypatch.setattr(providers, "_probe_lm_studio", lambda timeout=0.7: {"online": False, "models": []})
    with pytest.raises(ConnectionError, match="provider_unreachable"):
        providers.select_provider("lm_studio", "local-model")

def test_local_selection_can_switch_without_restart_in_fixture(monkeypatch):
    monkeypatch.setattr(providers, "_probe_lm_studio", lambda timeout=0.7: {"online": True, "models": ["a"]})
    monkeypatch.setattr(providers, "_probe_ollama", lambda timeout=0.7: {"online": True, "models": ["b"]})
    providers.select_provider("lm_studio", "a")
    assert providers.load_config()["active_provider"] == "lm_studio"
    providers.select_provider("ollama", "b")
    assert providers.load_config()["active_provider"] == "ollama"
