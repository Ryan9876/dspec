from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .dspy_runtime import make_lm
from .dspy_signatures import (
    ArchitectureToSolution,
    DSPY_AVAILABLE,
    IdeaToConstitution,
    ScopeToRequirements,
    SpecToTasks,
)
from .optimization_store import promote_candidate, promoted_status, sha256_file
from .quality import evaluate
from .security import sanitize

STAGE_INPUTS = {
    "constitution": ("raw_idea_description", "security_isolation_preferences"),
    "requirements": ("constitution_context", "functional_scope_answers"),
    "solution": ("constitution_context", "requirements_spec", "user_architectural_preferences"),
    "tasks": ("constitution_context", "requirements_spec", "solution_spec"),
}
OUTPUT_FIELDS = {
    "constitution": "constitution_spec",
    "requirements": "requirements_spec",
    "solution": "solution_spec",
    "tasks": "tasks_spec",
}
SIGNATURES = {
    "constitution": IdeaToConstitution,
    "requirements": ScopeToRequirements,
    "solution": ArchitectureToSolution,
    "tasks": SpecToTasks,
}


@dataclass(frozen=True)
class ReviewedExample:
    source_id: str
    stage: str
    split: str
    inputs: dict[str, str]
    expected_spec: str
    required_markers: tuple[str, ...]


@dataclass(frozen=True)
class DatasetBundle:
    stage: str
    sha256: str
    train: tuple[ReviewedExample, ...]
    validation: tuple[ReviewedExample, ...]

    def summary(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "dataset_sha256": self.sha256,
            "reviewed_train_examples": len(self.train),
            "reviewed_validation_examples": len(self.validation),
            "total_reviewed_examples": len(self.train) + len(self.validation),
        }


def _dataset_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reject_secret_like_text(value: str, source_id: str) -> None:
    if sanitize(value) != value:
        raise ValueError(f"Dataset example {source_id} contains credential-like text and cannot be used for optimization.")


def _parse_example(raw: dict[str, Any], *, stage: str, index: int) -> ReviewedExample:
    source_id = str(raw.get("source_id") or "").strip()
    if not source_id:
        raise ValueError(f"Dataset row {index} is missing source_id.")
    if raw.get("schema_version") != 1:
        raise ValueError(f"Dataset example {source_id} must use schema_version=1.")
    if raw.get("stage") != stage:
        raise ValueError(f"Dataset example {source_id} has stage={raw.get('stage')!r}; expected {stage!r}.")
    split = str(raw.get("split") or "").strip().lower()
    if split not in {"train", "validation"}:
        raise ValueError(f"Dataset example {source_id} must use split=train or split=validation.")
    if raw.get("reviewed") is not True:
        raise ValueError(f"Dataset example {source_id} is not explicitly reviewed.")

    inputs = raw.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError(f"Dataset example {source_id} must contain an inputs object.")
    expected_keys = set(STAGE_INPUTS[stage])
    actual_keys = set(inputs)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise ValueError(f"Dataset example {source_id} input keys mismatch; missing={missing}, extra={extra}.")
    normalized_inputs: dict[str, str] = {}
    for key in STAGE_INPUTS[stage]:
        value = inputs.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Dataset example {source_id} input {key} must be a non-empty string.")
        _reject_secret_like_text(value, source_id)
        normalized_inputs[key] = value.strip()

    expected_spec = raw.get("expected_spec")
    if not isinstance(expected_spec, str) or not expected_spec.strip():
        raise ValueError(f"Dataset example {source_id} must contain a non-empty expected_spec.")
    expected_spec = expected_spec.strip()
    _reject_secret_like_text(expected_spec, source_id)
    quality = evaluate(stage, expected_spec)
    if not quality["passed"]:
        raise ValueError(
            f"Reviewed exemplar {source_id} does not meet the deterministic DSpec quality gate "
            f"({quality['score']:.3f} < {quality['threshold']:.3f} or must-fix checks remain)."
        )

    markers_raw = raw.get("required_markers", [])
    if not isinstance(markers_raw, list) or any(not isinstance(x, str) or not x.strip() for x in markers_raw):
        raise ValueError(f"Dataset example {source_id} required_markers must be a list of non-empty strings.")
    markers = tuple(dict.fromkeys(x.strip() for x in markers_raw))
    lower = expected_spec.lower()
    absent = [marker for marker in markers if marker.lower() not in lower]
    if absent:
        raise ValueError(f"Dataset example {source_id} required markers are absent from expected_spec: {absent}.")

    return ReviewedExample(
        source_id=source_id,
        stage=stage,
        split=split,
        inputs=normalized_inputs,
        expected_spec=expected_spec,
        required_markers=markers,
    )


