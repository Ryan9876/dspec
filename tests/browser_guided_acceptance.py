from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from dspec.architecture_compatibility import enrich_architecture_options

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:3210"
SCREENSHOT = Path(
    os.environ.get(
        "DSPEC_GUIDED_ACCEPTANCE_SCREENSHOT",
        ROOT / "evidence" / "guided-acceptance.png",
    )
)


def wait_health(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/api/health", timeout=1.0) as response:
                if json.loads(response.read().decode()).get("status") == "healthy":
                    return
        except Exception as exc:
            last = exc
        time.sleep(0.2)
    raise RuntimeError(f"DSpec did not become healthy: {last}")


def api_json(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        return exc.code, json.loads(raw) if raw else {}


def start_backend(home: Path) -> subprocess.Popen[str]:
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
    wait_health()
    return process


def stop_backend(process: subprocess.Popen[str]) -> str:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    if process.stdout is None:
        return ""
    return process.stdout.read()


def architecture_source(session: dict, customization: str = "") -> str:
    payload = {
        "constitution": session.get("specs", {}).get("constitution", {}).get("content"),
        "requirements": session.get("specs", {}).get("requirements", {}).get("content"),
        "customization": customization.strip(),
        "solution_answers": [
            {key: answer.get(key) for key in ("question_id", "selected_option_id", "free_text_payload")}
            for answer in session.get("answers", [])
            if answer.get("stage") == "solution"
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def architecture_fixture(source_sha: str, customization: str = "") -> dict:
    customized = "sql server" in customization.lower()
    database = "SQL Server" if customized else "PostgreSQL"
    summary = (
        "Aligns the data tier with the existing SQL Server operating environment while preserving the same application boundaries."
        if customized
        else "Room to grow without unnecessary operational complexity."
    )
    return {
        "recommended_option_id": "best",
        "recommendation_confidence": "high",
        "alternative_objective": "Enterprise alignment",
        "decision_summary": "Best Fit balances future growth with manageable operating complexity.",
        "assumptions_unknowns": ["Cloud hosting cost depends on the eventual deployment target."],
        "source_context_sha256": source_sha,
        "customization": customization,
        "options": [
            {
                "id": "best",
                "role": "best_fit",
                "title": "Best Fit — Recommended",
                "plain_english_summary": summary,
                "why_recommended": "The requirements need structured shared data and future expansion.",
                "advantages": ["Strong overall fit"],
                "tradeoffs": ["Moderate operating complexity"],
                "operational_impact": "A small number of well-understood services must be operated.",
                "cost_level_or_range": "Moderate; exact hosting cost is UNKNOWN until deployment is selected.",
                "scalability_flexibility": "Supports multi-user growth and later service separation without requiring it now.",
                "why_engineers_care": "Core platform choices get more expensive to change after integrations depend on them.",
                "engineering_concept": {
                    "id": "separation-of-concerns",
                    "label": "Separation of concerns",
                    "mental_model": "Give different responsibilities clear boundaries.",
                },
                "reconsider_when": ["The application becomes permanently single-user and local-only."],
                "technical_details": [
                    {"category": "Database", "choice": database, "consequence": "Runs a relational database service."}
                ],
                "compatibility": {
                    "status": "PASS",
                    "issues": [],
                    "warnings": [],
                    "checked_constraints": ["customization reflected" if customized else "requirements grounded"],
                },
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
                "cost_level_or_range": "Low relative operating complexity; provider cost remains UNKNOWN.",
                "scalability_flexibility": "Best for modest scale; fewer seams for later independent scaling.",
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
                "compatibility": {"status": "PASS", "issues": [], "warnings": [], "checked_constraints": []},
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
                "cost_level_or_range": "Potentially higher licensing/hosting cost; exact amount is UNKNOWN.",
                "scalability_flexibility": "Strong enterprise integration flexibility with more platform coupling.",
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
                "compatibility": {"status": "PASS", "issues": [], "warnings": [], "checked_constraints": []},
            },
        ],
    }


def strategy_plan(name: str, label: str, local: int, standard: int, advanced: int, expected: float) -> dict:
    total = max(local + standard + advanced, 1)
    return {
        "strategy": name,
        "strategy_label": label,
        "budget_status": "WITHIN_EXPECTED",
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


def estimate_bundle(payload: dict, selected: str | None = None) -> dict:
    return {
        "status": "SELECTED" if selected else "ESTIMATE",
        "source_snapshot_sha256": "b" * 64,
        "recommended_strategy": "cost_optimized",
        "selected_strategy": selected,
        "budget": payload.get("budget"),
        "escalation": payload.get("escalation", "automatic"),
        "budget_behavior": payload.get("budget_behavior", "stop_before_exceeding"),
        "plans": {
            "cost_optimized": strategy_plan("cost_optimized", "Cost Optimized — Recommended", 7, 2, 1, 5.50),
            "balanced": strategy_plan("balanced", "Balanced", 3, 4, 3, 14.00),
            "maximum_capability": strategy_plan("maximum_capability", "Maximum Capability", 0, 1, 9, 33.00),
        },
    }


def main() -> None:
    home = Path(tempfile.mkdtemp(prefix="dspec-guided-acceptance-"))
    process = start_backend(home)
    backend_logs: list[str] = []
    browser = None
    page = None
    failure: BaseException | None = None
    try:
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
            page.goto(BASE, wait_until="domcontentloaded")
            expect(page.get_by_text("DSpec AI", exact=True)).to_be_visible()

            status, created = api_json(
                "/api/sessions",
                "POST",
                {"bundle_name": "guided-acceptance", "project_type": "greenfield"},
            )
            assert status == 201, created
            sid = created["id"]
            status, _ = api_json(
                "/api/spec/save",
                "POST",
                {
                    "session_id": sid,
                    "stage": "requirements",
                    "content": "# Requirements\n\nREQ-001 shared structured data with reliable updates for a growing team.",
                },
            )
            assert status == 200
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_text("guided-acceptance", exact=True)).to_be_visible()
            page.get_by_role("button", name="Solution").click()

            def architecture_route(route, request) -> None:
                raw = request.post_data_json
                payload = raw() if callable(raw) else raw
                customization = str((payload or {}).get("customization") or "")
                session_status, current_session = api_json(f"/api/sessions/{sid}")
                assert session_status == 200, current_session
                raw_body = architecture_fixture(architecture_source(current_session, customization), customization)
                body = enrich_architecture_options(
                    raw_body,
                    constitution=str(current_session.get("specs", {}).get("constitution", {}).get("content") or ""),
                    requirements=str(current_session.get("specs", {}).get("requirements", {}).get("content") or ""),
                    customization=customization,
                )
                route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

            page.route("**/api/decisions/architecture/options", architecture_route)
            page.get_by_role("button", name="Compare options").click()
            for label in ("Best Fit — Recommended", "Simplest", "Enterprise Alignment"):
                expect(page.get_by_text(label, exact=True)).to_be_visible()
            expect(page.get_by_text("Cost / range", exact=True).first).to_be_visible()
            expect(page.get_by_text("Scalability / flexibility", exact=True).first).to_be_visible()
            expect(page.get_by_text("Technical details", exact=True).first).to_be_visible()
            expect(page.get_by_text("PostgreSQL", exact=True)).not_to_be_visible()
            details = page.get_by_text("Technical details", exact=True).first
            details.focus()
            details.press("Enter")
            expect(page.get_by_text("PostgreSQL", exact=True)).to_be_visible()

            page.get_by_role("button", name="Customize technology choices").click()
            customization = "Use SQL Server instead of PostgreSQL because the company already operates it."
            page.get_by_placeholder("Describe the change in plain English", exact=False).fill(customization)
            page.get_by_role("button", name="Re-evaluate options").click()
            expect(page.get_by_text("Re-evaluated with your constraint:", exact=False)).to_contain_text("SQL Server")
            expect(page.get_by_text("Aligns the data tier with the existing SQL Server", exact=False)).to_be_visible()
            custom_best = page.locator("article").filter(has_text="Best Fit — Recommended")
            custom_details = custom_best.locator("details").first
            expect(custom_details).to_contain_text("SQL Server")

            page.set_viewport_size({"width": 700, "height": 900})
            for label in ("Best Fit — Recommended", "Simplest", "Enterprise Alignment"):
                expect(page.get_by_text(label, exact=True)).to_be_visible()
            page.set_viewport_size({"width": 1512, "height": 982})

            best_card = page.locator("article").filter(has_text="Best Fit — Recommended")
            best_card.get_by_role("button", name="Choose this approach").click()
            expect(best_card.get_by_role("button", name="Selected")).to_be_visible()

            backend_logs.append(stop_backend(process))
            process = start_backend(home)
            page.reload(wait_until="domcontentloaded")
            page.get_by_role("button", name="Solution").click()
            persisted_best = page.locator("article").filter(has_text="Best Fit — Recommended")
            expect(persisted_best.get_by_role("button", name="Selected")).to_be_visible()

            page.get_by_role("button", name="Refresh comparison").click()
            expect(page.get_by_text("Best Fit — Recommended", exact=True)).to_be_visible()
            status, _ = api_json(
                "/api/spec/save",
                "POST",
                {
                    "session_id": sid,
                    "stage": "requirements",
                    "content": "# Requirements\n\nREQ-001 changed authoritative requirement for a remote multi-user service.",
                },
            )
            assert status == 200
            simple_card = page.locator("article").filter(has_text="Simplest")
            simple_card.get_by_role("button", name="Choose this approach").click()
            expect(page.get_by_text("architecture_options_stale", exact=False)).to_be_visible()

            for stage, content in (
                ("solution", "# Solution\n\nSOL-001 governed implementation approach."),
                ("tasks", "# Tasks\n\nT-001 implement governed change. Maps to REQ-001. Verification: pytest -q"),
            ):
                status, response = api_json(
                    "/api/spec/save",
                    "POST",
                    {"session_id": sid, "stage": stage, "content": content},
                )
                assert status == 200, response
            page.reload(wait_until="domcontentloaded")
            page.get_by_role("button", name="Tasks").click()
            expect(page.get_by_text("Implementation strategy & cloud budget", exact=True)).to_be_visible()

            captured_estimates: list[dict] = []
            fail_estimate = {"value": False}

            def estimate_route(route, request) -> None:
                raw = request.post_data_json
                payload = raw() if callable(raw) else raw
                captured_estimates.append(payload or {})
                if fail_estimate["value"]:
                    route.fulfill(
                        status=503,
                        content_type="application/json",
                        body=json.dumps({"detail": {"error": "execution_estimate_unavailable", "state_preserved": True}}),
                    )
                    return
                route.fulfill(status=200, content_type="application/json", body=json.dumps(estimate_bundle(payload or {})))

            def select_route(route, request) -> None:
                raw = request.post_data_json
                payload = raw() if callable(raw) else raw
                chosen = str((payload or {}).get("strategy") or "")
                controls = captured_estimates[-1] if captured_estimates else {}
                route.fulfill(status=200, content_type="application/json", body=json.dumps(estimate_bundle(controls, chosen)))

            page.route("**/api/execution/estimate", estimate_route)
            page.route("**/api/execution/select", select_route)

            budget = page.locator("select").filter(has=page.locator("option", has_text="$25")).first
            budget.select_option("25")
            page.get_by_role("radio", name="Ask me first").check()
            page.get_by_role("button", name="Estimate implementation").click()
            assert captured_estimates, "estimate request was not captured"
            assert captured_estimates[-1]["budget"] == 25
            assert captured_estimates[-1]["escalation"] == "ask_first"
            assert captured_estimates[-1]["budget_behavior"] == "stop_before_exceeding"

            for amount in ("$5.50", "$14.00", "$33.00"):
                expect(page.get_by_text(amount, exact=True)).to_be_visible()
            expect(page.get_by_text("70%", exact=True)).to_be_visible()
            expect(page.get_by_text("90%", exact=True)).to_be_visible()

            balanced = page.locator("article").filter(has_text="Balanced")
            balanced.get_by_role("button", name="Use this strategy").click()
            expect(balanced.get_by_role("button", name="Selected")).to_be_visible()

            page.set_viewport_size({"width": 700, "height": 900})
            for label in ("Cost Optimized — Recommended", "Balanced", "Maximum Capability"):
                expect(page.get_by_text(label, exact=True)).to_be_visible()
            page.set_viewport_size({"width": 1512, "height": 982})

            fail_estimate["value"] = True
            budget.select_option("50")
            page.get_by_role("button", name="Recalculate").click()
            expect(page.get_by_text("execution_estimate_unavailable", exact=False)).to_be_visible()
            expect(balanced.get_by_role("button", name="Selected")).to_be_visible()
            expect(page.get_by_text("$14.00", exact=True)).to_be_visible()

            SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SCREENSHOT), full_page=True)
            if page_errors:
                raise AssertionError(f"Browser page errors: {page_errors}")
            if external_requests:
                raise AssertionError(f"Unexpected browser network egress: {external_requests}")
            print(
                "GUIDED_ACCEPTANCE_BROWSER_PASS",
                json.dumps(
                    {
                        "persistence_restart": True,
                        "stale_architecture_rejected": True,
                        "budget_payload": captured_estimates[0],
                        "failure_state_preserved": True,
                        "unexpected_external_requests": external_requests,
                    }
                ),
                flush=True,
            )
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
        if process.poll() is None:
            backend_logs.append(stop_backend(process))
        combined = "\n".join(log for log in backend_logs if log)
        if combined:
            print("BACKEND_LOG_BEGIN", flush=True)
            print(combined[-16000:], flush=True)
            print("BACKEND_LOG_END", flush=True)
        if failure is not None:
            print("GUIDED_ACCEPTANCE_BROWSER_FAILURE", repr(failure), flush=True)


if __name__ == "__main__":
    main()
