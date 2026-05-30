#!/usr/bin/env python3
"""Autoresearch runner for small MJWarp hide-and-seek training trials."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def bounded_int(value, default: int, lo: int, hi: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = default
    return max(lo, min(hi, out))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--hypothesis-json", type=Path, required=True)
    parser.add_argument("--journal-root", type=Path, required=True)
    parser.add_argument("--verify-cases", default=None)
    parser.add_argument("--verify-top", default=None)
    parser.add_argument("--avoid-candidates-json", default=None)
    parser.add_argument("--disable-meta-operator", action="store_true")
    args = parser.parse_args()

    experiment_root = args.experiment_root.resolve()
    hyp = load_json(args.hypothesis_json)
    worlds = bounded_int(hyp.get("worlds"), 64, 8, 512)
    updates = bounded_int(hyp.get("updates"), 4, 1, 32)
    horizon = bounded_int(hyp.get("horizon"), 32, 4, 128)
    seed = bounded_int(hyp.get("seed"), 0, 0, 1_000_000)
    env_jsonnet = str(hyp.get("env_jsonnet") or "examples/hide_and_seek_quadrant.jsonnet")

    artifact_dir = args.journal_root / "artifacts" / args.run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    result_rel = artifact_dir.relative_to(experiment_root) / "summary.json"
    ckpt_rel = artifact_dir.relative_to(experiment_root) / "policy.pt"

    cmd = [
        sys.executable,
        "-m",
        "modal",
        "run",
        str(experiment_root / "modal_mjwarp.py"),
        "--mode",
        "train",
        "--env-jsonnet",
        env_jsonnet,
        "--seed",
        str(seed),
        "--worlds",
        str(worlds),
        "--updates",
        str(updates),
        "--horizon",
        str(horizon),
        "--out",
        str(result_rel),
        "--checkpoint",
        str(ckpt_rel),
    ]
    proc = subprocess.run(cmd, cwd=str(experiment_root.parents[2]), text=True, capture_output=True, timeout=3600)
    (artifact_dir / "modal.stdout.log").write_text(proc.stdout)
    if proc.stderr:
        (artifact_dir / "modal.stderr.log").write_text(proc.stderr)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        return proc.returncode

    summary_path = artifact_dir / "summary.json"
    if not summary_path.exists():
        print(f"missing summary at {summary_path}", file=sys.stderr)
        return 2

    raw = load_json(summary_path)
    history = raw.get("history") if isinstance(raw.get("history"), list) else []
    first = history[0] if history else {}
    last = history[-1] if history else {}
    first_reward = float(
        first.get("mean_visibility_selfplay_reward", first.get("mean_hider_distance_reward", 0.0)) or 0.0
    )
    last_reward = float(
        last.get("mean_visibility_selfplay_reward", last.get("mean_hider_distance_reward", 0.0)) or 0.0
    )
    improvement = last_reward - first_reward
    caught_fraction = float(raw.get("final_caught_fraction", last.get("caught_fraction", 0.0)) or 0.0)
    # Autoresearch sorts lower scores as better. Use negative final reward with
    # a small improvement bonus so genuine learning ranks ahead of noise.
    score = int(round(1_000_000 - 10_000 * last_reward - 2_000 * improvement))
    summary = {
        "best": {
            "name": f"mjwarp_{worlds}w_{updates}u_{horizon}h_seed{seed}",
            "family": str(raw.get("objective") or "visibility_selfplay"),
            "score": score,
            "mean_visibility_selfplay_reward": last_reward,
            "caught_fraction": caught_fraction,
            "improvement": improvement,
        },
        "training": raw,
        "hypothesis": hyp,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    artifact_marker = artifact_dir / "best.ir"
    artifact_marker.write_text(json.dumps(summary["best"], sort_keys=True) + "\n")
    print(json.dumps({"artifact_dir": str(artifact_dir)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
