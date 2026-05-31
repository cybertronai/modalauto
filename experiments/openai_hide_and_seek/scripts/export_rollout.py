#!/usr/bin/env python3
"""Export a headless MuJoCo hide-and-seek policy rollout to JSON.

This intentionally avoids MjViewer. The native viewer is the unstable part on
some macOS/x86-emulated setups; the simulator and saved .npz policies can still
be used as the source of truth for physics and behavior.
"""

import argparse
import json
import os
import sys
from os.path import abspath, dirname, join

import numpy as np
from gym.spaces import Tuple

sys.path.insert(0, abspath(join(dirname(__file__), "..")))

from ma_policy.load_policy import load_policy
from mujoco_worldgen.util.envs import load_env
from mae_envs.wrappers.multi_agent import JoinMultiAgentActions


def quat_to_yaw(q):
    # MuJoCo quaternions are w, x, y, z.
    w, x, y, z = q
    return float(np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))


def body_record(sim, body_id):
    return {
        "name": sim.model.body_id2name(body_id),
        "pos": np.asarray(sim.data.body_xpos[body_id], dtype=float).round(5).tolist(),
        "yaw": round(quat_to_yaw(sim.data.body_xquat[body_id]), 5),
    }


def geom_record(sim, geom_id):
    return {
        "name": sim.model.geom_id2name(geom_id),
        "type": int(sim.model.geom_type[geom_id]),
        "pos": np.asarray(sim.data.geom_xpos[geom_id], dtype=float).round(5).tolist(),
        "size": np.asarray(sim.model.geom_size[geom_id], dtype=float).round(5).tolist(),
        "rgba": np.asarray(sim.model.geom_rgba[geom_id], dtype=float).round(5).tolist(),
    }


def visible_body_ids(sim, prefix):
    ids = []
    for i, name in enumerate(sim.model.body_names):
        if name and name.startswith(prefix):
            ids.append(i)
    return ids


def visible_geom_ids(sim, prefix):
    ids = []
    for i, name in enumerate(sim.model.geom_names):
        if name and name.startswith(prefix):
            ids.append(i)
    return ids


def summarize_obs(obs):
    summary = {}
    for key, value in obs.items():
        arr = np.asarray(value)
        if arr.dtype.kind in "biufc":
            summary[key] = {
                "shape": list(arr.shape),
                "mean": float(np.mean(arr)) if arr.size else 0.0,
                "min": float(np.min(arr)) if arr.size else 0.0,
                "max": float(np.max(arr)) if arr.size else 0.0,
            }
    return summary


def jsonable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def model_record(env):
    sim = env.unwrapped.sim
    geoms = []
    for geom_id, name in enumerate(sim.model.geom_names):
        if not name:
            continue
        geoms.append(geom_record(sim, geom_id))

    return {
        "metadata": jsonable(dict(env.unwrapped.metadata)),
        "geoms": geoms,
    }


def frame_from_env(env, obs, step_idx, reward=None, done=False):
    sim = env.unwrapped.sim
    agent_body_ids = visible_body_ids(sim, "agent")
    box_body_ids = visible_body_ids(sim, "moveable_box")
    ramp_body_ids = visible_body_ids(sim, "ramp")
    floor_geom_ids = visible_geom_ids(sim, "floor")

    metadata = env.unwrapped.metadata
    n_hiders = int(metadata.get("n_hiders", 0))
    n_seekers = int(metadata.get("n_seekers", 0))
    visibility = np.asarray(obs.get("mask_aa_obs", np.zeros((n_hiders + n_seekers, n_hiders + n_seekers))), dtype=bool)
    seeker_hider_visibility = visibility[n_hiders:n_hiders + n_seekers, :n_hiders]
    hiders_seen = np.any(seeker_hider_visibility, axis=0) if seeker_hider_visibility.size else np.zeros((n_hiders,), dtype=bool)
    seekers_seeing = np.any(seeker_hider_visibility, axis=1) if seeker_hider_visibility.size else np.zeros((n_seekers,), dtype=bool)
    prep_obs = np.asarray(obs.get("prep_obs", np.zeros((n_hiders + n_seekers, 1))), dtype=float)

    return {
        "step": step_idx,
        "reward": None if reward is None else np.asarray(reward, dtype=float).round(5).tolist(),
        "done": bool(done),
        "prep": bool(prep_obs.size and prep_obs.reshape(-1)[0] < 0.5),
        "visibility": {
            "agent_agent": visibility.astype(int).tolist(),
            "seeker_hider": seeker_hider_visibility.astype(int).tolist(),
            "hiders_seen": hiders_seen.astype(int).tolist(),
            "seekers_seeing": seekers_seeing.astype(int).tolist(),
            "any_hider_seen": bool(np.any(hiders_seen)),
        },
        "agents": [
            dict(
                body_record(sim, body_id),
                team=("hider" if idx < n_hiders else "seeker"),
                seen=bool(hiders_seen[idx]) if idx < n_hiders else False,
                seeing_hider=bool(seekers_seeing[idx - n_hiders]) if idx >= n_hiders and idx - n_hiders < len(seekers_seeing) else False,
            )
            for idx, body_id in enumerate(agent_body_ids[:n_hiders + n_seekers])
        ],
        "boxes": [body_record(sim, body_id) for body_id in box_body_ids],
        "ramps": [body_record(sim, body_id) for body_id in ramp_body_ids],
        "floor_geoms": [geom_record(sim, geom_id) for geom_id in floor_geom_ids[:8]],
        "obs_summary": summarize_obs(obs),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("env_jsonnet", default="examples/hide_and_seek_quadrant.jsonnet")
    parser.add_argument("policy_npz", default="examples/hide_and_seek_quadrant.npz")
    parser.add_argument("--steps", type=int, default=120)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="rollout.json")
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
    obs = env.reset()
    policy = load_policy(args.policy_npz, env=env, scope="policy_0")
    policy.reset()

    model = model_record(env)
    frames = [frame_from_env(env, obs, 0)]
    total_reward = None
    for step in range(1, args.steps + 1):
        action, info = policy.act(obs)
        obs, reward, done, env_info = env.step(action)
        reward_arr = np.asarray(reward, dtype=float)
        total_reward = reward_arr if total_reward is None else total_reward + reward_arr
        frames.append(frame_from_env(env, obs, step, reward=reward_arr, done=done))
        if done or env_info.get("discard_episode", False):
            break

    payload = {
        "source": {
            "env": args.env_jsonnet,
            "policy": args.policy_npz,
            "seed": args.seed,
            "steps_requested": args.steps,
        },
        "total_reward": None if total_reward is None else total_reward.round(5).tolist(),
        "model": model,
        "frames": frames,
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(payload, f)
    print("wrote {} frames to {}".format(len(frames), args.out))


if __name__ == "__main__":
    main()
