#!/usr/bin/env python3
"""Compile DSpec DSPy modules only after reviewed training/evaluation data is supplied."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import dspy
from backend.dspec_app.spec_engine import ArchitectureToSolution, configure_dspy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path, help="Reviewed JSONL examples with input fields and expected specification")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    configure_dspy()
    examples = []
    for line in args.dataset.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        examples.append(dspy.Example(**row).with_inputs("constitution", "requirements", "answers_json"))
    if not examples: raise SystemExit("Reviewed optimization dataset is empty.")
    program = dspy.ChainOfThought(ArchitectureToSolution)
    def metric(example, pred, trace=None):
        expected = str(example.specification).strip()
        actual = str(pred.specification).strip()
        return 1.0 if actual == expected else 0.0
    optimizer = dspy.MIPROv2(metric=metric, auto="light")
    compiled = optimizer.compile(program, trainset=examples)
    compiled.save(str(args.output))
    print(f"Saved candidate optimization artifact to {args.output}; promotion still requires evaluation evidence.")
    return 0
if __name__ == "__main__": raise SystemExit(main())
