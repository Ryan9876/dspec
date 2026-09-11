from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import UTC, datetime
from typing import Any

from . import db
from .config import APP_VERSION
from .execution_planning import render_execution_views, source_snapshot_sha256
from .guided_store import augment_session

AGENT_FILES = {
    "CLAUDE.md": """# Claude Code Project Guidelines: {name}\n\nRead all four tiers under `specs/{name}/` before modifying code. Requirements outrank task convenience. If `execution/` exists, treat it only as derived model-routing/context guidance; canonical requirements, solution, and tasks remain authoritative. Implement complete behavior; do not add TODO placeholders. Run each task's verification command before marking it complete. If the assigned capability cannot safely complete a task, return BLOCKED or ESCALATION_REQUIRED rather than weakening the spec.\n""",
    ".cursorrules": """# Cursor Rules: {name}\n\nThe DSpec 4-tier bundle in `specs/{name}/` is authoritative. Preserve explicit security/data boundaries, use strict typing, and do not weaken acceptance criteria to make implementation pass. Derived execution routing is guidance only and never overrides the canonical spec.\n""",
    "CODEX_PROMPT.md": """# Codex Execution Instructions: {name}\n\nRead constitution.md, requirements.md, solution.md, then tasks.md. Implement requirements in governed order, run the specified verification for each material task, preserve out-of-scope behavior, and report blockers rather than inventing evidence. If execution guidance is present, obey its minimum safe capability and escalation contract without treating it as product scope.\n""",
    "CHATGPT_PROMPT.md": """# ChatGPT Implementation Instructions: {name}\n\nUse the attached DSpec bundle as the source of truth. Read all four tiers before coding. Keep implementation, validation, deployment, and deployed-runtime verification as distinct states. Never claim a test or deployment that was not actually executed. Derived execution files are non-authoritative routing/context aids.\n""",
}


def _slug(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return value or "dspec-project"


def build_bundle(session_id: str, allow_draft: bool = False) -> tuple[bytes, dict[str, Any]]:
    session = augment_session(db.get_session(session_id))
    missing = [s for s in db.STAGES if s not in session["specs"]]
    if missing:
        raise ValueError("Missing spec tiers: " + ", ".join(missing))
    not_approved = [s for s in db.STAGES if session["specs"][s]["approval_status"] != "approved"]
    if not_approved and not allow_draft:
        raise PermissionError("Spec tiers are not approved: " + ", ".join(not_approved))

    name = _slug(session["bundle_name"])
    files: dict[str, bytes] = {}
    for stage in db.STAGES:
        spec = session["specs"][stage]
        prefix = "<!-- DRAFT / NOT APPROVED -->\n\n" if spec["approval_status"] != "approved" else ""
        files[f"specs/{name}/{stage}.md"] = (prefix + spec["content"]).encode()
    for filename, template in AGENT_FILES.items():
        files[filename] = template.format(name=name).encode()

    execution_manifest: dict[str, Any] = {"status": "not_selected"}
    execution_bundle = session.get("execution_plan")
    if execution_bundle and execution_bundle.get("status") == "SELECTED":
        current_source = source_snapshot_sha256(session)
        if execution_bundle.get("source_snapshot_sha256") == current_source:
            selected_plan = execution_bundle.get("selected_plan") or {}
            files["execution/execution-plan.json"] = json.dumps(selected_plan, indent=2).encode()
            for path, content in render_execution_views(selected_plan).items():
                files[path] = content.encode()
            for assignment in selected_plan.get("assignments", []):
                task_id = str(assignment.get("task_id") or "").strip()
                if not task_id:
                    continue
                context = {
                    "schema_version": 1,
                    "authoritative": False,
                    "source_snapshot_sha256": current_source,
                    "canonical_task_id": task_id,
                    "title": assignment.get("title"),
                    "task_text": assignment.get("task_text"),
                    "requirement_ids": assignment.get("requirement_ids") or [],
                    "solution_refs": assignment.get("solution_refs") or [],
                    "bounded_context_refs": assignment.get("bounded_context_refs") or [],
                    "minimum_safe_capability": assignment.get("minimum_safe_capability"),
                    "risk_flags": assignment.get("risk_flags") or [],
                    "validation": assignment.get("validation"),
                    "assigned_model": assignment.get("assigned_model"),
                    "note": "Bounded derived context. Canonical DSpec tiers remain authoritative.",
                }
                safe_task_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", task_id)
                files[f"execution/context/{safe_task_id}.json"] = json.dumps(context, indent=2).encode()
            execution_manifest = {
                "status": "selected",
                "strategy": execution_bundle.get("selected_strategy"),
                "source_snapshot_sha256": current_source,
                "authoritative": False,
                "note": "Derived execution views do not replace canonical DSpec requirements, solution, or tasks.",
            }
        else:
            execution_manifest = {
                "status": "stale_omitted",
                "authoritative": False,
                "note": "A stale execution plan was intentionally omitted from export.",
            }

    manifest: dict[str, Any] = {
        "$schema": "https://dspec.ai/schemas/bundle-manifest.v1.json",
        "bundle_name": name,
        "bundle_version": "0.1.0",
        "export_timestamp": datetime.now(UTC).isoformat(),
        "status": "draft" if not_approved else "approved",
        "generated_by": {
            "engine": "DSpec AI Core",
            "version": APP_VERSION,
            "optimization": "UNOPTIMIZED",
            "quality_gate": ">=0.90 per approved tier",
        },
        "specs": {},
        "agent_manifests": list(AGENT_FILES),
        "execution_plan": execution_manifest,
    }
    for stage in db.STAGES:
        spec = session["specs"][stage]
        path = f"specs/{name}/{stage}.md"
        manifest["specs"][stage] = {
            "file": path,
            "version": spec["version_number"],
            "revision": spec["revision_number"],
            "quality_score": spec["quality_score"],
            "approval_status": spec["approval_status"],
        }
    manifest["files"] = {path: hashlib.sha256(data).hexdigest() for path, data in files.items()}
    files["bundle-manifest.json"] = json.dumps(manifest, indent=2).encode()

    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return bio.getvalue(), manifest
