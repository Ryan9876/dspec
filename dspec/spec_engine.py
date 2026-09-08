from __future__ import annotations

import time
from typing import Any

from . import db
from .dspy_signatures import (
    ArchitectureToSolution,
    DiscoverSpecGaps,
    DSPY_AVAILABLE,
    IdeaToConstitution,
    ScopeToRequirements,
    SemanticSpecReview,
    SpecToTasks,
)
from .provider import ProviderGateway
from .quality import evaluate
from .security import load_api_key

_OUTPUT_FIELDS = {
    "constitution": "constitution_spec",
    "requirements": "requirements_spec",
    "solution": "solution_spec",
    "tasks": "tasks_spec",
}
_SIGNATURES = {
    "constitution": IdeaToConstitution,
    "requirements": ScopeToRequirements,
    "solution": ArchitectureToSolution,
    "tasks": SpecToTasks,
}


class SpecEngine:
    def __init__(self, gateway: ProviderGateway) -> None:
        self.gateway = gateway

    def _lm(self, selected: dict[str, str]):
        if not DSPY_AVAILABLE:
            raise RuntimeError("DSPy is not installed. Install the governed runtime dependencies before generation.")
        import dspy

        provider = selected["provider"]
        model = selected["model"]
        if provider == "lm_studio":
            from os import environ
            base = environ.get("DSPEC_LM_STUDIO_URL", "http://127.0.0.1:1234").rstrip("/") + "/v1"
            return dspy.LM(f"openai/{model}", api_base=base, api_key="lm-studio", model_type="chat", max_tokens=24000)
        if provider == "ollama":
            from os import environ
            base = environ.get("DSPEC_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
            return dspy.LM(f"ollama_chat/{model}", api_base=base, api_key="", max_tokens=24000)
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

    @staticmethod
    def _answers(session: dict[str, Any], stage: str) -> str:
        rows = [a for a in session.get("answers", []) if a["stage"] == stage]
        if not rows:
            return "No saved discovery answers for this stage."
        return "\n".join(
            f"- {a['question_id']}: selected={a.get('selected_option_id') or 'none'}; free_text={a.get('free_text_payload') or ''}"
            for a in rows
        )

    @staticmethod
    def _prior(session: dict[str, Any], stage: str) -> str:
        parts: list[str] = []
        for current in db.STAGES:
            if current == stage:
                break
            spec = session.get("specs", {}).get(current)
            if spec:
                parts.append(f"# {current.title()}\n{spec['content']}")
        return "\n\n".join(parts) if parts else "No prior tier exists."

    def _inputs(self, session: dict[str, Any], stage: str, instructions: str | None) -> dict[str, str]:
        answers = self._answers(session, stage)
        extra = instructions.strip() if instructions else "No additional instruction."
        specs = session.get("specs", {})
        if stage == "constitution":
            return {
                "raw_idea_description": f"{answers}\n\nAdditional user direction:\n{extra}",
                "security_isolation_preferences": (
                    "Preserve explicit user privacy/security/runtime constraints. "
                    "Never invent test, deployment, compliance, or security-certification evidence. "
                    "Record consequential unknowns as UNKNOWN, BLOCKED, or NOT TESTED."
                ),
            }
        if stage == "requirements":
            return {
                "constitution_context": specs.get("constitution", {}).get("content", "Constitution not yet drafted."),
                "functional_scope_answers": f"{answers}\n\nAdditional user direction:\n{extra}",
            }
        if stage == "solution":
            return {
                "constitution_context": specs.get("constitution", {}).get("content", "Constitution not yet drafted."),
                "requirements_spec": specs.get("requirements", {}).get("content", "Requirements not yet drafted."),
                "user_architectural_preferences": f"{answers}\n\nAdditional user direction:\n{extra}",
            }
        return {
            "constitution_context": specs.get("constitution", {}).get("content", "Constitution not yet drafted."),
            "requirements_spec": specs.get("requirements", {}).get("content", "Requirements not yet drafted."),
            "solution_spec": specs.get("solution", {}).get("content", "Solution not yet drafted."),
        }

    async def generate(self, session: dict[str, Any], stage: str, instructions: str | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
        if stage not in _SIGNATURES or _SIGNATURES[stage] is None:
            raise ValueError("Unsupported or unavailable DSpec stage.")
        import dspy

        selected = self.gateway.selected()
        lm = self._lm(selected)
        signature = _SIGNATURES[stage]
        output_field = _OUTPUT_FIELDS[stage]
        base = dspy.ChainOfThought(signature)

        def reward(_args: dict[str, Any], pred: dspy.Prediction) -> float:
            content = str(getattr(pred, output_field, "") or "")
            return float(evaluate(stage, content)["score"])

        program = dspy.Refine(module=base, N=3, reward_fn=reward, threshold=0.90, fail_count=2)
        started = time.perf_counter()
        with dspy.context(lm=lm):
            result = await dspy.asyncify(program)(**self._inputs(session, stage, instructions))
        latency_ms = int((time.perf_counter() - started) * 1000)
        content = str(getattr(result, output_field, "") or "").strip()
        if not content:
            raise RuntimeError("DSPy returned an empty specification.")

        semantic = getattr(result, "quality_assessment", None)
        if hasattr(semantic, "model_dump"):
            semantic_data = semantic.model_dump()
        elif isinstance(semantic, dict):
            semantic_data = semantic
        else:
            semantic_data = {"reported": False}

        structural = evaluate(stage, content)
        review = {**structural, "semantic_assessment": semantic_data, "refinement": {"module": "dspy.Refine", "attempt_limit": 3, "threshold": 0.90}}
        metrics = {
            "provider": selected["provider"],
            "model": selected["model"],
            "inference_latency_ms": latency_ms,
            "tokens_generated": None,
            "tokens_per_second": None,
            "measurement_scope": "request latency measured locally; provider token accounting not asserted",
        }
        return content, metrics, review


    async def semantic_review(self, session: dict[str, Any], stage: str, content: str) -> dict[str, Any]:
        structural = evaluate(stage, content)
        if SemanticSpecReview is None:
            return {
                "structural": structural,
                "semantic": None,
                "score": structural["score"],
                "passed": False,
                "semantic_status": "NOT TESTED",
                "semantic_error": "DSPy semantic review signature is unavailable.",
            }
        import dspy

        selected = self.gateway.selected()
        lm = self._lm(selected)
        program = dspy.Predict(SemanticSpecReview)
        with dspy.context(lm=lm):
            result = await dspy.asyncify(program)(
                stage=stage,
                prior_tiers=self._prior(session, stage),
                spec_markdown=content,
            )
        semantic = getattr(result, "review", None)
        if hasattr(semantic, "model_dump"):
            semantic_data = semantic.model_dump()
        elif isinstance(semantic, dict):
            semantic_data = semantic
        else:
            raise RuntimeError("DSPy returned an invalid semantic review result.")
        semantic_score = float(semantic_data.get("score", 0.0))
        semantic_must_fix = semantic_data.get("must_fix") or []
        combined = round(min(float(structural["score"]), semantic_score), 3)
        must_fix = list(structural.get("must_fix", []))
        must_fix.extend(
            {
                "id": f"semantic_{index + 1}",
                "label": "Semantic review",
                "passed": False,
                "weight": 0.0,
                "detail": str(item),
                "recommendation": str(item),
            }
            for index, item in enumerate(semantic_must_fix)
        )
        recommendations = list(structural.get("recommendations", [])) + [
            str(item) for item in (semantic_data.get("recommendations") or [])
        ]
        return {
            "structural": structural,
            "semantic": semantic_data,
            "score": combined,
            "threshold": 0.90,
            "passed": bool(structural["passed"] and semantic_score >= 0.90 and not semantic_must_fix),
            "passing": structural.get("passing", []),
            "must_fix": must_fix,
            "recommendations": recommendations,
            "semantic_status": "PASS" if semantic_score >= 0.90 and not semantic_must_fix else "FAIL",
            "provider": {"provider": selected["provider"], "model": selected["model"]},
        }

    async def discover(self, session: dict[str, Any], stage: str) -> dict[str, Any]:
        if DiscoverSpecGaps is None:
            raise RuntimeError("DSPy discovery signature is unavailable.")
        import dspy

        selected = self.gateway.selected()
        lm = self._lm(selected)
        program = dspy.Predict(DiscoverSpecGaps)
        current = session.get("specs", {}).get(stage, {}).get("content", "")
        inputs = {
            "stage": stage,
            "prior_tiers": self._prior(session, stage),
            "current_answers": f"{self._answers(session, stage)}\n\nCurrent draft:\n{current or 'No current draft.'}",
        }
        with dspy.context(lm=lm):
            result = await dspy.asyncify(program)(**inputs)
        discovery = getattr(result, "discovery", None)
        if hasattr(discovery, "model_dump"):
            return discovery.model_dump()
        if isinstance(discovery, dict):
            return discovery
        raise RuntimeError("DSPy returned an invalid discovery result.")
