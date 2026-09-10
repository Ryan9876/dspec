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
        id: str = Field(max_length=48)
        label: str = Field(max_length=80)
        rationale: str = Field(max_length=140)

    class DiscoveryQuestion(BaseModel):
        id: str = Field(max_length=48)
        question: str = Field(max_length=180)
        why_it_matters: str = Field(max_length=220)
        options: list[DiscoveryOption] = Field(min_length=2, max_length=3)
        recommended_option_id: str = Field(max_length=48)
        allow_free_text: bool = True

    class DiscoveryResult(BaseModel):
        questions: list[DiscoveryQuestion] = Field(max_length=3)
        gaps_found: list[str] = Field(max_length=3)

    class SemanticReviewResult(BaseModel):
        score: float = Field(ge=0.0, le=1.0)
        must_fix: list[str]
        recommendations: list[str]
        consistency_issues: list[str]

    class IdeaToConstitution(dspy.Signature):
        """Create a concrete project constitution from the actual product intent and saved user decisions. Keep governance proportional to the described product. Do not invent corporate boards, shareholders, public APIs, multi-user authorization, compliance regimes, cloud hosting, or solution-level schemas unless the supplied product intent requires them. Define constitution-level purpose, non-negotiable operating/security/privacy boundaries, runtime/deployment constraints, compatibility expectations, quality gates, and explicit unknowns without inventing validation evidence."""
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

    class SemanticSpecReview(dspy.Signature):
        """Review a DSpec tier as a strict independent reviewer. Evaluate completeness, internal consistency, cross-tier alignment, security/failure behavior where applicable, and downstream executability. Do not claim tests or runtime evidence. A score >= 0.90 requires no material must-fix issue."""
        stage: str = dspy.InputField(desc="Active DSpec tier.")
        prior_tiers: str = dspy.InputField(desc="Higher-authority prior tiers for consistency checking.")
        spec_markdown: str = dspy.InputField(desc="Specification draft under review.")
        discovery_answers: str = dspy.InputField(desc="Saved user decisions for this tier and prior tiers; flag contradictions with these decisions.")
        review: SemanticReviewResult = dspy.OutputField(desc="Independent semantic review result.")

    class ReviseSpec(dspy.Signature):
        """Apply one explicit review instruction to the current DSpec tier. Return the complete revised Markdown, preserve unrelated valid content and higher-authority constraints, do not broaden scope, and never invent validation, deployment, security-certification, or approval evidence."""
        stage: str = dspy.InputField(desc="constitution, requirements, solution, or tasks")
        prior_tiers: str = dspy.InputField(desc="Higher-authority prior tiers that must remain satisfied.")
        current_spec: str = dspy.InputField(desc="Current active tier draft to revise.")
        review_instruction: str = dspy.InputField(desc="One concrete must-fix or recommendation to apply.")
        revised_spec: str = dspy.OutputField(desc="Complete revised Markdown for the active tier.")
        quality_assessment: SpecQualityRubric = dspy.OutputField(desc="Semantic assessment of the revised tier.")

    class DiscoverSpecGaps(dspy.Signature):
        """Return only a compact structured discovery result for the actual active product context. Do not narrate analysis. Report at most 3 material gaps and ask at most 3 material questions; prefer 1-2 questions when they are sufficient and use a third only for another material decision. Each question must offer 2-3 concise coherent options with one recommended default and short rationale. Ground every item in supplied intent and active prior-tier context. Constitution questions focus only on purpose and non-negotiable product/operational/security/privacy boundaries; never invent boards, shareholders, public APIs, compliance programs, accounts, cloud hosting, or enterprise governance unless the active product intent requires them. Requirements questions focus on observable user outcomes, domain behavior, failure/recovery, and exclusions. Solution questions focus on consequential technical constraints. Tasks questions focus on sequencing and verification. Do not ask implementation trivia that can safely be inferred and do not repeat already answered decisions."""
        stage: str = dspy.InputField(desc="constitution, requirements, solution, or tasks")
        prior_tiers: str = dspy.InputField(desc="Current prior-tier specifications, if any.")
        current_answers: str = dspy.InputField(desc="Saved discovery input and current draft context.")
        discovery: DiscoveryResult = dspy.OutputField(desc="Structured gap analysis and MCQ questions.")

    DSPY_AVAILABLE = True
except Exception:
    DSPY_AVAILABLE = False
    IdeaToConstitution = ScopeToRequirements = ArchitectureToSolution = SpecToTasks = DiscoverSpecGaps = SemanticSpecReview = ReviseSpec = None  # type: ignore


def status() -> dict[str, Any]:
    from .optimization_store import promoted_status

    optimization = promoted_status()
    return {
        "available": DSPY_AVAILABLE,
        "optimization": optimization["overall"],
        "optimization_stages": optimization["stages"],
        "refinement": "Refine",
        "mipro_v2": (
            "PROMOTED_STATE_PRESENT"
            if optimization["promoted_stage_count"]
            else "BLOCKED_PENDING_REVIEWED_TRAINING_SET"
        ),
    }
