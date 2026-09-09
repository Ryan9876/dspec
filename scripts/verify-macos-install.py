from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any

BASE_URL = "http://127.0.0.1:3210"


def run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, env=env, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{result.stdout[-5000:]}\nstderr:\n{result.stderr[-5000:]}"
        )
    return result


def http_json(path: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> tuple[int, Any]:
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE_URL + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return response.status, json.loads(raw.decode() or "null")


def wait_health(expected_up: bool, timeout: float = 35.0) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            _, data = http_json("/api/health", timeout=1.0)
            if expected_up:
                return data
            last = data
        except Exception as exc:
            if not expected_up:
                return None
            last = repr(exc)
        time.sleep(0.25)
    raise RuntimeError(f"health wait timeout; expected_up={expected_up}; last={last}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def keychain_read(provider: str) -> str | None:
    service = f"dspec.ai.{provider}"
    result = run(["security", "find-generic-password", "-s", service, "-a", provider, "-w"], check=False, timeout=20)
    return result.stdout.strip() if result.returncode == 0 else None


def keychain_delete(provider: str) -> None:
    service = f"dspec.ai.{provider}"
    run(["security", "delete-generic-password", "-s", service, "-a", provider], check=False, timeout=20)


def record(results: list[dict[str, Any]], name: str, status: str, detail: str = "", data: Any = None) -> None:
    item: dict[str, Any] = {"name": name, "status": status, "detail": detail}
    if data is not None:
        item["data"] = data
    results.append(item)
    print(f"{status:10} {name}: {detail}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify DSpec candidate installation and core runtime behavior on GitHub-hosted macOS.")
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--expected-build", required=True)
    parser.add_argument("--report", default="evidence/macos-contract.json")
    args = parser.parse_args()

    expected = args.expected_build.strip()
    if len(expected) < 12:
        raise SystemExit("--expected-build must be an identifiable commit SHA.")
    if platform.system() != "Darwin":
        raise SystemExit(f"macOS contract requires Darwin, got {platform.system()}")

    dist = Path(args.dist).resolve()
    manifest_path = dist / "candidate-manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"Missing {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("build_hash") != expected:
        raise SystemExit(f"Manifest build {manifest.get('build_hash')} != {expected}")
    if manifest.get("validation_state") != "candidate":
        raise SystemExit("macOS contract only accepts candidate manifests.")

    package = dist / str(manifest.get("package_filename") or "")
    if not package.is_file():
        raise SystemExit(f"Missing candidate package {package}")
    if sha256(package) != manifest.get("sha256"):
        raise SystemExit("Candidate package checksum mismatch.")

    required_tools = ("python3", "security", "lsof", "osadecompile")
    missing = [tool for tool in required_tools if not shutil.which(tool)]
    if missing:
        raise SystemExit(f"Missing required macOS tools: {missing}")

    home = Path.home()
    install_root = home / ".dspec"
    apps = home / "Applications"
    if install_root.exists():
        raise SystemExit("GitHub macOS contract requires a clean ~/.dspec on its ephemeral runner.")

    results: list[dict[str, Any]] = []
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    validation_home = Path(tempfile.mkdtemp(prefix="dspec-macos-contract-home-"))
    env = os.environ.copy()
    env["DSPEC_HOME"] = str(validation_home)
    env["DSPEC_DISABLE_TRAY"] = "1"

    provider_for_keychain = "openai"
    try:
        record(results, "macOS platform", "PASS", platform.platform())
        record(results, "candidate checksum", "PASS", str(manifest["sha256"]))

        with tempfile.TemporaryDirectory(prefix="dspec-macos-contract-package-") as tmp:
            extracted = Path(tmp) / "candidate"
            extracted.mkdir()
            with zipfile.ZipFile(package) as zf:
                zf.extractall(extracted)

            build_info = json.loads((extracted / "build-info.json").read_text(encoding="utf-8"))
            if build_info.get("build_hash") != expected:
                raise RuntimeError("Extracted build-info.json does not match expected build.")
            record(results, "extracted build identity", "PASS", expected)

            occupied = run(["lsof", "-ti", "tcp:3210"], check=False, timeout=20).stdout.strip()
            if occupied:
                raise RuntimeError(f"Port 3210 unexpectedly occupied by {occupied}")
            record(results, "port 3210 preflight", "PASS", "free")

            install_started = time.monotonic()
            run(["bash", str(extracted / "scripts" / "install-macos.sh")], cwd=extracted, timeout=900)
            record(results, "macOS installer", "PASS", f"{time.monotonic() - install_started:.2f}s")

        installed_info = json.loads((install_root / "source" / "build-info.json").read_text(encoding="utf-8"))
        if installed_info.get("build_hash") != expected:
            raise RuntimeError("Installed build-info.json does not match expected build.")
        record(results, "installed build identity", "PASS", expected)

        venv_python = install_root / "venv" / "bin" / "python"
        run([str(venv_python), "-c", "import rumps; print(rumps.__version__ if hasattr(rumps, '__version__') else 'rumps-import-ok')"], timeout=30)
        record(results, "tray dependency import", "PASS", "rumps import succeeds")

        for app_name in ("Start DSpec.app", "Stop DSpec.app", "DSpec Status.app"):
            script = apps / app_name / "Contents" / "Resources" / "Scripts" / "main.scpt"
            if not script.is_file():
                raise RuntimeError(f"Native launcher missing compiled script: {app_name}")
            run(["osadecompile", str(script)], timeout=30)
        record(results, "native launcher compilation", "PASS", "Start/Stop/Status apps compile and decompile")
        record(results, "native launcher GUI execution", "NOT TESTED", "Headless CI does not claim LaunchServices/UI behavior")
        record(results, "menu-bar tray GUI behavior", "NOT TESTED", "Tray intentionally disabled in headless CI")

        dspec_bin = install_root / "bin" / "dspec"
        start_started = time.monotonic()
        start = run([str(dspec_bin), "start", "--no-browser"], env=env, timeout=75)
        start_data = json.loads(start.stdout)
        health = start_data.get("health") or wait_health(True)
        if health.get("build_hash") != expected:
            raise RuntimeError(f"Runtime build mismatch: {health}")
        if health.get("host") != "127.0.0.1" or health.get("port") != 3210 or health.get("database") != "connected":
            raise RuntimeError(f"Runtime health contract failed: {health}")
        if start_data.get("tray_pid") is not None:
            raise RuntimeError("DSPEC_DISABLE_TRAY=1 did not suppress tray startup.")
        record(results, "CLI start and runtime health", "PASS", f"{time.monotonic() - start_started:.3f}s", health)

        session_name = f"macos-contract-{uuid.uuid4().hex[:8]}"
        _, session = http_json("/api/sessions", "POST", {"bundle_name": session_name, "project_type": "greenfield"})
        session_id = session["id"]
        draft_text = "# macOS CI draft\n\nPersistence marker."
        http_json("/api/spec/draft", "POST", {"session_id": session_id, "stage": "constitution", "content": draft_text})
        record(results, "session and draft write", "PASS", session_id)

        dummy = f"sk-dspec-macos-contract-{uuid.uuid4().hex}"
        keychain_delete(provider_for_keychain)
        _, selection = http_json(
            "/api/provider/select",
            "POST",
            {"provider": provider_for_keychain, "model": "validation-no-inference", "api_key": dummy},
        )
        if selection.get("credential_storage") != "macos_keychain":
            raise RuntimeError(f"Expected macos_keychain storage, got {selection}")
        if keychain_read(provider_for_keychain) != dummy:
            raise RuntimeError("Disposable API key did not round-trip through macOS Keychain.")
        keychain_delete(provider_for_keychain)
        if keychain_read(provider_for_keychain) is not None:
            raise RuntimeError("Disposable API key remained in Keychain after deletion.")
        record(results, "macOS Keychain write/read/delete", "PASS", "disposable credential only")

        stop = run([str(dspec_bin), "stop"], env=env, timeout=45)
        stop_data = json.loads(stop.stdout)
        wait_health(False, 20)
        record(results, "CLI stop", "PASS", str(stop_data.get("status")))

        restart_started = time.monotonic()
        restarted = run([str(dspec_bin), "start", "--no-browser"], env=env, timeout=75)
        restart_data = json.loads(restarted.stdout)
        restart_health = restart_data.get("health") or wait_health(True)
        if restart_health.get("build_hash") != expected:
            raise RuntimeError("Restarted runtime build identity mismatch.")
        _, readback = http_json(f"/api/sessions/{session_id}")
        actual_draft = readback.get("drafts", {}).get("constitution", {}).get("content")
        if actual_draft != draft_text:
            raise RuntimeError(f"Draft persistence mismatch: {actual_draft!r}")
        record(results, "restart persistence", "PASS", f"{time.monotonic() - restart_started:.3f}s")

        providers_status, providers = http_json("/api/providers")
        if providers_status != 200:
            raise RuntimeError("Provider discovery endpoint failed.")
        record(results, "provider discovery API", "PASS", "no live inference attempted", providers)
        record(results, "live provider inference", "NOT TESTED", "No provider/model execution is authorized in macOS CI contract")

        run([str(dspec_bin), "stop"], env=env, timeout=45)
        wait_health(False, 20)
        final_port = run(["lsof", "-ti", "tcp:3210"], check=False, timeout=20).stdout.strip()
        if final_port:
            raise RuntimeError(f"Port 3210 still occupied after cleanup: {final_port}")
        record(results, "runtime cleanup", "PASS", "port 3210 released")

        # Exercise the actual compiled Start/Stop .app launchers through macOS
        # LaunchServices. This verifies native app dispatch into the runner,
        # backend startup/build identity, graceful shutdown, and that Stop does
        # not terminate an unrelated sentinel process. The hosted runner is
        # still not evidence of visible browser/menu-bar rendering.
        launchctl = shutil.which("launchctl")
        open_tool = shutil.which("open")
        if not launchctl or not open_tool:
            raise RuntimeError("LaunchServices contract requires launchctl and open.")

        sentinel = subprocess.Popen(["sleep", "90"])
        try:
            run([launchctl, "setenv", "DSPEC_DISABLE_TRAY", "1"], check=False, timeout=20)

            native_start_started = time.monotonic()
            run([open_tool, "-W", str(apps / "Start DSpec.app")], timeout=90)
            native_health = wait_health(True, 45)
            if native_health is None or native_health.get("build_hash") != expected:
                raise RuntimeError(f"Native Start DSpec.app build identity mismatch: {native_health}")
            if native_health.get("host") != "127.0.0.1" or native_health.get("port") != 3210:
                raise RuntimeError(f"Native Start DSpec.app health contract failed: {native_health}")
            record(
                results,
                "native Start DSpec.app LaunchServices execution",
                "PASS",
                f"{time.monotonic() - native_start_started:.3f}s",
                native_health,
            )
            record(
                results,
                "native launcher browser rendering",
                "NOT TESTED",
                "Start DSpec.app exercised the browser-enabled runner path, but hosted CI does not prove a visible browser window.",
            )

            if sentinel.poll() is not None:
                raise RuntimeError("Unrelated sentinel process exited during native Start DSpec.app execution.")

            native_stop_started = time.monotonic()
            run([open_tool, "-W", str(apps / "Stop DSpec.app")], timeout=60)
            wait_health(False, 30)
            remaining = run(["lsof", "-ti", "tcp:3210"], check=False, timeout=20).stdout.strip()
            if remaining:
                raise RuntimeError(f"Port 3210 remains occupied after native Stop DSpec.app: {remaining}")
            record(
                results,
                "native Stop DSpec.app LaunchServices execution",
                "PASS",
                f"{time.monotonic() - native_stop_started:.3f}s; port 3210 released",
            )

            if sentinel.poll() is not None:
                raise RuntimeError("Stop DSpec.app terminated an unrelated sentinel process.")
            record(results, "unrelated process preservation", "PASS", f"sentinel PID {sentinel.pid} remained alive")
        finally:
            run([str(dspec_bin), "stop"], timeout=30, check=False)
            run([launchctl, "unsetenv", "DSPEC_DISABLE_TRAY"], check=False, timeout=20)
            if sentinel.poll() is None:
                sentinel.terminate()
                try:
                    sentinel.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    sentinel.kill()
                    sentinel.wait(timeout=2)

        # Prove the install-once update contract with a synthetic validated
        # release. The fixture uses the same tested code with a different
        # build-info identity, so no live release is published or implied.
        with tempfile.TemporaryDirectory(prefix="dspec-macos-update-fixture-") as update_tmp:
            update_root = Path(update_tmp)
            stage = update_root / "stage"
            shutil.copytree(install_root / "source", stage)
            fixture_version = "0.1.2"
            fixture_build = f"update-fixture-{expected[:16]}"
            fixture_info = json.loads((stage / "build-info.json").read_text(encoding="utf-8"))
            fixture_info["version"] = fixture_version
            fixture_info["build_hash"] = fixture_build
            (stage / "build-info.json").write_text(json.dumps(fixture_info, indent=2) + "\n", encoding="utf-8")

            release_dir = update_root / "current"
            release_dir.mkdir()
            release_package = release_dir / f"DSpec-v{fixture_version}.zip"
            with zipfile.ZipFile(release_package, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(stage.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(stage).as_posix())
            release_digest = sha256(release_package)
            release_manifest = release_dir / "manifest.json"
            release_manifest.write_text(
                json.dumps(
                    {
                        "release_version": fixture_version,
                        "release_date": "2026-09-09T00:00:00Z",
                        "build_hash": fixture_build,
                        "minimum_runner_version": "0.1.1",
                        "package_filename": release_package.name,
                        "sha256": release_digest,
                        "validation_state": "validated",
                        "release_notes": "Synthetic hosted-macOS updater fixture only.",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            update_env = env.copy()
            update_env["DSPEC_RELEASE_MANIFEST"] = str(release_manifest)
            updated = run([str(dspec_bin), "start", "--no-browser"], env=update_env, timeout=180)
            updated_data = json.loads(updated.stdout)
            updated_health = updated_data.get("health") or wait_health(True)
            if updated_data.get("release", {}).get("status") != "release_applied":
                raise RuntimeError(f"Validated release was not applied: {updated_data}")
            if updated_health.get("version") != fixture_version or updated_health.get("build_hash") != fixture_build:
                raise RuntimeError(f"Updated runtime identity mismatch: {updated_health}")
            _, persisted_after_update = http_json(f"/api/sessions/{session_id}")
            if persisted_after_update.get("drafts", {}).get("constitution", {}).get("content") != draft_text:
                raise RuntimeError("User draft did not survive governed release update.")
            record(
                results,
                "install-once validated release update",
                "PASS",
                f"{fixture_version} applied with checksum verification; persisted draft preserved",
                updated_data,
            )

            run([str(dspec_bin), "stop"], env=update_env, timeout=45)
            wait_health(False, 20)

            # No manifest override on this second start. The installed shell
            # launcher must select ~/.dspec/runtime/app and therefore execute
            # the updated DSpec runner/application without another installer.
            updated_restart = run([str(dspec_bin), "start", "--no-browser"], env=env, timeout=90)
            updated_restart_data = json.loads(updated_restart.stdout)
            updated_restart_health = updated_restart_data.get("health") or wait_health(True)
            if updated_restart_health.get("version") != fixture_version or updated_restart_health.get("build_hash") != fixture_build:
                raise RuntimeError(f"Updated runtime was not retained for subsequent starts: {updated_restart_health}")
            record(
                results,
                "subsequent start uses updated runtime",
                "PASS",
                f"v{fixture_version} remained active without reinstall",
                updated_restart_data,
            )
            run([str(dspec_bin), "stop"], env=env, timeout=45)
            wait_health(False, 20)

        report = {
            "status": "PASS",
            "validation_scope": "github_hosted_macos_arm64_installer_cli_persistence_keychain_launchservices_contract",
            "expected_build": expected,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "candidate_package_sha256": manifest["sha256"],
            "results": results,
            "evidence_boundary": (
                "GitHub-hosted macOS evidence establishes LaunchServices execution of Start/Stop .app launchers, "
                "but does not establish visible browser rendering, behavior on the user's target Mac, menu-bar interaction, "
                "live LLM inference, deployment, deployment verification, or KNOWN_GOOD status."
            ),
        }
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        keychain_delete(provider_for_keychain)
        dspec_bin = install_root / "bin" / "dspec"
        if dspec_bin.exists():
            run([str(dspec_bin), "stop"], env=env, timeout=30, check=False)
        shutil.rmtree(validation_home, ignore_errors=True)


if __name__ == "__main__":
    main()
