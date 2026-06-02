#!/usr/bin/env python3
"""Autoresearch runner for small MJWarp hide-and-seek training trials."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import modal


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


def bounded_float(value, default: float, lo: float, hi: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        out = default
    return max(lo, min(hi, out))


def run_deployed_modal(
    function_name: str,
    env_jsonnet: str,
    seed: int,
    worlds: int,
    updates: int,
    horizon: int,
    lr: float,
    entropy_coef: float,
    hidden: int,
    prep_fraction: float,
    summary_path: Path,
    checkpoint_path: Path,
    rollout_path: Path,
) -> None:
    fn = modal.Function.from_name("openai-hide-and-seek-mjwarp", function_name)
    payload = fn.remote(env_jsonnet, seed, worlds, updates, horizon, lr, entropy_coef, hidden, prep_fraction)
    summary_path.write_text(json.dumps(json.loads(payload["result"]), indent=2))
    checkpoint_path.write_bytes(base64.b64decode(payload["checkpoint_bytes_b64"]))
    if payload.get("rollout"):
        rollout_path.write_text(json.dumps(json.loads(payload["rollout"]), indent=2))


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
    impl = hyp.get("implementation") if isinstance(hyp.get("implementation"), dict) else hyp
    worlds = bounded_int(hyp.get("worlds", impl.get("worlds")), 64, 8, 512)
    updates = bounded_int(hyp.get("updates", impl.get("updates")), 4, 1, 32)
    horizon = bounded_int(hyp.get("horizon", impl.get("horizon")), 32, 4, 128)
    seed = bounded_int(hyp.get("seed", impl.get("seed")), 0, 0, 1_000_000)
    lr = bounded_float(hyp.get("lr", impl.get("lr")), 3e-4, 1e-5, 5e-3)
    entropy_coef = bounded_float(hyp.get("entropy_coef", impl.get("entropy_coef")), 0.01, 0.0, 0.08)
    hidden = bounded_int(hyp.get("hidden", impl.get("hidden")), 64, 16, 128)
    prep_fraction = bounded_float(hyp.get("prep_fraction", impl.get("prep_fraction")), 0.4, 0.1, 0.8)
    env_jsonnet = str(hyp.get("env_jsonnet") or impl.get("env_jsonnet") or "examples/hide_and_seek_quadrant.jsonnet")
    modal_function = str(hyp.get("modal_function") or impl.get("modal_function") or hyp.get("gpu_function") or impl.get("gpu_function") or "train_smoke_mjwarp_h100_v3")

    artifact_dir = args.journal_root / "artifacts" / args.run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    result_rel = artifact_dir.relative_to(experiment_root) / "summary.json"
    ckpt_rel = artifact_dir.relative_to(experiment_root) / "policy.pt"

    try:
        run_deployed_modal(
            modal_function,
            env_jsonnet,
            seed,
            worlds,
            updates,
            horizon,
            lr,
            entropy_coef,
            hidden,
            prep_fraction,
            artifact_dir / "summary.json",
            artifact_dir / "policy.pt",
            artifact_dir / "rollout.json",
        )
    except Exception as exc:
        (artifact_dir / "modal.error.log").write_text(f"{type(exc).__name__}: {exc}\n")
        print(exc, file=sys.stderr)
        return 1

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
    first_hider = float(first.get("mean_hider_reward", 0.0) or 0.0)
    last_hider = float(raw.get("final_mean_hider_reward", last.get("mean_hider_reward", 0.0)) or 0.0)
    hider_improvement = last_hider - first_hider
    hider_seen_rate = float(raw.get("final_hider_seen_rate", last.get("hider_seen_rate", 1.0)) or 1.0)
    # Autoresearch sorts lower scores as better. Keep the paper-style
    # visibility PPO objective on its own scale so legacy smoke distance runs
    # cannot outrank actual hide-and-seek visibility rollouts.
    score = int(round(
        400_000
        + 200_000 * hider_seen_rate
        + 100_000 * caught_fraction
        - 20_000 * last_hider
        - 5_000 * hider_improvement
    ))
    summary = {
        "best": {
            "name": f"mjwarp_{worlds}w_{updates}u_{horizon}h_seed{seed}",
            "family": str(raw.get("objective") or "visibility_selfplay"),
            "score": score,
            "mean_visibility_selfplay_reward": last_reward,
            "mean_hider_reward": last_hider,
            "caught_fraction": caught_fraction,
            "hider_seen_rate": hider_seen_rate,
            "improvement": improvement,
            "hider_improvement": hider_improvement,
            "modal_function": modal_function,
            "rollout": str(artifact_dir / "rollout.json"),
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
