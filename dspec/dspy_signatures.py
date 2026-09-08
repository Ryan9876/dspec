from __future__ import annotations

from typing import Any

try:
    import dspy
    from pydantic import BaseModel, Field

    class SpecQualityRubric(BaseModel):
        has_no_ambiguous_todos: bool = Field(description="True when the draft contains no TODO, FIXME, TBD, or incomplete placeholder.")
        has_typed_schemas: bool = Field(description="True when applicable data models, API payloads, and state contracts use concrete types.")
        has_error_contracts: bool = Field(description="True when applicable interfaces define errors, status codes, and recovery behavior.")
        agent_executable: bool = Field(description="True when a downstream coding agent can act without inventing missing implementation decisions.")
        rubric_score: float = Field(ge=0.0, le=1.0, description="Semantic completeness estimate from 0.0 to 1.0.")

    class DiscoveryOption(BaseModel):
        id: str
        label: str
        rationale: str

    class DiscoveryQuestion(BaseModel):
        id: str
        question: str
        why_it_matters: str
        options: list[DiscoveryOption]
        recommended_option_id: str
        allow_free_text: bool = True

    class DiscoveryResult(BaseModel):
        questions: list[DiscoveryQuestion]
        gaps_found: list[str]

    class IdeaToConstitution(dspy.Signature):
        """Create concrete, commercial-ready project governance. Resolve ambiguity without inventing validation evidence. Define stack boundaries, security/privacy rules, runtime and deployment constraints, compatibility expectations, quality gates, and explicit unknowns."""
        raw_idea_description: str = dspy.InputField(desc="Product intent, saved discovery answers, and additional product-level direction.")
        security_isolation_preferences: str = dspy.InputField(desc="Privacy, credential, authorization, hosting, runtime, telemetry, and reversibility constraints.")
        constitution_spec: str = dspy.OutputField(desc="Complete Markdown constitution with explicit non-negotiable rules and measurable quality gates.")
        quality_assessment: SpecQualityRubric = dspy.OutputField(desc="Semantic assessment of the generated constitution.")

    class ScopeToRequirements(dspy.Signature):
        """Create exhaustive observable requirements without prescribing implementation unnecessarily. Include stable requirement IDs, end-to-end user journeys, entities/data semantics, failure and recovery behavior, non-functional requirements, exclusions, and testable acceptance criteria."""
        constitution_context: str = dspy.InputField(desc="Approved or current governing constitution.")
        functional_scope_answers: str = dspy.InputField(desc="Saved discovery answers and user-stated product outcomes for this stage.")
        requirements_spec: str = dspy.OutputField(desc="Complete Markdown requirements specification.")
        quality_assessment: SpecQualityRubric = dspy.OutputField(desc="Semantic assessment of requirements completeness and testability.")

    class ArchitectureToSolution(dspy.Signature):
        """Create a concrete technical solution that satisfies the supplied requirements and constitution. Include components, interfaces, typed data schemas, API request/response/error contracts, state transitions, dependencies, security boundaries, migration/compatibility, failure modes, observability, sequencing, and rollback."""
        constitution_context: str = dspy.InputField(desc="Governing project constraints.")
        requirements_spec: str = dspy.InputField(desc="Requirements and acceptance criteria that the solution must satisfy.")
        user_architectural_preferences: str = dspy.InputField(desc="User constraints/preferences and current-stage discovery answers; treat preferences as hypotheses unless explicitly required.")
        solution_spec: str = dspy.OutputField(desc="Complete Markdown technical solution with concrete schemas and contracts.")
        quality_assessment: SpecQualityRubric = dspy.OutputField(desc="Semantic assessment of architectural completeness.")

    class SpecToTasks(dspy.Signature):
        """Create ordered, executable work packages derived from requirements and solution. Each material task must map to requirement IDs, name exact implementation scope, dependencies, preservation constraints, and a concrete terminal verification command or objective assertion. Tasks must not create new product scope."""
        constitution_context: str = dspy.InputField(desc="Governing constraints.")
        requirements_spec: str = dspy.InputField(desc="Required behavior and acceptance criteria.")
        solution_spec: str = dspy.InputField(desc="Approved implementation direction.")
        tasks_spec: str = dspy.OutputField(desc="Complete ordered Markdown task plan with requirement traceability and verification.")
        quality_assessment: SpecQualityRubric = dspy.OutputField(desc="Semantic assessment of task executability and traceability.")

    class DiscoverSpecGaps(dspy.Signature):
        """Analyze current product input for the active DSpec stage. Ask only high-value questions whose answers materially improve completeness or reduce ambiguity. Prefer 1-3 concise multiple-choice questions, include a recommended default with rationale, and preserve a free-text option. Do not ask implementation trivia that can safely be inferred."""
        stage: str = dspy.InputField(desc="constitution, requirements, solution, or tasks")
        prior_tiers: str = dspy.InputField(desc="Current prior-tier specifications, if any.")
        current_answers: str = dspy.InputField(desc="Saved discovery input and current draft context.")
        discovery: DiscoveryResult = dspy.OutputField(desc="Structured gap analysis and MCQ questions.")

    DSPY_AVAILABLE = True
except Exception:
    DSPY_AVAILABLE = False
    IdeaToConstitution = ScopeToRequirements = ArchitectureToSolution = SpecToTasks = DiscoverSpecGaps = None  # type: ignore


def status() -> dict[str, Any]:
    return {
        "available": DSPY_AVAILABLE,
        "optimization": "UNOPTIMIZED",
        "refinement": "Refine",
        "mipro_v2": "BLOCKED_PENDING_REVIEWED_TRAINING_SET",
    }
