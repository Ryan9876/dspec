from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT = Path(os.environ.get("DSPEC_BROWSER_SCREENSHOT", ROOT / "evidence" / "browser-smoke.png"))


def wait_health(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:3210/api/health", timeout=1.0) as response:
                body = json.loads(response.read().decode())
                if body.get("status") == "healthy":
                    return
        except Exception as exc:
            last = exc
        time.sleep(0.2)
    raise RuntimeError(f"DSpec did not become healthy: {last}")


def main() -> None:
    home = Path(tempfile.mkdtemp(prefix="dspec-browser-"))
    env = os.environ.copy()
    env["DSPEC_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)
    env["DSPEC_DISABLE_TRAY"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "dspec.app:app", "--host", "127.0.0.1", "--port", "3210"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    page = None
    browser = None
    failure: BaseException | None = None
    try:
        wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1512, "height": 982})
            page_errors: list[str] = []
            console_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

            page.goto("http://127.0.0.1:3210", wait_until="domcontentloaded")
            expect(page.get_by_text("DSpec AI", exact=True)).to_be_visible(timeout=10_000)
            health_probe = page.evaluate("""async () => {
                const response = await fetch('/api/health');
                return {status: response.status, text: await response.text()};
            }""")
            print("BROWSER_HEALTH_PROBE", json.dumps(health_probe), flush=True)
            assert health_probe["status"] == 200, health_probe
            expect(page.get_by_text("Constitution", exact=True)).to_be_visible()
            expect(page.get_by_text("Requirements", exact=True)).to_be_visible()
            expect(page.get_by_text("Solution", exact=True)).to_be_visible()
            expect(page.get_by_text("Tasks", exact=True)).to_be_visible()

            page.once("dialog", lambda dialog: dialog.accept("browser-e2e"))
            page.get_by_role("button", name="New project").click()
            try:
                expect(page.get_by_text("browser-e2e", exact=True)).to_be_visible(timeout=10_000)
            except AssertionError:
                sessions_probe = page.evaluate("""async () => {
                    const response = await fetch('/api/sessions');
                    return {status: response.status, text: await response.text()};
                }""")
                print("BROWSER_SESSIONS_PROBE", json.dumps(sessions_probe), flush=True)
                raise

            page.get_by_role("button", name="Requirements").click()
            expect(page.get_by_text("Requirements draft", exact=True)).to_be_visible()

            page.get_by_role("button", name="LLM provider switcher").click()
            expect(page.get_by_text("LLM provider", exact=True)).to_be_visible()
            page.get_by_role("button", name="Cancel").click()

            page.get_by_role("button", name="Repository Audit").click()
            expect(page.get_by_text("Local repository audit", exact=True)).to_be_visible()
            page.get_by_placeholder("/Users/you/Workspace/project").fill(str(ROOT))
            page.get_by_role("button", name="Scan").click()
            expect(page.get_by_text("Evidence & gaps", exact=True)).to_be_visible(timeout=15_000)
            expect(page.get_by_text("Upgrade specification", exact=True)).to_be_visible()

            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_text("browser-e2e", exact=True)).to_be_visible(timeout=10_000)
            expect(page.get_by_text("DSpec AI", exact=True)).to_be_visible()

            SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SCREENSHOT), full_page=True)

            if page_errors:
                raise AssertionError(f"Browser page errors: {page_errors}")
            if console_errors:
                raise AssertionError(f"Browser console errors: {console_errors}")
            browser.close()
            browser = None
    except BaseException as exc:
        failure = exc
        SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
        if page is not None:
            try:
                page.screenshot(path=str(SCREENSHOT), full_page=True)
            except Exception as screenshot_error:
                print("SCREENSHOT_FAILURE", repr(screenshot_error), flush=True)
        raise
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        if process.stdout is not None:
            backend_log = process.stdout.read()
            if backend_log:
                print("BACKEND_LOG_BEGIN", flush=True)
                print(backend_log[-12000:], flush=True)
                print("BACKEND_LOG_END", flush=True)
        if failure is not None:
            print("BROWSER_FAILURE", repr(failure), flush=True)


if __name__ == "__main__":
    main()
