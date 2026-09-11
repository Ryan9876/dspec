from __future__ import annotations

from copy import deepcopy
from typing import Any


def _exposure_map(exposures: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("concept_id") or ""): item
        for item in exposures
        if str(item.get("concept_id") or "").strip()
    }


def _shorten(text: str, limit: int) -> str:
    value = " ".join(text.split())
    if len(value) <= limit:
        return value
    sentence = value.split(". ", 1)[0].rstrip(".")
    if sentence and len(sentence) <= limit:
        return sentence + "."
    return value[: max(0, limit - 1)].rstrip() + "…"


def adapt_concept_explanations(payload: dict[str, Any], exposures: list[dict[str, Any]]) -> dict[str, Any]:
    """Adjust only engineering-concept explanation depth.

    Recommendation IDs, rationale, risks, requirements, technical choices, and other
    decision semantics are deliberately left untouched.
    """
    adapted = deepcopy(payload)
    seen = _exposure_map(exposures)

    def adapt_option(option: dict[str, Any]) -> None:
        concept = option.get("engineering_concept")
        if not isinstance(concept, dict):
            return
        concept_id = str(concept.get("id") or "").strip()
        prior = seen.get(concept_id, {})
        count = int(prior.get("exposure_count") or 0)
        understood = bool(prior.get("user_marked_understood"))
        if understood:
            concept["mental_model"] = _shorten(str(concept.get("mental_model") or ""), 90)
            concept["explanation_depth"] = "concise_understood"
        elif count > 0:
            concept["mental_model"] = _shorten(str(concept.get("mental_model") or ""), 130)
            concept["explanation_depth"] = "concise_repeat"
        else:
            concept["explanation_depth"] = "full_first_encounter"

    for option in adapted.get("options") or []:
        if isinstance(option, dict):
            adapt_option(option)
    for question in adapted.get("questions") or []:
        if not isinstance(question, dict):
            continue
        for option in question.get("options") or []:
            if isinstance(option, dict):
                adapt_option(option)
    return adapted
