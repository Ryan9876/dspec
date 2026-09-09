from __future__ import annotations

import asyncio
from types import SimpleNamespace

import dspy
import pytest

from dspec.spec_engine import SpecEngine
from dspec.streaming import StageFieldStreamParser, provider_chunk_text


def simplify(events):
    return [(event.event, event.attempt, event.text) for event in events]


def test_parser_extracts_only_target_field_and_hides_reasoning():
    parser = StageFieldStreamParser("constitution_spec")
    events = []
    for chunk in [
        "[[ ## reasoning ## ]]\nprivate chain of thought\n",
        "[[ ## constitution_",
        "spec ## ]]\n# Constitution\n\nUseful content.",
        "\n[[ ## quality_assessment ## ]]\n{\"rubric_score\": 0.95}",
        "\n[[ ## completed ## ]]",
    ]:
        events.extend(parser.feed(chunk))

    assert simplify(events) == [
        ("candidate_start", 1, None),
        ("token", 1, "# Constitution\n\nUseful content."),
        ("token", 1, "\n"),
        ("candidate_end", 1, None),
    ]
    visible = "".join(event.text or "" for event in events)
    assert "private chain of thought" not in visible
    assert "rubric_score" not in visible


def test_parser_handles_split_end_marker_without_leaking_it():
    parser = StageFieldStreamParser("requirements_spec")
    events = []
    events.extend(parser.feed("[[ ## requirements_spec ## ]]\n# Requirements\n"))
    events.extend(parser.feed("Body[[ ## quality_"))
    events.extend(parser.feed("assessment ## ]]ignored"))

    assert simplify(events) == [
        ("candidate_start", 1, None),
        ("token", 1, "# Requirements\n"),
        ("token", 1, "Body"),
        ("candidate_end", 1, None),
    ]


def test_parser_tracks_multiple_refine_candidates_and_ignores_feedback_calls():
    parser = StageFieldStreamParser("solution_spec")
    events = []
    stream = [
        "[[ ## solution_spec ## ]]\n# Candidate one",
        "[[ ## quality_assessment ## ]]{}",
        "[[ ## discussion ## ]]internal feedback[[ ## advice ## ]]{}",
        "[[ ## solution_spec ## ]]\n# Candidate two",
        "[[ ## quality_assessment ## ]]{}",
    ]
    for chunk in stream:
        events.extend(parser.feed(chunk))

    assert simplify(events) == [
        ("candidate_start", 1, None),
        ("token", 1, "# Candidate one"),
        ("candidate_end", 1, None),
        ("candidate_start", 2, None),
        ("token", 2, "# Candidate two"),
        ("candidate_end", 2, None),
    ]


def test_finalize_flushes_incomplete_provisional_candidate_only():
    parser = StageFieldStreamParser("tasks_spec")
    events = parser.feed("[[ ## tasks_spec ## ]]\n# Tasks") + parser.finalize()

    assert simplify(events) == [
        ("candidate_start", 1, None),
        ("token", 1, "# Tasks"),
        ("candidate_end", 1, None),
    ]


def test_provider_chunk_text_reads_openai_compatible_delta():
    chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="hello"))]
    )
    assert provider_chunk_text(chunk) == "hello"
    assert provider_chunk_text(SimpleNamespace(choices=[])) is None
    assert provider_chunk_text(object()) is None



def test_generate_stream_uses_provider_chunks_and_returns_final_refine_prediction(
    monkeypatch: pytest.MonkeyPatch,
):
    final_content = """# Requirements

## User workflow
The user can save a governed draft and recover from provider interruption.

## Acceptance criteria
FR-1 preserves state across refresh and verification confirms the stored revision.

## Error and edge cases
Invalid input returns an explicit error response. Provider failure preserves state and offers recovery.
"""

    engine = SpecEngine(SimpleNamespace())

    async def selected(_gateway):
        return {"provider": "lm_studio", "model": "fixture-model"}

    monkeypatch.setattr("dspec.spec_engine.selected_for_inference", selected)
    monkeypatch.setattr(
        engine,
        "_generation_program",
        lambda stage, selected: (
            SimpleNamespace(),
            SimpleNamespace(),
            "requirements_spec",
            None,
        ),
    )

    def chunk(text):
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=text))]
        )

    final_prediction = dspy.Prediction(
        requirements_spec=final_content,
        quality_assessment={
            "has_no_ambiguous_todos": True,
            "has_typed_schemas": True,
            "has_error_contracts": True,
            "agent_executable": True,
            "rubric_score": 0.96,
        },
    )

    async def fake_streamer(**kwargs):
        assert "functional_scope_answers" in kwargs
        yield chunk("[[ ## reasoning ## ]]hidden")
        yield chunk("[[ ## requirements_spec ## ]]\\n# Provisional")
        yield chunk(" requirements")
        yield chunk("[[ ## quality_assessment ## ]]{}")
        yield final_prediction

    monkeypatch.setattr(dspy, "streamify", lambda _program: fake_streamer)

    session = {"answers": [], "specs": {}}

    async def collect():
        return [
            item
            async for item in engine.generate_stream(
                session,
                "requirements",
                "keep failure recovery explicit",
            )
        ]

    events = asyncio.run(collect())
    assert [item["event"] for item in events] == [
        "candidate_start",
        "token",
        "token",
        "candidate_end",
        "final",
    ]
    visible = "".join(item.get("text", "") for item in events if item["event"] == "token")
    assert visible == "# Provisional requirements"
    assert "hidden" not in visible
    final = events[-1]
    assert final["content"] == final_content.strip()
    assert final["review"]["score"] >= 0.90
    assert final["review"]["passed"] is True
    assert final["metrics"]["first_provisional_chunk_ms"] is not None
