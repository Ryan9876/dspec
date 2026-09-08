# DSpec DSPy optimization dataset contract

DS-CHG-001 does not ship fabricated MIPROv2 weights or synthetic claims of production tuning. The optimizer accepts only explicitly reviewed JSONL examples and keeps compilation separate from promotion.

## JSONL schema

Each line is one JSON object:

```json
{
  "schema_version": 1,
  "source_id": "reviewed-spec-001",
  "stage": "solution",
  "split": "train",
  "reviewed": true,
  "inputs": {
    "constitution_context": "...",
    "requirements_spec": "...",
    "user_architectural_preferences": "..."
  },
  "expected_spec": "# Solution ...",
  "required_markers": ["error contract", "rollback", "UUID"]
}
```

### Required fields

- `schema_version`: must equal `1`.
- `source_id`: stable unique identifier within the stage.
- `stage`: one of `constitution`, `requirements`, `solution`, or `tasks`.
- `split`: `train` or held-out `validation`.
- `reviewed`: must be literal `true`.
- `inputs`: exact DSPy input fields for the chosen stage.
- `expected_spec`: reviewer-approved exemplar output.
- `required_markers`: optional reviewer-authored phrases that a candidate should preserve semantically enough to include literally.

The validator rejects:
- unreviewed examples
- missing train or validation splits
- duplicate source IDs
- stage/input-shape mismatches
- exemplar specs that fail DSpec's deterministic 0.90 quality gate
- required markers absent from the approved exemplar
- credential-like content detected by DSpec's secret sanitizer

## Stage input fields

### Constitution

- `raw_idea_description`
- `security_isolation_preferences`

### Requirements

- `constitution_context`
- `functional_scope_answers`

### Solution

- `constitution_context`
- `requirements_spec`
- `user_architectural_preferences`

### Tasks

- `constitution_context`
- `requirements_spec`
- `solution_spec`

## Metric

Optimization uses a transparent deterministic metric:

- 80% DSpec structural quality score for the active stage
- 20% coverage of reviewer-authored required markers

The approved exemplar itself is also included as the labeled DSPy output for few-shot selection.

This metric is intentionally bounded. It does not prove semantic equivalence to a reviewer-approved production spec, security correctness, or production quality outside the reviewed validation set.

## Lifecycle

1. `dspec-optimize validate` validates data and makes no model calls.
2. `dspec-optimize compile` requires `--authorize-model-execution`, runs MIPROv2, evaluates baseline and optimized programs on held-out examples, and writes a candidate program JSON plus candidate manifest.
3. Candidate state is scanned for credential-like material before it is retained.
4. `dspec-optimize promote` requires `--authorize-promotion`.
5. Promotion verifies the program-state SHA-256, minimum held-out score, and declared minimum improvement over baseline.
6. Promoted state is copied transactionally into the local DSpec optimization store and is loaded by the matching generation stage.

Compilation success alone does not mean the candidate is promoted, production-ready, deployed, or known-good.

## Current DS-CHG-001 evidence boundary

As of the current change state, no reviewed production dataset has been supplied and no MIPROv2 model execution has been authorized. FR-2.3 therefore remains `BLOCKED` even though the implementation path exists.
