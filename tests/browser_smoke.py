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
            expect(page.get_by_text("v0.1.2", exact=True)).to_be_visible(timeout=10_000)
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

            # Render a provider-backed-style provisional stream in the browser
            # with timing gaps so the provisional and final-selection states
            # are observable rather than only source-reviewed.
            page.evaluate("""() => {
                const originalFetch = window.fetch.bind(window);
                window.__dspecOriginalFetch = originalFetch;
                window.fetch = (input, init) => {
                    const url = typeof input === "string" ? input : input.url;
                    if (url === "/api/spec/stream" && init?.method === "POST") {
                        const encoder = new TextEncoder();
                        const stream = new ReadableStream({
                            start(controller) {
                                const send = (value) => controller.enqueue(encoder.encode(value));
                                send('id: 1\\nevent: candidate_start\\ndata: {"seq":1,"attempt":1,"provisional":true}\\n\\n');
                                setTimeout(() => send('id: 2\\nevent: token\\ndata: {"seq":2,"attempt":1,"provisional":true,"text":"# Streaming requirements\\\\n\\\\nVisible while Refine evaluates."}\\n\\n'), 80);
                                setTimeout(() => send('id: 3\\nevent: candidate_end\\ndata: {"seq":3,"attempt":1,"provisional":true}\\n\\n'), 650);
                                setTimeout(() => send('id: 4\\nevent: candidate_selected\\ndata: {"seq":4,"provisional":false,"content":"# Final selected requirements\\\\n\\\\nAuthoritative Refine result."}\\n\\n'), 1150);
                                setTimeout(() => {
                                    send('id: 5\\nevent: complete\\ndata: {"seq":5,"specType":"requirements","revision":1,"version":1}\\n\\n');
                                    controller.close();
                                }, 1800);
                            }
                        });
                        return Promise.resolve(new Response(stream, {
                            status: 200,
                            headers: {
                                "Content-Type": "text/event-stream",
                                "X-DSpec-Start-Seq": "0"
                            }
                        }));
                    }
                    return originalFetch(input, init);
                };
            }""")
            page.get_by_role("button", name="Generate").click()
            expect(page.get_by_text("Provisional candidate 1", exact=False)).to_be_visible(timeout=5_000)
            expect(page.get_by_text("Visible while Refine evaluates.", exact=False)).to_be_visible(timeout=5_000)
            expect(page.get_by_text("Final Refine-selected draft", exact=False)).to_be_visible(timeout=5_000)
            expect(page.get_by_text("Authoritative Refine result.", exact=False)).to_be_visible(timeout=5_000)
            page.wait_for_timeout(900)
            page.evaluate("""() => {
                if (window.__dspecOriginalFetch) {
                    window.fetch = window.__dspecOriginalFetch;
                    delete window.__dspecOriginalFetch;
                }
            }""")

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
            page.locator("label").filter(has_text="Provider").locator("select").select_option("openai")
            key_input = page.locator('input[type="password"]')
            expect(key_input).to_be_visible()
            key_input.fill("short")
            expected_console_start = len(console_errors)
            page.get_by_role("button", name="Apply provider").click()
            expect(page.get_by_text("Provider settings could not be saved.", exact=False)).to_be_visible()
            expect(key_input).to_have_value("")
            expected_console = console_errors[expected_console_start:]
            assert expected_console == ["Failed to load resource: the server responded with a status of 422 (Unprocessable Entity)"], expected_console
            del console_errors[expected_console_start:]
            page.get_by_role("button", name="Cancel").click()

            # DS-CHG-002: rendered consequence-first architecture comparison.
            page.get_by_role("button", name="Solution").click()
            expect(page.get_by_text("Choose the implementation approach", exact=True)).to_be_visible()

            architecture_options = {
                "recommended_option_id": "best",
                "alternative_objective": "Enterprise alignment",
                "decision_summary": "Best Fit balances future growth with manageable operating complexity.",
                "source_context_sha256": "a" * 64,
                "customization": "",
                "options": [
                    {
                        "id": "best", "role": "best_fit", "title": "Best Fit — Recommended",
                        "plain_english_summary": "This gives the product room to grow without taking on unnecessary operational complexity.",
                        "why_recommended": "The requirements need structured shared data, integrations, and future expansion.",
                        "advantages": ["Strong overall fit"], "tradeoffs": ["Moderate operating complexity"],
                        "operational_impact": "A small number of well-understood services must be operated.",
                        "why_engineers_care": "Core platform choices become more expensive to change after data and integrations depend on them.",
                        "engineering_concept": {"id": "separation-of-concerns", "label": "Separation of concerns", "mental_model": "Give different responsibilities clear boundaries."},
                        "reconsider_when": ["The application becomes permanently single-user and local-only."],
                        "technical_details": [{"category": "Database", "choice": "PostgreSQL", "consequence": "Runs a relational database service."}],
                    },
                    {
                        "id": "simple", "role": "simplest", "title": "Simplest",
                        "plain_english_summary": "This keeps the number of moving parts as low as practical.",
                        "why_recommended": "Choose it when operational simplicity matters more than specialized components.",
                        "advantages": ["Fewer moving parts"], "tradeoffs": ["Less specialization"],
                        "operational_impact": "Lower day-to-day operating burden.",
                        "why_engineers_care": "Every additional component creates another failure and maintenance surface.",
                        "engineering_concept": {"id": "simplicity", "label": "Simplicity", "mental_model": "Do not add moving parts without a requirement."},
                        "reconsider_when": ["Scale or integration needs increase."],
                        "technical_details": [{"category": "Backend", "choice": "TypeScript", "consequence": "Keeps one primary application language."}],
                    },
                    {
                        "id": "alt", "role": "alternative", "title": "Enterprise Alignment",
                        "plain_english_summary": "This aligns closely with an existing Microsoft operating environment.",
                        "why_recommended": "Choose it when existing enterprise support and skills dominate the decision.",
                        "advantages": ["Environment fit"], "tradeoffs": ["Heavier platform"],
                        "operational_impact": "Uses established enterprise tooling and support patterns.",
                        "why_engineers_care": "Organizational skills and platform standards affect lifetime support cost.",
                        "engineering_concept": {"id": "platform-alignment", "label": "Platform alignment", "mental_model": "Prefer the existing operating environment unless requirements justify divergence."},
                        "reconsider_when": ["The operating environment changes."],
                        "technical_details": [{"category": "Backend", "choice": ".NET", "consequence": "Aligns with Microsoft tooling."}],
                    },
                ],
            }
            page.route("**/api/decisions/architecture/options", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(architecture_options)
            ))
            page.get_by_role("button", name="Compare options").click()
            expect(page.get_by_text("Best Fit — Recommended", exact=True)).to_be_visible()
            expect(page.get_by_text("Simplest", exact=True)).to_be_visible()
            expect(page.get_by_text("Enterprise Alignment", exact=True)).to_be_visible()
            expect(page.get_by_text("What this means for you", exact=True).first).to_be_visible()
            expect(page.get_by_text("Why engineers care", exact=True).first).to_be_visible()
            expect(page.get_by_text("Technical details", exact=True).first).to_be_visible()
            expect(page.get_by_text("PostgreSQL", exact=True)).not_to_be_visible()
            page.get_by_text("Technical details", exact=True).first.click()
            expect(page.get_by_text("PostgreSQL", exact=True)).to_be_visible()
            page.unroute("**/api/decisions/architecture/options")

            # Persist minimal Solution/Tasks fixtures so the Tasks-stage strategy
            # screen is exercised without invoking a live provider.
            tiers_probe = page.evaluate(
                """async ({sessionId}) => {
                    const headers = {'Content-Type': 'application/json'};
                    const solution = await fetch('/api/spec/save', {
                        method: 'POST', headers,
                        body: JSON.stringify({session_id: sessionId, stage: 'solution', content: '# Solution\\n\\nSOL-001 governed implementation approach.'}),
                    });
                    const tasks = await fetch('/api/spec/save', {
                        method: 'POST', headers,
                        body: JSON.stringify({session_id: sessionId, stage: 'tasks', content: '# Tasks\\n\\n## T-001 — Implement governed change\\nMaps to REQ-001. Verification: pytest -q'}),
                    });
                    return {solution: solution.status, tasks: tasks.status};
                }""",
                {"sessionId": save_probe["session_id"]},
            )
            assert tiers_probe == {"solution": 200, "tasks": 200}, tiers_probe
            page.reload(wait_until="domcontentloaded")
            page.get_by_role("button", name="Tasks").click()
            expect(page.get_by_text("Implementation strategy & cloud budget", exact=True)).to_be_visible()
            expect(page.get_by_text("Automatically escalate — Recommended", exact=True)).to_be_visible()
            expect(page.get_by_text("Budget controls may pause paid work", exact=False)).to_be_visible()

            def strategy_plan(name, label, local, standard, advanced, expected):
                return {
                    "strategy": name, "strategy_label": label, "budget_status": "NO_LIMIT", "blocked_task_count": 0,
                    "model_mix": {
                        "local": {"tasks": local, "percent": float(local * 10)},
                        "standard": {"tasks": standard, "percent": float(standard * 10)},
                        "advanced": {"tasks": advanced, "percent": float(advanced * 10)},
                    },
                    "cloud_api_cost_estimate": {"low": round(expected * 0.6, 2), "expected": expected, "high": round(expected * 1.6, 2)},
                    "cost_confidence": "ESTIMATE",
                    "estimate_assumptions": ["Cloud API cost only; local compute is not priced."],
                }

            estimate_fixture = {
                "status": "ESTIMATE", "source_snapshot_sha256": "b" * 64,
                "recommended_strategy": "cost_optimized", "selected_strategy": None,
                "budget": None, "escalation": "automatic", "budget_behavior": "stop_before_exceeding",
                "plans": {
                    "cost_optimized": strategy_plan("cost_optimized", "Cost Optimized — Recommended", 7, 2, 1, 4.25),
                    "balanced": strategy_plan("balanced", "Balanced", 3, 4, 3, 12.50),
                    "maximum_capability": strategy_plan("maximum_capability", "Maximum Capability", 0, 1, 9, 31.75),
                },
            }
            page.route("**/api/execution/estimate", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(estimate_fixture)
            ))
            page.get_by_role("button", name="Estimate implementation").click()
            expect(page.get_by_text("Cost Optimized — Recommended", exact=True)).to_be_visible()
            expect(page.get_by_text("Balanced", exact=True)).to_be_visible()
            expect(page.get_by_text("Maximum Capability", exact=True)).to_be_visible()
            expect(page.get_by_text("$4.25", exact=True)).to_be_visible()
            expect(page.get_by_text("Estimated model mix", exact=True).first).to_be_visible()
            page.unroute("**/api/execution/estimate")

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
