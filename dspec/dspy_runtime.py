from __future__ import annotations

import re

from .dspy_signatures import DSPY_AVAILABLE
from .network_policy import local_endpoint
from .security import load_api_key


def make_lm(
    provider: str,
    model: str,
    *,
    max_tokens: int = 24000,
    temperature: float | None = None,
    num_retries: int = 3,
):
    if not DSPY_AVAILABLE:
        raise RuntimeError("DSPy is not installed. Install the governed runtime dependencies before generation.")

    import dspy

    common: dict[str, object] = {
        "max_tokens": max_tokens,
        "num_retries": num_retries,
    }
    if temperature is not None:
        common["temperature"] = temperature

    if provider == "lm_studio":
        base = local_endpoint("lm_studio") + "/v1"
        return dspy.LM(
            f"openai/{model}",
            api_base=base,
            api_key="lm-studio",
            model_type="chat",
            **common,
        )
    if provider == "ollama":
        base = local_endpoint("ollama")
        return dspy.LM(
            f"ollama_chat/{model}",
            api_base=base,
            api_key="",
            **common,
        )
    if provider == "openai":
        key = load_api_key("openai")
        if not key:
            raise RuntimeError("OpenAI API key is not configured.")
        model_family = model.lower()
        reasoning_model = bool(
            re.match(
                r"^(?:o[1345](?:-(?:mini|nano|pro))?(?:-\d{4}-\d{2}-\d{2})?|gpt-5(?!-chat)(?:-.*)?)$",
                model_family,
            )
        )
        if reasoning_model:
            # DSPy requires reasoning-family OpenAI models to omit low
            # max_tokens/temperature constructor values. Pass the bounded
            # completion budget through the provider-native parameter instead.
            return dspy.LM(
                f"openai/{model}",
                api_key=key,
                max_completion_tokens=max_tokens,
                num_retries=num_retries,
            )
        return dspy.LM(f"openai/{model}", api_key=key, **common)
    if provider == "anthropic":
        key = load_api_key("anthropic")
        if not key:
            raise RuntimeError("Anthropic API key is not configured.")
        return dspy.LM(f"anthropic/{model}", api_key=key, **common)
    raise ValueError(f"Unsupported provider: {provider}")
