from __future__ import annotations

import hashlib
import json
import time
from typing import Any, AsyncIterator

from . import db
from .dspy_signatures import (
    ArchitectureToSolution,
    DiscoverSpecGaps,
    RequirementsToArchitectureOptions,
    DSPY_AVAILABLE,
    IdeaToConstitution,
    ReviseSpec,
    ScopeToRequirements,
    SemanticSpecReview,
    SpecToTasks,
    TasksToExecutionProfiles,
)
from .dspy_runtime import make_lm
from .optimization_store import load_promoted_state
from .provider import ProviderGateway
from .provider_selection import selected_for_inference
from .execution_planning import normalize_profile
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
    @staticmethod
    def _selected_architecture(session: dict[str, Any]) -> str:
        for item in session.get("engineering_decisions", []):
            if item.get("decision_type") != "architecture":
                continue
            options = item.get("options") or {}
            selected_id = item.get("selected_option_id")
            selected = next(
                (option for option in options.get("options", []) if option.get("id") == selected_id),
                None,
            )
            if selected:
                return json.dumps(
                    {
                        "selected_option": selected,
                        "customization": item.get("custom") or {},
                        "source_context_sha256": item.get("source_context_sha256"),
                    },
                    sort_keys=True,
                )
        return "No architecture option has been selected yet."

    @staticmethod
    def architecture_source_sha256(session: dict[str, Any]) -> str:
        payload = {
            "constitution": session.get("specs", {}).get("constitution", {}).get("content"),
            "requirements": session.get("specs", {}).get("requirements", {}).get("content"),
            "solution_answers": [
                {
                    key: answer.get(key)
                    for key in ("question_id", "selected_option_id", "free_text_payload")
                }
                for answer in session.get("answers", [])
                if answer.get("stage") == "solution"
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod\n    def _prior(session: dict[str, Any], stage: str) -> str:
        parts: list[str] = []
        for current in db.STAGES:
            if current == stage:
                break
            spec = session.get("specs", {}).get(current)
            if spec:
                parts.append(f"# {current.title()}\n{spec['content']}")
        return "\n\n".join(parts) if parts else "No prior tier exists."

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
                "user_architectural_preferences": (
                    f"{answers}\n\nSelected architecture decision:\n{self._selected_architecture(session)}"
                    f"\n\nAdditional user direction:\n{extra}"
                ),
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

    async def architecture_options(self, session: dict[str, Any]) -> dict[str, Any]:
        if RequirementsToArchitectureOptions is None:
            raise RuntimeError("DSPy architecture-option signature is unavailable.")
        requirements = session.get("specs", {}).get("requirements", {}).get("content", "").strip()
        if not requirements:
            raise ValueError("A Requirements draft is required before comparing implementation approaches.")
        import dspy

        selected = await selected_for_inference(self.gateway)
        lm = self._lm(selected)
        program = dspy.Predict(RequirementsToArchitectureOptions)
        concepts = db.list_concept_exposures()
        prior_concepts = "\n".join(
            f"- {item['concept_id']}: {item['label']} (encountered {item['exposure_count']} times)"
            for item in concepts[:30]
        ) or "No previously encountered engineering concepts."
        with dspy.context(lm=lm):
            result = await dspy.asyncify(program)(
                constitution_context=session.get("specs", {}).get("constitution", {}).get("content", "No Constitution draft."),
                requirements_spec=requirements,
                user_preferences=self._answers(session, "solution"),
                prior_concepts=prior_concepts,
            )
        options = getattr(result, "architecture_options", None)
        if hasattr(options, "model_dump"):
            data = options.model_dump()
        elif isinstance(options, dict):
            data = options
        else:
            raise RuntimeError("DSPy returned invalid architecture options.")

        rows = data.get("options") or []
        roles = [str(item.get("role") or "") for item in rows if isinstance(item, dict)]
        if len(rows) != 3 or sorted(roles) != ["alternative", "best_fit", "simplest"]:
            raise RuntimeError("Architecture comparison must contain exactly Best Fit, Simplest, and one meaningful alternative.")
        best_fit = next(item for item in rows if item.get("role") == "best_fit")
        if data.get("recommended_option_id") != best_fit.get("id"):
            data["recommended_option_id"] = best_fit.get("id")
        source_sha = self.architecture_source_sha256(session)
        data["source_context_sha256"] = source_sha
        db.record_concept_exposures(
            [
                {
                    "id": str((item.get("engineering_concept") or {}).get("id") or ""),
                    "label": str((item.get("engineering_concept") or {}).get("label") or ""),
                }
                for item in rows
            ]
        )
        return data

    async def task_execution_profiles(self, session: dict[str, Any]) -> dict[str, Any]:
        if TasksToExecutionProfiles is None:
            raise RuntimeError("DSPy task-routing signature is unavailable.")
        specs = session.get("specs", {})
        requirements = specs.get("requirements", {}).get("content", "").strip()
        solution = specs.get("solution", {}).get("content", "").strip()
        tasks = specs.get("tasks", {}).get("content", "").strip()
        if not requirements or not solution or not tasks:
            raise ValueError("Requirements, Solution, and Tasks drafts are required before estimating implementation routing.")
        import dspy

        selected = await selected_for_inference(self.gateway)
        lm = self._lm(selected)
        program = dspy.Predict(TasksToExecutionProfiles)
        with dspy.context(lm=lm):
            result = await dspy.asyncify(program)(
                requirements_spec=requirements,
                solution_spec=solution,
                tasks_spec=tasks,
            )
        profiles = getattr(result, "execution_profiles", None)
        if hasattr(profiles, "model_dump"):
            data = profiles.model_dump()
        elif isinstance(profiles, dict):
            data = profiles
        else:
            raise RuntimeError("DSPy returned invalid task execution profiles.")
        rows = data.get("profiles") or []
        if not rows:
            raise RuntimeError("Task routing produced no task profiles.")
        data["profiles"] = [
            normalize_profile(item, str(item.get("task_text") or ""))
            for item in rows if isinstance(item, dict)
        ]
        return data

    async def discover(self, session: dict[str, Any], stage: str) -> dict[str, Any]:
        if DiscoverSpecGaps is None:
            raise RuntimeError("DSPy discovery signature is unavailable.")
        self.validate_input(session, stage)
        import dspy

        selected = await selected_for_inference(self.gateway)
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
