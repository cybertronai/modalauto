#!/usr/bin/env python3
"""Smoke test an exported OpenAI hide-and-seek MJCF with MuJoCo Warp."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import numpy as np


def decode_array(record):
    data = base64.b64decode(record["data_b64"])
    return np.frombuffer(data, dtype=np.dtype(record["dtype"])).reshape(record["shape"]).copy()


def set_batched_field(field, values):
    """Copy a host array into a Warp field, handling common batch layouts."""
    import warp as wp

    host = np.asarray(values, dtype=np.float32)
    target_shape = tuple(field.shape)
    if target_shape == host.shape:
        batch = host
    elif len(target_shape) == 2 and target_shape[1:] == host.shape:
        batch = np.broadcast_to(host, target_shape).copy()
    elif len(target_shape) == 1 and target_shape[0] == host.size:
        batch = host.reshape(target_shape)
    else:
        raise ValueError(f"cannot copy host shape {host.shape} into Warp field {target_shape}")
    field.assign(wp.array(batch, dtype=field.dtype, device=field.device))


def tensor_sample(field, max_rows=2):
    arr = field.numpy()
    return np.asarray(arr[:max_rows], dtype=float).round(5).tolist()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("state_json")
    parser.add_argument("--steps", type=int, default=16)
    parser.add_argument("--worlds", type=int, default=32)
    parser.add_argument("--random-ctrl", action="store_true")
    parser.add_argument("--out", default="mjwarp_smoke.json")
    args = parser.parse_args()

    import mujoco
    import mujoco_warp as mjw
    import warp as wp

    wp.init()

    payload = json.loads(Path(args.state_json).read_text())
    mjm = mujoco.MjModel.from_xml_string(payload["xml"])

    # Keep the smoke test conservative. Larger contact/joint buffers can be
    # raised later when we move to many worlds and full training.
    mjd = mujoco.MjData(mjm)
    mjd.qpos[:] = decode_array(payload["state"]["qpos"])
    mjd.qvel[:] = decode_array(payload["state"]["qvel"])
    if mjm.nu:
        mjd.ctrl[:] = decode_array(payload["state"]["ctrl"])
    mujoco.mj_forward(mjm, mjd)

    mjw_model = mjw.put_model(mjm)
    mjw_data = mjw.make_data(mjm, nworld=args.worlds, nconmax=max(1024, mjm.ngeom * 64), njmax=4096)
    set_batched_field(mjw_data.qpos, mjd.qpos)
    set_batched_field(mjw_data.qvel, mjd.qvel)
    if mjm.nu and hasattr(mjw_data, "ctrl"):
        set_batched_field(mjw_data.ctrl, mjd.ctrl)

    rng = np.random.default_rng(0)
    ctrl_norms = []
    for _ in range(args.steps):
        if args.random_ctrl and mjm.nu and hasattr(mjw_data, "ctrl"):
            ctrl = rng.uniform(-0.35, 0.35, size=tuple(mjw_data.ctrl.shape)).astype(np.float32)
            mjw_data.ctrl.assign(wp.array(ctrl, dtype=mjw_data.ctrl.dtype, device=mjw_data.ctrl.device))
            ctrl_norms.append(float(np.linalg.norm(ctrl[0])))
        mjw.step(mjw_model, mjw_data)

    result = {
        "source": payload["source"],
        "worlds": args.worlds,
        "steps": args.steps,
        "mujoco_model": {
            "nq": int(mjm.nq),
            "nv": int(mjm.nv),
            "nu": int(mjm.nu),
            "nbody": int(mjm.nbody),
            "ngeom": int(mjm.ngeom),
            "neq": int(mjm.neq),
        },
        "warp_fields": {
            "qpos_shape": list(mjw_data.qpos.shape),
            "qvel_shape": list(mjw_data.qvel.shape),
            "ctrl_shape": list(mjw_data.ctrl.shape) if hasattr(mjw_data, "ctrl") else None,
        },
        "qpos_sample": tensor_sample(mjw_data.qpos),
        "qvel_sample": tensor_sample(mjw_data.qvel),
        "ctrl_norms": [round(x, 5) for x in ctrl_norms[:8]],
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
