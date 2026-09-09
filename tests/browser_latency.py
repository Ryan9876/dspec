from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path(os.environ.get("DSPEC_BROWSER_LATENCY_REPORT", ROOT / "evidence" / "browser-latency.json"))
MODAL_SCREENSHOT = Path(
    os.environ.get("DSPEC_PROVIDER_MODAL_SCREENSHOT", ROOT / "evidence" / "provider-modal.png")
)
LIMIT_MS = 50.0


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


def measure_ui_latency(page) -> dict[str, object]:
    return page.evaluate(
        """async () => {
            const thresholdMs = 50;
            const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
            const exactLeafText = (text) =>
                Array.from(document.querySelectorAll("body *")).some(
                    el => el.children.length === 0
                      && (el.textContent || "").trim() === text
                      && el.getClientRects().length > 0
                );
            const buttonByText = (text) =>
                Array.from(document.querySelectorAll("button")).find(button => {
                    if ((button.textContent || "").trim() === text) return true;
                    return Array.from(button.querySelectorAll("span")).some(
                        span => span.children.length === 0
                          && (span.textContent || "").trim() === text
                    );
                });

            async function measure(button, predicate, label) {
                if (!button) throw new Error("Missing latency-test button: " + label);
                const start = performance.now();
                button.click();
                const deadline = start + 1000;
                while (performance.now() < deadline) {
                    await frame();
                    if (predicate()) {
                        await frame();
                        return performance.now() - start;
                    }
                }
                throw new Error("Timed out waiting for rendered state: " + label);
            }

            const samples = {
                stage_switch_ms: [],
                provider_modal_open_ms: [],
                provider_modal_close_ms: [],
                workspace_switch_ms: [],
            };

            for (let i = 0; i < 4; i += 1) {
                samples.stage_switch_ms.push(
                    await measure(buttonByText("Solution"), () => exactLeafText("Solution draft"), "Solution draft")
                );
                samples.stage_switch_ms.push(
                    await measure(buttonByText("Requirements"), () => exactLeafText("Requirements draft"), "Requirements draft")
                );
            }

            for (let i = 0; i < 5; i += 1) {
                const providerButton = document.querySelector('button[aria-label="LLM provider switcher"]');
                samples.provider_modal_open_ms.push(
                    await measure(providerButton, () => exactLeafText("LLM provider"), "provider modal open")
                );
                samples.provider_modal_close_ms.push(
                    await measure(buttonByText("Cancel"), () => !exactLeafText("LLM provider"), "provider modal close")
                );
            }

            for (let i = 0; i < 3; i += 1) {
                samples.workspace_switch_ms.push(
                    await measure(
                        buttonByText("Repository Audit"),
                        () => exactLeafText("Local repository audit"),
                        "repository audit workspace"
                    )
                );
                samples.workspace_switch_ms.push(
                    await measure(
                        buttonByText("Spec Builder"),
                        () => exactLeafText("Requirements draft"),
                        "spec builder workspace"
                    )
                );
            }

            const all = Object.values(samples).flat();
            const sorted = [...all].sort((a, b) => a - b);
            const p95 = sorted[Math.max(0, Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * 0.95)))];
            return {
                threshold_ms: thresholdMs,
                max_ms: Math.max(...all),
                p95_ms: p95,
                mean_ms: all.reduce((sum, value) => sum + value, 0) / all.length,
                sample_count: all.length,
                samples,
                measurement_boundary:
                    "DOM state transition observed in Chromium plus one subsequent requestAnimationFrame; Playwright transport excluded.",
            };
        }"""
    )


def main() -> None:
    home = Path(tempfile.mkdtemp(prefix="dspec-browser-latency-"))
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

    report: dict[str, object] = {
        "status": "FAIL",
        "requirement": "NFR-1.1",
        "threshold_ms": LIMIT_MS,
    }
    browser = None
    try:
        wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1512, "height": 982})
            page.set_default_timeout(15_000)
            page_errors: list[str] = []
            console_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

            page.goto("http://127.0.0.1:3210", wait_until="domcontentloaded")
            expect(page.get_by_text("DSpec AI", exact=True)).to_be_visible(timeout=10_000)

            page.once("dialog", lambda dialog: dialog.accept("latency-e2e"))
            page.get_by_role("button", name="New project").click()
            expect(page.get_by_text("latency-e2e", exact=True)).to_be_visible(timeout=10_000)
            expect(page.locator(".monaco-editor")).to_be_visible(timeout=15_000)

            page.get_by_role("button", name="Requirements").click()
            expect(page.get_by_text("Requirements draft", exact=True)).to_be_visible()

            measured = measure_ui_latency(page)
            report.update(measured)

            page.get_by_role("button", name="LLM provider switcher").click()
            expect(page.get_by_text("LLM provider", exact=True)).to_be_visible()
            MODAL_SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(MODAL_SCREENSHOT), full_page=True)
            page.get_by_role("button", name="Cancel").click()

            if page_errors:
                raise AssertionError(f"Browser page errors: {page_errors}")
            if console_errors:
                raise AssertionError(f"Browser console errors: {console_errors}")

            max_ms = float(measured["max_ms"])
            if max_ms > LIMIT_MS:
                raise AssertionError(
                    f"NFR-1.1 failed: max client render latency {max_ms:.3f} ms > {LIMIT_MS:.3f} ms"
                )

            report.update({
                "status": "PASS",
                "acceptance": "PASS",
                "evidence_boundary": (
                    "PASS applies to measured client-side stage/workspace/modal interactions in CI Chromium. "
                    "It does not establish target-Mac GUI latency or live-LLM first-chunk latency."
                ),
            })
            browser.close()
            browser = None
    except BaseException as exc:
        report["error"] = repr(exc)
        raise
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("BROWSER_LATENCY_REPORT", json.dumps(report, sort_keys=True), flush=True)

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


if __name__ == "__main__":
    main()
