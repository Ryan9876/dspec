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

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT = Path(
    os.environ.get(
        "DSPEC_GUIDED_BROWSER_SCREENSHOT",
        ROOT / "evidence" / "guided-decisions.png",
    )
)


def wait_health(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:3210/api/health", timeout=1.0) as response:
                if json.loads(response.read().decode()).get("status") == "healthy":
                    return
        except Exception as exc:
            last = exc
        time.sleep(0.2)
    raise RuntimeError(f"DSpec did not become healthy: {last}")


def strategy_plan(name: str, label: str, local: int, standard: int, advanced: int, expected: float) -> dict:
    total = max(local + standard + advanced, 1)
    return {
        "strategy": name,
        "strategy_label": label,
        "budget_status": "NO_LIMIT",
        "blocked_task_count": 0,
        "model_mix": {
            "local": {"tasks": local, "percent": round(local / total * 100, 1)},
            "standard": {"tasks": standard, "percent": round(standard / total * 100, 1)},
            "advanced": {"tasks": advanced, "percent": round(advanced / total * 100, 1)},
        },
        "cloud_api_cost_estimate": {
            "low": round(expected * 0.6, 2),
            "expected": expected,
            "high": round(expected * 1.6, 2),
        },
        "cost_confidence": "ESTIMATE",
        "estimate_assumptions": ["Cloud API cost only; local compute is not priced."],
    }


def main() -> None:
    home = Path(tempfile.mkdtemp(prefix="dspec-guided-browser-"))
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
    browser = None
    page = None
    failure: BaseException | None = None
    try:
        wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1512, "height": 982})
            page.set_default_timeout(15_000)
            page_errors: list[str] = []
            external_requests: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))

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
            expect(page.get_by_text("DSpec AI", exact=True)).to_be_visible()
            expect(page.get_by_text("v0.1.3", exact=True)).to_be_visible()

            fixture = page.evaluate(
                """async () => {
                    const headers = {'Content-Type': 'application/json'};
                    const created = await fetch('/api/sessions', {
                        method: 'POST', headers,
                        body: JSON.stringify({bundle_name: 'guided-decisions-e2e', project_type: 'greenfield'}),
                    });
                    const session = await created.json();
                    const save = async (stage, content) => {
                        const response = await fetch('/api/spec/save', {
                            method: 'POST', headers,
                            body: JSON.stringify({session_id: session.id, stage, content}),
                        });
                        return response.status;
                    };
                    return {
                        id: session.id,
                        requirements: await save('requirements', '# Requirements\\n\\nREQ-001 shared structured data with reliable updates.'),
                    };
                }"""
            )
            assert fixture["requirements"] == 200, fixture
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_text("guided-decisions-e2e", exact=True)).to_be_visible()

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
                        "id": "best",
                        "role": "best_fit",
                        "title": "Best Fit — Recommended",
                        "plain_english_summary": "Room to grow without unnecessary operational complexity.",
                        "why_recommended": "The requirements need structured shared data and future expansion.",
                        "advantages": ["Strong overall fit"],
                        "tradeoffs": ["Moderate operating complexity"],
                        "operational_impact": "A small number of well-understood services must be operated.",
                        "why_engineers_care": "Core platform choices get more expensive to change after integrations depend on them.",
                        "engineering_concept": {
                            "id": "separation-of-concerns",
                            "label": "Separation of concerns",
                            "mental_model": "Give different responsibilities clear boundaries.",
                        },
                        "reconsider_when": ["The application becomes permanently single-user and local-only."],
                        "technical_details": [
                            {"category": "Database", "choice": "PostgreSQL", "consequence": "Runs a relational database service."}
                        ],
                    },
                    {
                        "id": "simple",
                        "role": "simplest",
                        "title": "Simplest",
                        "plain_english_summary": "Keep the number of moving parts as low as practical.",
                        "why_recommended": "Choose it when operating simplicity dominates.",
                        "advantages": ["Fewer moving parts"],
                        "tradeoffs": ["Less specialization"],
                        "operational_impact": "Lower day-to-day operating burden.",
                        "why_engineers_care": "Every component creates another failure and maintenance surface.",
                        "engineering_concept": {
                            "id": "simplicity",
                            "label": "Simplicity",
                            "mental_model": "Do not add moving parts without a requirement.",
                        },
                        "reconsider_when": ["Scale or integration needs increase."],
                        "technical_details": [
                            {"category": "Backend", "choice": "TypeScript", "consequence": "Keeps one primary application language."}
                        ],
                    },
                    {
                        "id": "alt",
                        "role": "alternative",
                        "title": "Enterprise Alignment",
                        "plain_english_summary": "Align closely with an existing Microsoft operating environment.",
                        "why_recommended": "Choose it when existing enterprise support and skills dominate.",
                        "advantages": ["Environment fit"],
                        "tradeoffs": ["Heavier platform"],
                        "operational_impact": "Uses established enterprise tooling and support patterns.",
                        "why_engineers_care": "Organizational skills affect lifetime support cost.",
                        "engineering_concept": {
                            "id": "platform-alignment",
                            "label": "Platform alignment",
                            "mental_model": "Prefer the existing environment unless requirements justify divergence.",
                        },
                        "reconsider_when": ["The operating environment changes."],
                        "technical_details": [
                            {"category": "Backend", "choice": ".NET", "consequence": "Aligns with Microsoft tooling."}
                        ],
                    },
                ],
            }
            page.route(
                "**/api/decisions/architecture/options",
                lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(architecture_options)),
            )
            page.get_by_role("button", name="Compare options").click()
            expect(page.get_by_text("Best Fit — Recommended", exact=True)).to_be_visible()
            expect(page.get_by_text("Simplest", exact=True)).to_be_visible()
            expect(page.get_by_text("Enterprise Alignment", exact=True)).to_be_visible()
            expect(page.get_by_text("What this means for you", exact=True).first).to_be_visible()
            expect(page.get_by_text("Why engineers care", exact=True).first).to_be_visible()
            expect(page.get_by_text("PostgreSQL", exact=True)).not_to_be_visible()
            page.get_by_text("Technical details", exact=True).first.click()
            expect(page.get_by_text("PostgreSQL", exact=True)).to_be_visible()
            page.unroute("**/api/decisions/architecture/options")

            tier_status = page.evaluate(
                """async ({sessionId}) => {
                    const headers = {'Content-Type': 'application/json'};
                    const save = async (stage, content) => {
                        const response = await fetch('/api/spec/save', {
                            method: 'POST', headers,
                            body: JSON.stringify({session_id: sessionId, stage, content}),
                        });
                        return response.status;
                    };
                    return {
                        solution: await save('solution', '# Solution\\n\\nSOL-001 governed implementation approach.'),
                        tasks: await save('tasks', '# Tasks\\n\\nT-001 implement governed change. Maps to REQ-001. Verification: pytest -q'),
                    };
                }""",
                {"sessionId": fixture["id"]},
            )
            assert tier_status == {"solution": 200, "tasks": 200}, tier_status
            page.reload(wait_until="domcontentloaded")
            page.get_by_role("button", name="Tasks").click()
            expect(page.get_by_text("Implementation strategy & cloud budget", exact=True)).to_be_visible()
            expect(page.get_by_text("Automatically escalate — Recommended", exact=True)).to_be_visible()
            expect(page.get_by_text("Budget controls may pause paid work", exact=False)).to_be_visible()

            estimate_fixture = {
                "status": "ESTIMATE",
                "source_snapshot_sha256": "b" * 64,
                "recommended_strategy": "cost_optimized",
                "selected_strategy": None,
                "budget": None,
                "escalation": "automatic",
                "budget_behavior": "stop_before_exceeding",
                "plans": {
                    "cost_optimized": strategy_plan("cost_optimized", "Cost Optimized — Recommended", 7, 2, 1, 4.25),
                    "balanced": strategy_plan("balanced", "Balanced", 3, 4, 3, 12.50),
                    "maximum_capability": strategy_plan("maximum_capability", "Maximum Capability", 0, 1, 9, 31.75),
                },
            }
            page.route(
                "**/api/execution/estimate",
                lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(estimate_fixture)),
            )
            page.get_by_role("button", name="Estimate implementation").click()
            expect(page.get_by_text("Cost Optimized — Recommended", exact=True)).to_be_visible()
            expect(page.get_by_text("Balanced", exact=True)).to_be_visible()
            expect(page.get_by_text("Maximum Capability", exact=True)).to_be_visible()
            expect(page.get_by_text("$4.25", exact=True)).to_be_visible()
            expect(page.get_by_text("Estimated model mix", exact=True).first).to_be_visible()
            page.unroute("**/api/execution/estimate")

            SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SCREENSHOT), full_page=True)
            if page_errors:
                raise AssertionError(f"Browser page errors: {page_errors}")
            if external_requests:
                raise AssertionError(f"Unexpected browser network egress: {external_requests}")
            print("GUIDED_DECISIONS_BROWSER_PASS", json.dumps({"unexpected_external_requests": external_requests}), flush=True)
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
            print("GUIDED_DECISIONS_BROWSER_FAILURE", repr(failure), flush=True)


if __name__ == "__main__":
    main()
