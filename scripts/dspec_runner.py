#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

HOST = "127.0.0.1"
PORT = 3210
BASE = Path(os.environ.get("DSPEC_HOME", "~/.dspec")).expanduser()
RUNTIME = BASE / "runtime"
PID_FILE = RUNTIME / "dspec.pid"
LOG_FILE = RUNTIME / "dspec.log"
APP_DIR = Path(os.environ.get("DSPEC_APP_DIR", str(Path(__file__).resolve().parents[1]))).expanduser()


def port_open() -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((HOST, PORT)) == 0


def health() -> dict | None:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/health", timeout=1.0) as response:
            return json.loads(response.read().decode())
    except Exception:
        return None


def pid() -> int | None:
    if not PID_FILE.exists(): return None
    try: return int(PID_FILE.read_text().strip())
    except Exception: return None


def command_for_pid(value: int) -> str:
    proc = subprocess.run(["ps", "-p", str(value), "-o", "command="], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def is_dspec_pid(value: int) -> bool:
    cmd = command_for_pid(value)
    return "uvicorn" in cmd and "backend.dspec_app.main:app" in cmd


def start() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if health():
        print(f"DSpec already healthy at http://{HOST}:{PORT}")
        return 0
    if port_open():
        print("Port 3210 is occupied by a process that does not answer the DSpec health contract.", file=sys.stderr)
        return 2
    python = APP_DIR / ".venv" / "bin" / "python"
    if not python.exists(): python = Path(sys.executable)
    env = os.environ.copy()
    env["DSPEC_FRONTEND_DIR"] = str(APP_DIR / "out")
    log = LOG_FILE.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [str(python), "-m", "uvicorn", "backend.dspec_app.main:app", "--host", HOST, "--port", str(PORT)],
        cwd=APP_DIR,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    PID_FILE.write_text(str(process.pid))
    for _ in range(30):
        state = health()
        if state:
            print(json.dumps(state, indent=2))
            return 0
        if process.poll() is not None: break
        time.sleep(1)
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
        print("PID file does not identify a DSpec uvicorn process; refusing to terminate it.", file=sys.stderr)
        return 2
    os.kill(value, signal.SIGTERM)
    for _ in range(50):
        if not command_for_pid(value): break
        time.sleep(0.1)
    if command_for_pid(value): os.kill(value, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    if port_open():
        print("Port 3210 remains occupied after DSpec process termination.", file=sys.stderr)
        return 3
    print("DSpec stopped; standalone LLM services were not touched.")
    return 0


def status() -> int:
    state = health()
    if state:
        print(json.dumps(state, indent=2)); return 0
    print("DSpec is stopped or unhealthy."); return 1


def safe_extract(archive_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for item in archive.infolist():
            target = (destination / item.filename).resolve()
            target.relative_to(destination)
        archive.extractall(destination)


def apply_release(manifest_path: Path) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("state") not in {"validated", "deployment-verified", "known-good"}:
        raise RuntimeError("release manifest is not in an allowed validated state")
    package = manifest_path.parent / manifest["package_filename"]
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    if digest != manifest["sha256"]:
        raise RuntimeError("package SHA-256 does not match release manifest")
    db = RUNTIME / "dspec.db"
    if db.exists(): shutil.copy2(db, RUNTIME / f"dspec.db.backup-{int(time.time())}")
    staging = RUNTIME / "app-next"
    if staging.exists(): shutil.rmtree(staging)
    staging.mkdir(parents=True)
    safe_extract(package, staging)
    live = RUNTIME / "app"
    previous = RUNTIME / "app-previous"
    if previous.exists(): shutil.rmtree(previous)
    if live.exists(): live.replace(previous)
    staging.replace(live)
    print(f"Installed validated release {manifest.get('release_version', 'unknown')} into {live}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual DSpec local runner")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start"); sub.add_parser("stop"); sub.add_parser("status")
    update = sub.add_parser("apply-release"); update.add_argument("manifest", type=Path)
    args = parser.parse_args()
    if args.command == "start": return start()
    if args.command == "stop": return stop()
    if args.command == "status": return status()
    if args.command == "apply-release": return apply_release(args.manifest)
    return 1

if __name__ == "__main__": raise SystemExit(main())
