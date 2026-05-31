#!/usr/bin/env python3
"""Experiment layout helpers for autoresearch runs."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path


AUTORESEARCH_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = AUTORESEARCH_ROOT
EXPERIMENTS_ROOT = AUTORESEARCH_ROOT / "experiments"
DEFAULT_EXPERIMENT = "matmul"


@dataclass(frozen=True)
class ExperimentLayout:
    root: Path
    name: str
    journal_dir: Path
    team_db: Path
    research_db: Path
    board_dir: Path
    worktree_root: Path
    workflow_path: Path


def _slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip().lower())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or DEFAULT_EXPERIMENT


def experiment_root(experiment: str | None = None, root: Path | None = None) -> Path:
    if root is not None:
        return root.expanduser().resolve()

    env_root = os.environ.get("AUTORESEARCH_EXPERIMENT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    name = experiment or os.environ.get("AUTORESEARCH_EXPERIMENT") or DEFAULT_EXPERIMENT
    return (EXPERIMENTS_ROOT / _slug(name)).resolve()


def layout(experiment: str | None = None, root: Path | None = None) -> ExperimentLayout:
    exp_root = experiment_root(experiment=experiment, root=root)
    name = exp_root.name
    journal_dir = exp_root / "journal"
    return ExperimentLayout(
        root=exp_root,
        name=name,
        journal_dir=journal_dir,
        team_db=journal_dir / "team_journal.db",
        research_db=journal_dir / "research_memory.db",
        board_dir=journal_dir / "messages",
        worktree_root=exp_root / "worktrees",
        workflow_path=exp_root / "workflow.json",
    )


def load_workflow(workflow_path: Path) -> dict:
    if not workflow_path.exists():
        return {}
    return json.loads(workflow_path.read_text())


def render_workflow_args(values: list[str], exp: ExperimentLayout) -> list[str]:
    replacements = {
        "experiment_root": str(exp.root),
        "journal": str(exp.journal_dir),
        "worktrees": str(exp.worktree_root),
        "workflow": str(exp.workflow_path),
    }
    return [str(value).format(**replacements) for value in values]


DEFAULT_LAYOUT = layout()
DEFAULT_JOURNAL_DIR = DEFAULT_LAYOUT.journal_dir
DEFAULT_TEAM_DB = DEFAULT_LAYOUT.team_db
DEFAULT_RESEARCH_DB = DEFAULT_LAYOUT.research_db
DEFAULT_BOARD = DEFAULT_LAYOUT.board_dir
DEFAULT_WORKTREE_ROOT = DEFAULT_LAYOUT.worktree_root
