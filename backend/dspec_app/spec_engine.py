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
else:
    IdeaToConstitution = ScopeToRequirements = ArchitectureToSolution = SpecToTasks = None

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


def generate(stage: str, context: dict[str, str], answers_json: str) -> dict:
    configure_dspy()
    signature = SIGNATURES.get(stage)
    if signature is None:
        raise ValueError("invalid stage")
    generator = dspy.ChainOfThought(signature)
    if stage == "constitution":
        prediction = generator(raw_idea=context.get("idea", ""), answers_json=answers_json)
    elif stage == "requirements":
        prediction = generator(constitution=context.get("constitution", ""), answers_json=answers_json)
    elif stage == "solution":
        prediction = generator(
            constitution=context.get("constitution", ""), requirements=context.get("requirements", ""), answers_json=answers_json
        )
    else:
        prediction = generator(
            constitution=context.get("constitution", ""),
            requirements=context.get("requirements", ""),
            solution=context.get("solution", ""),
            answers_json=answers_json,
        )
    content = str(prediction.specification)
    review = validate_candidate(stage, content)
    if review["must_fix"] or review["score"] < 0.90:
        def metric(_args: dict, pred: Any) -> float:
            candidate = str(getattr(pred, "specification", ""))
            return validate_candidate(stage, candidate)["score"]
        refined = dspy.Refine(generator, N=2, reward_fn=metric, threshold=0.90)
        if stage == "constitution":
            prediction = refined(raw_idea=context.get("idea", ""), answers_json=answers_json)
        elif stage == "requirements":
            prediction = refined(constitution=context.get("constitution", ""), answers_json=answers_json)
        elif stage == "solution":
            prediction = refined(
                constitution=context.get("constitution", ""), requirements=context.get("requirements", ""), answers_json=answers_json
            )
        else:
            prediction = refined(
                constitution=context.get("constitution", ""),
                requirements=context.get("requirements", ""),
                solution=context.get("solution", ""),
                answers_json=answers_json,
            )
        content = str(prediction.specification)
        review = validate_candidate(stage, content)
    return {"content": content, "review": review}
