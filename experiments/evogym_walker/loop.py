#!/usr/bin/env python3
"""evogym autoresearch loop — mirrors matmul_loop.py shape.

One process. Generate candidate batch → score each (multi-seed) → verify →
write CSV + summary JSON + best body NPY + run note. Same artifact shape
as matmul_loop so EvoFlow viz can render either domain.

Direction is MAXIMIZE here (vs matmul's MINIMIZE). best = highest score.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
import numpy as np

# Make `autoresearch` importable when this module is run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from autoresearch.backend import experiment_config, team_journal
from autoresearch.experiments.evogym_walker.walker import scorer as evo_scorer
from autoresearch.experiments.evogym_walker.walker import candidates as evo_candidates
from autoresearch.experiments.evogym_walker.walker.candidates import Candidate


# team_journal.official_score is INTEGER, but evogym uses float rewards.
# Store a scaled int for existing schema compatibility and expose the real
# value in buckets_json for the UI/export layer.
SCORE_SCALE = 10000


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LAYOUT = experiment_config.layout("evogym_walker")
JOURNAL_ROOT = DEFAULT_LAYOUT.journal_dir


@dataclass
class Row:
    name: str
    family: str
    semantic: str
    score: float | None
    score_std: float
    score_min: float
    score_max: float
    n_voxels: int
    error: str
    notes: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def score_candidates(
    cands: list[Candidate],
    seeds: list[int],
    rollout_steps: int,
) -> list[Row]:
    rows: list[Row] = []
    for c in cands:
        result = evo_scorer.score_body(c.body, rollout_steps=rollout_steps, seeds=seeds)
        if result["semantic"] != "ok":
            rows.append(Row(
                name=c.name, family=c.family, semantic="invalid",
                score=None, score_std=0.0, score_min=0.0, score_max=0.0,
                n_voxels=int((c.body != 0).sum()),
                error=result["error"], notes=c.notes,
            ))
            continue
        b = result["buckets"]
        rows.append(Row(
            name=c.name, family=c.family, semantic="ok",
            score=result["official_score"],
            score_std=b["std"], score_min=b["min"], score_max=b["max"],
            n_voxels=b["n_voxels"],
            error="", notes=c.notes,
        ))
    return rows


def render_rollout_frames(
    body: np.ndarray,
    rollout_steps: int,
    seed: int,
    frame_stride: int = 2,
) -> list[np.ndarray]:
    """Render a single Walker-v0 rollout for artifact visualization."""
    import gymnasium as gym
    import evogym.envs  # noqa: F401

    rng = np.random.default_rng(seed)
    env = gym.make("Walker-v0", body=body, render_mode="img")
    frames: list[np.ndarray] = []
    try:
        obs, _info = env.reset(seed=seed)
        n_acts = env.action_space.shape[0]
        phases = rng.uniform(0, 2 * np.pi, size=n_acts)
        freq = 0.15
        for t in range(rollout_steps):
            raw = 0.5 * (np.sin(freq * t * 2 * np.pi + phases) + 1)
            a = 0.6 + raw * (1.6 - 0.6)
            obs, _r, term, trunc, _info = env.step(a)
            if t % frame_stride == 0:
                img = env.render()
                if img is not None:
                    frames.append(np.asarray(img, dtype=np.uint8))
            if term or trunc:
                break
    finally:
        env.close()
    return frames


def save_rollout_gif(frames: list[np.ndarray], path: Path, max_w: int = 640) -> bool:
    if not frames:
        return False
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    imgs = []
    for frame in frames:
        im = Image.fromarray(frame)
        if im.width > max_w:
            scale = max_w / im.width
            im = im.resize((max_w, int(im.height * scale)), Image.LANCZOS)
        imgs.append(im)
    imgs[0].save(
        path,
        format="GIF",
        save_all=True,
        append_images=imgs[1:],
        duration=40,
        loop=0,
        optimize=True,
    )
    return True


def write_rollout_media(
    rows: list[Row],
    cands: list[Candidate],
    artifact_dir: Path,
    seed: int,
    rollout_steps: int,
) -> dict[str, str]:
    media: dict[str, str] = {}
    errors: dict[str, str] = {}
    viz_dir = artifact_dir / "viz"
    for r, c in zip(rows, cands):
        if r.semantic != "ok":
            continue
        try:
            frames = render_rollout_frames(c.body, rollout_steps, seed)
            gif_path = viz_dir / f"{c.name}.gif"
            if save_rollout_gif(frames, gif_path):
                media[c.name] = str(gif_path)
        except Exception as exc:
            errors[c.name] = str(exc)
    if errors:
        (artifact_dir / "rollout_errors.json").write_text(json.dumps(errors, indent=2))
    return media


def write_run(
    run_id: str,
    rows: list[Row],
    cands: list[Candidate],
    journal_root: Path,
    seeds: list[int],
    rollout_steps: int,
    render_rollouts: bool = True,
) -> Path:
    artifact_dir = journal_root / "artifacts" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    bodies_dir = artifact_dir / "bodies"
    bodies_dir.mkdir(parents=True, exist_ok=True)

    # CSV
    csv_path = artifact_dir / "candidates.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "name", "family", "semantic", "score", "score_std",
            "score_min", "score_max", "n_voxels", "error", "notes",
        ])
        writer.writeheader()
        for r in rows:
            d = r.__dict__.copy()
            d["score"] = "" if d["score"] is None else f"{d['score']:.6f}"
            writer.writerow(d)

    for c in cands:
        np.save(bodies_dir / f"{c.name}.npy", c.body)

    # Best body
    valid = [(r, c) for r, c in zip(rows, cands) if r.semantic == "ok"]
    if valid:
        valid.sort(key=lambda rc: -rc[0].score)  # MAXIMIZE
        best_row, best_cand = valid[0]
        np.save(artifact_dir / "best.npy", best_cand.body)
        body_str = "\n".join(" ".join({0:"E",1:"R",2:"S",3:"H",4:"V"}[int(v)]
                                       for v in row) for row in best_cand.body)
        (artifact_dir / "best.txt").write_text(body_str + "\n")
    else:
        best_row = None

    # Summary JSON
    summary = {
        "run_id": run_id,
        "created_at": now_iso(),
        "domain": "evogym-walker",
        "direction": "maximize",
        "primary_metric": "mean_forward_reward",
        "n_candidates": len(rows),
        "n_ok": sum(1 for r in rows if r.semantic == "ok"),
        "n_invalid": sum(1 for r in rows if r.semantic == "invalid"),
        "seeds": seeds,
        "rollout_steps": rollout_steps,
        "best": None if best_row is None else best_row.__dict__,
        "top_5": [r.__dict__ for r in
                  sorted([r for r in rows if r.semantic == "ok"],
                         key=lambda r: -r.score)[:5]],
    }
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    rollout_media = {}
    if render_rollouts:
        rollout_media = write_rollout_media(
            rows,
            cands,
            artifact_dir,
            seed=seeds[0] if seeds else 0,
            rollout_steps=rollout_steps,
        )
        if rollout_media:
            summary["rollout_media"] = rollout_media
            (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    # Run note follows the journal/runs convention used by the frontend.
    runs_dir = journal_root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{run_id}.md").write_text(
        f"# evogym run {run_id}\n\n"
        f"- Domain: Walker-v0, direction = maximize forward_reward\n"
        f"- Candidates: {len(rows)} ({summary['n_ok']} ok, {summary['n_invalid']} invalid)\n"
        f"- Seeds per eval: {seeds}\n"
        f"- Rollout steps: {rollout_steps}\n"
        f"- Best: {best_row.name if best_row else 'none'} "
        f"= {best_row.score if best_row else '—'}\n"
        f"- Artifacts: `{artifact_dir.relative_to(journal_root)}/`\n"
    )
    return artifact_dir


def write_journal(
    run_id: str,
    rows: list[Row],
    cands: list[Candidate],
    journal_root: Path,
    artifact_dir: Path,
    seeds: list[int],
    rollout_steps: int,
    hypothesis_record: dict | None = None,
) -> None:
    """Write one hypothesis/submission/verification row per candidate."""
    db_path = journal_root / "team_journal.db"
    team_journal.init_db(db_path)
    db = team_journal.connect(db_path)
    stamp = team_journal.now()

    team_id = "evogym-loop-team"
    db.execute(
        "INSERT OR IGNORE INTO teams (id, status, focus, context_json, created_at, updated_at) "
        "VALUES (?, 'active', ?, '{}', ?, ?)",
        (team_id, "evogym Walker-v0 morphology search", stamp, stamp),
    )
    agent_id = "evogym-loop-agent"
    db.execute(
        "INSERT OR IGNORE INTO agents (id, role, team_id, status, created_at, updated_at) "
        "VALUES (?, 'implementor', ?, 'idle', ?, ?)",
        (agent_id, team_id, stamp, stamp),
    )

    batch_context = {
        "run_id": run_id,
        "n_candidates": len(cands),
        "seeds": seeds,
        "rollout_steps": rollout_steps,
        "domain": "evogym-walker",
        "direction": "maximize",
        "batch_hypothesis": hypothesis_record,
    }

    for r, c in zip(rows, cands):
        hyp_id = team_journal.next_id(db, "hyp", "hypotheses")
        n_h = int((c.body == 3).sum())
        n_v = int((c.body == 4).sum())
        n_voxels = int((c.body != 0).sum())
        ctx = dict(batch_context, candidate=c.name, family=c.family,
                   notes=c.notes, n_voxels=n_voxels)
        db.execute(
            """
            INSERT INTO hypotheses
                (id, team_id, proposer_agent_id, priority, status,
                 title, rationale, expected_movement, context_json,
                 created_at, updated_at)
            VALUES (?, ?, ?, 0, 'submitted', ?, ?, ?, ?, ?, ?)
            """,
            (
                hyp_id, team_id, agent_id, f"{c.name} · {c.family}", c.notes,
                f"voxels={n_voxels} h-act={n_h} v-act={n_v}; "
                f"score via {len(seeds)}-seed mean over {rollout_steps} steps",
                json.dumps(ctx), stamp, stamp,
            ),
        )

        sub_id = team_journal.next_id(db, "sub", "submissions")
        body_path = artifact_dir / f"bodies/{c.name}.npy"
        summary = {
            "name": c.name,
            "family": c.family,
            "notes": c.notes,
            "n_voxels": n_voxels,
            "n_h_actuators": n_h,
            "n_v_actuators": n_v,
        }
        rollout_gif = artifact_dir / "viz" / f"{c.name}.gif"
        if rollout_gif.exists():
            summary["rollout_gif"] = str(rollout_gif)
        db.execute(
            """
            INSERT INTO submissions
                (id, hypothesis_id, team_id, implementor_agent_id, status,
                 artifact_path, candidate_summary_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'verified', ?, ?, ?, ?)
            """,
            (sub_id, hyp_id, team_id, agent_id,
             str(body_path), json.dumps(summary), stamp, stamp),
        )

        ver_id = team_journal.next_id(db, "ver", "verifications")
        official_score = (
            int(round(r.score * SCORE_SCALE))
            if r.semantic == "ok" and r.score is not None else None
        )
        buckets = {
            "score_float": r.score,
            "score_std": r.score_std,
            "score_min": r.score_min,
            "score_max": r.score_max,
            "score_scale": SCORE_SCALE,
            "n_voxels": n_voxels,
            "stochastic": True,
            "seeds": seeds,
        }
        if rollout_gif.exists():
            buckets["rollout_gif"] = str(rollout_gif)
        decision = "accept" if r.semantic == "ok" else "reject"
        db.execute(
            """
            INSERT INTO verifications
                (id, submission_id, verifier_agent_id, semantic, official_score,
                 buckets_json, decision, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ver_id, sub_id, agent_id, r.semantic, official_score,
             json.dumps(buckets), decision, r.error, stamp),
        )

    db.commit()
    db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="evogym_loop_v1")
    parser.add_argument("--seeds", default="42,43,44",
                        help="Comma-sep seeds for multi-seed scoring")
    parser.add_argument("--rollout-steps", type=int, default=80)
    parser.add_argument("--seed", type=int, default=None,
                        help="seed for candidate gen; defaults to a stable hash of run id + hypothesis")
    parser.add_argument("--journal-root", type=Path, default=JOURNAL_ROOT)
    parser.add_argument("--experiment-root", type=Path, default=None,
                        help="override experiment root (workflow.json passes this)")
    parser.add_argument("--hypothesis-json", type=Path, default=None,
                        help="agent-provided hypothesis (README runner contract)")
    parser.add_argument("--n-mutations", type=int, default=4,
                        help="extra mutated children of the random batch's first candidate")
    parser.add_argument("--no-rollout-artifacts", action="store_true",
                        help="skip rendered rollout GIF artifacts")
    parser.add_argument("--no-journal", action="store_true",
                        help="write artifacts only; let autoresearch-agent record journal rows")
    args = parser.parse_args(argv)

    # Honor workflow.json --experiment-root substitution
    if args.experiment_root is not None:
        layout = experiment_config.layout(root=args.experiment_root)
        args.journal_root = layout.journal_dir

    hypothesis_record: dict | None = None
    if args.hypothesis_json is not None and args.hypothesis_json.exists():
        hypothesis_record = json.loads(args.hypothesis_json.read_text())
        print(f"[hyp] {hypothesis_record.get('title', '(untitled)')}", file=sys.stderr)

    if args.seed is None:
        seed_material = json.dumps(
            {"run_id": args.run_id, "hypothesis": hypothesis_record},
            sort_keys=True,
            default=str,
        )
        args.seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:8], 16)

    seeds = [int(s) for s in args.seeds.split(",")]
    rng = np.random.default_rng(args.seed)

    start = time.perf_counter()
    cands = evo_candidates.baseline_batch(seed=args.seed)
    # Add mutations of the canonical snake (first candidate)
    cands.extend(evo_candidates.mutate_batch(cands[0], args.n_mutations, rng, strength=0.1))
    print(f"[batch] {len(cands)} candidates", file=sys.stderr)

    rows = score_candidates(cands, seeds, args.rollout_steps)
    artifact_dir = write_run(args.run_id, rows, cands,
                              args.journal_root.expanduser().resolve(),
                              seeds, args.rollout_steps,
                              render_rollouts=not args.no_rollout_artifacts)
    if not args.no_journal:
        write_journal(args.run_id, rows, cands,
                      args.journal_root.expanduser().resolve(),
                      artifact_dir, seeds, args.rollout_steps,
                      hypothesis_record=hypothesis_record)

    valid = sorted([r for r in rows if r.semantic == "ok"], key=lambda r: -r.score)
    out = {
        "artifact_dir": str(artifact_dir),
        "best": None if not valid else {
            "name": valid[0].name,
            "score": valid[0].score,
            "score_std": valid[0].score_std,
            "family": valid[0].family,
        },
        "elapsed_seconds": round(time.perf_counter() - start, 3),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
