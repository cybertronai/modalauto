#!/usr/bin/env python3
"""Clear generated autoresearch experiment run state."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from autoresearch.backend import experiment_config
from autoresearch.backend import team_journal


EXPERIMENT_ALIASES = {
    "matmall": "matmul",
}

FRONTEND_EXPORTS = {
    "real-data.js",
    "real-runs.js",
}


@dataclass(frozen=True)
class ClearTarget:
    path: Path
    label: str


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    command: str


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
    targets.extend(frontend_export_targets())
    if not keep_worktrees:
        targets.append(ClearTarget(exp.worktree_root, "agent worktrees"))
    return unique_targets(targets)


def frontend_export_targets() -> list[ClearTarget]:
    frontend = experiment_config.AUTORESEARCH_ROOT / "frontend"
    return [ClearTarget(frontend / name, "frontend export") for name in sorted(FRONTEND_EXPORTS)]


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
    frontend = (experiment_config.AUTORESEARCH_ROOT / "frontend").resolve()
    if path.parent == frontend and path.name in FRONTEND_EXPORTS:
        return True
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
    delay = 0.1
    for attempt in range(6):
        try:
            if not path.exists() and not path.is_symlink():
                return
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 5:
                raise
            time.sleep(delay)
            delay *= 2


def list_processes() -> list[ProcessInfo]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    processes = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        processes.append(ProcessInfo(pid, parts[1]))
    return processes


def is_experiment_process(process: ProcessInfo, exp: experiment_config.ExperimentLayout) -> bool:
    if process.pid == os.getpid():
        return False
    command = process.command
    if "autoresearch-agent" not in command and "/loop.py" not in command:
        return False
    exp_root = str(exp.root.expanduser().resolve())
    needles = [
        exp_root,
        f"--experiment {exp.name}",
        f"--experiment={exp.name}",
        str(exp.team_db),
        str(exp.research_db),
        str(exp.journal_dir),
        str(exp.worktree_root),
    ]
    return any(needle in command for needle in needles)


def experiment_processes(exp: experiment_config.ExperimentLayout) -> list[ProcessInfo]:
    return [process for process in list_processes() if is_experiment_process(process, exp)]


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_exit(processes: list[ProcessInfo], timeout: float) -> list[ProcessInfo]:
    deadline = time.monotonic() + max(0.0, timeout)
    remaining = processes
    while remaining and time.monotonic() < deadline:
        time.sleep(0.1)
        remaining = [process for process in remaining if process_alive(process.pid)]
    return remaining


def process_role(command: str) -> str:
    match = re.search(r"autoresearch-agent\s+([A-Za-z0-9_-]+)", command)
    if match:
        return match.group(1)
    if "/loop.py" in command:
        return "loop"
    return "process"


def command_arg(command: str, name: str) -> str | None:
    match = re.search(rf"{re.escape(name)}(?:=|\s+)([^\s]+)", command)
    return match.group(1) if match else None


def process_summary(process: ProcessInfo, *, debug: bool = False) -> dict[str, object]:
    item: dict[str, object] = {
        "pid": process.pid,
        "role": process_role(process.command),
    }
    agent_id = command_arg(process.command, "--agent-id")
    run_id = command_arg(process.command, "--run-id")
    if agent_id:
        item["agent_id"] = agent_id
    if run_id:
        item["run_id"] = run_id
    if debug:
        item["command"] = process.command
    return item


def stop_experiment_processes(
    exp: experiment_config.ExperimentLayout,
    *,
    timeout: float,
    dry_run: bool,
    debug: bool,
) -> dict[str, object]:
    processes = experiment_processes(exp)
    summary = {
        "enabled": True,
        "dry_run": dry_run,
        "matched": len(processes),
        "processes": [process_summary(process, debug=debug) for process in processes],
        "terminated": [],
        "killed": [],
        "remaining": [],
    }
    if dry_run or not processes:
        return summary

    for process in processes:
        try:
            os.kill(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    remaining = wait_for_exit(processes, timeout)
    terminated = [process for process in processes if not process_alive(process.pid)]
    summary["terminated"] = [process_summary(process, debug=debug) for process in terminated]

    for process in remaining:
        try:
            os.kill(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    still_remaining = wait_for_exit(remaining, 2.0)
    killed = [process for process in remaining if not process_alive(process.pid)]
    summary["killed"] = [process_summary(process, debug=debug) for process in killed]
    summary["remaining"] = [process_summary(process, debug=debug) for process in still_remaining]
    return summary


def clear_runs(
    exp: experiment_config.ExperimentLayout,
    *,
    dry_run: bool = True,
    keep_worktrees: bool = False,
    reinit: bool = True,
    stop_agents: bool = True,
    stop_timeout: float = 5.0,
    debug: bool = False,
) -> dict[str, object]:
    stop_result = {"enabled": False}
    if stop_agents:
        stop_result = stop_experiment_processes(exp, timeout=stop_timeout, dry_run=dry_run, debug=debug)
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
        "processes": stop_result,
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
    parser.add_argument("--no-stop-agents", action="store_true", help="do not stop local agents for this experiment before deleting")
    parser.add_argument("--stop-timeout", type=float, default=5.0, help="seconds to wait after SIGTERM before SIGKILL")
    parser.add_argument("--debug", action="store_true", help="include full matched process commands in the JSON output")
    args = parser.parse_args(argv)

    experiment = normalize_experiment_name(args.experiment)
    exp = experiment_config.layout(experiment, args.experiment_root)
    result = clear_runs(
        exp,
        dry_run=not args.yes,
        keep_worktrees=args.keep_worktrees,
        reinit=not args.no_reinit,
        stop_agents=not args.no_stop_agents,
        stop_timeout=args.stop_timeout,
        debug=args.debug,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
