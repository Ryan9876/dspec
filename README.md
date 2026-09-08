# DSpec AI 0.1.0 Prototype Candidate

DSpec is a local, spec-first workflow for turning product intent into four governed specification tiers and exporting them for coding agents.

## State

This repository is an implementation candidate for `DS-CHG-001`. It is not a production release and does not claim MIPROv2 optimization or live-provider validation.

## Build and run

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
npm install
npm run build
python scripts/dspec_runner.py start
```

Open `http://127.0.0.1:3210`.

Stop it explicitly:

```bash
python scripts/dspec_runner.py stop
```

The service binds only to `127.0.0.1:3210`.

## Validation

```bash
pytest
npm run typecheck
npm run build
```

## macOS manual controls

On the target Mac, after installing the runtime package:

```bash
scripts/build_macos_launchers.sh
```

This creates `Start DSpec.app`, `Stop DSpec.app`, and `DSpec Status.app`. No boot daemon is installed.

## Known prototype limits

- Live LM Studio, Ollama, OpenAI, and Anthropic generation requires user-selected provider/model configuration and has not been verified by repository tests.
- MIPROv2 optimization is intentionally blocked until reviewed training/evaluation data and authorized model execution exist. `scripts/optimize_mipro.py` provides the real candidate compilation path.
- Repository audit scores are evidence-based static heuristics, not a security certification or dependency vulnerability scan.
- macOS Keychain, `.app` launchers, and menu bar integration require validation on macOS.
