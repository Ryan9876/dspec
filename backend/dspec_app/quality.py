from __future__ import annotations

import re
from dataclasses import dataclass, asdict

PLACEHOLDER_PATTERNS = [
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bTBD\b", re.IGNORECASE),
    re.compile(r"implement\s+later", re.IGNORECASE),
    re.compile(r"placeholder", re.IGNORECASE),
]

@dataclass
class QualityReview:
    score: float
    passing: list[str]
    must_fix: list[dict]
    recommendations: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


def review_spec(stage: str, content: str) -> QualityReview:
    text = content.strip()
    passing: list[str] = []
    must_fix: list[dict] = []
    recommendations: list[dict] = []
    if len(text) < 240:
        must_fix.append({"id": "content_depth", "message": "Draft is too short to demonstrate an actionable specification."})
    else:
        passing.append("Substantive draft content present")

    found_placeholders = [p.pattern for p in PLACEHOLDER_PATTERNS if p.search(text)]
    if found_placeholders:
        must_fix.append({"id": "ambiguous_placeholder", "message": "Ambiguous unfinished placeholder language is present."})
    else:
        passing.append("No ambiguous unfinished placeholder markers detected")

    lower = text.lower()
    stage_checks: dict[str, list[tuple[str, tuple[str, ...]]]] = {
        "constitution": [
            ("security", ("security", "authorization", "credential")),
            ("quality", ("quality", "verification", "acceptance")),
            ("runtime", ("runtime", "deployment", "operational")),
        ],
        "requirements": [
            ("acceptance criteria", ("acceptance", "must", "shall")),
            ("error/edge behavior", ("error", "failure", "edge case")),
            ("entities/state", ("entity", "state", "data")),
        ],
        "solution": [
            ("typed schema", ("schema", "integer", "string", "text", "boolean")),
            ("API error contracts", ("error", "status", "response")),
            ("rollback/recovery", ("rollback", "recovery", "failure")),
        ],
        "tasks": [
            ("verification commands", ("pytest", "npm", "curl", "verify", "test")),
            ("dependencies/order", ("depends", "after", "before", "sequence")),
            ("acceptance evidence", ("pass", "assert", "expected", "acceptance")),
        ],
    }
    present = 0
    for label, words in stage_checks.get(stage, []):
        if any(word in lower for word in words):
            passing.append(f"{label.capitalize()} evidence present")
            present += 1
        else:
            must_fix.append({"id": f"missing_{label.replace(' ', '_')}", "message": f"Missing explicit {label} evidence."})

    heading_count = len(re.findall(r"^#{1,4}\s+", text, re.MULTILINE))
    if heading_count >= 3:
        passing.append("Structured section hierarchy present")
    else:
        recommendations.append({"id": "structure", "message": "Use at least three explicit Markdown sections for agent readability."})

    base = 0.35
    base += min(len(text) / 5000, 0.20)
    base += 0.30 * (present / max(1, len(stage_checks.get(stage, []))))
    base += 0.10 if not found_placeholders else 0
    base += 0.05 if heading_count >= 3 else 0
    score = max(0.0, min(1.0, base - (0.12 * len(must_fix))))
    return QualityReview(round(score, 3), passing, must_fix, recommendations)
