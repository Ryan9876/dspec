# DSpec AI — Local Working Prototype

DSpec AI is a local, spec-first workspace for turning product intent into a governed four-tier software specification and handoff bundle.

**Prototype version:** 0.1.0 candidate  
**Runtime:** `http://127.0.0.1:3210`  
**Lifecycle:** manual start/stop only

## Implemented in this candidate

- Four-stage Constitution → Requirements → Solution → Tasks workspace.
- Transactional SQLite persistence with WAL for projects, inputs, drafts, reviews, and settings.
- Deterministic quality gate plus optional semantic AI review.
- Structured DSPy signature registry for the four generation tiers; optimization remains explicitly blocked until a reviewed training set exists.
- Zero-reload provider selection for LM Studio, Ollama, OpenAI, and Anthropic.
- Local provider discovery and write-only cloud credential handling (macOS Keychain on Mac; protected `0600` development fallback elsewhere).
- Read-only bounded repository audit with SHA-256 cache and four evidence-based health dimensions.
- Approved or clearly labeled draft ZIP export with all four tiers plus Claude, Cursor, Codex, and ChatGPT handoff files.
- Manual runner with PID verification, loopback-only port `3210`, health polling, release-manifest checksum validation, backup-before-update, and refusal to kill unknown processes.
- Native macOS launcher installer for Start DSpec, Stop DSpec, and DSpec Status.

## Run from source

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m dspec.runner start
```

Open `http://localhost:3210`.

Stop it with:

```bash
python -m dspec.runner stop
```

## macOS install

From the extracted candidate folder:

```bash
./scripts/install-macos.sh
```

This copies the source to `~/.dspec/source`, creates a private virtual environment, and creates three launchers in `~/Applications`. It does **not** install a boot daemon or Login Item.

## AI providers

Local discovery probes:

- LM Studio: `http://127.0.0.1:1234/v1/models`
- Ollama: `http://127.0.0.1:11434/api/tags`

Cloud keys are entered only through the provider modal and are never returned in API responses. On macOS they are written to Keychain service entries under `dspec.ai.<provider>`.

## DSPy optimization state

The generation architecture includes formal structured signatures. The candidate is intentionally **UNOPTIMIZED**. No reviewed production spec dataset or authorized MIPROv2 compile evidence was supplied, so FR-2.3 remains `BLOCKED` rather than fabricated.

## Validation

Run:

```bash
PYTHONPATH=. pytest -q
```

Browser/runtime validation should be performed against the exact candidate being packaged. macOS launcher/Keychain behavior must be tested on an actual Mac before the change can be called fully validated or known-good.

## Important prototype limitations

- The checked-in runtime UI is a self-contained static client served by FastAPI. The active technical solution calls for a Next.js static export; restoring that build-time source/export pipeline remains an implementation gap for this reconstructed candidate and must be closed before the solution is fully satisfied.
- Semantic AI review and live generation depend on an available selected provider and were not validated against real model credentials in the reconstruction environment.
- Audit scores are bounded static evidence, not a security certification or dependency CVE scan.
- Discovery inputs and draft edits auto-save to SQLite; the explicit **Save draft** action remains available for immediate persistence. The UI also makes a best-effort keepalive save when the page is hidden or closed. Rendered-browser persistence still requires verification on an unrestricted browser.
