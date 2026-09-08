from __future__ import annotations

def valid_spec(stage: str) -> str:
    anchors = {
        "constitution": "Security credential authorization controls are explicit. Quality verification and acceptance gates are explicit. Runtime deployment operational recovery is explicit.",
        "requirements": "Acceptance criteria state what the system must do. Error and failure edge case behavior is explicit. Entity state and data semantics are explicit.",
        "solution": "Typed schema fields include string text integer boolean types. API error response status contracts are explicit. Rollback recovery and failure handling are explicit.",
        "tasks": "Each task includes pytest and npm test verification. Work depends on approved requirements and runs in sequence. PASS assertions and expected acceptance evidence are explicit."
    }
    body = "\n".join(f"- Concrete executable detail {i}: {anchors[stage]}" for i in range(34))
    return f"# {stage.title()}\n\n## Scope\n\n{anchors[stage]}\n\n## Detailed Contract\n\n{body}\n\n## Verification\n\n{anchors[stage]}\n"
