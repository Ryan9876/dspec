from __future__ import annotations

from copy import deepcopy

from dspec.architecture_compatibility import enrich_architecture_options, validate_architecture_option
from dspec.explanation_policy import adapt_concept_explanations
from dspec.spec_engine import SpecEngine


def option(*, database: str = "PostgreSQL", summary: str = "A coherent application stack.") -> dict:
    return {
        "id": "best",
        "role": "best_fit",
        "title": "Best Fit — Recommended",
        "plain_english_summary": summary,
        "why_recommended": "Best overall requirements fit.",
        "advantages": ["Good fit"],
        "tradeoffs": ["Some operating work"],
        "operational_impact": "Operate the application and database.",
        "cost_level_or_range": "UNKNOWN — deployment pricing is not established.",
        "scalability_flexibility": "Supports growth without requiring early service decomposition.",
        "why_engineers_care": "Core platform choices are expensive to reverse later.",
        "engineering_concept": {
            "id": "separation-of-concerns",
            "label": "Separation of concerns",
            "mental_model": "Keep responsibilities behind clear boundaries so each part can change without forcing unrelated parts to change.",
        },
        "reconsider_when": ["The workload changes materially."],
        "technical_details": [
            {"category": "Database", "choice": database, "consequence": "Relational persistence."}
        ],
    }


def test_explicit_customization_is_deterministically_rechecked():
    missing = validate_architecture_option(
        option(database="PostgreSQL"),
        constitution="",
        requirements="Shared structured data.",
        customization="Use SQL Server instead of PostgreSQL because it is already operated.",
    )
    assert missing["status"] == "FAIL"
    assert any("sql server" in issue.lower() for issue in missing["issues"])

    reflected = validate_architecture_option(
        option(database="SQL Server"),
        constitution="",
        requirements="Shared structured data.",
        customization="Use SQL Server instead of PostgreSQL because it is already operated.",
    )
    assert reflected["status"] == "PASS"
    assert any("sql server" in item.lower() for item in reflected["checked_constraints"])


def test_local_only_constraint_rejects_cloud_only_stack():
    result = validate_architecture_option(
        option(summary="This option requires public cloud only hosting."),
        constitution="The product must remain local-only.",
        requirements="Persistent structured data.",
    )
    assert result["status"] == "FAIL"
    assert any("cloud" in issue.lower() for issue in result["issues"])


def test_enrichment_surfaces_consistent_dimensions_and_uncertainty():
    payload = {
        "recommended_option_id": "best",
        "recommendation_confidence": "high",
        "alternative_objective": "portability",
        "decision_summary": "Best Fit is recommended.",
        "assumptions_unknowns": ["Deployment target is UNKNOWN."],
        "options": [option()],
    }
    enriched = enrich_architecture_options(payload, constitution="", requirements="Shared data.")
    row = enriched["options"][0]
    assert "Cost / range — UNKNOWN" in row["plain_english_summary"]
    assert "Scalability / flexibility — Supports growth" in row["plain_english_summary"]
    assert enriched["recommendation_confidence"] == "low"
    assert "Recommendation confidence: LOW" in enriched["decision_summary"]


def test_concept_memory_changes_explanation_only():
    payload = {
        "recommended_option_id": "best",
        "decision_summary": "Best Fit is recommended.",
        "options": [option()],
    }
    original = deepcopy(payload)
    adapted = adapt_concept_explanations(
        payload,
        [{"concept_id": "separation-of-concerns", "exposure_count": 4, "user_marked_understood": 0}],
    )
    assert adapted["recommended_option_id"] == original["recommended_option_id"]
    assert adapted["decision_summary"] == original["decision_summary"]
    assert adapted["options"][0]["why_recommended"] == original["options"][0]["why_recommended"]
    assert adapted["options"][0]["technical_details"] == original["options"][0]["technical_details"]
    assert adapted["options"][0]["engineering_concept"]["explanation_depth"] == "concise_repeat"
    assert len(adapted["options"][0]["engineering_concept"]["mental_model"]) <= 130


def test_selected_architecture_is_bound_into_solution_generation_input():
    session = {
        "specs": {
            "constitution": {"content": "# Constitution\nLocal app."},
            "requirements": {"content": "# Requirements\nREQ-001 persistent data."},
        },
        "answers": [],
        "engineering_decisions": [
            {
                "decision_type": "architecture",
                "selected_option_id": "best",
                "source_context_sha256": "a" * 64,
                "custom": {"instruction": "Use SQL Server"},
                "options": {"options": [option(database="SQL Server")]},
            }
        ],
    }
    engine = SpecEngine(None)  # type: ignore[arg-type]
    inputs = engine._inputs(session, "solution", None)
    assert "Selected architecture decision" in inputs["user_architectural_preferences"]
    assert "SQL Server" in inputs["user_architectural_preferences"]
    assert "Use SQL Server" in inputs["user_architectural_preferences"]
