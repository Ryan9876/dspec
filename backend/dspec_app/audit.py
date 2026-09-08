from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from .db import connect, utcnow

DEFAULT_IGNORES = {".git", "node_modules", "dist", ".venv", "venv", "__pycache__", ".next", "out", "build"}
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".sql", ".toml", ".yaml", ".yml", ".html", ".css", ".sh"}
MAX_FILES = 5000
MAX_FILE_BYTES = 2_000_000


def _safe_files(root: Path, ignore: set[str], limit: int = MAX_FILES):
    root = root.resolve()
    count = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = [d for d in dirs if d not in ignore and not d.startswith(".") and not (current_path / d).is_symlink()]
        for name in files:
            if count >= limit:
                return
            path = current_path / name
            if path.is_symlink() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                resolved = path.resolve()
                resolved.relative_to(root)
                if resolved.stat().st_size > MAX_FILE_BYTES:
                    continue
            except (OSError, ValueError):
                continue
            count += 1
            yield resolved


def _summarize(path: Path, content: str) -> dict:
    low = content.lower()
    return {
        "path": str(path),
        "has_tests": "pytest" in low or "describe(" in low or "test(" in low or "unittest" in low,
        "has_api": "fastapi" in low or "apirouter" in low or "app.get(" in low or "app.post(" in low or "route(" in low,
        "has_types": "interface " in content or ": str" in content or ": int" in content or "pydantic" in low or " zod" in low,
        "has_security": "authorization" in low or "credential" in low or "keychain" in low or "secret" in low,
        "has_verification": "pytest" in low or "npm test" in low or "curl " in low or "verification" in low,
        "has_requirements": "acceptance criteria" in low or "user story" in low or "functional requirement" in low,
        "has_governance": "constitution" in low or "governance" in low or "security" in low,
    }


def scan_repository(repo_path: str, ignore_patterns: list[str] | None = None, use_hash_cache: bool = True) -> dict:
    started = time.perf_counter()
    root = Path(repo_path).expanduser()
    if not root.exists() or not root.is_dir():
        raise ValueError("repo_path must be an existing directory")
    root = root.resolve()
    ignore = DEFAULT_IGNORES | set(ignore_patterns or [])
    summaries = []
    cached = 0
    files = list(_safe_files(root, ignore))
    conn = connect()
    try:
        for path in files:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            cache_row = conn.execute("SELECT file_hash,ast_summary_json FROM file_ast_cache WHERE file_path=?", (str(path),)).fetchone()
            if use_hash_cache and cache_row and cache_row["file_hash"] == digest:
                summaries.append(json.loads(cache_row["ast_summary_json"]))
                cached += 1
                continue
            text = raw.decode("utf-8", errors="ignore")
            summary = _summarize(path, text)
            summaries.append(summary)
            conn.execute(
                "INSERT INTO file_ast_cache(file_path,file_hash,ast_summary_json,last_scanned_at) VALUES(?,?,?,?) ON CONFLICT(file_path) DO UPDATE SET file_hash=excluded.file_hash,ast_summary_json=excluded.ast_summary_json,last_scanned_at=excluded.last_scanned_at",
                (str(path), digest, json.dumps(summary), utcnow()),
            )
        conn.commit()
    finally:
        conn.close()

    names = {p.name.lower() for p in files}
    total = max(len(summaries), 1)
    security_hits = sum(bool(s["has_security"]) for s in summaries)
    requirements_hits = sum(bool(s["has_requirements"]) for s in summaries)
    architecture_hits = sum(bool(s["has_api"] or s["has_types"]) for s in summaries)
    test_hits = sum(bool(s["has_tests"] or s["has_verification"]) for s in summaries)
    governance_bonus = 25 if {"constitution.md", "requirements.md", "solution.md", "tasks.md"}.issubset(names) else 0
    governance = min(100.0, governance_bonus + 75.0 * security_hits / total)
    requirements = min(100.0, (35.0 if "requirements.md" in names else 0.0) + 65.0 * requirements_hits / total)
    architecture = min(100.0, (25.0 if "solution.md" in names else 0.0) + 75.0 * architecture_hits / total)
    tests = min(100.0, (25.0 if "tasks.md" in names else 0.0) + 75.0 * test_hits / total)
    composite = round((governance + requirements + architecture + tests) / 4.0, 1)
    gaps = []
    if governance < 70: gaps.append("Governance/security evidence is weak or sparse in the scanned files.")
    if requirements < 70: gaps.append("Requirements and acceptance evidence is incomplete or not co-located with the codebase.")
    if architecture < 70: gaps.append("Typed architecture/API/schema evidence is incomplete in the scanned source.")
    if tests < 70: gaps.append("Test and executable verification evidence is incomplete in the scanned repository.")
    diff = "# Structural Diff\n\n" + ("\n".join(f"- {g}" for g in gaps) if gaps else "- No high-level evidence gap crossed the prototype threshold.")
    upgrade = "# Upgrade Specification\n\n" + "\n".join(f"{i+1}. {g}" for i, g in enumerate(gaps)) if gaps else "# Upgrade Specification\n\nNo prototype-threshold remediation identified."
    return {
        "repo_path": str(root),
        "total_files_scanned": len(summaries),
        "cached_files_skipped": cached,
        "scan_duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "health_score": {
            "composite_score": composite,
            "governance_security_score": round(governance, 1),
            "requirements_clarity_score": round(requirements, 1),
            "architecture_consistency_score": round(architecture, 1),
            "test_task_coverage_score": round(tests, 1),
        },
        "critical_gaps": gaps,
        "structural_diff_md": diff,
        "upgrade_spec_md": upgrade,
        "limitations": [
            "Scores reflect reported static repository evidence, not a security certification.",
            "Dependency vulnerability databases and deep cross-language semantic analysis are not included in this prototype score.",
        ],
    }
