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

    class EngineeringConcept(BaseModel):
        id: str
        label: str
        mental_model: str

    class TechnicalDetail(BaseModel):
        category: str
        choice: str
        consequence: str

    class ArchitectureOption(BaseModel):
        id: str
        role: str = Field(description="best_fit, simplest, or alternative")
        title: str
        plain_english_summary: str
        why_recommended: str
        advantages: list[str]
        tradeoffs: list[str]
        operational_impact: str
        why_engineers_care: str
        engineering_concept: EngineeringConcept
        reconsider_when: list[str]
        technical_details: list[TechnicalDetail]

    class ArchitectureOptionsResult(BaseModel):
        recommended_option_id: str
        alternative_objective: str
        options: list[ArchitectureOption]
        decision_summary: str

    class TaskExecutionProfile(BaseModel):
        task_id: str
        title: str
        task_text: str
        requirement_ids: list[str]
        solution_refs: list[str]
        reasoning_complexity: str = Field(description="easy, medium, or hard")
        context_breadth: str = Field(description="isolated, component, subsystem, or cross_system")
        ambiguity: str = Field(description="low, medium, or high")
        blast_radius: str
        risk_flags: list[str]
        minimum_safe_capability: str = Field(description="local, standard, or advanced")
        validation: str
        bounded_context_refs: list[str]

    class TaskExecutionProfilesResult(BaseModel):
        profiles: list[TaskExecutionProfile]
        summary: str

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
        """Create ordered, executable work packages derived from requirements and solution. Give every material task a stable T-### identifier. Each material task must map to requirement IDs, name exact implementation scope, dependencies, preservation constraints, and a concrete terminal verification command or objective assertion. Tasks must not create new product scope. Keep tasks granular enough that downstream capability/risk classification can route work without rewriting the canonical task."""
        constitution_context: str = dspy.InputField(desc="Governing constraints.")
        requirements_spec: str = dspy.InputField(desc="Required behavior and acceptance criteria.")
        solution_spec: str = dspy.InputField(desc="Approved implementation direction.")
        tasks_spec: str = dspy.OutputField(desc="Complete ordered Markdown task plan with requirement traceability and verification.")
        quality_assessment: SpecQualityRubric = dspy.OutputField(desc="Semantic assessment of task executability and traceability.")

    class RequirementsToArchitectureOptions(dspy.Signature):
        """Compare exactly three coherent implementation approaches derived from the actual requirements and governing constraints. Always return best_fit, simplest, and one meaningful requirement-specific alternative. Explain user/business consequences first, then engineering principle and technical details. Do not ask the user to choose isolated technologies that may be incompatible. Best Fit must be the recommendation unless the supplied constraints make another role definition impossible. Preserve explicit environment, deployment, licensing, security, and organizational constraints."""
        constitution_context: str = dspy.InputField(desc="Governing product, security, runtime, and operational constraints.")
        requirements_spec: str = dspy.InputField(desc="Observable requirements and acceptance criteria that must drive the options.")
        user_preferences: str = dspy.InputField(desc="Saved user preferences and constraints; treat implementation preferences as hypotheses unless explicitly required.")
        prior_concepts: str = dspy.InputField(desc="Previously encountered engineering concept IDs/labels used only to tune explanation depth, never to change the recommendation.")
        architecture_options: ArchitectureOptionsResult = dspy.OutputField(desc="Exactly three side-by-side coherent stack/architecture options with Best Fit recommended and plain-English consequences before technical details.")

    class TasksToExecutionProfiles(dspy.Signature):
        """Classify canonical implementation tasks for safe downstream model routing without changing task scope. For every task preserve its ID/text and map requirement/solution references. Assess reasoning complexity, context breadth, ambiguity, blast radius, risk flags, minimum safe capability, validation, and bounded context references. Security-sensitive, authorization, credential, destructive-data, irreversible migration, or irreversible external-write work must require advanced capability. When uncertain, choose the safer higher capability and state the risk rather than under-classifying."""
        requirements_spec: str = dspy.InputField(desc="Authoritative requirements and acceptance criteria.")
        solution_spec: str = dspy.InputField(desc="Approved implementation direction.")
        tasks_spec: str = dspy.InputField(desc="Canonical task plan; do not rewrite or create new product scope.")
        execution_profiles: TaskExecutionProfilesResult = dspy.OutputField(desc="One routing profile per canonical task, preserving traceability and validation.")

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
        """Analyze the actual saved product input for the active DSpec stage. Ground every question in supplied intent and prior-tier context. Ask only high-value questions whose answers materially improve completeness or reduce ambiguity, prefer 1-3 concise multiple-choice questions, include a recommended default with rationale, and preserve free-text expansion. Present user-facing choices in plain English first: consequences, recommendation, advantages/tradeoffs, risks, and why the decision matters. Introduce the engineering term/principle in context and keep precise technical details available rather than hiding them. Constitution questions focus on purpose and non-negotiable product/operational/security/privacy boundaries. Requirements questions focus on user outcomes, domain behavior, failure/recovery, and exclusions. Solution questions focus on consequential technical constraints. Tasks questions focus on sequencing and verification. Do not ask implementation trivia that can safely be inferred."""
        stage: str = dspy.InputField(desc="constitution, requirements, solution, or tasks")
        prior_tiers: str = dspy.InputField(desc="Current prior-tier specifications, if any.")
        current_answers: str = dspy.InputField(desc="Saved discovery input and current draft context.")
        discovery: DiscoveryResult = dspy.OutputField(desc="Structured gap analysis and MCQ questions.")

    DSPY_AVAILABLE = True
except Exception:
    DSPY_AVAILABLE = False
    IdeaToConstitution = ScopeToRequirements = ArchitectureToSolution = SpecToTasks = DiscoverSpecGaps = SemanticSpecReview = ReviseSpec = RequirementsToArchitectureOptions = TasksToExecutionProfiles = None  # type: ignore


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
