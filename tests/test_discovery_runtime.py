from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import dspy
import pytest
from dspy.adapters.json_adapter import JSONAdapter

import dspec.spec_engine as spec_engine_module
from dspec.spec_engine import SpecEngine


def _session() -> dict:
    return {
        "answers": [
            {
                "stage": "constitution",
                "question_id": "assistant-constitution",
                "selected_option_id": "Local-first / private by default",
                "free_text_payload": "Create a simple number generator",
            }
        ],
        "drafts": {},
        "specs": {},
    }


def test_discovery_uses_single_json_adapter_path_with_reasoning_compatible_budget(
    monkeypatch: pytest.MonkeyPatch,
):
    engine = SpecEngine(SimpleNamespace())
    selected = {"provider": "lm_studio", "model": "meta/muse-glimmer"}
    lm_calls: list[dict[str, object]] = []
    contexts: list[dict[str, object]] = []

    async def fake_selected(_gateway):
        return selected

    def fake_make_lm(provider, model, **kwargs):
        lm_calls.append({"provider": provider, "model": model, **kwargs})
        return object()

    @contextmanager
    def fake_context(**kwargs):
        contexts.append(kwargs)
        yield

    async def fake_run(**_inputs):
        return SimpleNamespace(
            discovery={
                "gaps_found": ["The output range is not specified."],
                "questions": [
                    {
                        "id": "range",
                        "question": "What number range should the generator use?",
                        "why_it_matters": "It defines the observable output behavior.",
                        "options": [
                            {"id": "1-10", "label": "1 to 10", "rationale": "Simple default."},
                            {"id": "1-100", "label": "1 to 100", "rationale": "Broader range."},
                        ],
                        "recommended_option_id": "1-10",
                        "allow_free_text": True,
                    }
                ],
            }
        )

    monkeypatch.setattr(spec_engine_module, "selected_for_inference", fake_selected)
    monkeypatch.setattr(spec_engine_module, "make_lm", fake_make_lm)
    monkeypatch.setattr(spec_engine_module.db, "list_concept_exposures", lambda: [])
    monkeypatch.setattr(spec_engine_module.db, "record_concept_exposures", lambda _items: None)
    monkeypatch.setattr(dspy, "context", fake_context)
    monkeypatch.setattr(dspy, "asyncify", lambda _program: fake_run)

    result = asyncio.run(engine.discover(_session(), "constitution"))

    assert len(lm_calls) == 1
    assert lm_calls[0] == {
        "provider": "lm_studio",
        "model": "meta/muse-glimmer",
        "max_tokens": spec_engine_module.DISCOVERY_MAX_OUTPUT_TOKENS,
        "temperature": 0.0,
        "num_retries": 1,
    }
    assert 2248 < spec_engine_module.DISCOVERY_MAX_OUTPUT_TOKENS < 24000
    assert len(contexts) == 1
    assert isinstance(contexts[0]["adapter"], JSONAdapter)
    assert result["diagnostics"]["provider"] == "lm_studio"
    assert result["diagnostics"]["model"] == "meta/muse-glimmer"
    assert result["diagnostics"]["max_output_tokens"] == spec_engine_module.DISCOVERY_MAX_OUTPUT_TOKENS
    assert "reasoning/visible split are not asserted" in result["diagnostics"]["measurement_scope"]
