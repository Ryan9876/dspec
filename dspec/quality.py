from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

TODO_RE = re.compile(r"\b(TODO|FIXME|TBD)\b|//\s*TODO", re.IGNORECASE)

@dataclass
class Check:
    id: str
    label: str
    passed: bool
    weight: float
    detail: str
    recommendation: str | None = None


def evaluate(stage: str, content: str) -> dict[str, Any]:
    text = content.strip()
    lower = text.lower()
    checks: list[Check] = []
    checks.append(Check("nonempty", "Substantive content", len(text) >= 120, 0.15, f"{len(text)} characters", "Expand the draft with concrete scope and acceptance behavior."))
    checks.append(Check("no_placeholders", "No ambiguous placeholders", TODO_RE.search(text) is None, 0.20, "No TODO/FIXME/TBD markers" if TODO_RE.search(text) is None else "Placeholder marker detected", "Replace every placeholder with a concrete decision or explicitly blocked requirement."))
    checks.append(Check("structure", "Structured Markdown", ("#" in text and ("##" in text or "- " in text)), 0.10, "Markdown hierarchy detected", "Organize the spec into explicit sections."))

    if stage == "constitution":
        checks.extend([
            Check("security", "Security boundary defined", any(k in lower for k in ["security", "credential", "secret", "authorization"]), 0.20, "Security-related rule detected", "Add explicit security/credential boundaries."),
            Check("quality", "Quality/validation rule defined", any(k in lower for k in ["quality", "validation", "test", "acceptance"]), 0.20, "Quality rule detected", "Add validation and quality gates."),
            Check("runtime", "Operational/runtime constraint defined", any(k in lower for k in ["runtime", "deploy", "port", "host", "operation"]), 0.15, "Runtime constraint detected", "Define runtime and operational constraints."),
        ])
    elif stage == "requirements":
        checks.extend([
            Check("stories", "User workflow/story coverage", any(k in lower for k in ["user story", "workflow", "journey", "user can"]), 0.20, "Workflow language detected", "Add user stories or end-to-end workflows."),
            Check("acceptance", "Acceptance criteria present", any(k in lower for k in ["acceptance", "must", "shall", "verification"]), 0.20, "Acceptance language detected", "Add observable acceptance criteria."),
            Check("edge", "Edge/error cases present", any(k in lower for k in ["error", "failure", "edge case", "invalid"]), 0.15, "Failure behavior detected", "Add error and edge-case behavior."),
        ])
    elif stage == "solution":
        checks.extend([
            Check("typed_schema", "Typed data/schema definitions", bool(re.search(r"\b(TEXT|INTEGER|REAL|BOOLEAN|UUID|VARCHAR|JSON|DATETIME|str|int|float|bool)\b", text, re.IGNORECASE)), 0.20, "Concrete type token detected", "Define concrete field/column types and relationships."),
            Check("errors", "API/error contracts", any(k in lower for k in ["error response", "error contract", "status code", "422", "400", "500", "503"]), 0.20, "Error contract language detected", "Document request, response, error models, and status codes for each API."),
            Check("interfaces", "Interfaces/components are explicit", any(k in lower for k in ["api", "endpoint", "component", "interface", "schema"]), 0.15, "Interface language detected", "Define components and their interfaces."),
        ])
    else:
        checks.extend([
            Check("commands", "Verification commands/assertions", bool(re.search(r"`[^`]*(pytest|npm|pnpm|curl|python|uvicorn|test)[^`]*`", text, re.IGNORECASE)), 0.25, "Executable verification command detected", "Add a concrete terminal verification command to each material task."),
            Check("sequence", "Ordered work packages", bool(re.search(r"(^|\n)\s*(\d+\.|- \[[ xX]\])", text)), 0.20, "Ordered/checklist task structure detected", "Order work into granular tasks with dependencies."),
            Check("trace", "Traceability to requirements", any(k in lower for k in ["fr-", "requirement", "acceptance"]), 0.10, "Requirement trace detected", "Map tasks to requirement IDs or acceptance criteria."),
        ])

    total_weight = sum(c.weight for c in checks)
    score = sum(c.weight for c in checks if c.passed) / total_weight if total_weight else 0.0
    passing = [asdict(c) for c in checks if c.passed]
    must_fix = [asdict(c) for c in checks if not c.passed]
    return {
        "score": round(score, 3),
        "threshold": 0.90,
        "passed": score >= 0.90 and not TODO_RE.search(text),
        "checks": [asdict(c) for c in checks],
        "passing": passing,
        "must_fix": must_fix,
        "recommendations": [c.recommendation for c in checks if not c.passed and c.recommendation],
        "assessment_scope": "deterministic structural checks; semantic AI review is separate",
    }
