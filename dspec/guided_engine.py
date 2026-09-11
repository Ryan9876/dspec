from __future__ import annotations

import hashlib
import json
from typing import Any

from .dspy_runtime import make_lm
from .dspy_signatures import RequirementsToArchitectureOptions, TasksToExecutionProfiles
from .execution_planning import normalize_profile
from .guided_store import list_concept_exposures, record_concept_exposures
from .provider import ProviderGateway
from .provider_selection import selected_for_inference


def architecture_source_sha256(
    session: dict[str, Any],
    customization: str | None = None,
) -> str:
    payload = {
        "intent_context_sha256": session.get("intent_context_sha256"),
        "constitution": session.get("specs", {}).get("constitution", {}).get("content"),
        "requirements": session.get("specs", {}).get("requirements", {}).get("content"),
        "customization": (customization or "").strip(),
        "solution_answers": [
            {
                key: answer.get(key)
                for key in ("question_id", "selected_option_id", "free_text_payload")
            }
            for answer in session.get("answers", [])
            if answer.get("stage") == "solution"
            and not str(answer.get("question_id") or "").startswith("engineering-decision:")
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def selected_architecture_context(decisions: list[dict[str, Any]]) -> str:
    for item in decisions:
        if item.get("decision_type") != "architecture" or item.get("status") != "selected":
            continue
        options = item.get("options") or {}
        selected_id = item.get("selected_option_id")
        selected = next(
            (
                option
                for option in options.get("options", [])
                if isinstance(option, dict) and option.get("id") == selected_id
            ),
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
    return "No guided architecture option has been selected yet."


def _prior_concept_context() -> str:
    rows = list_concept_exposures(limit=20)
    if not rows:
        return "No previously encountered engineering concepts."
    # Exposure memory is not an expertise score. Only the minimum reusable
    # context is supplied so the model may shorten repeated explanations.
    return "\n".join(
        f"- {item['concept_id']}: {item['label']} (encountered {item['exposure_count']} times)"
        for item in rows
    )


def _validate_architecture_options(data: dict[str, Any]) -> dict[str, Any]:
    rows = [item for item in data.get("options", []) if isinstance(item, dict)]
    roles = [str(item.get("role") or "") for item in rows]
    if len(rows) != 3 or sorted(roles) != ["alternative", "best_fit", "simplest"]:
        raise RuntimeError(
            "Architecture comparison must contain exactly Best Fit, Simplest, and one meaningful alternative."
        )
    ids = [str(item.get("id") or "") for item in rows]
    if not all(ids) or len(set(ids)) != 3:
        raise RuntimeError("Architecture comparison returned missing or duplicate option IDs.")
    best_fit = next(item for item in rows if item.get("role") == "best_fit")
    data["recommended_option_id"] = best_fit["id"]
    return data


async def architecture_options(
    session: dict[str, Any],
    gateway: ProviderGateway,
    customization: str | None = None,
) -> dict[str, Any]:
    if RequirementsToArchitectureOptions is None:
        raise RuntimeError("DSPy architecture-option signature is unavailable.")

    requirements = session.get("specs", {}).get("requirements", {}).get("content", "").strip()
    if not requirements:
        raise ValueError(
            "A current Requirements draft is required before comparing implementation approaches."
        )

    import dspy

    selected = await selected_for_inference(gateway)
    lm = make_lm(
        selected["provider"],
        selected["model"],
        max_tokens=7000,
        temperature=0.1,
        num_retries=2,
    )
    program = dspy.Predict(RequirementsToArchitectureOptions)
    solution_answers = "\n".join(
        f"- {answer.get('question_id')}: {answer.get('selected_option_id') or 'none'}; {answer.get('free_text_payload') or ''}"
        for answer in session.get("answers", [])
        if answer.get("stage") == "solution"
        and not str(answer.get("question_id") or "").startswith("engineering-decision:")
    ) or "No saved Solution-stage preferences."
    custom_text = (customization or "").strip()
    if custom_text:
        solution_answers += (
            "\n\nUser-requested customization to re-evaluate for compatibility and consequences:\n"
            + custom_text
        )

    with dspy.context(lm=lm):
        result = await dspy.asyncify(program)(
            constitution_context=session.get("specs", {}).get("constitution", {}).get(
                "content", "No current Constitution draft."
            ),
            requirements_spec=requirements,
            user_preferences=solution_answers,
            prior_concepts=_prior_concept_context(),
        )

    options = getattr(result, "architecture_options", None)
    if hasattr(options, "model_dump"):
        data = options.model_dump()
    elif isinstance(options, dict):
        data = dict(options)
    else:
        raise RuntimeError("DSPy returned invalid architecture options.")

    data = _validate_architecture_options(data)
    data["source_context_sha256"] = architecture_source_sha256(session, custom_text)
    data["customization"] = custom_text
    data["provider"] = {"provider": selected["provider"], "model": selected["model"]}

    record_concept_exposures(
        [
            {
                "id": str((item.get("engineering_concept") or {}).get("id") or ""),
                "label": str((item.get("engineering_concept") or {}).get("label") or ""),
            }
            for item in data.get("options", [])
            if isinstance(item, dict)
        ]
    )
    return data


async def task_execution_profiles(
    session: dict[str, Any],
    gateway: ProviderGateway,
) -> dict[str, Any]:
    if TasksToExecutionProfiles is None:
        raise RuntimeError("DSPy task-routing signature is unavailable.")

    specs = session.get("specs", {})
    requirements = specs.get("requirements", {}).get("content", "").strip()
    solution = specs.get("solution", {}).get("content", "").strip()
    tasks = specs.get("tasks", {}).get("content", "").strip()
    if not requirements or not solution or not tasks:
        raise ValueError(
            "Requirements, Solution, and Tasks drafts are required before estimating implementation routing."
        )

    import dspy

    selected = await selected_for_inference(gateway)
    lm = make_lm(
        selected["provider"],
        selected["model"],
        max_tokens=10000,
        temperature=0.0,
        num_retries=2,
    )
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
        data = dict(profiles)
    else:
        raise RuntimeError("DSPy returned invalid task execution profiles.")

    rows = [item for item in data.get("profiles", []) if isinstance(item, dict)]
    if not rows:
        raise RuntimeError("Task routing produced no task profiles.")
    data["profiles"] = [normalize_profile(item, str(item.get("task_text") or "")) for item in rows]
    data["provider"] = {"provider": selected["provider"], "model": selected["model"]}
    return data