def validate_dataset(path: Path, stage: str) -> DatasetBundle:
    if stage not in STAGE_INPUTS:
        raise ValueError(f"Unsupported DSpec stage: {stage}")
    if not path.is_file():
        raise FileNotFoundError(path)

    rows: list[ReviewedExample] = []
    seen: set[str] = set()
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Dataset row {index} is not valid JSON: {exc.msg}.") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"Dataset row {index} must be a JSON object.")
        if raw.get("stage") != stage:
            continue
        item = _parse_example(raw, stage=stage, index=index)
        if item.source_id in seen:
            raise ValueError(f"Duplicate source_id in stage {stage}: {item.source_id}")
        seen.add(item.source_id)
        rows.append(item)

    train = tuple(row for row in rows if row.split == "train")
    validation = tuple(row for row in rows if row.split == "validation")
    if not train:
        raise ValueError(f"No reviewed train examples found for stage {stage}.")
    if not validation:
        raise ValueError(f"No held-out validation examples found for stage {stage}.")
    return DatasetBundle(stage=stage, sha256=_dataset_sha256(path), train=train, validation=validation)


def _marker_coverage(markers: list[str] | tuple[str, ...], content: str) -> float:
    if not markers:
        return 1.0
    lower = content.lower()
    return sum(1 for marker in markers if marker.lower() in lower) / len(markers)


def _metric_for(stage: str):
    output_field = OUTPUT_FIELDS[stage]

    def metric(example: Any, prediction: Any, trace: Any = None) -> float:
        del trace
        content = str(getattr(prediction, output_field, "") or "")
        structural = float(evaluate(stage, content)["score"])
        markers = getattr(example, "required_markers", []) or []
        coverage = _marker_coverage(markers, content)
        # Structural completeness is the dominant measure. Reviewer-authored
        # markers preserve domain-critical content without requiring exact text.
        return round((0.80 * structural) + (0.20 * coverage), 6)

    return metric


def _to_dspy_examples(bundle: DatasetBundle):
    if not DSPY_AVAILABLE:
        raise RuntimeError("DSPy is not installed.")
    import dspy

    output_field = OUTPUT_FIELDS[bundle.stage]
    input_fields = STAGE_INPUTS[bundle.stage]

    def convert(item: ReviewedExample):
        fields: dict[str, Any] = dict(item.inputs)
        fields[output_field] = item.expected_spec
        fields["required_markers"] = list(item.required_markers)
        fields["source_id"] = item.source_id
        return dspy.Example(**fields).with_inputs(*input_fields)

    return [convert(x) for x in bundle.train], [convert(x) for x in bundle.validation]


def _average_score(program: Any, examples: list[Any], *, stage: str, lm: Any) -> float:
    import dspy

    metric = _metric_for(stage)
    input_fields = STAGE_INPUTS[stage]
    scores: list[float] = []
    with dspy.context(lm=lm):
        for example in examples:
            inputs = {key: getattr(example, key) for key in input_fields}
            prediction = program(**inputs)
            scores.append(float(metric(example, prediction)))
    return round(statistics.fmean(scores), 6) if scores else 0.0


