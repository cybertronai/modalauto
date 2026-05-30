#!/usr/bin/env python3
"""ViZDoom Basic autoresearch runner.

Evaluates simple policy candidates on the parent ViZDoom checkout's Basic task
and writes the same journal/artifact shape as the other modalauto experiments.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import os
import random
import sys
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any


MODALAUTO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VIZDOOM_ROOT = MODALAUTO_ROOT.parent
sys.path.insert(0, str(MODALAUTO_ROOT))
sys.path.insert(0, str(MODALAUTO_ROOT.parent))

try:
    from autoresearch.backend import experiment_config, team_journal
except ModuleNotFoundError:
    import modalauto

    sys.modules.setdefault("autoresearch", modalauto)
    from modalauto.backend import experiment_config, team_journal


DEFAULT_LAYOUT = experiment_config.layout("vizdoom-basic")
JOURNAL_ROOT = DEFAULT_LAYOUT.journal_dir
SCORE_SCALE = 1000


@dataclass(frozen=True)
class Policy:
    name: str
    family: str
    notes: str
    kind: str
    fire_period: int = 1
    strafe_period: int = 0
    random_fire_prob: float = 0.0


@dataclass
class Row:
    name: str
    family: str
    semantic: str
    score: float | None
    score_std: float
    score_min: float
    score_max: float
    rewards: list[float]
    error: str
    notes: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_parent_vizdoom_paths(vizdoom_root: Path) -> None:
    """Prefer the sibling checkout's built package over any global install."""
    root = vizdoom_root.expanduser().resolve()
    candidate_paths = [root / "bin" / "python3.14"]
    candidate_paths.extend(sorted(root.glob("build/lib*/")))
    candidate_paths.append(root / "src" / "lib_python")
    for path in reversed(candidate_paths):
        if path.exists():
            sys.path.insert(0, str(path))


def import_vizdoom(vizdoom_root: Path) -> Any:
    add_parent_vizdoom_paths(vizdoom_root)
    import vizdoom as vzd

    return vzd


def score_key(row: Row) -> float:
    if row.score is None:
        return float("-inf")
    return row.score


