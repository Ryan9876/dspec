from __future__ import annotations

import ast
import hashlib
import os
import time
from pathlib import Path
from typing import Any

from . import db

DEFAULT_IGNORES = {".git","node_modules","dist","build",".next","out",".venv","venv","__pycache__",".pytest_cache"}
BINARY_EXTENSIONS = {".png",".jpg",".jpeg",".gif",".webp",".ico",".pdf",".zip",".gz",".tar",".woff",".woff2",".ttf",".otf",".mp3",".mp4",".mov",".bin",".exe",".dll",".so",".dylib",".db",".sqlite",".sqlite3"}
TEXT_EXTENSIONS = {".py",".js",".jsx",".ts",".tsx",".json",".md",".yml",".yaml",".toml",".ini",".cfg",".sql",".html",".css",".scss",".sh",".zsh",".bash",".txt",".env",".graphql",".gql"}


def _safe_file(root: Path, path: Path) -> bool:
    try:
        resolved = path.resolve()
        return resolved == root or root in resolved.parents
    except OSError:
        return False


def _summary(path: Path, text: str) -> dict[str, Any]:
    lower = text.lower()
    result: dict[str, Any] = {
        "lines": text.count("\n") + 1,
        "has_tests": "test" in path.name.lower() or "pytest" in lower or "vitest" in lower or "jest" in lower,
        "has_typed_api": any(x in lower for x in ["pydantic", "basemodel", "interface ", "type ", "z.object", "openapi"]),
        "has_security": any(x in lower for x in ["authorization", "authentication", "keychain", "secret", "credential", "trustedhost", "cors"]),
        "has_acceptance": any(x in lower for x in ["acceptance criteria", "verification", "requirements"]),
        "has_schema": any(x in lower for x in ["create table", "prisma", "sqlalchemy", "schema", "migration"]),
    }
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
            result["functions"] = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(tree))
            result["classes"] = sum(isinstance(n, ast.ClassDef) for n in ast.walk(tree))
            result["syntax_ok"] = True
        except SyntaxError:
            result["syntax_ok"] = False
    return result


def scan_repository(repo_path: str, ignore_patterns: list[str] | None = None, use_hash_cache: bool = True, max_files: int = 5000, max_bytes: int = 20_000_000) -> dict[str, Any]:
    started = time.perf_counter()
    root = Path(repo_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("Repository path does not exist or is not a directory")

    ignores = DEFAULT_IGNORES | set(ignore_patterns or [])
    scanned = 0
    cached = 0
    bytes_read = 0
    summaries: list[dict[str, Any]] = []
    files: list[str] = []

    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = [d for d in dirs if d not in ignores and not d.startswith(".") and _safe_file(root, current_path / d)]
        for name in names:
            if scanned >= max_files or bytes_read >= max_bytes:
                break
            path = current_path / name
            if name.startswith(".") and name not in {".cursorrules"}:
                continue
            if not _safe_file(root, path) or path.is_symlink():
                continue
            if path.suffix.lower() in BINARY_EXTENSIONS:
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS and path.name not in {"Dockerfile","Makefile","CLAUDE.md","README.md"}:
                continue
            try:
                raw = path.read_bytes()
            except (OSError, PermissionError):
                continue
            if b"\x00" in raw[:4096]:
                continue
            digest = hashlib.sha256(raw).hexdigest()
            rel = str(path.relative_to(root))
            cached_item = db.cache_get(str(path), digest) if use_hash_cache else None
            if cached_item:
                summary = cached_item
                cached += 1
            else:
                text = raw.decode("utf-8", errors="replace")
                summary = _summary(path, text)
                db.cache_put(str(path), digest, summary)
            summary = {"path": rel, **summary}
            summaries.append(summary)
            files.append(rel)
            scanned += 1
            bytes_read += len(raw)

    def ratio(key: str) -> float:
        if not summaries:
            return 0.0
        return sum(1 for s in summaries if s.get(key)) / len(summaries)

    names_lower = {f.lower() for f in files}
    has_governance = any(x in names_lower for x in {"constitution.md","security.md","claude.md"}) or any("governance" in f for f in names_lower)
    has_requirements = any("requirements" in f or "prd" in f for f in names_lower)
    has_solution = any("solution" in f or "architecture" in f for f in names_lower)
    has_tasks = any("tasks" in f for f in names_lower)
    has_ci = any(f.startswith(".github/workflows/") for f in names_lower)

    governance = min(100.0, 20 + (25 if has_governance else 0) + ratio("has_security") * 35 + (20 if has_ci else 0))
    requirements = min(100.0, 20 + (40 if has_requirements else 0) + ratio("has_acceptance") * 40)
    architecture = min(100.0, 20 + (30 if has_solution else 0) + ratio("has_typed_api") * 25 + ratio("has_schema") * 25)
    tests = min(100.0, 15 + (25 if has_tasks else 0) + ratio("has_tests") * 45 + (15 if has_ci else 0))
    composite = round((governance + requirements + architecture + tests) / 4, 1)

    gaps: list[str] = []
    if not has_governance: gaps.append("No explicit governance/security specification detected.")
    if not has_requirements: gaps.append("No requirements/acceptance specification detected.")
    if not has_solution: gaps.append("No explicit solution/architecture specification detected.")
    if not has_tasks: gaps.append("No governed task/verification specification detected.")
    if ratio("has_tests") < 0.05: gaps.append("Limited automated test evidence detected.")
    if not has_ci: gaps.append("No GitHub Actions workflow detected.")

    detected_stack: dict[str, str] = {}
    if "package.json" in names_lower:
        detected_stack["frontend_or_node"] = "Node/JavaScript project detected"
    if "pyproject.toml" in names_lower or "requirements.txt" in names_lower:
        detected_stack["python"] = "Python project detected"
    if any(f.endswith(".tsx") for f in names_lower):
        detected_stack["frontend"] = "TypeScript/React-family files detected"

    duration = int((time.perf_counter() - started) * 1000)
    return {
        "repo_path": str(root),
        "total_files_scanned": scanned,
        "cached_files_skipped": cached,
        "scan_duration_ms": duration,
        "limits": {"max_files": max_files, "max_bytes": max_bytes, "bytes_read": bytes_read, "truncated": scanned >= max_files or bytes_read >= max_bytes},
        "health_score": {
            "composite_score": composite,
            "governance_security_score": round(governance,1),
            "requirements_clarity_score": round(requirements,1),
            "architecture_consistency_score": round(architecture,1),
            "test_task_coverage_score": round(tests,1),
        },
        "detected_stack": detected_stack,
        "critical_gaps": gaps,
        "structural_diff_md": "\n".join(f"- {g}" for g in gaps) if gaps else "- No high-confidence structural gaps detected by bounded static evidence.",
        "assessment_scope": "Bounded static file evidence only; not a security certification, CVE scan, or deep semantic code review.",
    }
