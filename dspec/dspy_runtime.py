from __future__ import annotations

import os

from .dspy_signatures import DSPY_AVAILABLE
from .security import load_api_key


def make_lm(provider: str, model: str):
    if not DSPY_AVAILABLE:
        raise RuntimeError("DSPy is not installed. Install the governed runtime dependencies before generation.")

    import dspy

    if provider == "lm_studio":
        base = os.environ.get("DSPEC_LM_STUDIO_URL", "http://127.0.0.1:1234").rstrip("/") + "/v1"
        return dspy.LM(
            f"openai/{model}",
            api_base=base,
            api_key="lm-studio",
            model_type="chat",
            max_tokens=24000,
        )
    if provider == "ollama":
        base = os.environ.get("DSPEC_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        return dspy.LM(
            f"ollama_chat/{model}",
            api_base=base,
            api_key="",
            max_tokens=24000,
        )
    if provider == "openai":
        key = load_api_key("openai")
        if not key:
            raise RuntimeError("OpenAI API key is not configured.")
        return dspy.LM(f"openai/{model}", api_key=key, max_tokens=24000)
    if provider == "anthropic":
        key = load_api_key("anthropic")
        if not key:
            raise RuntimeError("Anthropic API key is not configured.")
        return dspy.LM(f"anthropic/{model}", api_key=key, max_tokens=24000)
    raise ValueError(f"Unsupported provider: {provider}")
