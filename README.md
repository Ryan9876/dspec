# DSpec AI — Local Working Prototype

DSpec AI is a local, spec-first workspace that turns product intent into a governed four-tier software specification and downstream coding-agent handoff.

**Prototype:** 0.1.0 candidate  
**Runtime:** `http://127.0.0.1:3210`  
**Lifecycle:** explicit manual start/stop only  
**Change:** `DS-CHG-001-dspec-ai-prototype`

## Implemented

- Four-stage Constitution → Requirements → Solution → Tasks workspace with explicit review and approval state.
- Next.js App Router frontend exported to static assets and served by FastAPI on the same loopback-only port.
- Transactional SQLite persistence with WAL for projects, discovery answers, revisions, review results, and settings.
- Monaco specification editing with debounced local autosave plus explicit Save.
- Formal DSPy signatures for all four tiers and contextual MCQ gap discovery.
- `dspy.Refine` bounded self-correction using the deterministic DSpec quality metric and a 0.90 threshold.
- Provider switcher for LM Studio, Ollama, OpenAI, and Anthropic without backend restart.
- LM Studio and Ollama local model discovery.
- Write-only cloud credential entry; macOS Keychain on Mac and `0600` protected fallback outside macOS.
- Resumable SSE event contract with ring-buffer replay and 15-second heartbeat frames.
- Read-only bounded repository audit with SHA-256 cache and four evidence-based health dimensions.
- Approved or explicitly labeled draft ZIP export with all four tiers plus Claude, Cursor, Codex, and ChatGPT handoff files.
- Manual runner with PID verification, strict `127.0.0.1:3210` binding, health polling, checksum validation, backup-before-update, and refusal to terminate unknown processes.
- Runner refuses Google Drive update manifests unless `validation_state` is exactly `validated`.
- macOS Start/Stop/Status launcher installer and optional `rumps` menu-bar controller.
- CI for Python compile/tests and Next.js typecheck/static export.

## Run from source

Python 3.11+ and Node.js 20.9+ are required for a source build.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
./scripts/build-frontend.sh
python -m dspec.runner start
```

Open `http://localhost:3210`.

Stop it with:

```bash
python -m dspec.runner stop
```

Check status:

```bash
python -m dspec.runner status
```

## macOS launcher install

From a checked-out or extracted candidate:

```bash
chmod +x scripts/*.sh
./scripts/install-macos.sh
```

The installer places DSpec under `~/.dspec/source`, creates a private Python virtual environment, and creates:

- `~/Applications/Start DSpec.app`
- `~/Applications/Stop DSpec.app`
- `~/Applications/DSpec Status.app`

It does **not** create a Login Item, LaunchAgent, or boot daemon.

## Providers

Local discovery probes:

- LM Studio: `http://127.0.0.1:1234/v1/models`
- Ollama: `http://127.0.0.1:11434/api/tags`

Cloud provider keys are entered only through the local provider modal. Keys are never returned in API responses.

Generation is executed through DSPy under the currently selected provider. Changing provider/model changes subsequent DSPy calls without restarting DSpec.

## DSPy optimization state

The runtime generation path uses structured DSPy signatures and `dspy.Refine`.

It is intentionally **UNOPTIMIZED**. No reviewed production training set, MIPROv2 compiled artifact, or authorized optimization evaluation has been supplied. FR-2.3 therefore remains `BLOCKED`; the project does not fabricate optimization evidence.

## Build a candidate package

After the frontend build:

```bash
python scripts/build-package.py
```

This creates:

- `dist/dspec-build-v0.1.0.zip`
- `dist/candidate-manifest.json`

The generated manifest is intentionally marked `"validation_state": "candidate"`. The local runner will not auto-install it from the Google Drive `current` release location until durable release governance explicitly promotes the exact package to `validated`.

## Validation

```bash
python -m compileall -q dspec
pytest -q
cd frontend
npm run typecheck
npm run build
```

The repository CI executes these checks against committed source.

## Evidence boundaries

The following states must remain distinct:

- Automated Python/Next build success does not prove rendered browser behavior.
- Fixture/provider-contract tests do not prove live LM inference.
- A package checksum does not make the package validated or known-good.
- macOS launcher, Keychain, and menu-bar behavior require execution on an actual Mac.
- Repository audit scoring is bounded static evidence, not a security certification or dependency-CVE assessment.
- MIPROv2 remains blocked until reviewed data and authorized model execution exist.

No production deployment or known-good status should be inferred from this prototype candidate.
