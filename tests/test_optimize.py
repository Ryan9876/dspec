from __future__ import annotations

import json
from pathlib import Path

import pytest

from dspec.optimize import validate_dataset
from dspec.optimization_store import (
    active_manifest_path,
    active_program_path,
    load_promoted_state,
    promote_candidate,
    promoted_status,
    sha256_file,
)


GOOD_CONSTITUTION = """# Project Constitution

## Security
All credentials remain local and secrets are never logged. Authorization boundaries must be explicit.

## Quality and Validation
Every material behavior requires objective acceptance criteria, automated tests where practical, and honest NOT TESTED states.

## Runtime
The application binds only to 127.0.0.1 on port 3210. Deployment and runtime verification are separate lifecycle states.
"""


def _row(source_id: str, split: str, *, reviewed: bool = True, idea: str = "Build a local spec-first tool.") -> dict:
    return {
        "schema_version": 1,
        "source_id": source_id,
        "stage": "constitution",
        "split": split,
        "reviewed": reviewed,
        "inputs": {
            "raw_idea_description": idea,
            "security_isolation_preferences": "Local-only credentials, no telemetry, explicit runtime evidence.",
        },
        "expected_spec": GOOD_CONSTITUTION,
        "required_markers": ["Security", "127.0.0.1", "acceptance"],
    }


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def test_validate_reviewed_dataset_with_heldout_split(tmp_path: Path):
    dataset = _write_jsonl(
        tmp_path / "reviewed.jsonl",
        [_row("train-1", "train"), _row("train-2", "train"), _row("val-1", "validation")],
    )
    bundle = validate_dataset(dataset, "constitution")
    assert bundle.stage == "constitution"
    assert len(bundle.train) == 2
    assert len(bundle.validation) == 1
    assert len(bundle.sha256) == 64


def test_dataset_rejects_unreviewed_or_secret_like_examples(tmp_path: Path):
    unreviewed = _write_jsonl(
        tmp_path / "unreviewed.jsonl",
        [_row("train-1", "train", reviewed=False), _row("val-1", "validation")],
    )
    with pytest.raises(ValueError, match="not explicitly reviewed"):
        validate_dataset(unreviewed, "constitution")

    secret = _write_jsonl(
        tmp_path / "secret.jsonl",
        [
            _row("train-1", "train", idea="Use api_key=sk-1234567890abcdefghijklmnop"),
            _row("val-1", "validation"),
        ],
    )
    with pytest.raises(ValueError, match="credential-like"):
        validate_dataset(secret, "constitution")


def test_dataset_requires_quality_gated_reviewed_exemplar(tmp_path: Path):
    row = _row("train-1", "train")
    row["expected_spec"] = "# Thin\nTODO"
    dataset = _write_jsonl(tmp_path / "bad-quality.jsonl", [row, _row("val-1", "validation")])
    with pytest.raises(ValueError, match="does not meet the deterministic DSpec quality gate"):
        validate_dataset(dataset, "constitution")


def _candidate(tmp_path: Path, *, optimized: float = 0.95, baseline: float = 0.91) -> Path:
    program = tmp_path / "constitution-program.json"
    program.write_text('{"compiled": true}\n', encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "state": "candidate",
        "candidate_id": "constitution-test-candidate",
        "stage": "constitution",
        "optimizer": "MIPROv2",
        "dataset_sha256": "d" * 64,
        "baseline_validation_score": baseline,
        "optimized_validation_score": optimized,
        "promotion_minimum_score": 0.90,
        "promotion_minimum_improvement": 0.0,
        "program_state_file": program.name,
        "program_state_sha256": sha256_file(program),
    }
    path = tmp_path / "constitution-candidate.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def test_promote_candidate_is_explicit_checksum_verified_and_loadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DSPEC_OPTIMIZATION_DIR", str(tmp_path / "active"))
    manifest = _candidate(tmp_path)

    with pytest.raises(ValueError, match="explicit --authorize-promotion"):
        promote_candidate(manifest, authorize=False)

    active = promote_candidate(manifest, authorize=True)
    assert active["state"] == "promoted"
    assert active_program_path("constitution").is_file()
    assert active_manifest_path("constitution").is_file()
    assert promoted_status()["stages"]["constitution"]["state"] == "PROMOTED"

    class FakeProgram:
        loaded: str | None = None

        def load(self, path: str) -> None:
            self.loaded = path

    program = FakeProgram()
    loaded = load_promoted_state(program, "constitution")
    assert loaded is not None
    assert loaded["candidate_id"] == "constitution-test-candidate"
    assert program.loaded == str(active_program_path("constitution"))


def test_promotion_rejects_tampered_or_below_threshold_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DSPEC_OPTIMIZATION_DIR", str(tmp_path / "active"))

    low = _candidate(tmp_path / "low", optimized=0.89, baseline=0.88)
    with pytest.raises(ValueError, match="below promotion minimum"):
        promote_candidate(low, authorize=True)

    tamper_dir = tmp_path / "tamper"
    tamper_dir.mkdir()
    manifest = _candidate(tamper_dir)
    (tamper_dir / "constitution-program.json").write_text('{"compiled": false}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        promote_candidate(manifest, authorize=True)