def action_for(policy: Policy, step: int, rng: random.Random) -> list[int]:
    # Available buttons in basic.cfg: MOVE_LEFT, MOVE_RIGHT, ATTACK.
    if policy.kind == "attack_only":
        return [0, 0, 1 if step % policy.fire_period == 0 else 0]
    if policy.kind == "left_fire":
        return [1, 0, 1 if step % policy.fire_period == 0 else 0]
    if policy.kind == "right_fire":
        return [0, 1, 1 if step % policy.fire_period == 0 else 0]
    if policy.kind == "sweep_fire":
        left = (step // max(policy.strafe_period, 1)) % 2 == 0
        return [1 if left else 0, 0 if left else 1, 1 if step % policy.fire_period == 0 else 0]
    if policy.kind == "random":
        return [rng.randrange(2), rng.randrange(2), 1 if rng.random() < policy.random_fire_prob else 0]
    return [0, 0, 0]


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return len(payload).to_bytes(4, "big") + kind + payload + crc.to_bytes(4, "big")


def png_bytes(rgb: bytes, width: int, height: int) -> bytes:
    rows = []
    stride = width * 3
    for y in range(height):
        rows.append(b"\x00" + rgb[y * stride:(y + 1) * stride])
    return b"".join([
        b"\x89PNG\r\n\x1a\n",
        png_chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"),
        png_chunk(b"IDAT", zlib.compress(b"".join(rows), level=6)),
        png_chunk(b"IEND", b""),
    ])


def screen_rgb_bytes(screen: Any) -> tuple[bytes, int, int]:
    shape = tuple(int(v) for v in getattr(screen, "shape", ()))
    if len(shape) == 3 and shape[2] >= 3:
        height, width = shape[:2]
        return screen[:, :, :3].astype("uint8").tobytes(), width, height
    if len(shape) == 3 and shape[0] >= 3:
        channels, height, width = shape
        rgb = screen[:3, :, :].transpose(1, 2, 0).astype("uint8")
        return rgb.tobytes(), width, height
    if len(shape) == 2:
        height, width = shape
        gray = screen.astype("uint8").tobytes()
        rgb = bytearray()
        for value in gray:
            rgb.extend((value, value, value))
        return bytes(rgb), width, height
    raise ValueError(f"unsupported screen buffer shape: {shape}")


def capture_policy_frames(
    policy: Policy,
    vizdoom_root: Path,
    seed: int,
    frame_skip: int,
    max_frames: int = 14,
) -> list[tuple[bytes, int, int]]:
    vzd = import_vizdoom(vizdoom_root)
    scenario = vizdoom_root.expanduser().resolve() / "scenarios" / "basic.cfg"
    game = vzd.DoomGame()
    game.load_config(str(scenario))
    game.set_window_visible(False)
    game.set_console_enabled(False)
    game.set_screen_format(vzd.ScreenFormat.RGB24)
    if hasattr(game, "set_seed"):
        game.set_seed(int(seed))
    game.init()
    frames: list[tuple[bytes, int, int]] = []
    try:
        rng = random.Random(seed)
        game.new_episode()
        step = 0
        capture_every = max(1, 300 // max_frames)
        while not game.is_episode_finished() and len(frames) < max_frames:
            state = game.get_state()
            if state is not None and step % capture_every == 0:
                frames.append(screen_rgb_bytes(state.screen_buffer))
            game.make_action(action_for(policy, step, rng), frame_skip)
            step += 1
    finally:
        game.close()
    return frames


def write_policy_recording_svg(
    policy: Policy,
    path: Path,
    vizdoom_root: Path,
    seed: int,
    frame_skip: int,
) -> None:
    frames = capture_policy_frames(policy, vizdoom_root, seed, frame_skip)
    if not frames:
        raise ValueError("no ViZDoom frames captured")

    width, height = frames[0][1], frames[0][2]
    duration = max(1.0, len(frames) * 0.12)
    key_times = ";".join(f"{i / max(1, len(frames) - 1):.4f}" for i in range(len(frames)))
    images = []
    for i, (rgb, frame_width, frame_height) in enumerate(frames):
        if frame_width != width or frame_height != height:
            continue
        values = ";".join("1" if j == i else "0" for j in range(len(frames)))
        encoded = base64.b64encode(png_bytes(rgb, width, height)).decode("ascii")
        images.append(
            f'<image width="{width}" height="{height}" href="data:image/png;base64,{encoded}" opacity="{1 if i == 0 else 0}">'
            f'<animate attributeName="opacity" dur="{duration:.2f}s" repeatCount="indefinite" '
            f'calcMode="discrete" keyTimes="{key_times}" values="{values}"/>'
            f'</image>'
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="ViZDoom recording for {html.escape(policy.name)}">
  <title>{html.escape(policy.name)} ViZDoom Basic recording</title>
  {''.join(images)}
</svg>
""")


def evaluate_policy(
    policy: Policy,
    vizdoom_root: Path,
    seeds: list[int],
    episodes: int,
    frame_skip: int,
) -> Row:
    try:
        vzd = import_vizdoom(vizdoom_root)
        scenario = vizdoom_root.expanduser().resolve() / "scenarios" / "basic.cfg"
        rewards: list[float] = []
        for seed in seeds:
            game = vzd.DoomGame()
            game.load_config(str(scenario))
            game.set_window_visible(False)
            game.set_console_enabled(False)
            if hasattr(game, "set_seed"):
                game.set_seed(int(seed))
            game.init()
            try:
                rng = random.Random(seed)
                for _ in range(episodes):
                    game.new_episode()
                    step = 0
                    while not game.is_episode_finished():
                        game.make_action(action_for(policy, step, rng), frame_skip)
                        step += 1
                    rewards.append(float(game.get_total_reward()))
            finally:
                game.close()
        return Row(
            name=policy.name,
            family=policy.family,
            semantic="ok",
            score=float(mean(rewards)),
            score_std=float(pstdev(rewards)) if len(rewards) > 1 else 0.0,
            score_min=float(min(rewards)),
            score_max=float(max(rewards)),
            rewards=rewards,
            error="",
            notes=policy.notes,
        )
    except Exception as exc:
        return Row(
            name=policy.name,
            family=policy.family,
            semantic="invalid",
            score=None,
            score_std=0.0,
            score_min=0.0,
            score_max=0.0,
            rewards=[],
            error=str(exc),
            notes=policy.notes,
        )


def candidate_batch(hypothesis_record: dict | None = None) -> list[Policy]:
    policies = [
        Policy("attack_every_tick", "hand_designed", "Stand still and fire every action.", "attack_only"),
        Policy("attack_every_2", "hand_designed", "Conserve ammo cadence: fire every second action.", "attack_only", fire_period=2),
        Policy("left_fire", "hand_designed", "Strafe left while firing.", "left_fire"),
        Policy("right_fire", "hand_designed", "Strafe right while firing.", "right_fire"),
        Policy("slow_sweep_fire", "hand_designed", "Alternate strafe direction every 12 steps while firing.", "sweep_fire", strafe_period=12),
        Policy("fast_sweep_fire", "hand_designed", "Alternate strafe direction every 4 steps while firing.", "sweep_fire", strafe_period=4),
        Policy("random_70pct_fire", "random", "Random movement with 70% attack probability.", "random", random_fire_prob=0.7),
    ]
    if hypothesis_record:
        title = str(hypothesis_record.get("title") or "").strip() or "agent_hypothesis"
        policies.append(Policy(
            "hypothesis_attack_bias",
            "hypothesis",
            f"Agent-provided hypothesis: {title}",
            "random",
            random_fire_prob=0.9,
        ))
    return policies


def write_run(
    run_id: str,
    rows: list[Row],
    policies: list[Policy],
    journal_root: Path,
    vizdoom_root: Path,
    seeds: list[int],
    episodes: int,
    frame_skip: int,
) -> Path:
    artifact_dir = journal_root / "artifacts" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    approach_media = {}
    for policy in policies:
        media_path = artifact_dir / "viz" / f"{policy.name}.svg"
        write_policy_recording_svg(policy, media_path, vizdoom_root, seeds[0] if seeds else 1, frame_skip)
        approach_media[policy.name] = str(media_path)

    csv_path = artifact_dir / "candidates.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "name", "family", "semantic", "score", "score_std", "score_min",
            "score_max", "rewards", "error", "notes",
        ])
        writer.writeheader()
        for row in rows:
            data = row.__dict__.copy()
            data["score"] = "" if row.score is None else f"{row.score:.6f}"
            data["score_std"] = f"{row.score_std:.6f}"
            data["score_min"] = f"{row.score_min:.6f}"
            data["score_max"] = f"{row.score_max:.6f}"
            data["rewards"] = json.dumps(row.rewards)
            writer.writerow(data)

    valid = [(row, policy) for row, policy in zip(rows, policies) if row.semantic == "ok" and row.score is not None]
    valid.sort(key=lambda item: -score_key(item[0]))
    best_row, best_policy = valid[0] if valid else (None, None)
    if best_policy is not None:
        (artifact_dir / "best_policy.json").write_text(json.dumps(best_policy.__dict__, indent=2))

    summary = {
        "run_id": run_id,
        "created_at": now_iso(),
        "domain": "vizdoom-basic",
        "direction": "maximize",
        "primary_metric": "mean_total_reward",
        "vizdoom_root": str(vizdoom_root.expanduser().resolve()),
        "scenario": str(vizdoom_root.expanduser().resolve() / "scenarios" / "basic.cfg"),
        "seeds": seeds,
        "episodes_per_seed": episodes,
        "frame_skip": frame_skip,
        "n_candidates": len(rows),
        "n_ok": sum(1 for row in rows if row.semantic == "ok"),
        "n_invalid": sum(1 for row in rows if row.semantic == "invalid"),
        "approach_media": approach_media,
        "best": None if best_row is None else best_row.__dict__,
        "top_5": [row.__dict__ for row in sorted(
            [row for row in rows if row.semantic == "ok" and row.score is not None],
            key=lambda row: -score_key(row),
        )[:5]],
    }
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    runs_dir = journal_root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{run_id}.md").write_text(
        f"# vizdoom-basic run {run_id}\n\n"
        f"- Scenario: `{summary['scenario']}`\n"
        f"- Direction: maximize mean total reward\n"
        f"- Candidates: {summary['n_candidates']} ({summary['n_ok']} ok, {summary['n_invalid']} invalid)\n"
        f"- Seeds: {seeds}, episodes per seed: {episodes}, frame skip: {frame_skip}\n"
        f"- Best: {best_row.name if best_row else 'none'} = {best_row.score if best_row else 'n/a'}\n"
        f"- Artifacts: `{artifact_dir.relative_to(journal_root)}/`\n"
    )
    return artifact_dir


def write_journal(
    run_id: str,
    rows: list[Row],
    policies: list[Policy],
    journal_root: Path,
    artifact_dir: Path,
    seeds: list[int],
    episodes: int,
    frame_skip: int,
    hypothesis_record: dict | None,
) -> None:
    db_path = journal_root / "team_journal.db"
    team_journal.init_db(db_path)
    db = team_journal.connect(db_path)
    stamp = team_journal.now()
    team_id = "vizdoom-basic-loop-team"
    agent_id = "vizdoom-basic-loop-agent"
    db.execute(
        "INSERT OR IGNORE INTO teams (id, status, focus, context_json, created_at, updated_at) "
        "VALUES (?, 'active', ?, '{}', ?, ?)",
        (team_id, "ViZDoom Basic policy search", stamp, stamp),
    )
    db.execute(
        "INSERT OR IGNORE INTO agents (id, role, team_id, status, created_at, updated_at) "
        "VALUES (?, 'implementor', ?, 'idle', ?, ?)",
        (agent_id, team_id, stamp, stamp),
    )

    for row, policy in zip(rows, policies):
        hyp_id = team_journal.next_id(db, "hyp", "hypotheses")
        context = {
            "run_id": run_id,
            "domain": "vizdoom-basic",
            "direction": "maximize",
            "policy": policy.__dict__,
            "seeds": seeds,
            "episodes_per_seed": episodes,
            "frame_skip": frame_skip,
            "batch_hypothesis": hypothesis_record,
        }
        db.execute(
            """
            INSERT INTO hypotheses
                (id, team_id, proposer_agent_id, priority, status,
                 title, rationale, expected_movement, context_json,
                 created_at, updated_at)
            VALUES (?, ?, ?, 0, 'submitted', ?, ?, ?, ?, ?, ?)
            """,
            (
                hyp_id, team_id, agent_id, f"{policy.name} - {policy.family}",
                policy.notes, "maximize mean total reward on ViZDoom basic.cfg",
                json.dumps(context), stamp, stamp,
            ),
        )

        sub_id = team_journal.next_id(db, "sub", "submissions")
        policy_path = artifact_dir / "policies" / f"{policy.name}.json"
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        policy_path.write_text(json.dumps(policy.__dict__, indent=2))
        approach_media = artifact_dir / "viz" / f"{policy.name}.svg"
        db.execute(
            """
            INSERT INTO submissions
                (id, hypothesis_id, team_id, implementor_agent_id, status,
                 artifact_path, candidate_summary_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'verified', ?, ?, ?, ?)
            """,
            (
                sub_id, hyp_id, team_id, agent_id, str(policy_path),
                json.dumps({
                    "name": policy.name,
                    "family": policy.family,
                    "notes": policy.notes,
                    "approach_media": str(approach_media),
                }),
                stamp, stamp,
            ),
        )

        ver_id = team_journal.next_id(db, "ver", "verifications")
        official_score = int(round(row.score * SCORE_SCALE)) if row.score is not None else None
        buckets = {
            "score_float": row.score,
            "score_std": row.score_std,
            "score_min": row.score_min,
            "score_max": row.score_max,
            "score_scale": SCORE_SCALE,
            "rewards": row.rewards,
            "seeds": seeds,
            "episodes_per_seed": episodes,
            "frame_skip": frame_skip,
            "stochastic": False,
            "approach_media": str(approach_media),
        }
        decision = "accept" if row.semantic == "ok" else "reject"
        db.execute(
            """
            INSERT INTO verifications
                (id, submission_id, verifier_agent_id, semantic, official_score,
                 buckets_json, decision, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ver_id, sub_id, agent_id, row.semantic, official_score,
                json.dumps(buckets), decision, row.error, stamp,
            ),
        )
    db.commit()
    db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="vizdoom_basic_v1")
    parser.add_argument("--experiment-root", type=Path, default=None)
    parser.add_argument("--journal-root", type=Path, default=JOURNAL_ROOT)
    parser.add_argument("--hypothesis-json", type=Path, default=None)
    parser.add_argument("--vizdoom-root", type=Path, default=Path(os.environ.get("VIZDOOM_ROOT", DEFAULT_VIZDOOM_ROOT)))
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--frame-skip", type=int, default=1)
    parser.add_argument("--verify-cases", default=None, help="accepted for modalauto runner compatibility")
    parser.add_argument("--verify-top", default=None, help="accepted for modalauto runner compatibility")
    args = parser.parse_args(argv)

    if args.experiment_root is not None:
        layout = experiment_config.layout(root=args.experiment_root)
        args.journal_root = layout.journal_dir

    hypothesis_record = None
    if args.hypothesis_json is not None and args.hypothesis_json.exists():
        hypothesis_record = json.loads(args.hypothesis_json.read_text())

    seeds = [int(seed.strip()) for seed in args.seeds.split(",") if seed.strip()]
    policies = candidate_batch(hypothesis_record)
    start = time.perf_counter()
    vizdoom_root = args.vizdoom_root.expanduser().resolve()
    rows = [evaluate_policy(policy, vizdoom_root, seeds, args.episodes, args.frame_skip) for policy in policies]
    journal_root = args.journal_root.expanduser().resolve()
    artifact_dir = write_run(
        args.run_id, rows, policies, journal_root, vizdoom_root,
        seeds, args.episodes, args.frame_skip,
    )
    write_journal(
        args.run_id, rows, policies, journal_root, artifact_dir,
        seeds, args.episodes, args.frame_skip, hypothesis_record,
    )

    valid = sorted(
        [row for row in rows if row.semantic == "ok" and row.score is not None],
        key=lambda row: -score_key(row),
    )
    output = {
        "artifact_dir": str(artifact_dir),
        "best": None if not valid else {
            "name": valid[0].name,
            "score": valid[0].score,
            "score_std": valid[0].score_std,
            "family": valid[0].family,
            "semantic": valid[0].semantic,
        },
        "elapsed_seconds": round(time.perf_counter() - start, 3),
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
