from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile DSpec DSPy modules with MIPROv2 after a reviewed training set exists.")
    parser.add_argument("--trainset", required=True, help="Reviewed JSONL examples")
    parser.add_argument("--output", required=True, help="Compiled artifact path")
    parser.add_argument("--model", required=True, help="DSPy LM model identifier")
    args = parser.parse_args()
    train = Path(args.trainset)
    if not train.exists():
        raise SystemExit("Training set does not exist")
    try:
        import dspy
        from dspy.teleprompt import MIPROv2
    except Exception as exc:
        raise SystemExit("Install AI extras first: pip install 'dspy>=3,<4'") from exc
    examples = []
    for line in train.read_text(encoding="utf-8").splitlines():
        if line.strip():
            examples.append(json.loads(line))
    if not examples:
        raise SystemExit("Training set is empty")
    raise SystemExit("Training data loaded. Module-specific metric wiring remains BLOCKED until the reviewed dataset schema is approved.")

if __name__ == "__main__":
    main()
