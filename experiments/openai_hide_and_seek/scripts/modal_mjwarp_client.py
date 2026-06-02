#!/usr/bin/env python3
"""Invoke the deployed MJWarp Modal trainer without rebuilding the app."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import modal


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-name", default="openai-hide-and-seek-mjwarp")
    parser.add_argument("--function", default="train_smoke_mjwarp")
    parser.add_argument("--env-jsonnet", default="examples/hide_and_seek_quadrant.jsonnet")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--worlds", type=int, default=32)
    parser.add_argument("--updates", type=int, default=4)
    parser.add_argument("--horizon", type=int, default=32)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--entropy-coef", type=float, default=0.01)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--prep-fraction", type=float, default=0.4)
    parser.add_argument("--out", default="visualization/public/rollouts/mjwarp_ppo_train.json")
    parser.add_argument("--checkpoint", default="visualization/public/rollouts/mjwarp_ppo_policy.pt")
    parser.add_argument("--rollout", default="visualization/public/rollouts/mjwarp_ppo_rollout.json")
    args = parser.parse_args()

    fn = modal.Function.from_name(args.app_name, args.function)
    payload = fn.remote(
        args.env_jsonnet,
        args.seed,
        args.worlds,
        args.updates,
        args.horizon,
        args.lr,
        args.entropy_coef,
        args.hidden,
        args.prep_fraction,
    )
    parsed = json.loads(payload["result"])

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(parsed, indent=2))

    ckpt_path = ROOT / args.checkpoint
    ckpt_path.parent.mkdir(parents=True, exist_ok=True)
    ckpt_path.write_bytes(base64.b64decode(payload["checkpoint_bytes_b64"]))
    if payload.get("rollout"):
        rollout_path = ROOT / args.rollout
        rollout_path.parent.mkdir(parents=True, exist_ok=True)
        rollout_path.write_text(json.dumps(json.loads(payload["rollout"]), indent=2))
    print(f"wrote deployed MJWarp train result to {out_path}")


if __name__ == "__main__":
    main()
