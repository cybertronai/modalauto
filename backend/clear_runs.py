#!/usr/bin/env python3
"""Clear generated autoresearch experiment run state."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from autoresearch.backend import experiment_config
from autoresearch.backend import team_journal


EXPERIMENT_ALIASES = {
    "matmall": "matmul",
}


@dataclass(frozen=True)
class ClearTarget:
    path: Path
    label: str


def normalize_experiment_name(name: str | None) -> str | None:
    if name is None:
        return None
    return EXPERIMENT_ALIASES.get(name, name)


def db_targets(db_path: Path, label: str) -> list[ClearTarget]:
    return [
        ClearTarget(db_path, label),
        ClearTarget(Path(str(db_path) + "-shm"), f"{label} shared-memory file"),
        ClearTarget(Path(str(db_path) + "-wal"), f"{label} write-ahead log"),
    ]


def generated_targets(exp: experiment_config.ExperimentLayout, keep_worktrees: bool = False) -> list[ClearTarget]:
    targets = [
        ClearTarget(exp.journal_dir / "artifacts", "run artifacts"),
        ClearTarget(exp.journal_dir / "runs", "run notes"),
        ClearTarget(exp.board_dir, "message board"),
        *db_targets(exp.team_db, "team journal database"),
        *db_targets(exp.research_db, "research memory database"),
    ]
    targets.extend(ClearTarget(path, "journal jsonl") for path in exp.journal_dir.glob("*.jsonl"))
    targets.extend(ClearTarget(path, "message backup") for path in exp.journal_dir.glob("messages.before-*"))
    if not keep_worktrees:
        targets.append(ClearTarget(exp.worktree_root, "agent worktrees"))
    return unique_targets(targets)


def unique_targets(targets: list[ClearTarget]) -> list[ClearTarget]:
    seen: set[Path] = set()
    out = []
    for target in targets:
        path = target.path.expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        out.append(ClearTarget(path, target.label))
    return out


def is_safe_generated_path(path: Path, exp_root: Path) -> bool:
    path = path.expanduser().resolve()
    exp_root = exp_root.expanduser().resolve()
    if path == exp_root:
        return False
    try:
        path.relative_to(exp_root)
    except ValueError:
        return False
    return (
        path.name in {"artifacts", "runs", "messages", "worktrees"}
        or path.name.startswith("messages.before-")
        or path.parent.name == "journal"
    )


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def clear_runs(
    exp: experiment_config.ExperimentLayout,
    *,
    dry_run: bool = True,
    keep_worktrees: bool = False,
    reinit: bool = True,
) -> dict[str, object]:
    targets = generated_targets(exp, keep_worktrees=keep_worktrees)
    planned = []
    removed = []
    skipped = []
    for target in targets:
        exists = target.path.exists()
        safe = is_safe_generated_path(target.path, exp.root)
        item = {
            "path": str(target.path),
            "label": target.label,
            "exists": exists,
            "safe": safe,
        }
        planned.append(item)
        if not exists:
            continue
        if not safe:
            skipped.append({**item, "reason": "outside generated experiment paths"})
            continue
        if not dry_run:
            remove_path(target.path)
            removed.append(item)
    if not dry_run and reinit:
        team_journal.init_db(exp.team_db)
        exp.worktree_root.mkdir(parents=True, exist_ok=True)
    return {
        "experiment": exp.name,
        "experiment_root": str(exp.root),
        "dry_run": dry_run,
        "reinitialized": bool(not dry_run and reinit),
        "planned": planned,
        "removed": removed,
        "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clear generated autoresearch run state.")
    parser.add_argument("--experiment", default=experiment_config.DEFAULT_EXPERIMENT, help="experiment name under experiments/")
    parser.add_argument("--experiment-root", type=Path, help="experiment directory containing journal/ and worktrees/")
    parser.add_argument("--yes", action="store_true", help="actually delete generated state; otherwise only print a dry run")
    parser.add_argument("--keep-worktrees", action="store_true", help="leave experiment worktrees in place")
    parser.add_argument("--no-reinit", action="store_true", help="do not recreate an empty team journal after deleting")
    args = parser.parse_args(argv)

    experiment = normalize_experiment_name(args.experiment)
    exp = experiment_config.layout(experiment, args.experiment_root)
    result = clear_runs(
        exp,
        dry_run=not args.yes,
        keep_worktrees=args.keep_worktrees,
        reinit=not args.no_reinit,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
