from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from .config import HOST, PORT, db_path, dspec_home, log_path, pid_path, runtime_dir


def _health(timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/health", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid() -> int | None:
    try:
        return int(pid_path().read_text().strip())
    except Exception:
        return None


def _tray_pid_path() -> Path:
    return runtime_dir() / "tray.pid"


def _read_tray_pid() -> int | None:
    try:
        return int(_tray_pid_path().read_text().strip())
    except Exception:
        return None


def _start_tray(root: Path, env: dict[str, str]) -> int | None:
    if sys.platform != "darwin" or os.environ.get("DSPEC_DISABLE_TRAY") == "1":
        return None
    current = _read_tray_pid()
    if current and _pid_alive(current):
        return current
    proc = subprocess.Popen(
        [sys.executable, "-m", "dspec.tray"],
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def _stop_tray() -> None:
    pid = _read_tray_pid()
    if not pid:
        return
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    _tray_pid_path().unlink(missing_ok=True)


def _verify_dspec_pid(pid: int) -> bool:
    try:
        out = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True).strip().lower()
        return "dspec" in out or "uvicorn" in out
    except Exception:
        return False


def _port_pid() -> int | None:
    if shutil.which("lsof"):
        p = subprocess.run(["lsof", "-ti", f"tcp:{PORT}"], capture_output=True, text=True)
        for line in p.stdout.splitlines():
            if line.strip().isdigit(): return int(line.strip())
    return None


def _source_root() -> Path:
    override = os.environ.get("DSPEC_APP_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    runtime_app = runtime_dir() / "app"
    if (runtime_app / "dspec" / "app.py").exists():
        return runtime_app
    return Path(__file__).resolve().parents[1]


def _manifest_candidates() -> list[Path]:
    explicit = os.environ.get("DSPEC_RELEASE_MANIFEST")
    out: list[Path] = [Path(explicit).expanduser()] if explicit else []
    cloud = Path.home() / "Library" / "CloudStorage"
    if cloud.exists():
        for root in cloud.glob("GoogleDrive-*"):
            for mydrive in [root / "My Drive", root]:
                out.append(mydrive / "ChatGPT Projects" / "DSpec" / "02 - Releases" / "current" / "manifest.json")
    return out


def _verify_and_apply_release() -> str:
    for manifest_path in _manifest_candidates():
        if not manifest_path.exists():
            continue
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        package = manifest_path.parent / data["package_filename"]
        if not package.exists():
            return "manifest_found_package_missing"
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        if digest != data["sha256"]:
            raise RuntimeError("Release package checksum mismatch; refusing update")
        target = runtime_dir() / "app"
        marker = runtime_dir() / "active-release.json"
        current = json.loads(marker.read_text()) if marker.exists() else {}
        if current.get("sha256") == digest:
            return "release_current"
        if db_path().exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(db_path(), runtime_dir() / f"dspec.db.backup-{stamp}")
        temp = runtime_dir() / "app.next"
        if temp.exists(): shutil.rmtree(temp)
        temp.mkdir(parents=True)
        with zipfile.ZipFile(package) as zf:
            for member in zf.infolist():
                dest = (temp / member.filename).resolve()
                if temp.resolve() not in dest.parents and dest != temp.resolve():
                    raise RuntimeError("Unsafe path in release archive")
            zf.extractall(temp)
        if target.exists(): shutil.rmtree(target)
        temp.replace(target)
        marker.write_text(json.dumps({"version": data.get("release_version"), "sha256": digest}, indent=2))
        return "release_applied"
    return "no_release_manifest"


def start(open_browser: bool = True) -> dict:
    health = _health()
    if health:
        if open_browser:
            subprocess.Popen(["open", f"http://localhost:{PORT}"] if sys.platform == "darwin" else ["xdg-open", f"http://localhost:{PORT}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"status": "already_running", "health": health}
    occupied = _port_pid()
    if occupied:
        if not _verify_dspec_pid(occupied):
            raise RuntimeError(f"Port {PORT} is occupied by non-DSpec PID {occupied}; refusing to terminate it")
        os.kill(occupied, signal.SIGTERM)
        for _ in range(50):
            if not _pid_alive(occupied): break
            time.sleep(0.1)
    release = _verify_and_apply_release()
    root = _source_root()
    log = open(log_path(), "a", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "dspec.app:app", "--host", HOST, "--port", str(PORT)], cwd=root, env=env, stdout=log, stderr=log, start_new_session=True)
    pid_path().write_text(str(proc.pid))
    for _ in range(300):
        health = _health(0.5)
        if health:
            tray_pid = _start_tray(root, env)
            if open_browser:
                cmd = ["open", f"http://localhost:{PORT}"] if sys.platform == "darwin" else ["xdg-open", f"http://localhost:{PORT}"]
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"status": "started", "pid": proc.pid, "tray_pid": tray_pid, "release": release, "health": health}
        if proc.poll() is not None:
            raise RuntimeError(f"DSpec exited during startup with code {proc.returncode}. See {log_path()}")
        time.sleep(0.1)
    proc.terminate()
    raise RuntimeError("DSpec did not become healthy within 30 seconds")


def stop(include_tray: bool = True) -> dict:
    pid = _read_pid() or _port_pid()
    if not pid:
        if include_tray:
            _stop_tray()
        return {"status": "not_running"}
    if not _verify_dspec_pid(pid):
        raise RuntimeError(f"PID {pid} does not look like DSpec; refusing to terminate")
    os.kill(pid, signal.SIGTERM)
    outcome = "stopped"
    for _ in range(50):
        if not _pid_alive(pid):
            break
        time.sleep(0.1)
    else:
        os.kill(pid, signal.SIGKILL)
        outcome = "killed_after_timeout"
    pid_path().unlink(missing_ok=True)
    if include_tray:
        _stop_tray()
    return {"status": outcome, "pid": pid}


def status() -> dict:
    return {
        "health": _health(),
        "pid": _read_pid(),
        "port_pid": _port_pid(),
        "tray_pid": _read_tray_pid(),
        "home": str(dspec_home()),
        "log": str(log_path()),
    }


def cli() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["start", "stop", "status"])
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    if args.action == "start": result = start(not args.no_browser)
    elif args.action == "stop": result = stop()
    else: result = status()
    print(json.dumps(result, indent=2))

if __name__ == "__main__": cli()
