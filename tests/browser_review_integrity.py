"""Rendered review/approval/export with real APIs and deterministic DSPy inference."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from browser_smoke import ROOT, wait_health
from test_app import DRAFTS


def main():
    evidence = ROOT / "evidence"
    evidence.mkdir(exist_ok=True)
    env = {**os.environ, "DSPEC_HOME": tempfile.mkdtemp(prefix="dspec-review-browser-"), "PYTHONPATH": str(ROOT), "DSPEC_DISABLE_TRAY": "1"}
    with tempfile.TemporaryFile(mode="w+") as log:
        process = subprocess.Popen([sys.executable, "tests/review_fixture_server.py"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_health()
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1512, "height": 982})
                errors, external = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: external.append(request.url) if urlsplit(request.url).scheme in {"http", "https", "ws", "wss"} and urlsplit(request.url).hostname not in {"127.0.0.1", "localhost", "::1"} else None)
                base = "http://127.0.0.1:3210"
                api = page.request
                sid = api.post(base + "/api/sessions", data={"bundle_name": "review-integrity"}).json()["id"]
                for stage, content in DRAFTS.items():
                    assert api.post(base + "/api/spec/save", data={"session_id": sid, "stage": stage, "content": content}).ok
                    assert api.post(base + "/api/spec/review", data={"session_id": sid, "stage": stage}).json()["passed"]
                    assert api.post(base + "/api/spec/approve", data={"session_id": sid, "stage": stage}).ok
                assert api.get(base + f"/api/export/{sid}").ok
                page.goto(base)
                expect(page.get_by_role("button", name="review-integrity", exact=True)).to_be_visible()
                page.get_by_role("button", name="Requirements").click()
                expect(page.locator(".monaco-editor")).to_be_visible()
                expect(page.get_by_text("4/4", exact=True)).to_be_visible()
                expect(page.get_by_text("Approved revision", exact=True)).to_be_visible()
                page.get_by_role("button", name="Apply recommendation", exact=True).first.click()
                expect(page.get_by_text("Draft buffer has changes", exact=False)).to_be_visible()
                expect(page.get_by_text("1/4", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="Approve", exact=True)).to_be_disabled()
                state = api.get(base + f"/api/sessions/{sid}").json()
                assert "Retry recovery" in state["drafts"]["requirements"]["content"]
                assert state["specs"]["requirements"]["revision_number"] == 1
                assert api.get(base + f"/api/export/{sid}").status == 409
                assert api.get(base + f"/api/export/{sid}?allow_draft=true").ok
                page.get_by_role("button", name="Review", exact=True).click()
                expect(page.get_by_text("Semantic review: PASS", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="Approve", exact=True)).to_be_enabled()
                page.get_by_role("button", name="Approve", exact=True).click()
                expect(page.get_by_text("2/4", exact=True)).to_be_visible()
                expect(page.get_by_text("Approved revision", exact=True)).to_be_visible()
                page.get_by_role("button", name="Tasks").click()
                expect(page.get_by_text("Semantic review: STALE", exact=False)).to_be_visible()
                expect(page.get_by_role("button", name="Approve", exact=True)).to_be_disabled()
                page.reload()
                page.get_by_role("button", name="Tasks").click()
                expect(page.get_by_text("Semantic review: STALE", exact=False)).to_be_visible()
                page.screenshot(path=str(evidence / "review-integrity.png"), full_page=True)
                assert errors == [], errors
                assert external == [], external
                print("BROWSER_REVIEW_INTEGRITY", json.dumps({"status": "PASS", "model_execution": "deterministic DSPy DummyLM only", "unexpected_external_requests": external, "page_errors": errors}), flush=True)
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            log.seek(0)
            print(log.read()[-8000:], flush=True)


if __name__ == "__main__":
    main()
