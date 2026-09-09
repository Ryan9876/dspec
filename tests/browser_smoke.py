from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

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
            page.set_default_timeout(15_000)
            page.set_default_navigation_timeout(15_000)
            page_errors: list[str] = []
            console_errors: list[str] = []
            external_requests: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

            def record_request(request) -> None:
                parsed = urlsplit(request.url)
                if parsed.scheme in {"http", "https", "ws", "wss"} and parsed.hostname not in {
                    "127.0.0.1",
                    "localhost",
                    "::1",
                }:
                    external_requests.append(request.url)

            page.on("request", record_request)

            page.goto("http://127.0.0.1:3210", wait_until="domcontentloaded")
            expect(page.get_by_text("DSpec AI", exact=True)).to_be_visible(timeout=10_000)
            health_probe = page.evaluate("""async () => {
                const response = await fetch('/api/health', {signal: AbortSignal.timeout(10000)});
                return {status: response.status, text: await response.text()};
            }""")
            print("BROWSER_HEALTH_PROBE", json.dumps(health_probe), flush=True)
            assert health_probe["status"] == 200, health_probe
            expect(page.get_by_role("button", name="LLM provider switcher")).to_contain_text("offline", timeout=10_000)
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
                    const response = await fetch('/api/sessions', {signal: AbortSignal.timeout(10000)});
                    return {status: response.status, text: await response.text()};
                }""")
                print("BROWSER_SESSIONS_PROBE", json.dumps(sessions_probe), flush=True)
                raise

            page.route("**/api/spec/stream", lambda route: route.fulfill(
                status=200,
                content_type="text/event-stream",
                headers={"X-DSpec-Start-Seq": "0"},
                body='id: 1\nevent: error\ndata: {"seq":1,"error":"generation_failed","message":"LM Studio unavailable","state_preserved":true,"fallback_options":["lm_studio","ollama","openai","anthropic"]}\n\n',
            ))
            page.get_by_role("button", name="Generate").click()
            expect(page.get_by_text("Generation interrupted", exact=True)).to_be_visible()
            expect(page.get_by_text("Saved project state is preserved.", exact=False)).to_be_visible()
            expect(page.get_by_text("Ollama · offline", exact=True)).to_be_visible()
            page.get_by_role("button", name="Cancel").click()
            expect(page.get_by_role("button", name="Switch provider")).to_be_visible()
            page.unroute("**/api/spec/stream")

            page.get_by_role("button", name="Requirements").click()
            expect(page.get_by_text("Requirements draft", exact=True)).to_be_visible()

            review_fixture = """# Requirements Definition

## User workflow
The user can create a governed project, move through each specification stage, and save draft progress without losing prior-stage context.

## Acceptance criteria
The application must preserve saved specification content across browser refresh and must make review status visible before approval.
"""
            save_probe = page.evaluate(
                """async ({content}) => {
                    const sessionsResponse = await fetch('/api/sessions');
                    const sessions = await sessionsResponse.json();
                    const session = sessions.find(item => item.bundle_name === 'browser-e2e');
                    if (!session) return {status: 404, body: 'session missing'};
                    const response = await fetch('/api/spec/save', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({session_id: session.id, stage: 'requirements', content}),
                    });
                    return {status: response.status, body: await response.json(), session_id: session.id};
                }""",
                {"content": review_fixture},
            )
            print("BROWSER_REVIEW_FIXTURE_SAVE", json.dumps(save_probe), flush=True)
            assert save_probe["status"] == 200, save_probe

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
            page.get_by_role("button", name="Requirements").click()
            expect(page.locator(".monaco-editor")).to_be_visible(timeout=15_000)

            expect(page.get_by_text("Passing assertions", exact=True)).to_be_visible()
            expect(page.get_by_text("Must fix", exact=True)).to_be_visible()
            expect(page.get_by_text("Actionable recommendations", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="Apply fix").first).to_be_visible()
            expect(page.get_by_role("button", name="Apply recommendation").first).to_be_visible()

            revision_requests: list[dict[str, object]] = []

            def fulfill_revision(route) -> None:
                request_body = json.loads(route.request.post_data or "{}")
                revision_requests.append(request_body)
                revised = str(request_body.get("content") or "") + (
                    "\n\n## Recovery behavior\n"
                    "Invalid input produces an explicit validation response and preserves the user's saved draft."
                )
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "content": revised,
                        "review": {
                            "score": 0.95,
                            "threshold": 0.90,
                            "passed": True,
                            "passing": [{
                                "id": "applied-correction",
                                "label": "Applied correction",
                                "detail": "The selected review instruction was applied to the draft buffer.",
                            }],
                            "must_fix": [],
                            "recommendations": [],
                        },
                        "metrics": {
                            "provider": "fixture",
                            "model": "fixture",
                            "inference_latency_ms": 1,
                        },
                        "draft": {
                            "content": revised,
                            "updated_at": "2026-09-09T00:00:00Z",
                        },
                        "formal_revision_created": False,
                    }),
                )

            page.route("**/api/spec/revise", fulfill_revision)
            page.get_by_role("button", name="Apply fix").first.click()
            expect(page.get_by_text("Applied correction", exact=True)).to_be_visible()
            assert revision_requests, "Review apply action did not call /api/spec/revise"
            assert revision_requests[0].get("stage") == "requirements"
            assert revision_requests[0].get("instruction") == "Add error and edge-case behavior."
            print("BROWSER_REVIEW_APPLY", json.dumps(revision_requests[0]), flush=True)

            SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SCREENSHOT), full_page=True)

            if page_errors:
                raise AssertionError(f"Browser page errors: {page_errors}")
            if console_errors:
                raise AssertionError(f"Browser console errors: {console_errors}")
            if external_requests:
                raise AssertionError(f"Unexpected browser network egress: {external_requests}")
            print("BROWSER_NETWORK_EGRESS", json.dumps({"unexpected_external_requests": external_requests}), flush=True)
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
