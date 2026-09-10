from __future__ import annotations

from .dspy_signatures import DSPY_AVAILABLE
from .network_policy import local_endpoint
from .security import load_api_key


def make_lm(
    provider: str,
    model: str,
    *,
    max_tokens: int = 24000,
    temperature: float | None = None,
):
    if not DSPY_AVAILABLE:
        raise RuntimeError("DSPy is not installed. Install the governed runtime dependencies before generation.")

    import dspy

    common: dict[str, object] = {"max_tokens": max_tokens}
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
        return dspy.LM(f"openai/{model}", api_key=key, **common)
    if provider == "anthropic":
        key = load_api_key("anthropic")
        if not key:
            raise RuntimeError("Anthropic API key is not configured.")
        return dspy.LM(f"anthropic/{model}", api_key=key, **common)
    raise ValueError(f"Unsupported provider: {provider}")
