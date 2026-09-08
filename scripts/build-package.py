from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
STAGE = DIST / "package"
VERSION = "0.1.0"


def current_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return os.environ.get("GITHUB_SHA", "UNKNOWN")


def main() -> None:
    frontend = ROOT / "frontend" / "out"
    if not (frontend / "index.html").exists():
        raise SystemExit("frontend/out is missing; run scripts/build-frontend.sh first")

    commit = current_commit()
    built_at = datetime.now(UTC).isoformat()

    if STAGE.exists():
        shutil.rmtree(STAGE)
    DIST.mkdir(exist_ok=True)
    STAGE.mkdir()

    for name in ["dspec", "scripts", "docs"]:
        source = ROOT / name
        if source.exists():
            shutil.copytree(source, STAGE / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(frontend, STAGE / "frontend" / "out")
    for name in ["pyproject.toml", "requirements.txt", "README.md"]:
        shutil.copy2(ROOT / name, STAGE / name)

    build_info = {
        "version": VERSION,
        "build_hash": commit,
        "built_at": built_at,
        "source_repository": "Ryan9876/dspec",
    }
    (STAGE / "build-info.json").write_text(json.dumps(build_info, indent=2) + "\n", encoding="utf-8")

    package_name = f"dspec-build-v{VERSION}.zip"
    package_path = DIST / package_name
    if package_path.exists():
        package_path.unlink()
    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(STAGE.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(STAGE).as_posix())

    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    manifest = {
        "release_version": f"v{VERSION}",
        "release_date": built_at,
        "build_hash": commit,
        "minimum_runner_version": "0.1.0",
        "package_filename": package_name,
        "sha256": digest,
        "validation_state": "candidate",
        "release_notes": "DS-CHG-001 local working prototype candidate. Candidate status does not authorize runner auto-update.",
    }
    (DIST / "candidate-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
