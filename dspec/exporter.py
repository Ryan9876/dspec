from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import UTC, datetime
from typing import Any

from . import db
from .execution_planning import render_execution_views, source_snapshot_sha256

AGENT_FILES = {
    "CLAUDE.md": """# Claude Code Project Guidelines: {name}\n\nRead all four tiers under `specs/{name}/` before modifying code. Requirements outrank task convenience. Implement complete behavior; do not add TODO placeholders. Run each task's verification command before marking it complete.\n""",
    ".cursorrules": """# Cursor Rules: {name}\n\nThe DSpec 4-tier bundle in `specs/{name}/` is authoritative. Preserve explicit security/data boundaries, use strict typing, and do not weaken acceptance criteria to make implementation pass.\n""",
    "CODEX_PROMPT.md": """# Codex Execution Instructions: {name}\n\nRead constitution.md, requirements.md, solution.md, then tasks.md. Implement requirements in governed order, run the specified verification for each material task, preserve out-of-scope behavior, and report blockers rather than inventing evidence.\n""",
    "CHATGPT_PROMPT.md": """# ChatGPT Implementation Instructions: {name}\n\nUse the attached DSpec bundle as the source of truth. Read all four tiers before coding. Keep implementation, validation, deployment, and deployed-runtime verification as distinct states. Never claim a test or deployment that was not actually executed.\n""",
}


def _slug(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return value or "dspec-project"


def build_bundle(session_id: str, allow_draft: bool = False) -> tuple[bytes, dict[str, Any]]:
    session = db.get_session(session_id)
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
        "generated_by": {"engine": "DSpec AI Core", "version": "0.1.0", "optimization": "UNOPTIMIZED", "quality_gate": ">=0.90 per approved tier"},
        "specs": {},
        "agent_manifests": list(AGENT_FILES),
        "execution_plan": execution_manifest,
    }
    for stage in db.STAGES:
        spec = session["specs"][stage]
        path = f"specs/{name}/{stage}.md"
        manifest["specs"][stage] = {"file": path, "version": spec["version_number"], "revision": spec["revision_number"], "quality_score": spec["quality_score"], "approval_status": spec["approval_status"]}
    manifest["files"] = {path: hashlib.sha256(data).hexdigest() for path, data in files.items()}
    files["bundle-manifest.json"] = json.dumps(manifest, indent=2).encode()
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return bio.getvalue(), manifest
