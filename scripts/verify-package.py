from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

REQUIRED_FILES = {
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "build-info.json",
    "frontend/out/index.html",
    "frontend/out/monaco/vs/loader.js",
    "scripts/install-macos.sh",
    "scripts/start.sh",
    "scripts/stop.sh",
    "scripts/status.sh",
    "dspec/app.py",
    "dspec/runner.py",
    "docs/optimization-dataset.md",
}

FORBIDDEN_PREFIXES = (
    ".git/",
    ".github/",
    ".venv/",
    "node_modules/",
    "dist/",
    "runtime/",
)

FORBIDDEN_NAMES = {
    ".env",
    "config.json",
    "dspec.db",
    "dspec.db-wal",
    "dspec.db-shm",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def inspect_package(dist: Path, expected_build: str) -> tuple[dict[str, Any], Path]:
    manifest_path = dist / "candidate-manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("candidate-manifest.json is missing.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("validation_state") != "candidate":
        raise RuntimeError(
            f"Package manifest must remain candidate-only; got {manifest.get('validation_state')!r}."
        )
    if manifest.get("build_hash") != expected_build:
        raise RuntimeError(
            f"Manifest build hash {manifest.get('build_hash')!r} does not match expected {expected_build!r}."
        )

    release_version = str(manifest.get("release_version") or "").strip().lstrip("v")
    if not release_version:
        raise RuntimeError("Candidate release_version is missing.")
    expected_package_name = f"DSpec-v{release_version}.zip"
    package_name = str(manifest.get("package_filename") or "")
    if package_name != expected_package_name:
        raise RuntimeError(
            f"Candidate package name {package_name!r} must be {expected_package_name!r}."
        )
    package = dist / package_name
    if not package.is_file():
        raise RuntimeError(f"Candidate package is missing: {package_name!r}.")

    digest = sha256(package)
    if digest != manifest.get("sha256"):
        raise RuntimeError("Candidate package SHA-256 does not match its manifest.")

    with zipfile.ZipFile(package) as zf:
        names = set(zf.namelist())
        unsafe = sorted(name for name in names if not _safe_member(name))
        if unsafe:
            raise RuntimeError(f"Unsafe ZIP member paths detected: {unsafe[:10]}")

        missing = sorted(REQUIRED_FILES - names)
        if missing:
            raise RuntimeError(f"Required candidate files are missing: {missing}")

        forbidden = sorted(
            name
            for name in names
            if name in FORBIDDEN_NAMES
            or PurePosixPath(name).name in FORBIDDEN_NAMES
            or any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)
        )
        if forbidden:
            raise RuntimeError(f"Forbidden runtime/source artifacts are packaged: {forbidden[:10]}")

        build_info = json.loads(zf.read("build-info.json"))
        if build_info.get("build_hash") != expected_build:
            raise RuntimeError("Embedded build-info.json does not match the expected source build.")
        if str(build_info.get("version") or "").strip().lstrip("v") != release_version:
            raise RuntimeError("Embedded build-info.json version does not match candidate release_version.")

    return manifest, package


def verify_extracted_runtime(package: Path, expected_build: str, timeout: float = 25.0) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dspec-package-verify-") as tmp:
        root = Path(tmp)
        extracted = root / "candidate"
        home = root / "home"
        extracted.mkdir()
        home.mkdir()

        with zipfile.ZipFile(package) as zf:
            for member in zf.infolist():
                if not _safe_member(member.filename):
                    raise RuntimeError(f"Unsafe ZIP member path: {member.filename}")
            zf.extractall(extracted)

        env = os.environ.copy()
        env["DSPEC_HOME"] = str(home)
        env["PYTHONPATH"] = str(extracted)

        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "dspec.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "3210",
            ],
            cwd=extracted,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        last_error: str | None = None
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen("http://127.0.0.1:3210/api/health", timeout=2.0) as response:
                        body = json.loads(response.read())
                    checks = {
                        "status": body.get("status") == "healthy",
                        "build_hash": body.get("build_hash") == expected_build,
                        "host": body.get("host") == "127.0.0.1",
                        "port": body.get("port") == 3210,
                        "database": body.get("database") == "connected",
                    }
                    if not all(checks.values()):
                        raise RuntimeError(f"Extracted runtime health checks failed: {checks}; body={body}")
                    return {
                        "health": body,
                        "checks": checks,
                    }
                except Exception as exc:
                    last_error = repr(exc)
                    if process.poll() is not None:
                        break
                    time.sleep(0.25)

            output = ""
            if process.stdout is not None and process.poll() is not None:
                output = process.stdout.read()[-6000:]
            raise RuntimeError(
                f"Extracted candidate did not become healthy within {timeout:.1f}s; "
                f"last_error={last_error}; output={output}"
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a DSpec candidate package and prove extracted runtime build identity."
    )
    parser.add_argument("--dist", default="dist", help="Directory containing candidate-manifest.json.")
    parser.add_argument("--expected-build", required=True, help="Exact source commit the package must identify.")
    parser.add_argument(
        "--report",
        default="dist/package-verification.json",
        help="Machine-readable verification report path.",
    )
    args = parser.parse_args()

    expected = args.expected_build.strip()
    if len(expected) < 12:
        raise SystemExit("--expected-build must be an identifiable commit SHA.")

    dist = Path(args.dist).resolve()
    manifest, package = inspect_package(dist, expected)
    runtime = verify_extracted_runtime(package, expected)

    report = {
        "status": "PASS",
        "validation_scope": "candidate_package_integrity_and_extracted_runtime_identity",
        "build_hash": expected,
        "package_filename": package.name,
        "package_sha256": manifest["sha256"],
        "manifest_validation_state": manifest["validation_state"],
        "required_files_verified": sorted(REQUIRED_FILES),
        "forbidden_artifact_check": "PASS",
        "zip_path_safety": "PASS",
        "runtime": runtime,
        "evidence_boundary": (
            "This verifies a CI candidate package only. It does not establish target-macOS validation, "
            "live LLM inference, deployment, deployment verification, or KNOWN_GOOD status."
        ),
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
