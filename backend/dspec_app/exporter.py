from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone

from .db import STAGES, get_session


def safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.")
    return value or "dspec-project"


def _agent_files(bundle: str) -> dict[str, str]:
    header = f"This repository is governed by the four-tier DSpec bundle in specs/{bundle}/. Read all four tiers before modifying code."
    return {
        "CLAUDE.md": f"# Claude Code Guidelines: {bundle}\n\n{header}\n\n## Execution\nRun each verification command defined by tasks.md and preserve explicit requirements.\n",
        ".cursorrules": f"# Cursor Guidelines: {bundle}\n\n{header}\n\nDo not weaken acceptance criteria or leave unfinished placeholder code.\n",
        "CODEX_PROMPT.md": f"# Codex Instructions: {bundle}\n\n{header}\n\nImplement requirements through the approved solution and tasks; validate actual behavior before completion claims.\n",
        "CHATGPT_PROMPT.md": f"# ChatGPT Instructions: {bundle}\n\n{header}\n\nTreat tasks as execution steps, not a substitute for requirements or acceptance criteria.\n",
    }


def build_bundle(session_id: str, allow_draft: bool = False) -> tuple[bytes, dict]:
    session = get_session(session_id)
    specs = session["specs"]
    blockers = []
    for stage in STAGES:
        spec = specs[stage]
        if not spec["content"].strip(): blockers.append(f"{stage}: empty")
        if not allow_draft and not spec["approved"]: blockers.append(f"{stage}: not approved")
        if not allow_draft and float(spec["quality_score"]) < 0.90: blockers.append(f"{stage}: score below 0.90")
    if blockers:
        raise ValueError("Export blocked: " + "; ".join(blockers))
    bundle = safe_name(session["bundle_name"])
    files: dict[str, bytes] = {}
    for stage in STAGES:
        files[f"specs/{bundle}/{stage}.md"] = specs[stage]["content"].encode("utf-8")
    for path, content in _agent_files(bundle).items():
        files[path] = content.encode("utf-8")
    manifest = {
        "schema_version": 1,
        "bundle_name": bundle,
        "bundle_version": "0.1.0",
        "export_timestamp": datetime.now(timezone.utc).isoformat(),
        "state": "draft" if allow_draft else "approved",
        "generated_by": {"engine": "DSpec AI Core", "version": "0.1.0", "optimization": "unoptimized baseline"},
        "specs": {
            stage: {
                "file": f"specs/{bundle}/{stage}.md",
                "version": specs[stage]["version"],
                "revision": specs[stage]["revision"],
                "quality_score": specs[stage]["quality_score"],
                "approved": specs[stage]["approved"],
            }
            for stage in STAGES
        },
        "agent_manifests": ["CLAUDE.md", ".cursorrules", "CODEX_PROMPT.md", "CHATGPT_PROMPT.md"],
    }
    files["bundle-manifest.json"] = json.dumps(manifest, indent=2).encode("utf-8")
    manifest["files"] = {path: hashlib.sha256(data).hexdigest() for path, data in files.items() if path != "bundle-manifest.json"}
    files["bundle-manifest.json"] = json.dumps(manifest, indent=2).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return output.getvalue(), manifest
