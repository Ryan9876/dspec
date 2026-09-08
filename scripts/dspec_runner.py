#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

HOST = "127.0.0.1"
PORT = 3210
RUNNER_VERSION = "1.0.0"
BASE = Path(os.environ.get("DSPEC_HOME", "~/.dspec")).expanduser()
RUNTIME = BASE / "runtime"
PID_FILE = RUNTIME / "dspec.pid"
LOG_FILE = RUNTIME / "dspec.log"
INSTALLED_RELEASE = RUNTIME / "installed-release.json"
SOURCE_APP_DIR = Path(os.environ.get("DSPEC_APP_DIR", str(Path(__file__).resolve().parents[1]))).expanduser()


def port_open() -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((HOST, PORT)) == 0


def health() -> dict | None:
    try:
        request = urllib.request.Request(f"http://{HOST}:{PORT}/api/health", headers={"Host": f"{HOST}:{PORT}"})
        with urllib.request.urlopen(request, timeout=1.0) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode())
            if payload.get("port") != PORT or payload.get("status") != "healthy":
                return None
            return payload
    except Exception:
        return None


def pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def command_for_pid(value: int) -> str:
    proc = subprocess.run(["ps", "-p", str(value), "-o", "command="], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def is_dspec_pid(value: int) -> bool:
    cmd = command_for_pid(value)
    return "uvicorn" in cmd and "backend.dspec_app.main:app" in cmd


def terminate_owned_process(value: int) -> None:
    if not is_dspec_pid(value):
        raise RuntimeError("PID does not identify a DSpec uvicorn process")
    os.kill(value, signal.SIGTERM)
    for _ in range(50):
        if not command_for_pid(value):
            break
        time.sleep(0.1)
    if command_for_pid(value):
        os.kill(value, signal.SIGKILL)
        for _ in range(20):
            if not command_for_pid(value):
                break
            time.sleep(0.1)


def active_app_dir() -> Path:
    installed = RUNTIME / "app"
    if (installed / "backend" / "dspec_app" / "main.py").exists():
        return installed
    return SOURCE_APP_DIR


def version_tuple(value: str) -> tuple[int, ...]:
    cleaned = value.strip().lower().lstrip("v")
    parts = []
    for piece in cleaned.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def discover_release_manifest() -> Path | None:
    explicit = os.environ.get("DSPEC_RELEASE_MANIFEST")
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None

    candidates = [
        "~/Library/CloudStorage/GoogleDrive-*/My Drive/ChatGPT Projects/DSpec/02 - Releases/current/manifest.json",
        "~/Library/CloudStorage/GoogleDrive-*/Shared drives/ChatGPT Projects/DSpec/02 - Releases/current/manifest.json",
        "~/Google Drive/My Drive/ChatGPT Projects/DSpec/02 - Releases/current/manifest.json",
    ]
    for pattern in candidates:
        for value in sorted(glob.glob(str(Path(pattern).expanduser()))):
            path = Path(value)
            if path.is_file():
                return path
    return None


def installed_release_version() -> str | None:
    if not INSTALLED_RELEASE.exists():
        return None
    try:
        return str(json.loads(INSTALLED_RELEASE.read_text(encoding="utf-8")).get("release_version") or "")
    except Exception:
        return None


def maybe_apply_release() -> None:
    manifest_path = discover_release_manifest()
    if manifest_path is None:
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    release_version = str(manifest.get("release_version") or "")
    if not release_version or release_version == installed_release_version():
        return
    apply_release(manifest_path)


def start() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    state = health()
    if state:
        print(f"DSpec already healthy at http://{HOST}:{PORT}")
        return 0

    value = pid()
    if value is not None:
        if is_dspec_pid(value):
            print(f"Recycling stale DSpec process {value}.")
            terminate_owned_process(value)
            PID_FILE.unlink(missing_ok=True)
        elif command_for_pid(value):
            print("PID file points to a non-DSpec live process; refusing to terminate it.", file=sys.stderr)
            return 2
        else:
            PID_FILE.unlink(missing_ok=True)

    if port_open():
        print("Port 3210 is occupied by an unknown process; refusing to kill it.", file=sys.stderr)
        return 2

    try:
        maybe_apply_release()
    except Exception as exc:
        print(f"Validated release check failed; current installed source was not replaced: {exc}", file=sys.stderr)
        return 4

    app_dir = active_app_dir()
    python = app_dir / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path(sys.executable)
    env = os.environ.copy()
    env["DSPEC_FRONTEND_DIR"] = str(app_dir / "out")
    log = LOG_FILE.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [str(python), "-m", "uvicorn", "backend.dspec_app.main:app", "--host", HOST, "--port", str(PORT)],
        cwd=app_dir,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    for _ in range(30):
        state = health()
        if state:
            print(json.dumps(state, indent=2))
            return 0
        if process.poll() is not None:
            break
        time.sleep(1)
    if is_dspec_pid(process.pid):
        terminate_owned_process(process.pid)
    PID_FILE.unlink(missing_ok=True)
    print(f"DSpec did not become healthy. See {LOG_FILE}", file=sys.stderr)
    return 3


def stop() -> int:
    value = pid()
    if value is None:
        if port_open():
            print("Port 3210 is occupied but no DSpec PID file exists; refusing to kill an unknown process.", file=sys.stderr)
            return 2
        print("DSpec is not running.")
        return 0
    if not is_dspec_pid(value):
        if command_for_pid(value):
            print("PID file does not identify a DSpec uvicorn process; refusing to terminate it.", file=sys.stderr)
            return 2
        PID_FILE.unlink(missing_ok=True)
        if port_open():
            print("Stale PID removed, but port 3210 is occupied by an unknown process.", file=sys.stderr)
            return 2
        print("Removed stale DSpec PID file; DSpec was not running.")
        return 0
    terminate_owned_process(value)
    PID_FILE.unlink(missing_ok=True)
    if port_open():
        print("Port 3210 remains occupied after DSpec process termination.", file=sys.stderr)
        return 3
    print("DSpec stopped; standalone LLM services were not touched.")
    return 0


def status() -> int:
    state = health()
    if state:
        state["release_manifest"] = str(discover_release_manifest() or "not detected")
        state["installed_release"] = installed_release_version() or "source candidate"
        print(json.dumps(state, indent=2))
        return 0
    details = {
        "status": "stopped_or_unhealthy",
        "port_3210_occupied": port_open(),
        "pid_file": pid(),
        "release_manifest": str(discover_release_manifest() or "not detected"),
        "installed_release": installed_release_version() or "source candidate",
    }
    print(json.dumps(details, indent=2))
    return 1


def safe_extract(archive_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for item in archive.infolist():
            target = (destination / item.filename).resolve()
            target.relative_to(destination)
        archive.extractall(destination)


def backup_database() -> None:
    source = RUNTIME / "dspec.db"
    if not source.exists():
        return
    target = RUNTIME / f"dspec.db.backup-{int(time.time())}"
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def apply_release(manifest_path: Path) -> int:
    manifest_path = manifest_path.expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("state") not in {"validated", "deployment-verified", "known-good"}:
        raise RuntimeError("release manifest is not in an allowed validated state")
    minimum = str(manifest.get("minimum_runner_version") or "0.0.0")
    if version_tuple(RUNNER_VERSION) < version_tuple(minimum):
        raise RuntimeError(f"runner {RUNNER_VERSION} is below required {minimum}")
    package_name = str(manifest.get("package_filename") or "")
    if not package_name or Path(package_name).name != package_name:
        raise RuntimeError("release package filename is invalid")
    package = manifest_path.parent / package_name
    if not package.is_file():
        raise RuntimeError("release package is missing")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    if digest.lower() != str(manifest.get("sha256") or "").lower():
        raise RuntimeError("package SHA-256 does not match release manifest")

    backup_database()
    staging = RUNTIME / "app-next"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    safe_extract(package, staging)
    if not (staging / "backend" / "dspec_app" / "main.py").exists():
        shutil.rmtree(staging)
        raise RuntimeError("release package does not contain a DSpec backend")

    live = RUNTIME / "app"
    previous = RUNTIME / "app-previous"
    if previous.exists():
        shutil.rmtree(previous)
    if live.exists():
        live.replace(previous)
    staging.replace(live)
    marker = {
        "release_version": manifest.get("release_version"),
        "sha256": digest,
        "installed_at": int(time.time()),
        "manifest": str(manifest_path),
    }
    INSTALLED_RELEASE.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    print(f"Installed validated release {manifest.get('release_version', 'unknown')} into {live}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual DSpec local runner")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("status")
    update = sub.add_parser("apply-release")
    update.add_argument("manifest", type=Path)
    args = parser.parse_args()
    if args.command == "start":
        return start()
    if args.command == "stop":
        return stop()
    if args.command == "status":
        return status()
    if args.command == "apply-release":
        return apply_release(args.manifest)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