def compile_candidate(args: argparse.Namespace) -> dict[str, Any]:
    if not args.authorize_model_execution:
        raise ValueError("MIPROv2 execution requires explicit --authorize-model-execution.")

    bundle = validate_dataset(Path(args.dataset), args.stage)
    if SIGNATURES[args.stage] is None:
        raise RuntimeError("DSPy signatures are unavailable.")

    try:
        import dspy
        import optuna  # noqa: F401
        from dspy.teleprompt import MIPROv2
    except ModuleNotFoundError as exc:
        if exc.name == "optuna":
            raise RuntimeError("MIPROv2 requires the optional optimizer dependency: pip install -e '.[optimize]'") from exc
        raise
    except ImportError as exc:
        raise RuntimeError("MIPROv2 is unavailable in the installed DSPy runtime.") from exc

    trainset, valset = _to_dspy_examples(bundle)
    lm = make_lm(args.provider, args.model)
    base = dspy.ChainOfThought(SIGNATURES[args.stage])
    baseline_score = _average_score(base, valset, stage=args.stage, lm=lm)

    metric = _metric_for(args.stage)
    optimizer = MIPROv2(
        metric=metric,
        auto=args.auto,
        prompt_model=lm,
        task_model=lm,
        num_threads=args.num_threads,
        verbose=bool(args.verbose),
    )
    with dspy.context(lm=lm):
        compiled = optimizer.compile(
            base.deepcopy(),
            trainset=trainset,
            valset=valset,
            max_bootstrapped_demos=args.max_bootstrapped_demos,
            max_labeled_demos=args.max_labeled_demos,
        )

    optimized_score = _average_score(compiled, valset, stage=args.stage, lm=lm)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    program_path = output_dir / f"{args.stage}-program.json"
    compiled.save(str(program_path), save_program=False)
    try:
        program_path.chmod(0o600)
    except OSError:
        pass

    program_text = program_path.read_text(encoding="utf-8")
    if sanitize(program_text) != program_text:
        program_path.unlink(missing_ok=True)
        raise RuntimeError("Compiled DSPy state contained credential-like material and was rejected.")

    now = datetime.now(UTC)
    candidate_id = f"{args.stage}-{now.strftime('%Y%m%dT%H%M%SZ')}-{bundle.sha256[:10]}"
    eligible = (
        optimized_score >= float(args.minimum_score)
        and optimized_score + 1e-12 >= baseline_score + float(args.minimum_improvement)
    )
    manifest = {
        "schema_version": 1,
        "state": "candidate",
        "candidate_id": candidate_id,
        "created_at": now.isoformat(),
        "stage": args.stage,
        "optimizer": "MIPROv2",
        "dspy_version": getattr(dspy, "__version__", "UNKNOWN"),
        "provider": args.provider,
        "model": args.model,
        "dataset_sha256": bundle.sha256,
        "reviewed_train_examples": len(bundle.train),
        "reviewed_validation_examples": len(bundle.validation),
        "baseline_validation_score": baseline_score,
        "optimized_validation_score": optimized_score,
        "promotion_minimum_score": float(args.minimum_score),
        "promotion_minimum_improvement": float(args.minimum_improvement),
        "promotion_eligible": eligible,
        "program_state_file": program_path.name,
        "program_state_sha256": sha256_file(program_path),
        "optimizer_settings": {
            "auto": args.auto,
            "max_bootstrapped_demos": args.max_bootstrapped_demos,
            "max_labeled_demos": args.max_labeled_demos,
            "num_threads": args.num_threads,
        },
        "evidence_boundary": (
            "Candidate is not active until separately promoted. Scores apply only to the supplied reviewed "
            "held-out validation examples and do not establish production quality outside that dataset."
        ),
    }
    manifest_path = output_dir / f"{args.stage}-candidate.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    try:
        manifest_path.chmod(0o600)
    except OSError:
        pass
    return {**manifest, "candidate_manifest": str(manifest_path)}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate, compile, inspect, and promote DSpec DSPy optimization state.")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate a reviewed optimization JSONL dataset without model execution.")
    validate.add_argument("--dataset", required=True)
    validate.add_argument("--stage", required=True, choices=tuple(STAGE_INPUTS))

    compile_cmd = sub.add_parser("compile", help="Run MIPROv2 and emit a non-active candidate artifact.")
    compile_cmd.add_argument("--dataset", required=True)
    compile_cmd.add_argument("--stage", required=True, choices=tuple(STAGE_INPUTS))
    compile_cmd.add_argument("--provider", required=True, choices=("lm_studio", "ollama", "openai", "anthropic"))
    compile_cmd.add_argument("--model", required=True)
    compile_cmd.add_argument("--output-dir", required=True)
    compile_cmd.add_argument("--auto", choices=("light", "medium", "heavy"), default="light")
    compile_cmd.add_argument("--max-bootstrapped-demos", type=int, default=4)
    compile_cmd.add_argument("--max-labeled-demos", type=int, default=4)
    compile_cmd.add_argument("--num-threads", type=int, default=1)
    compile_cmd.add_argument("--minimum-score", type=float, default=0.90)
    compile_cmd.add_argument("--minimum-improvement", type=float, default=0.0)
    compile_cmd.add_argument("--authorize-model-execution", action="store_true")
    compile_cmd.add_argument("--verbose", action="store_true")

    promote = sub.add_parser("promote", help="Promote a checksum-verified candidate into the local active optimization store.")
    promote.add_argument("--candidate-manifest", required=True)
    promote.add_argument("--authorize-promotion", action="store_true")

    sub.add_parser("status", help="Show locally promoted optimizer state.")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    try:
        if args.command == "validate":
            result = validate_dataset(Path(args.dataset), args.stage).summary()
            result["model_execution"] = "NOT PERFORMED"
        elif args.command == "compile":
            result = compile_candidate(args)
        elif args.command == "promote":
            result = promote_candidate(Path(args.candidate_manifest), authorize=bool(args.authorize_promotion))
        else:
            result = promoted_status()
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
