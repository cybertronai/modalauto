#!/usr/bin/env python3
"""Verify that an IR implements general 16x16 matmul, not one fixed case."""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autoresearch.experiments.matmul.matmul import matmul  # noqa: E402


N = 16


def direct_matmul(inputs: list[int]) -> list[int]:
    a = [inputs[i * N:(i + 1) * N] for i in range(N)]
    off = N * N
    b = [inputs[off + i * N:off + (i + 1) * N] for i in range(N)]
    return [
        sum(a[i][k] * b[k][j] for k in range(N))
        for i in range(N)
        for j in range(N)
    ]


def random_inputs(rng: random.Random, low: int, high: int) -> list[int]:
    return [rng.randint(low, high) for _ in range(2 * N * N)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ir", type=Path)
    parser.add_argument("--cases", type=int, default=16)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--low", type=int, default=-7)
    parser.add_argument("--high", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    ir = args.ir.read_text()
    rng = random.Random(args.seed)
    result = {
        "ir": str(args.ir),
        "cases": args.cases,
        "seed": args.seed,
        "semantic": "ok",
        "official_score_16x16": None,
        "error": "",
    }

    for case in range(args.cases):
        inputs = random_inputs(rng, args.low, args.high)
        expected = direct_matmul(inputs)
        try:
            actual, _ = matmul._simulate(ir, inputs)
        except Exception as exc:  # noqa: BLE001
            result["semantic"] = "invalid"
            result["error"] = f"simulate_error_case_{case}: {exc}"
            break
        if actual != expected:
            result["semantic"] = "invalid"
            result["error"] = f"wrong_output_case_{case}"
            break

    if result["semantic"] == "ok":
        try:
            result["official_score_16x16"] = matmul.score_16x16(ir)
        except Exception as exc:  # noqa: BLE001
            result["semantic"] = "invalid"
            result["error"] = f"score_16x16_error: {exc}"

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        if result["semantic"] == "ok":
            print(
                f"ok cases={result['cases']} "
                f"score_16x16={result['official_score_16x16']}"
            )
        else:
            print(f"invalid {result['error']}")

    return 0 if result["semantic"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
