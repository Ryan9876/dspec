from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import dspec_home

STAGES = ("constitution", "requirements", "solution", "tasks")


def optimization_root() -> Path:
    override = os.environ.get("DSPEC_OPTIMIZATION_DIR")
    root = Path(override).expanduser() if override else dspec_home() / "optimization"
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def _stage_dir(stage: str) -> Path:
    if stage not in STAGES:
        raise ValueError(f"Unsupported DSpec stage: {stage}")
    path = optimization_root() / stage
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def active_manifest_path(stage: str) -> Path:
    return _stage_dir(stage) / "active.json"


def active_program_path(stage: str) -> Path:
    return _stage_dir(stage) / "program.json"


def read_active_manifest(stage: str) -> dict[str, Any] | None:
    path = active_manifest_path(stage)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("state") != "promoted" or data.get("stage") != stage:
        return None
    return data


def promoted_status() -> dict[str, Any]:
    stages: dict[str, Any] = {}
    promoted = 0
    for stage in STAGES:
        manifest = read_active_manifest(stage)
        if manifest:
            promoted += 1
            stages[stage] = {
                "state": "PROMOTED",
                "candidate_id": manifest.get("candidate_id"),
                "optimizer": manifest.get("optimizer"),
                "optimized_score": manifest.get("optimized_validation_score"),
                "baseline_score": manifest.get("baseline_validation_score"),
                "dataset_sha256": manifest.get("dataset_sha256"),
                "program_state_sha256": manifest.get("program_state_sha256"),
                "promoted_at": manifest.get("promoted_at"),
            }
        else:
            stages[stage] = {"state": "UNOPTIMIZED"}
    return {
        "overall": "PROMOTED" if promoted else "UNOPTIMIZED",
        "promoted_stage_count": promoted,
        "stages": stages,
    }


def load_promoted_state(program: Any, stage: str) -> dict[str, Any] | None:
    manifest = read_active_manifest(stage)
    if not manifest:
        return None
    program_path = active_program_path(stage)
    if not program_path.exists():
        raise RuntimeError(f"Promoted optimization state is missing for stage {stage}.")
    digest = sha256_file(program_path)
    expected = str(manifest.get("program_state_sha256") or "")
    if not expected or digest != expected:
        raise RuntimeError(f"Promoted optimization state checksum mismatch for stage {stage}.")
    program.load(str(program_path))
    return manifest


def promote_candidate(candidate_manifest_path: Path, *, authorize: bool) -> dict[str, Any]:
    if not authorize:
        raise ValueError("Promotion requires explicit --authorize-promotion.")
    if not candidate_manifest_path.exists():
        raise FileNotFoundError(candidate_manifest_path)

    candidate = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
    if candidate.get("state") != "candidate":
        raise ValueError("Only candidate optimization manifests may be promoted.")
    stage = str(candidate.get("stage") or "")
    if stage not in STAGES:
        raise ValueError("Candidate manifest has an unsupported stage.")

    program_path = candidate_manifest_path.parent / str(candidate.get("program_state_file") or "")
    if not program_path.is_file():
        raise ValueError("Candidate program state file is missing.")
    digest = sha256_file(program_path)
    if digest != candidate.get("program_state_sha256"):
        raise ValueError("Candidate program state checksum does not match the manifest.")

    optimized = float(candidate.get("optimized_validation_score", 0.0))
    baseline = float(candidate.get("baseline_validation_score", 0.0))
    minimum = float(candidate.get("promotion_minimum_score", 0.90))
    minimum_improvement = float(candidate.get("promotion_minimum_improvement", 0.0))
    if optimized < minimum:
        raise ValueError(f"Candidate validation score {optimized:.3f} is below promotion minimum {minimum:.3f}.")
    if optimized + 1e-12 < baseline + minimum_improvement:
        raise ValueError(
            f"Candidate score {optimized:.3f} does not satisfy baseline {baseline:.3f} "
            f"+ minimum improvement {minimum_improvement:.3f}."
        )

    stage_dir = _stage_dir(stage)
    target_program = active_program_path(stage)
    target_manifest = active_manifest_path(stage)
    tmp_program = stage_dir / "program.json.tmp"
    tmp_manifest = stage_dir / "active.json.tmp"

    shutil.copy2(program_path, tmp_program)
    os.chmod(tmp_program, 0o600)
    active = {
        **candidate,
        "state": "promoted",
        "promoted_at": datetime.now(UTC).isoformat(),
        "source_candidate_manifest": candidate_manifest_path.name,
    }
    tmp_manifest.write_text(json.dumps(active, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp_manifest, 0o600)
    os.replace(tmp_program, target_program)
    os.replace(tmp_manifest, target_manifest)
    return active
