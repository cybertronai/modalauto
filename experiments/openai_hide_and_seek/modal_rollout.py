"""Modal runner for original OpenAI hide-and-seek rollouts.

This keeps the source of truth in the MuJoCo/worldgen environment. Modal is
used because the legacy macOS/x86 stack segfaults during MjSim construction on
this machine before the policy or viewer runs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import modal


ROOT = Path(__file__).resolve().parent
if modal.is_local():
    WORLDGEN = ROOT.parents[3] / "mujoco-worldgen"
else:
    WORLDGEN = Path("/root/mujoco-worldgen")
REMOTE_ROOT = Path("/root/openai_hide_and_seek")
REMOTE_WORLDGEN = Path("/root/mujoco-worldgen")
LEGACY_PYTHON = "/opt/mae/bin/python"

MUJOCO_ENV = {
    "MUJOCO_GL": "osmesa",
    "MUJOCO_PY_MUJOCO_PATH": "/root/.mujoco/mujoco210",
    "LD_LIBRARY_PATH": "/root/.mujoco/mujoco210/bin:/usr/lib/x86_64-linux-gnu",
    "PYTHONPATH": "/root/openai_hide_and_seek:/root/mujoco-worldgen",
}


image = (
    modal.Image.from_registry("python:3.10-bullseye", add_python=None)
    .apt_install(
        "build-essential",
        "curl",
        "gcc",
        "g++",
        "libglew-dev",
        "libglfw3",
        "libglfw3-dev",
        "libgl1-mesa-dev",
        "libosmesa6-dev",
        "patchelf",
        "unzip",
        "wget",
    )
    .run_commands(
        "curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest "
        "| tar -xj -C /usr/local/bin --strip-components=1 bin/micromamba && "
        "micromamba create -y -p /opt/mae -c conda-forge python=3.7 pip && "
        "micromamba clean -a -y"
    )
    .run_commands(
        "mkdir -p /root/.mujoco && "
        "wget -q https://mujoco.org/download/mujoco210-linux-x86_64.tar.gz -O /tmp/mujoco210.tar.gz && "
        "tar -xzf /tmp/mujoco210.tar.gz -C /root/.mujoco && "
        "rm /tmp/mujoco210.tar.gz"
    )
    .run_commands(
        "/opt/mae/bin/python -m pip install 'pip<24' 'setuptools<60' wheel && "
        "/opt/mae/bin/python -m pip install "
        "'Cython<3' "
        "numpy==1.18.5 "
        "cloudpickle==0.5.2 "
        "click==7.0 "
        "gym==0.10.8 "
        "jsonnet==0.20.0 "
        "xmltodict==0.12.0 "
        "numpy-stl==2.10.1 "
        "mujoco-py==2.1.2.14 "
        "tensorflow==1.15.5 "
        "'protobuf<4' "
        "'opencv-python-headless<4.3' && "
        "/opt/mae/bin/python -m pip install baselines==0.1.5 --no-deps"
    )
    .env(MUJOCO_ENV)
    .run_commands("/opt/mae/bin/python - <<'PY'\nimport mujoco_py\nprint('mujoco_py precompiled')\nPY")
    .add_local_dir(ROOT, str(REMOTE_ROOT), copy=True)
    .add_local_dir(WORLDGEN, str(REMOTE_WORLDGEN), copy=True)
)

app = modal.App("openai-hide-and-seek-rollout", image=image)


def _run_export(script: str, args: list[str]) -> str:
    out_path = REMOTE_ROOT / "rollout.json"
    cmd = [
        LEGACY_PYTHON,
        "-u",
        str(REMOTE_ROOT / "scripts" / script),
        *args,
        "--out",
        str(out_path),
    ]
    subprocess.run(cmd, cwd=str(REMOTE_ROOT), check=True)
    return out_path.read_text()


@app.function(timeout=1800, cpu=4, memory=8192)
def export_policy_rollout(
    env_jsonnet: str = "examples/hide_and_seek_quadrant.jsonnet",
    policy_npz: str = "examples/hide_and_seek_quadrant.npz",
    steps: int = 120,
    seed: int = 0,
) -> str:
    return _run_export(
        "export_rollout.py",
        [env_jsonnet, policy_npz, "--steps", str(steps), "--seed", str(seed)],
    )


@app.function(timeout=1800, cpu=4, memory=8192)
def export_random_rollout(
    env_jsonnet: str = "examples/hide_and_seek_quadrant.jsonnet",
    steps: int = 60,
    seed: int = 0,
    random_actions: bool = False,
) -> str:
    args = [env_jsonnet, "--steps", str(steps), "--seed", str(seed)]
    if random_actions:
        args.append("--random")
    return _run_export("export_random_rollout.py", args)


@app.function(timeout=1800, cpu=4, memory=8192, gpu="A10G")
def export_policy_rollout_a10g(
    env_jsonnet: str = "examples/hide_and_seek_quadrant.jsonnet",
    policy_npz: str = "examples/hide_and_seek_quadrant.npz",
    steps: int = 120,
    seed: int = 0,
) -> str:
    return _run_export(
        "export_rollout.py",
        [env_jsonnet, policy_npz, "--steps", str(steps), "--seed", str(seed)],
    )


@app.local_entrypoint()
def main(
    mode: str = "policy",
    env_jsonnet: str = "examples/hide_and_seek_quadrant.jsonnet",
    policy_npz: str = "examples/hide_and_seek_quadrant.npz",
    steps: int = 120,
    seed: int = 0,
    out: str = "visualization/public/rollouts/hide_and_seek_quadrant_seed0.json",
    gpu: bool = False,
    random_actions: bool = False,
):
    if mode == "random":
        payload = export_random_rollout.remote(env_jsonnet, steps, seed, random_actions)
    elif gpu:
        payload = export_policy_rollout_a10g.remote(env_jsonnet, policy_npz, steps, seed)
    else:
        payload = export_policy_rollout.remote(env_jsonnet, policy_npz, steps, seed)

    parsed = json.loads(payload)
    out_path = ROOT / out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(parsed))
    print("wrote {} frames to {}".format(len(parsed.get("frames", [])), out_path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.parse_args()
