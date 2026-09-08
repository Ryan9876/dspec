from __future__ import annotations

from typing import Any

from .quality import review_spec
from .providers import CredentialStore, LOCAL_ENDPOINTS, load_config

try:
    import dspy
except ImportError:
    dspy = None

if dspy is not None:
    class IdeaToConstitution(dspy.Signature):
        """Create explicit project governance, stack constraints, security boundaries, and quality gates."""
        raw_idea: str = dspy.InputField()
        answers_json: str = dspy.InputField()
        specification: str = dspy.OutputField()

    class ScopeToRequirements(dspy.Signature):
        """Create complete user journeys, functional requirements, entities, edge cases, and acceptance criteria."""
        constitution: str = dspy.InputField()
        answers_json: str = dspy.InputField()
        specification: str = dspy.OutputField()

    class ArchitectureToSolution(dspy.Signature):
        """Create concrete architecture, typed schemas, APIs, failure behavior, migration, and recovery design."""
        constitution: str = dspy.InputField()
        requirements: str = dspy.InputField()
        answers_json: str = dspy.InputField()
        specification: str = dspy.OutputField()

    class SpecToTasks(dspy.Signature):
        """Create ordered executable work packages mapped to requirements with verification commands."""
        constitution: str = dspy.InputField()
        requirements: str = dspy.InputField()
        solution: str = dspy.InputField()
        answers_json: str = dspy.InputField()
        specification: str = dspy.OutputField()

    class ReviseSpecification(dspy.Signature):
        """Revise a specification only to address the supplied review recommendation while preserving unaffected content and constraints."""
        stage: str = dspy.InputField()
        current_specification: str = dspy.InputField()
        recommendation: str = dspy.InputField()
        specification: str = dspy.OutputField()
else:
    IdeaToConstitution = ScopeToRequirements = ArchitectureToSolution = SpecToTasks = ReviseSpecification = None

SIGNATURES = {
    "constitution": IdeaToConstitution,
    "requirements": ScopeToRequirements,
    "solution": ArchitectureToSolution,
    "tasks": SpecToTasks,
}


def validate_candidate(stage: str, content: str) -> dict:
    return review_spec(stage, content).to_dict()


def dspy_ready() -> bool:
    return dspy is not None


def configure_dspy() -> dict:
    if dspy is None:
        raise RuntimeError("dspy_not_installed")
    cfg = load_config()
    provider = cfg["active_provider"]
    model = cfg["active_model"]
    if not model:
        raise RuntimeError("model_not_selected")
    store = CredentialStore()
    if provider == "lm_studio":
        lm = dspy.LM(f"openai/{model}", api_base=f"{LOCAL_ENDPOINTS[provider]}/v1", api_key="local")
    elif provider == "ollama":
        lm = dspy.LM(f"ollama_chat/{model}", api_base=LOCAL_ENDPOINTS[provider])
    elif provider == "openai":
        key = store.get(provider)
        if not key:
            raise RuntimeError("provider_not_configured")
        lm = dspy.LM(f"openai/{model}", api_key=key)
    elif provider == "anthropic":
        key = store.get(provider)
        if not key:
            raise RuntimeError("provider_not_configured")
        lm = dspy.LM(f"anthropic/{model}", api_key=key)
    else:
        raise RuntimeError("unsupported_provider")
    dspy.configure(lm=lm)
    return cfg


def _refine(stage: str, generator: Any, inputs: dict[str, str], prediction: Any) -> tuple[str, dict]:
    content = str(prediction.specification)
    review = validate_candidate(stage, content)
    if not review["must_fix"] and review["score"] >= 0.90:
        return content, review

    def metric(_args: dict, pred: Any) -> float:
        candidate = str(getattr(pred, "specification", ""))
        candidate_review = validate_candidate(stage, candidate)
        if candidate_review["must_fix"]:
            return min(candidate_review["score"], 0.89)
        return candidate_review["score"]

    refined = dspy.Refine(generator, N=2, reward_fn=metric, threshold=0.90)
    prediction = refined(**inputs)
    content = str(prediction.specification)
    return content, validate_candidate(stage, content)


def generate(stage: str, context: dict[str, str], answers_json: str) -> dict:
    configure_dspy()
    signature = SIGNATURES.get(stage)
    if signature is None:
        raise ValueError("invalid stage")
    generator = dspy.ChainOfThought(signature)
    if stage == "constitution":
        inputs = {"raw_idea": context.get("idea", ""), "answers_json": answers_json}
    elif stage == "requirements":
        inputs = {"constitution": context.get("constitution", ""), "answers_json": answers_json}
    elif stage == "solution":
        inputs = {
            "constitution": context.get("constitution", ""),
            "requirements": context.get("requirements", ""),
            "answers_json": answers_json,
        }
    else:
        inputs = {
            "constitution": context.get("constitution", ""),
            "requirements": context.get("requirements", ""),
            "solution": context.get("solution", ""),
            "answers_json": answers_json,
        }
    content, review = _refine(stage, generator, inputs, generator(**inputs))
    return {"content": content, "review": review}


def revise(stage: str, current_specification: str, recommendation: str) -> dict:
    configure_dspy()
    if ReviseSpecification is None:
        raise RuntimeError("dspy_not_installed")
    generator = dspy.ChainOfThought(ReviseSpecification)
    inputs = {
        "stage": stage,
        "current_specification": current_specification,
        "recommendation": recommendation,
    }
    content, review = _refine(stage, generator, inputs, generator(**inputs))
    return {"content": content, "review": review}
