from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

KNOWN_TECH_TERMS = (
    "microsoft sql server",
    "sql server",
    "postgresql",
    "postgres",
    "mysql",
    "mariadb",
    "sqlite",
    "mongodb",
    "asp.net",
    ".net",
    "typescript",
    "javascript",
    "python",
    "java",
    "next.js",
    "react",
    "azure",
    "aws",
    "gcp",
    "kubernetes",
    "windows",
    "linux",
    "macos",
    "entra id",
    "active directory",
)


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _option_text(option: dict[str, Any]) -> str:
    parts = [
        str(option.get("title") or ""),
        str(option.get("plain_english_summary") or ""),
        str(option.get("operational_impact") or ""),
    ]
    for detail in option.get("technical_details") or []:
        if isinstance(detail, dict):
            parts.extend(
                [
                    str(detail.get("category") or ""),
                    str(detail.get("choice") or ""),
                    str(detail.get("consequence") or ""),
                ]
            )
    return _normalized(" ".join(parts))


def _explicit_required_technology(text: str) -> set[str]:
    lowered = _normalized(text)
    required: set[str] = set()
    for term in KNOWN_TECH_TERMS:
        term_pattern = re.escape(term)
        patterns = (
            rf"\bmust\s+use\s+{term_pattern}\b",
            rf"\brequire(?:s|d)?\s+{term_pattern}\b",
            rf"\bstandard(?:ize|ized)?\s+on\s+{term_pattern}\b",
            rf"\buse\s+{term_pattern}\s+instead\b",
        )
        if any(re.search(pattern, lowered) for pattern in patterns):
            required.add(term)
    return required


def validate_architecture_option(
    option: dict[str, Any],
    *,
    constitution: str,
    requirements: str,
    customization: str = "",
) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    checked: list[str] = []
    option_text = _option_text(option)
    governing = _normalized(f"{constitution}\n{requirements}")
    requested = _explicit_required_technology(f"{constitution}\n{requirements}\n{customization}")

    details = option.get("technical_details") or []
    if not isinstance(details, list) or not any(isinstance(item, dict) and str(item.get("choice") or "").strip() for item in details):
        issues.append("The option does not identify the technical components needed to validate the stack.")
    else:
        checked.append("technical components present")

    for term in sorted(requested):
        if term not in option_text:
            issues.append(f"Explicit required technology is not reflected in this option: {term}.")
        else:
            checked.append(f"explicit technology constraint: {term}")

    local_only = any(phrase in governing for phrase in ("local-only", "must remain local", "no cloud", "must not use cloud"))
    cloud_only = any(phrase in option_text for phrase in ("cloud-only", "public cloud only", "requires public cloud"))
    if local_only and cloud_only:
        issues.append("The option requires cloud hosting but the governing constraints require a local-only deployment.")
        checked.append("deployment boundary")

    if "windows only" in governing and "linux only" in option_text:
        issues.append("The option is Linux-only but the governing constraints require Windows-only deployment.")
        checked.append("operating system constraint")
    if "linux only" in governing and "windows only" in option_text:
        issues.append("The option is Windows-only but the governing constraints require Linux-only deployment.")
        checked.append("operating system constraint")

    durable_required = any(
        phrase in governing
        for phrase in ("durable data", "persistent data", "data must persist", "durable storage", "persistent storage")
    )
    ephemeral = any(phrase in option_text for phrase in ("in-memory database", "ephemeral storage", "memory-only database"))
    if durable_required and ephemeral:
        issues.append("The option uses ephemeral/in-memory storage but the requirements require durable persisted data.")
        checked.append("data durability")

    high_write = any(phrase in governing for phrase in ("high write concurrency", "many concurrent writers", "heavy concurrent writes"))
    if high_write and "sqlite" in option_text:
        warnings.append("SQLite may become an operational constraint under the stated high concurrent-write requirement; validate workload limits before selection.")
        checked.append("write-concurrency risk")

    status = "FAIL" if issues else "PASS"
    return {
        "status": status,
        "issues": issues,
        "warnings": warnings,
        "checked_constraints": checked,
        "scope": "deterministic contradiction checks only; PASS does not claim universal stack compatibility",
    }


def enrich_architecture_options(
    payload: dict[str, Any],
    *,
    constitution: str,
    requirements: str,
    customization: str = "",
) -> dict[str, Any]:
    enriched = deepcopy(payload)
    confidence = str(enriched.get("recommendation_confidence") or "unknown").strip().lower()
    if confidence not in {"high", "medium", "low", "unknown"}:
        confidence = "unknown"
    enriched["recommendation_confidence"] = confidence
    assumptions = [str(item).strip() for item in enriched.get("assumptions_unknowns") or [] if str(item).strip()]
    if any(any(word in item.lower() for word in ("unknown", "missing", "unclear", "not provided")) for item in assumptions):
        if confidence in {"high", "medium"}:
            confidence = "low"
            enriched["recommendation_confidence"] = confidence
    summary = str(enriched.get("decision_summary") or "").strip()
    confidence_line = f"Recommendation confidence: {confidence.upper()}."
    unknown_line = " Assumptions/unknowns: " + "; ".join(assumptions) if assumptions else ""
    if confidence_line.lower() not in summary.lower():
        enriched["decision_summary"] = (summary + " " + confidence_line + unknown_line).strip()

    rows = enriched.get("options") or []
    for option in rows:
        if not isinstance(option, dict):
            continue
        compatibility = validate_architecture_option(
            option,
            constitution=constitution,
            requirements=requirements,
            customization=customization,
        )
        option["compatibility"] = compatibility
        base_summary = str(option.get("plain_english_summary") or "").strip()
        cost = str(option.get("cost_level_or_range") or "UNKNOWN — insufficient cost evidence.").strip()
        scale = str(option.get("scalability_flexibility") or "UNKNOWN — scalability/flexibility implications were not established.").strip()
        option["plain_english_summary"] = (
            f"{base_summary}\n\nCost / range — {cost}\nScalability / flexibility — {scale}"
        ).strip()
        impact = str(option.get("operational_impact") or "").strip()
        if compatibility["issues"]:
            impact += " Compatibility issue — " + " ".join(compatibility["issues"])
        elif compatibility["warnings"]:
            impact += " Compatibility warning — " + " ".join(compatibility["warnings"])
        elif customization and compatibility["checked_constraints"]:
            impact += " Compatibility check — no deterministic conflict was found for the explicit customization; semantic review still applies."
        option["operational_impact"] = impact.strip()
    return enriched
