from __future__ import annotations

from typing import Any

try:
    import dspy
    from pydantic import BaseModel, Field

    class SpecQualityRubric(BaseModel):
        has_no_ambiguous_todos: bool
        has_typed_schemas: bool
        has_error_contracts: bool
        agent_executable: bool
        rubric_score: float = Field(ge=0.0, le=1.0)

    class IdeaToConstitution(dspy.Signature):
        """Create concrete project governance, stack, security, operational constraints, and quality gates."""
        raw_idea_description: str = dspy.InputField()
        security_isolation_preferences: str = dspy.InputField()
        constitution_spec: str = dspy.OutputField()
        quality_assessment: SpecQualityRubric = dspy.OutputField()

    class ScopeToRequirements(dspy.Signature):
        """Create user journeys, entities, functional requirements, edge cases, and acceptance criteria."""
        constitution_context: str = dspy.InputField()
        functional_scope_answers: str = dspy.InputField()
        requirements_spec: str = dspy.OutputField()
        quality_assessment: SpecQualityRubric = dspy.OutputField()

    class ArchitectureToSolution(dspy.Signature):
        """Create explicit components, typed data schemas, API contracts, state, error handling, and rollout details."""
        constitution_context: str = dspy.InputField()
        requirements_spec: str = dspy.InputField()
        user_architectural_preferences: str = dspy.InputField()
        solution_spec: str = dspy.OutputField()
        quality_assessment: SpecQualityRubric = dspy.OutputField()

    class SpecToTasks(dspy.Signature):
        """Create ordered implementation tasks mapped to requirements with concrete verification commands."""
        constitution_context: str = dspy.InputField()
        requirements_spec: str = dspy.InputField()
        solution_spec: str = dspy.InputField()
        tasks_spec: str = dspy.OutputField()
        quality_assessment: SpecQualityRubric = dspy.OutputField()

    DSPY_AVAILABLE = True
except Exception:
    DSPY_AVAILABLE = False
    IdeaToConstitution = ScopeToRequirements = ArchitectureToSolution = SpecToTasks = None  # type: ignore


def status() -> dict[str, Any]:
    return {"available": DSPY_AVAILABLE, "optimization": "UNOPTIMIZED", "mipro_v2": "BLOCKED_PENDING_REVIEWED_TRAINING_SET"}
