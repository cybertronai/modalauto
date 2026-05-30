#!/usr/bin/env python3
"""Export a headless MuJoCo rollout with sampled actions.

This is a simulator smoke test that avoids TensorFlow policy loading. It proves
whether the MuJoCo environment can step physics locally without MjViewer.
"""

import argparse
import json
import os
import sys
from os.path import abspath, dirname, join

import numpy as np
from gym.spaces import Tuple

sys.path.insert(0, abspath(join(dirname(__file__), "..")))

from mujoco_worldgen.util.envs import load_env
from mae_envs.wrappers.multi_agent import JoinMultiAgentActions


def quat_to_yaw(q):
    w, x, y, z = q
    return float(np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))


def body_record(sim, body_id):
    return {
        "name": sim.model.body_id2name(body_id),
        "pos": np.asarray(sim.data.body_xpos[body_id], dtype=float).round(5).tolist(),
        "yaw": round(quat_to_yaw(sim.data.body_xquat[body_id]), 5),
    }


def ids_with_prefix(names, prefix):
    return [i for i, name in enumerate(names) if name and name.startswith(prefix)]


def frame(env, step, reward=None, done=False):
    sim = env.unwrapped.sim
    metadata = env.unwrapped.metadata
    n_hiders = int(metadata.get("n_hiders", 0))
    n_seekers = int(metadata.get("n_seekers", 0))
    agent_ids = ids_with_prefix(sim.model.body_names, "agent")[:n_hiders + n_seekers]
    return {
        "step": step,
        "reward": None if reward is None else np.asarray(reward, dtype=float).round(4).tolist(),
        "done": bool(done),
        "agents": [
            dict(body_record(sim, body_id), team=("hider" if idx < n_hiders else "seeker"))
            for idx, body_id in enumerate(agent_ids)
        ],
        "boxes": [body_record(sim, i) for i in ids_with_prefix(sim.model.body_names, "moveable_box")],
        "ramps": [body_record(sim, i) for i in ids_with_prefix(sim.model.body_names, "ramp")],
    }


def zero_like_action(action_space):
    action = action_space.sample()
    for k, v in action.items():
        arr = np.asarray(v)
        if arr.dtype.kind in "iu":
            # DiscretizeActionWrapper midpoint is usually no-op.
            action[k] = np.zeros_like(arr)
            if "movement" in k:
                action[k][...] = 4
        else:
            action[k] = np.zeros_like(arr, dtype=arr.dtype)
    return action


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("env_jsonnet")
    parser.add_argument("--steps", type=int, default=60)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="random_rollout.json")
    parser.add_argument("--random", action="store_true")
    args = parser.parse_args()

    core_dir = abspath(join(dirname(__file__), ".."))
    env, _ = load_env(
        args.env_jsonnet,
        core_dir=core_dir,
        envs_dir=("mae_envs/envs",),
        xmls_dir="xmls",
        return_args_remaining=True,
    )
    if isinstance(env.action_space, Tuple):
        env = JoinMultiAgentActions(env)

    env.seed(args.seed)
    env.reset()
    frames = [frame(env, 0)]
    for step in range(1, args.steps + 1):
        action = env.action_space.sample() if args.random else zero_like_action(env.action_space)
        _, rew, done, info = env.step(action)
        frames.append(frame(env, step, rew, done))
        if done or info.get("discard_episode", False):
            break

    payload = {"source": {"env": args.env_jsonnet, "seed": args.seed, "random": args.random}, "frames": frames}
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(payload, f)
    print("wrote {} frames to {}".format(len(frames), args.out))


if __name__ == "__main__":
    main()
