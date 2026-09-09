from __future__ import annotations

from typing import Any

from . import db
from .provider import DEFAULT_MODELS, ProviderGateway

LOCAL_PROVIDERS = ("lm_studio", "ollama")


def reconcile_selected(
    gateway: ProviderGateway,
    discovered: dict[str, Any],
) -> tuple[dict[str, str], bool]:
    """Return the effective selected model without replacing explicit choices."""
    selected = gateway.selected()
    provider = selected["provider"]

    if provider in LOCAL_PROVIDERS:
        info = discovered.get(provider)
        models = list(info.models) if info and info.online else []
        if selected["model"] == DEFAULT_MODELS[provider] and len(models) == 1:
            selected = {"provider": provider, "model": models[0]}
            db.set_setting("active_provider", selected)
        return selected, bool(info and info.online and selected["model"] in models)

    info = discovered.get(provider)
    return selected, bool(info and info.configured)


async def selected_for_inference(gateway: ProviderGateway) -> dict[str, str]:
    """Resolve a local bootstrap placeholder or fail before invoking DSPy."""
    discovered = await gateway.discover()
    selected, ready = reconcile_selected(gateway, discovered)
    if ready:
        return selected

    provider = selected["provider"]
    if provider in LOCAL_PROVIDERS:
        info = discovered.get(provider)
        if not info or not info.online:
            raise RuntimeError(f"{provider} is offline.")
        if not info.models:
            raise RuntimeError(f"{provider} is online but no model is loaded.")
        raise RuntimeError("The selected local model is not currently available.")

    raise RuntimeError(f"{provider} is not configured.")
