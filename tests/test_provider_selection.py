from pathlib import Path

import pytest

from dspec import db
from dspec.provider import DEFAULT_MODELS, ProviderGateway, ProviderInfo
from dspec.provider_selection import reconcile_selected


@pytest.fixture()
def gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProviderGateway:
    monkeypatch.setenv("DSPEC_HOME", str(tmp_path / "home"))
    db.init_db()
    return ProviderGateway()


def discovered(provider: str, models: list[str], online: bool = True) -> dict[str, ProviderInfo]:
    return {
        "lm_studio": ProviderInfo(
            "lm_studio",
            provider == "lm_studio" and online,
            models if provider == "lm_studio" else [],
            True,
        ),
        "ollama": ProviderInfo(
            "ollama",
            provider == "ollama" and online,
            models if provider == "ollama" else [],
            True,
        ),
        "openai": ProviderInfo("openai", False, [], False),
        "anthropic": ProviderInfo("anthropic", False, [], False),
    }


def test_single_discovered_local_model_replaces_bootstrap_placeholder(gateway: ProviderGateway):
    selected, ready = reconcile_selected(gateway, discovered("lm_studio", ["qwen-local"]))

    assert selected == {"provider": "lm_studio", "model": "qwen-local"}
    assert ready is True
    assert gateway.selected() == selected


def test_multiple_discovered_models_require_explicit_choice(gateway: ProviderGateway):
    selected, ready = reconcile_selected(
        gateway,
        discovered("lm_studio", ["qwen-local", "llama-local"]),
    )

    assert selected["model"] == DEFAULT_MODELS["lm_studio"]
    assert ready is False


def test_explicit_local_model_is_not_silently_replaced(gateway: ProviderGateway):
    gateway.select("lm_studio", "chosen-model")

    selected, ready = reconcile_selected(
        gateway,
        discovered("lm_studio", ["different-model"]),
    )

    assert selected == {"provider": "lm_studio", "model": "chosen-model"}
    assert ready is False
    assert gateway.selected() == selected


def test_explicit_local_model_is_ready_when_discovered(gateway: ProviderGateway):
    gateway.select("ollama", "qwen3:latest")

    selected, ready = reconcile_selected(
        gateway,
        discovered("ollama", ["qwen3:latest"]),
    )

    assert selected == {"provider": "ollama", "model": "qwen3:latest"}
    assert ready is True
