"""Modal smoke runner for MuJoCo Warp on OpenAI hide-and-seek MJCFs."""

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


def ignore_runtime_paths(path: Path) -> bool:
    parts = set(path.parts)
    return bool(parts & {"journal", "worktrees", "node_modules", "dist", "__pycache__"})

image = (
    modal.Image.from_registry("python:3.10-bullseye", add_python=None)
    .apt_install(
        "build-essential",
        "curl",
        "gcc",
        "g++",
        "git",
        "libglew-dev",
        "libglfw3",
        "libglfw3-dev",
        "libgl1-mesa-dev",
        "libosmesa6-dev",
        "ninja-build",
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
    .run_commands("python -m pip install --upgrade pip wheel setuptools")
    .run_commands("python -m pip install 'numpy<2' mujoco mujoco-warp warp-lang")
    .run_commands(
        "python -m pip install --index-url https://download.pytorch.org/whl/cu121 "
        "--trusted-host download.pytorch.org torch==2.2.2"
    )
    .env(MUJOCO_ENV)
    .run_commands("/opt/mae/bin/python - <<'PY'\nimport mujoco_py\nprint('mujoco_py precompiled')\nPY")
    .run_commands("python - <<'PY'\nimport mujoco, mujoco_warp, warp\nprint('mjwarp import ok')\nPY")
    .add_local_dir(ROOT, str(REMOTE_ROOT), ignore=ignore_runtime_paths)
    .add_local_dir(WORLDGEN, str(REMOTE_WORLDGEN))
)

app = modal.App("openai-hide-and-seek-mjwarp", image=image)


@app.function(timeout=1800, cpu=8, memory=16384, gpu="A10G", scaledown_window=900)
def smoke_mjwarp(
    env_jsonnet: str = "examples/hide_and_seek_quadrant.jsonnet",
    seed: int = 0,
    steps: int = 16,
    worlds: int = 32,
    random_ctrl: bool = False,
) -> str:
    state_path = REMOTE_ROOT / "mjcf_state.json"
    result_path = REMOTE_ROOT / "mjwarp_smoke.json"

    export_cmd = [
        LEGACY_PYTHON,
        "-u",
        str(REMOTE_ROOT / "scripts" / "export_mjcf_state.py"),
        env_jsonnet,
        "--seed",
        str(seed),
        "--out",
        str(state_path),
    ]
    subprocess.run(export_cmd, cwd=str(REMOTE_ROOT), check=True)

    smoke_cmd = [
        "python",
        "-u",
        str(REMOTE_ROOT / "scripts" / "mjwarp_smoke.py"),
        str(state_path),
        "--steps",
        str(steps),
        "--worlds",
        str(worlds),
        "--out",
        str(result_path),
    ]
    if random_ctrl:
        smoke_cmd.append("--random-ctrl")
    subprocess.run(smoke_cmd, cwd=str(REMOTE_ROOT), check=True)
    return result_path.read_text()


def run_train_subprocess(
    env_jsonnet: str = "examples/hide_and_seek_quadrant.jsonnet",
    seed: int = 0,
    worlds: int = 64,
    updates: int = 4,
    horizon: int = 32,
    lr: float = 3e-4,
    entropy_coef: float = 0.01,
    hidden: int = 64,
    prep_fraction: float = 0.4,
) -> dict[str, str]:
    state_path = REMOTE_ROOT / "mjcf_state.json"
    result_path = REMOTE_ROOT / "mjwarp_train_smoke.json"
    ckpt_path = REMOTE_ROOT / "mjwarp_smoke_policy.pt"
    rollout_path = REMOTE_ROOT / "mjwarp_policy_rollout.json"

    export_cmd = [
        LEGACY_PYTHON,
        "-u",
        str(REMOTE_ROOT / "scripts" / "export_mjcf_state.py"),
        env_jsonnet,
        "--seed",
        str(seed),
        "--out",
        str(state_path),
    ]
    subprocess.run(export_cmd, cwd=str(REMOTE_ROOT), check=True)

    train_cmd = [
        "python",
        "-u",
        str(REMOTE_ROOT / "scripts" / "mjwarp_train_smoke.py"),
        str(state_path),
        "--worlds",
        str(worlds),
        "--updates",
        str(updates),
        "--horizon",
        str(horizon),
        "--lr",
        str(lr),
        "--entropy-coef",
        str(entropy_coef),
        "--hidden",
        str(hidden),
        "--prep-fraction",
        str(prep_fraction),
        "--out",
        str(result_path),
        "--checkpoint",
        str(ckpt_path),
        "--rollout-out",
        str(rollout_path),
    ]
    proc = subprocess.run(train_cmd, cwd=str(REMOTE_ROOT), text=True, capture_output=True)
    (REMOTE_ROOT / "mjwarp_train_stdout.log").write_text(proc.stdout or "")
    (REMOTE_ROOT / "mjwarp_train_stderr.log").write_text(proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(
            "training subprocess failed with code {}\nSTDOUT:\n{}\nSTDERR:\n{}".format(
                proc.returncode,
                (proc.stdout or "")[-8000:],
                (proc.stderr or "")[-8000:],
            )
        )
    return {
        "result": result_path.read_text(),
        "checkpoint_bytes_b64": __import__("base64").b64encode(ckpt_path.read_bytes()).decode("ascii"),
        "rollout": rollout_path.read_text() if rollout_path.exists() else "",
    }


@app.function(timeout=3600, cpu=8, memory=24576, gpu="A10G", scaledown_window=600)
def train_smoke_mjwarp(
    env_jsonnet: str = "examples/hide_and_seek_quadrant.jsonnet",
    seed: int = 0,
    worlds: int = 64,
    updates: int = 4,
    horizon: int = 32,
    lr: float = 3e-4,
    entropy_coef: float = 0.01,
    hidden: int = 64,
    prep_fraction: float = 0.4,
) -> dict[str, str]:
    return run_train_subprocess(env_jsonnet, seed, worlds, updates, horizon, lr, entropy_coef, hidden, prep_fraction)


@app.function(timeout=3600, cpu=16, memory=49152, gpu="H100", min_containers=1, max_containers=4, scaledown_window=1800)
def train_smoke_mjwarp_h100(
    env_jsonnet: str = "examples/hide_and_seek_quadrant.jsonnet",
    seed: int = 0,
    worlds: int = 256,
    updates: int = 8,
    horizon: int = 64,
    lr: float = 3e-4,
    entropy_coef: float = 0.01,
    hidden: int = 64,
    prep_fraction: float = 0.4,
) -> dict[str, str]:
    return run_train_subprocess(env_jsonnet, seed, worlds, updates, horizon, lr, entropy_coef, hidden, prep_fraction)


@app.function(timeout=3600, cpu=16, memory=49152, gpu="H100", min_containers=1, max_containers=4, scaledown_window=1800)
def train_smoke_mjwarp_h100_v2(
    env_jsonnet: str = "examples/hide_and_seek_quadrant.jsonnet",
    seed: int = 0,
    worlds: int = 256,
    updates: int = 8,
    horizon: int = 64,
    lr: float = 3e-4,
    entropy_coef: float = 0.01,
    hidden: int = 64,
    prep_fraction: float = 0.4,
) -> dict[str, str]:
    return run_train_subprocess(env_jsonnet, seed, worlds, updates, horizon, lr, entropy_coef, hidden, prep_fraction)


@app.function(timeout=3600, cpu=16, memory=49152, gpu="H100", min_containers=1, max_containers=4, scaledown_window=1800)
def train_smoke_mjwarp_h100_v3(
    env_jsonnet: str = "examples/hide_and_seek_quadrant.jsonnet",
    seed: int = 0,
    worlds: int = 256,
    updates: int = 6,
    horizon: int = 64,
    lr: float = 3e-4,
    entropy_coef: float = 0.01,
    hidden: int = 64,
    prep_fraction: float = 0.4,
) -> dict[str, str]:
    return run_train_subprocess(env_jsonnet, seed, worlds, updates, horizon, lr, entropy_coef, hidden, prep_fraction)


@app.function(timeout=3600, cpu=16, memory=49152, gpu="H100", min_containers=1, max_containers=4, scaledown_window=1800)
def train_smoke_mjwarp_h100_v4(
    env_jsonnet: str = "examples/hide_and_seek_quadrant.jsonnet",
    seed: int = 0,
    worlds: int = 256,
    updates: int = 6,
    horizon: int = 64,
    lr: float = 3e-4,
    entropy_coef: float = 0.01,
    hidden: int = 64,
    prep_fraction: float = 0.4,
) -> dict[str, str]:
    return run_train_subprocess(env_jsonnet, seed, worlds, updates, horizon, lr, entropy_coef, hidden, prep_fraction)


@app.function(timeout=3600, cpu=16, memory=98304, gpu="H200", max_containers=2, scaledown_window=1800)
def train_smoke_mjwarp_h200(
    env_jsonnet: str = "examples/hide_and_seek_quadrant.jsonnet",
    seed: int = 0,
    worlds: int = 1024,
    updates: int = 8,
    horizon: int = 64,
    lr: float = 3e-4,
    entropy_coef: float = 0.01,
    hidden: int = 64,
    prep_fraction: float = 0.4,
) -> dict[str, str]:
    return run_train_subprocess(env_jsonnet, seed, worlds, updates, horizon, lr, entropy_coef, hidden, prep_fraction)


@app.local_entrypoint()
def main(
    mode: str = "smoke",
    env_jsonnet: str = "examples/hide_and_seek_quadrant.jsonnet",
    seed: int = 0,
    steps: int = 16,
    worlds: int = 32,
    updates: int = 4,
    horizon: int = 32,
    random_ctrl: bool = False,
    out: str = "visualization/public/rollouts/mjwarp_smoke.json",
    checkpoint: str = "visualization/public/rollouts/mjwarp_smoke_policy.pt",
):
    if mode == "train":
        payload = train_smoke_mjwarp.remote(env_jsonnet, seed, worlds, updates, horizon)
        parsed = json.loads(payload["result"])
        ckpt_path = ROOT / checkpoint
        ckpt_path.parent.mkdir(parents=True, exist_ok=True)
        ckpt_path.write_bytes(__import__("base64").b64decode(payload["checkpoint_bytes_b64"]))
    elif mode == "train-h100":
        payload = train_smoke_mjwarp_h100.remote(env_jsonnet, seed, worlds, updates, horizon)
        parsed = json.loads(payload["result"])
        ckpt_path = ROOT / checkpoint
        ckpt_path.parent.mkdir(parents=True, exist_ok=True)
        ckpt_path.write_bytes(__import__("base64").b64decode(payload["checkpoint_bytes_b64"]))
    elif mode == "train-h200":
        payload = train_smoke_mjwarp_h200.remote(env_jsonnet, seed, worlds, updates, horizon)
        parsed = json.loads(payload["result"])
        ckpt_path = ROOT / checkpoint
        ckpt_path.parent.mkdir(parents=True, exist_ok=True)
        ckpt_path.write_bytes(__import__("base64").b64decode(payload["checkpoint_bytes_b64"]))
    else:
        payload = smoke_mjwarp.remote(env_jsonnet, seed, steps, worlds, random_ctrl)
        parsed = json.loads(payload)
    out_path = ROOT / out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(parsed, indent=2))
    print("wrote MJWarp {} result to {}".format(mode, out_path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.parse_args()
