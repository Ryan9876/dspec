"""Only the model boundary is deterministic; run the production HTTP/DSPy/DB path."""
import json

import uvicorn
from dspy.utils import DummyLM

from dspec.app import app, engine
import dspec.spec_engine as spec_engine
from test_app import DRAFTS


def fixture_lm(selected):
    return DummyLM({
        "[[ ## review_instruction ## ]]": {
            "reasoning": "Preserve the original scope and add explicit recovery.",
            "revised_spec": DRAFTS["requirements"] + "\n\n## Retry recovery\nRetry preserves all saved user input.",
            "quality_assessment": json.dumps({
                "has_no_ambiguous_todos": True, "has_typed_schemas": True,
                "has_error_contracts": True, "agent_executable": True, "rubric_score": 0.97,
            }),
        },
        "[[ ## spec_markdown ## ]]": {
            "review": json.dumps({"score": 0.97, "must_fix": [], "consistency_issues": [], "recommendations": ["Add retry recovery."]}),
        },
    })


async def fixture_selected_for_inference(gateway):
    # This server intentionally replaces only the model boundary with DummyLM.
    # Keep provider selection deterministic without probing a real local service.
    return gateway.selected()


if __name__ == "__main__":
    engine._lm = fixture_lm
    spec_engine.selected_for_inference = fixture_selected_for_inference
    uvicorn.run(app, host="127.0.0.1", port=3210)
