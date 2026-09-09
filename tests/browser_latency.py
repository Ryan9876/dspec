from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from statistics import mean

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path(os.environ.get("DSPEC_BROWSER_LATENCY_REPORT", ROOT / "evidence" / "browser-latency.json"))
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


MEASURE_JS = r"""
async ({buttonName, targetText, targetVisible}) => {
  const visible = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none'
      && style.visibility !== 'hidden'
      && Number(style.opacity || '1') !== 0
      && rect.width > 0
      && rect.height > 0;
  };

  const button = [...document.querySelectorAll('button')].find((el) =>
    el.getAttribute('aria-label') === buttonName
    || (el.textContent || '').trim() === buttonName
  );
  if (!button) throw new Error('button not found: ' + buttonName);

  const targetMatches = () => [...document.querySelectorAll('body *')].some((el) =>
    (el.textContent || '').trim() === targetText && visible(el)
  );

  const initial = targetMatches();
  if (initial === targetVisible) {
    throw new Error(
      'target already in expected state before click: '
      + targetText + ' visible=' + String(targetVisible)
    );
  }

  const start = performance.now();
  button.click();

  return await new Promise((resolve, reject) => {
    const deadline = start + 1000;
    const check = () => {
      const now = performance.now();
      if (targetMatches() === targetVisible) {
        resolve(now - start);
        return;
      }
      if (now >= deadline) {
        reject(new Error(
          'timed out waiting for target state: '
          + targetText + ' visible=' + String(targetVisible)
        ));
        return;
      }
      requestAnimationFrame(check);
    };
    check();
  });
}
"""


def summarize(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)
    p95_index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * 0.95)))
    return {
        "count": len(samples),
        "mean_ms": round(mean(samples), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "max_ms": round(max(samples), 3),
        "limit_ms": LIMIT_MS,
    }


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
        "limit_ms": LIMIT_MS,
        "measurement_scope": (
            "Chromium in-page performance.now() from programmatic button click "
            "until the required rendered DOM state becomes visible/hidden."
        ),
    }

    try:
        wait_health()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1512, "height": 982})
            page.set_default_timeout(15_000)
            page.goto("http://127.0.0.1:3210", wait_until="domcontentloaded")
            expect(page.get_by_text("DSpec AI", exact=True)).to_be_visible(timeout=10_000)

            # Establish a normal active project before measuring workflow interactions.
            page.once("dialog", lambda dialog: dialog.accept("latency-e2e"))
            page.get_by_role("button", name="New project").click()
            expect(page.get_by_text("latency-e2e", exact=True)).to_be_visible(timeout=10_000)
            expect(page.get_by_text("Constitution draft", exact=True)).to_be_visible(timeout=10_000)

            stage_samples: list[float] = []
            stage_sequence = [
                ("Requirements", "Requirements draft"),
                ("Solution", "Solution draft"),
                ("Tasks", "Tasks draft"),
                ("Constitution", "Constitution draft"),
            ]
            for _ in range(5):
                for button_name, target_text in stage_sequence:
                    elapsed = float(page.evaluate(
                        MEASURE_JS,
                        {
                            "buttonName": button_name,
                            "targetText": target_text,
                            "targetVisible": True,
                        },
                    ))
                    stage_samples.append(elapsed)

            modal_samples: list[float] = []
            for _ in range(10):
                opened = float(page.evaluate(
                    MEASURE_JS,
                    {
                        "buttonName": "LLM provider switcher",
                        "targetText": "LLM provider",
                        "targetVisible": True,
                    },
                ))
                modal_samples.append(opened)

                closed = float(page.evaluate(
                    MEASURE_JS,
                    {
                        "buttonName": "Cancel",
                        "targetText": "LLM provider",
                        "targetVisible": False,
                    },
                ))
                modal_samples.append(closed)

            stage_summary = summarize(stage_samples)
            modal_summary = summarize(modal_samples)
            all_samples = stage_samples + modal_samples
            overall = summarize(all_samples)

            report.update({
                "status": "PASS",
                "stage_switch": {
                    **stage_summary,
                    "samples_ms": [round(x, 3) for x in stage_samples],
                },
                "provider_modal_toggle": {
                    **modal_summary,
                    "samples_ms": [round(x, 3) for x in modal_samples],
                },
                "overall": overall,
                "acceptance": "PASS" if overall["max_ms"] <= LIMIT_MS else "FAIL",
                "evidence_boundary": (
                    "This measures client-side rendered interaction latency in CI Chromium. "
                    "It does not establish live-LLM first-token latency, target-Mac GUI latency, "
                    "or performance under arbitrary end-user hardware/load."
                ),
            })

            if overall["max_ms"] > LIMIT_MS:
                report["status"] = "FAIL"
                raise AssertionError(
                    f"NFR-1.1 failed: observed max {overall['max_ms']}ms exceeds {LIMIT_MS}ms"
                )

            browser.close()
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("BROWSER_LATENCY_REPORT", json.dumps(report), flush=True)

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
