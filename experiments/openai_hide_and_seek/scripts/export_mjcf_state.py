#!/usr/bin/env python3
"""Export a generated OpenAI hide-and-seek MJCF and initial simulator state."""

import argparse
import base64
import json
import os
import sys
from os.path import abspath, dirname, join

import numpy as np
from gym.spaces import Tuple

sys.path.insert(0, abspath(join(dirname(__file__), "..")))

from mae_envs.wrappers.multi_agent import JoinMultiAgentActions
from mujoco_worldgen.util.envs import load_env


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


def array_record(arr):
    arr = np.asarray(arr)
    return {
        "dtype": str(arr.dtype),
        "shape": list(arr.shape),
        "data_b64": base64.b64encode(arr.tobytes()).decode("ascii"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("env_jsonnet")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="mjcf_state.json")
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
    sim = env.unwrapped.sim

    payload = {
        "source": {"env": args.env_jsonnet, "seed": args.seed},
        "xml": sim.model.get_xml(),
        "metadata": jsonable(dict(env.unwrapped.metadata)),
        "model": {
            "nq": int(sim.model.nq),
            "nv": int(sim.model.nv),
            "nu": int(sim.model.nu),
            "na": int(sim.model.na),
            "nbody": int(sim.model.nbody),
            "ngeom": int(sim.model.ngeom),
            "neq": int(sim.model.neq),
            "jnt_type": np.asarray(sim.model.jnt_type, dtype=int).tolist(),
            "eq_type": np.asarray(sim.model.eq_type, dtype=int).tolist(),
            "body_names": list(sim.model.body_names),
            "geom_names": list(sim.model.geom_names),
        },
        "state": {
            "qpos": array_record(sim.data.qpos),
            "qvel": array_record(sim.data.qvel),
            "act": array_record(sim.data.act),
            "ctrl": array_record(sim.data.ctrl),
            "eq_active": np.asarray(sim.model.eq_active, dtype=int).tolist(),
            "eq_obj1id": np.asarray(sim.model.eq_obj1id, dtype=int).tolist(),
            "eq_obj2id": np.asarray(sim.model.eq_obj2id, dtype=int).tolist(),
            "eq_data": np.asarray(sim.model.eq_data, dtype=float).tolist(),
        },
        "obs_keys": sorted(obs.keys()),
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(payload, f)
    print("wrote MJCF state to {}".format(args.out))


if __name__ == "__main__":
    main()
