from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

from . import db
from .dspy_signatures import (
    ArchitectureToSolution,
    DiscoverSpecGaps,
    DSPY_AVAILABLE,
    IdeaToConstitution,
    ReviseSpec,
    ScopeToRequirements,
    SemanticSpecReview,
    SpecToTasks,
)
from .dspy_runtime import make_lm
from .optimization_store import load_promoted_state
from .provider import ProviderGateway
from .provider_selection import selected_for_inference
from .quality import evaluate
from .streaming import StageFieldStreamParser, provider_chunk_text

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

DISCOVERY_MAX_OUTPUT_TOKENS = 900
DISCOVERY_MAX_PRIOR_CHARS = 6000
DISCOVERY_MAX_ANSWERS_CHARS = 2500
DISCOVERY_MAX_DRAFT_CHARS = 4000


class ProductIntentRequired(ValueError):
    pass


class SpecEngine:
    def __init__(self, gateway: ProviderGateway) -> None:
        self.gateway = gateway

    def _lm(self, selected: dict[str, str]):
        return make_lm(selected["provider"], selected["model"])

    @staticmethod
    def _project_intent(session: dict[str, Any]) -> str:
        for answer in session.get("answers", []):
            if answer.get("stage") == "constitution" and answer.get("question_id") == "assistant-constitution":
                value = str(answer.get("free_text_payload") or "").strip()
                if value:
                    return value
        return ""

    @staticmethod
    def _answers(session: dict[str, Any], stage: str, exclude_ids: set[str] | None = None) -> str:
        excluded = exclude_ids or set()
        rows = [
            a for a in session.get("answers", [])
            if a["stage"] == stage and a.get("question_id") not in excluded
        ]
        if not rows:
            return "No saved discovery answers for this stage."

        question_context: dict[str, dict[str, Any]] = {}
        gaps: list[str] = []
        visible_rows: list[dict[str, Any]] = []
        for answer in rows:
            if answer.get("question_id") == f"discovery-context-{stage}":
                try:
                    payload = json.loads(str(answer.get("free_text_payload") or "{}"))
                    gaps = [str(item) for item in payload.get("gaps_found", []) if str(item).strip()]
                    for question in payload.get("questions", []):
                        if isinstance(question, dict) and question.get("id"):
                            question_context[str(question["id"])] = question
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
                continue
            visible_rows.append(answer)

        lines: list[str] = []
        if gaps:
            lines.append("Discovery gaps identified: " + "; ".join(gaps))
        for answer in visible_rows:
            question_id = str(answer["question_id"])
            selected_id = str(answer.get("selected_option_id") or "none")
            user_text = str(answer.get("free_text_payload") or "").strip()
            context = question_context.get(question_id)
            if context:
                question_text = str(context.get("question") or question_id)
                option = next(
                    (
                        item for item in context.get("options", [])
                        if isinstance(item, dict) and str(item.get("id")) == selected_id
                    ),
                    None,
                )
                selected_label = str(option.get("label")) if option else selected_id
                rationale = str(option.get("rationale") or "") if option else ""
                line = f"- {question_text}: selected={selected_label}"
                if rationale:
                    line += f"; rationale={rationale}"
                if user_text:
                    line += f"; user_context={user_text}"
                lines.append(line)
            else:
                lines.append(
                    f"- {question_id}: selected={selected_id}; free_text={user_text}"
                )
        return "\n".join(lines) if lines else "No saved discovery answers for this stage."

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

    @staticmethod
    def _clip_context(value: str, max_chars: int) -> str:
        text = value.strip()
        if len(text) <= max_chars:
            return text
        omitted = len(text) - max_chars
        return text[:max_chars].rstrip() + f"\n\n[DSpec context truncated: {omitted} characters omitted]"

    def _discovery_inputs(self, session: dict[str, Any], stage: str) -> dict[str, str]:
        self.validate_input(session, stage)
        answers = self._answers(session, stage)
        current = (
            session.get("drafts", {}).get(stage, {}).get("content")
            or session.get("specs", {}).get(stage, {}).get("content", "")
        )
        return {
            "stage": stage,
            "prior_tiers": self._clip_context(
                self._prior(session, stage),
                DISCOVERY_MAX_PRIOR_CHARS,
            ),
            "current_answers": (
                self._clip_context(answers, DISCOVERY_MAX_ANSWERS_CHARS)
                + "\n\nCurrent active draft:\n"
                + (
                    self._clip_context(current, DISCOVERY_MAX_DRAFT_CHARS)
                    if current
                    else "No active draft."
                )
            ),
        }

    def validate_input(self, session: dict[str, Any], stage: str) -> None:
        if stage == "constitution" and not self._project_intent(session):
            raise ProductIntentRequired("Describe what you want to build before analyzing gaps or generating the Constitution.")

    def _inputs(self, session: dict[str, Any], stage: str, instructions: str | None) -> dict[str, str]:
        self.validate_input(session, stage)
        answers = self._answers(
            session,
            stage,
            {"assistant-constitution"} if stage == "constitution" else None,
        )
        extra = instructions.strip() if instructions else "No additional instruction."
        specs = session.get("specs", {})
        if stage == "constitution":
            intent = self._project_intent(session)
            return {
                "raw_idea_description": f"Project intent:\n{intent}\n\nSaved discovery decisions:\n{answers}\n\nAdditional user direction:\n{extra}",
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

    def _generation_program(
        self,
        stage: str,
        selected: dict[str, str],
    ) -> tuple[Any, Any, str, dict[str, Any] | None]:
        if stage not in _SIGNATURES or _SIGNATURES[stage] is None:
            raise ValueError("Unsupported or unavailable DSpec stage.")

        import dspy

        lm = self._lm(selected)
        signature = _SIGNATURES[stage]
        output_field = _OUTPUT_FIELDS[stage]

        # Stage signatures already define the structured outputs DSpec needs.
        # Adding ChainOfThought inserts a separate reasoning field before the
        # user-visible spec and materially delays useful streamed content.
        base = dspy.Predict(signature)
        promoted = load_promoted_state(base, stage)
        # Saved DSPy state can include the LM used during compilation. Runtime
        # provider selection remains authoritative.
        base.set_lm(lm)

        def reward(_args: dict[str, Any], pred: dspy.Prediction) -> float:
            content = str(getattr(pred, output_field, "") or "")
            structural_score = float(evaluate(stage, content)["score"])
            semantic = getattr(pred, "quality_assessment", None)
            if hasattr(semantic, "model_dump"):
                semantic = semantic.model_dump()
            semantic_score = float(semantic.get("rubric_score", 0.0)) if isinstance(semantic, dict) else 0.0
            return min(structural_score, semantic_score)

        program = dspy.Refine(module=base, N=3, reward_fn=reward, threshold=0.90, fail_count=2)
        return lm, program, output_field, promoted

    @staticmethod
    def _generation_result(
        *,
        stage: str,
        selected: dict[str, str],
        output_field: str,
        promoted: dict[str, Any] | None,
        result: Any,
        latency_ms: int,
        first_provisional_chunk_ms: int | None = None,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
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
        semantic_score = float(semantic_data.get("rubric_score", 0.0)) if isinstance(semantic_data, dict) else 0.0
        combined_score = round(min(float(structural["score"]), semantic_score), 3)
        must_fix = list(structural.get("must_fix", []))
        recommendations = list(structural.get("recommendations", []))
        if semantic_score < 0.90:
            semantic_fix = {
                "id": "generation_semantic_quality",
                "label": "Generation semantic quality",
                "passed": False,
                "weight": 0.0,
                "detail": f"Model-reported semantic completeness {semantic_score:.2f} is below 0.90.",
                "recommendation": "Refine the draft using the saved product intent and discovery decisions before approval.",
            }
            must_fix.append(semantic_fix)
            recommendations.append(semantic_fix["recommendation"])

        review = {
            **structural,
            "score": combined_score,
            "passed": bool(structural["passed"] and semantic_score >= 0.90),
            "must_fix": must_fix,
            "recommendations": recommendations,
            "semantic_assessment": semantic_data,
            "refinement": {"module": "dspy.Refine", "attempt_limit": 3, "threshold": 0.90},
            "optimization": {
                "state": "PROMOTED" if promoted else "UNOPTIMIZED",
                "candidate_id": promoted.get("candidate_id") if promoted else None,
                "optimizer": promoted.get("optimizer") if promoted else None,
                "program_state_sha256": promoted.get("program_state_sha256") if promoted else None,
            },
        }
        metrics = {
            "provider": selected["provider"],
            "model": selected["model"],
            "inference_latency_ms": latency_ms,
            "tokens_generated": None,
            "tokens_per_second": None,
            "first_provisional_chunk_ms": first_provisional_chunk_ms,
            "measurement_scope": (
                "request latency measured locally; first_provisional_chunk_ms is the first provider-backed "
                "stage-content chunk when observed; provider token accounting not asserted"
            ),
        }
        return content, metrics, review

    async def generate(
        self,
        session: dict[str, Any],
        stage: str,
        instructions: str | None = None,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        import dspy

        selected = await selected_for_inference(self.gateway)
        lm, program, output_field, promoted = self._generation_program(stage, selected)
        started = time.perf_counter()
        with dspy.context(lm=lm):
            result = await dspy.asyncify(program)(**self._inputs(session, stage, instructions))
        latency_ms = int((time.perf_counter() - started) * 1000)
        return self._generation_result(
            stage=stage,
            selected=selected,
            output_field=output_field,
            promoted=promoted,
            result=result,
            latency_ms=latency_ms,
        )

    async def generate_stream(
        self,
        session: dict[str, Any],
        stage: str,
        instructions: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream provisional stage content while preserving Refine authority.

        Raw DSPy/provider chunks may contain reasoning, quality assessment, and
        Refine feedback. Only the active stage specification field is emitted.
        The final event contains the authoritative Refine-selected prediction;
        callers must persist only that final result.
        """

        import dspy

        selected = await selected_for_inference(self.gateway)
        lm, program, output_field, promoted = self._generation_program(stage, selected)
        parser = StageFieldStreamParser(output_field)
        started = time.perf_counter()
        first_chunk_ms: int | None = None
        final_prediction: Any | None = None

        with dspy.context(lm=lm):
            streamer = dspy.streamify(program)
            async for value in streamer(**self._inputs(session, stage, instructions)):
                streamed_text = provider_chunk_text(value)
                if streamed_text is not None:
                    for item in parser.feed(streamed_text):
                        payload: dict[str, Any] = {
                            "event": item.event,
                            "attempt": item.attempt,
                            "provisional": True,
                        }
                        if item.text is not None:
                            payload["text"] = item.text
                            if first_chunk_ms is None and item.text:
                                first_chunk_ms = int((time.perf_counter() - started) * 1000)
                        yield payload
                    continue
                if isinstance(value, dspy.Prediction):
                    final_prediction = value

        for item in parser.finalize():
            payload = {
                "event": item.event,
                "attempt": item.attempt,
                "provisional": True,
            }
            if item.text is not None:
                payload["text"] = item.text
                if first_chunk_ms is None and item.text:
                    first_chunk_ms = int((time.perf_counter() - started) * 1000)
            yield payload

        if final_prediction is None:
            raise RuntimeError("DSPy streaming completed without a final prediction.")

        latency_ms = int((time.perf_counter() - started) * 1000)
        content, metrics, review = self._generation_result(
            stage=stage,
            selected=selected,
            output_field=output_field,
            promoted=promoted,
            result=final_prediction,
            latency_ms=latency_ms,
            first_provisional_chunk_ms=first_chunk_ms,
        )
        yield {
            "event": "final",
            "content": content,
            "metrics": metrics,
            "review": review,
        }


    async def revise(
        self,
        session: dict[str, Any],
        stage: str,
        content: str,
        instruction: str,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        if ReviseSpec is None:
            raise RuntimeError("DSPy review revision signature is unavailable.")
        current = content.strip()
        review_instruction = instruction.strip()
        if not current:
            raise ValueError("Current specification content is required.")
        if not review_instruction:
            raise ValueError("A review instruction is required.")

        import dspy

        selected = await selected_for_inference(self.gateway)
        lm = self._lm(selected)
        base = dspy.ChainOfThought(ReviseSpec)
        base.set_lm(lm)

        def reward(_args: dict[str, Any], pred: dspy.Prediction) -> float:
            revised = str(getattr(pred, "revised_spec", "") or "")
            return float(evaluate(stage, revised)["score"])

        program = dspy.Refine(module=base, N=3, reward_fn=reward, threshold=0.90, fail_count=2)
        started = time.perf_counter()
        with dspy.context(lm=lm):
            result = await dspy.asyncify(program)(
                stage=stage,
                prior_tiers=self._prior(session, stage),
                current_spec=current,
                review_instruction=review_instruction,
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        revised = str(getattr(result, "revised_spec", "") or "").strip()
        if not revised:
            raise RuntimeError("DSPy returned an empty revised specification.")

        semantic = getattr(result, "quality_assessment", None)
        if hasattr(semantic, "model_dump"):
            semantic_data = semantic.model_dump()
        elif isinstance(semantic, dict):
            semantic_data = semantic
        else:
            semantic_data = {"reported": False}

        review = {
            **evaluate(stage, revised),
            "semantic_assessment": semantic_data,
            "revision_application": {
                "instruction": review_instruction,
                "scope": "single_review_instruction",
                "formal_revision_created": False,
            },
        }
        metrics = {
            "provider": selected["provider"],
            "model": selected["model"],
            "inference_latency_ms": latency_ms,
            "tokens_generated": None,
            "tokens_per_second": None,
            "measurement_scope": "request latency measured locally; provider token accounting not asserted",
        }
        return revised, metrics, review


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

        selected = await selected_for_inference(self.gateway)
        lm = self._lm(selected)
        program = dspy.Predict(SemanticSpecReview)
        with dspy.context(lm=lm):
            result = await dspy.asyncify(program)(
                stage=stage,
                prior_tiers=self._prior(session, stage),
                spec_markdown=content,
                discovery_answers="\n\n".join(
                    f"{current}:\n{self._answers(session, current)}"
                    for current in db.STAGES[:db.STAGES.index(stage) + 1]
                ),
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
        consistency_issues = semantic_data.get("consistency_issues") or []
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
        must_fix.extend(
            {
                "id": f"consistency_{index + 1}",
                "label": "Cross-tier consistency",
                "passed": False,
                "weight": 0.0,
                "detail": str(item),
                "recommendation": str(item),
            }
            for index, item in enumerate(consistency_issues)
        )
        recommendations = list(structural.get("recommendations", [])) + [
            str(item) for item in (semantic_data.get("recommendations") or [])
        ]
        return {
            "structural": structural,
            "semantic": semantic_data,
            "score": combined,
            "threshold": 0.90,
            "passed": bool(structural["passed"] and semantic_score >= 0.90 and not must_fix),
            "passing": structural.get("passing", []),
            "must_fix": must_fix,
            "recommendations": recommendations,
            "semantic_status": "PASS" if semantic_score >= 0.90 and not semantic_must_fix and not consistency_issues else "FAIL",
            "provider": {"provider": selected["provider"], "model": selected["model"]},
        }

    async def discover(self, session: dict[str, Any], stage: str) -> dict[str, Any]:
        if DiscoverSpecGaps is None:
            raise RuntimeError("DSPy discovery signature is unavailable.")
        import dspy
        from dspy.adapters.chat_adapter import ChatAdapter

        selected = await selected_for_inference(self.gateway)
        inputs = self._discovery_inputs(session, stage)
        lm = make_lm(
            selected["provider"],
            selected["model"],
            max_tokens=DISCOVERY_MAX_OUTPUT_TOKENS,
            temperature=0.0,
            num_retries=1,
        )
        program = dspy.Predict(DiscoverSpecGaps)
        started = time.perf_counter()
        with dspy.context(
            lm=lm,
            adapter=ChatAdapter(use_json_adapter_fallback=False),
        ):
            result = await dspy.asyncify(program)(**inputs)
        latency_ms = int((time.perf_counter() - started) * 1000)

        discovery = getattr(result, "discovery", None)
        if hasattr(discovery, "model_dump"):
            payload = discovery.model_dump()
        elif isinstance(discovery, dict):
            payload = discovery
        else:
            raise RuntimeError("DSPy returned an invalid discovery result.")

        payload["diagnostics"] = {
            "provider": selected["provider"],
            "model": selected["model"],
            "request_latency_ms": latency_ms,
            "input_chars": sum(len(value) for value in inputs.values()),
            "max_output_tokens": DISCOVERY_MAX_OUTPUT_TOKENS,
            "measurement_scope": "local DSpec request timing; provider token accounting not asserted",
        }
        return payload