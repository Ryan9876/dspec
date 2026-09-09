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

from .config import APP_VERSION, HOST, PORT, db_path, dspec_home, log_path, pid_path, runtime_dir


def _health(timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/health", timeout=timeout) as response:
            return json.loads(response.read().decode())
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
        process = subprocess.run(["lsof", "-ti", f"tcp:{PORT}"], capture_output=True, text=True)
        for line in process.stdout.splitlines():
            if line.strip().isdigit():
                return int(line.strip())
    return None


def _source_root() -> Path:
    override = os.environ.get("DSPEC_APP_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    active = runtime_dir() / "app"
    if (active / "dspec" / "app.py").exists():
        return active
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


def _marker_path() -> Path:
    return runtime_dir() / "active-release.json"


def _previous_marker_path() -> Path:
    return runtime_dir() / "active-release.previous.json"


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _semver_tuple(value: str) -> tuple[int, int, int]:
    core = str(value).strip().lstrip("v").split("-", 1)[0]
    parts = core.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise RuntimeError(f"Invalid semantic version: {value!r}")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _safe_extract(package: Path, temp: Path) -> None:
    with zipfile.ZipFile(package) as archive:
        for member in archive.infolist():
            dest = (temp / member.filename).resolve()
            if temp.resolve() not in dest.parents and dest != temp.resolve():
                raise RuntimeError("Unsafe path in release archive")
        archive.extractall(temp)


def _install_release_dependencies(temp: Path) -> None:
    install_target = f"{temp}[tray]"
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", install_target],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout)[-4000:]
        raise RuntimeError(f"Release dependency installation failed: {detail}")


def _verify_package_identity(temp: Path, manifest: dict) -> None:
    info_path = temp / "build-info.json"
    if not info_path.is_file():
        raise RuntimeError("Release package is missing build-info.json")
    info = _load_json(info_path)
    package_version = str(info.get("version", "")).strip().lstrip("v")
    release_version = str(manifest.get("release_version", "")).strip().lstrip("v")
    if package_version != release_version:
        raise RuntimeError("Release package version does not match manifest")
    expected_build = str(manifest.get("build_hash", "")).strip()
    if expected_build and str(info.get("build_hash", "")).strip() != expected_build:
        raise RuntimeError("Release package build identity does not match manifest")


def _verify_and_apply_release() -> dict:
    for manifest_path in _manifest_candidates():
        if not manifest_path.exists():
            continue
        data = _load_json(manifest_path)
        if data.get("validation_state") != "validated":
            return {"status": "manifest_not_validated", "manifest": str(manifest_path)}

        release_version = str(data.get("release_version", "")).strip().lstrip("v")
        _semver_tuple(release_version)
        minimum_runner = str(data.get("minimum_runner_version", "0.1.1")).strip().lstrip("v")
        if _semver_tuple(APP_VERSION) < _semver_tuple(minimum_runner):
            return {
                "status": "runner_update_required",
                "release_version": release_version,
                "minimum_runner_version": minimum_runner,
                "runner_version": APP_VERSION,
            }

        package_name = str(data.get("package_filename", "")).strip()
        package = manifest_path.parent / package_name
        if not package.exists():
            return {
                "status": "manifest_found_package_missing",
                "release_version": release_version,
                "package_filename": package_name,
            }

        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        if digest != str(data.get("sha256", "")).strip():
            raise RuntimeError("Release package checksum mismatch; refusing update")

        target = runtime_dir() / "app"
        current = _load_json(_marker_path())
        if current.get("sha256") == digest and (target / "dspec" / "app.py").exists():
            return {
                "status": "release_current",
                "release_version": release_version,
                "sha256": digest,
            }

        temp = runtime_dir() / "app.next"
        if temp.exists():
            shutil.rmtree(temp)
        temp.mkdir(parents=True)
        try:
            _safe_extract(package, temp)
            _verify_package_identity(temp, data)
            _install_release_dependencies(temp)

            if db_path().exists():
                stamp = time.strftime("%Y%m%d-%H%M%S")
                shutil.copy2(db_path(), runtime_dir() / f"dspec.db.backup-{stamp}")

            previous = runtime_dir() / "app.previous"
            if previous.exists():
                shutil.rmtree(previous)
            if target.exists():
                target.replace(previous)

            marker = _marker_path()
            previous_marker = _previous_marker_path()
            if previous_marker.exists():
                previous_marker.unlink()
            if marker.exists():
                shutil.copy2(marker, previous_marker)

            temp.replace(target)
            marker.write_text(
                json.dumps(
                    {
                        "version": release_version,
                        "build_hash": data.get("build_hash"),
                        "package_filename": package_name,
                        "sha256": digest,
                        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception:
            if temp.exists():
                shutil.rmtree(temp, ignore_errors=True)
            raise

        return {
            "status": "release_applied",
            "release_version": release_version,
            "sha256": digest,
            "package_filename": package_name,
        }
    return {"status": "no_release_manifest"}


def _rollback_release() -> dict:
    target = runtime_dir() / "app"
    previous = runtime_dir() / "app.previous"
    marker = _marker_path()
    previous_marker = _previous_marker_path()

    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    restored = False
    if previous.exists():
        previous.replace(target)
        restored = True

    if marker.exists():
        marker.unlink()
    if previous_marker.exists():
        previous_marker.replace(marker)

    return {
        "status": "rolled_back",
        "restored_previous_runtime": restored,
        "active_release": _load_json(marker),
    }


def _open_browser() -> None:
    command = ["open", f"http://localhost:{PORT}"] if sys.platform == "darwin" else ["xdg-open", f"http://localhost:{PORT}"]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _launch_backend(root: Path, open_browser: bool) -> dict:
    log = open(log_path(), "a", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "dspec.app:app", "--host", HOST, "--port", str(PORT)],
        cwd=root,
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    pid_path().write_text(str(proc.pid))
    for _ in range(300):
        health = _health(0.5)
        if health:
            tray_pid = _start_tray(root, env)
            if open_browser:
                _open_browser()
            return {"pid": proc.pid, "tray_pid": tray_pid, "health": health}
        if proc.poll() is not None:
            raise RuntimeError(f"DSpec exited during startup with code {proc.returncode}. See {log_path()}")
        time.sleep(0.1)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    raise RuntimeError("DSpec did not become healthy within 30 seconds")


def start(open_browser: bool = True) -> dict:
    health = _health()
    if health:
        root = _source_root()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
        tray_pid = _start_tray(root, env)
        if open_browser:
            _open_browser()
        return {
            "status": "already_running",
            "tray_pid": tray_pid,
            "release": {"status": "update_check_skipped_while_running"},
            "health": health,
        }

    occupied = _port_pid()
    if occupied:
        if not _verify_dspec_pid(occupied):
            raise RuntimeError(f"Port {PORT} is occupied by non-DSpec PID {occupied}; refusing to terminate it")
        os.kill(occupied, signal.SIGTERM)
        for _ in range(50):
            if not _pid_alive(occupied):
                break
            time.sleep(0.1)

    release = _verify_and_apply_release()
    root = _source_root()
    try:
        launched = _launch_backend(root, open_browser)
        return {"status": "started", "release": release, **launched}
    except RuntimeError as original:
        if release.get("status") != "release_applied":
            raise
        rollback = _rollback_release()
        fallback_root = _source_root()
        try:
            launched = _launch_backend(fallback_root, open_browser)
        except Exception:
            raise original
        return {
            "status": "started_after_release_rollback",
            "release": {**release, "rollback": rollback},
            **launched,
        }


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
    health = _health()
    return {
        "health": health,
        "version": (health or {}).get("version") or APP_VERSION,
        "runner_version": APP_VERSION,
        "active_release": _load_json(_marker_path()),
        "pid": _read_pid(),
        "port_pid": _port_pid(),
        "tray_pid": _read_tray_pid(),
        "home": str(dspec_home()),
        "log": str(log_path()),
    }


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.action == "start":
        result = start(not args.no_browser)
    elif args.action == "stop":
        result = stop()
    else:
        result = status()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    cli()
