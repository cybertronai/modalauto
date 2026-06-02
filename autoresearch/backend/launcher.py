#!/usr/bin/env python3
"""Single-command launcher for local autoresearch runs."""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from autoresearch.backend import clear_runs
from autoresearch.backend import experiment_config
from autoresearch.backend import team_journal


REPO_ROOT = experiment_config.AUTORESEARCH_ROOT


@dataclass
class LaunchProcess:
    name: str
    process: subprocess.Popen


def repo_python() -> str:
    return sys.executable


def init_experiment(exp: experiment_config.ExperimentLayout) -> None:
    team_journal.init_db(exp.team_db)
    exp.worktree_root.mkdir(parents=True, exist_ok=True)
    exp.board_dir.mkdir(parents=True, exist_ok=True)


def clear_experiment(
    exp: experiment_config.ExperimentLayout,
    *,
    keep_worktrees: bool,
    stop_timeout: float,
) -> None:
    result = clear_runs.clear_runs(
        exp,
        dry_run=False,
        keep_worktrees=keep_worktrees,
        reinit=True,
        stop_agents=True,
        stop_timeout=stop_timeout,
    )
    removed = len(result.get("removed", []))
    matched = result.get("processes", {}).get("matched", 0)
    print(f"cleared {removed} generated paths; stopped {matched} matching processes")


def start_manager(args: argparse.Namespace, exp: experiment_config.ExperimentLayout) -> LaunchProcess:
    cmd = [
        repo_python(),
        "-m",
        "autoresearch.backend.agent_runtime",
        "topline_manager",
        "--agent-id",
        args.agent_id,
        "--interval",
        str(args.interval),
    ]
    if args.experiment_root:
        cmd.extend(["--experiment-root", str(exp.root)])
    else:
        cmd.extend(["--experiment", exp.name])
    if args.max_steps is not None:
        cmd.extend(["--max-steps", str(args.max_steps)])
    if args.once:
        cmd.append("--once")
    if args.no_apply_scale:
        cmd.append("--no-apply-scale")
    if args.allow_idle_retire:
        cmd.append("--allow-idle-retire")
    if args.completion_stop_on_target:
        cmd.append("--completion-stop-on-target")
    if args.completion_plateau_steps:
        cmd.extend(["--completion-plateau-steps", str(args.completion_plateau_steps)])
    if args.completion_max_seconds:
        cmd.extend(["--completion-max-seconds", str(args.completion_max_seconds)])
    process = subprocess.Popen(cmd, cwd=str(REPO_ROOT))
    return LaunchProcess("manager", process)


def start_frontend(args: argparse.Namespace, exp: experiment_config.ExperimentLayout) -> LaunchProcess:
    cmd = [
        repo_python(),
        str(REPO_ROOT / "frontend" / "scripts" / "serve.py"),
        "--port",
        str(args.port),
        "--host",
        args.host,
        "--default-journal",
        exp.name,
    ]
    process = subprocess.Popen(cmd, cwd=str(REPO_ROOT / "frontend"))
    return LaunchProcess("frontend", process)


def stop_processes(processes: list[LaunchProcess]) -> None:
    for item in processes:
        if item.process.poll() is None:
            item.process.terminate()
    deadline = time.monotonic() + 5
    for item in processes:
        while item.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
    for item in processes:
        if item.process.poll() is None:
            item.process.kill()


def wait_for_processes(processes: list[LaunchProcess]) -> int:
    try:
        manager_done = False
        while True:
            for item in processes:
                code = item.process.poll()
                if code is None:
                    continue
                if item.name == "manager" and not manager_done:
                    manager_done = True
                    print(f"{item.name} exited with status {code}; dashboard is still running", flush=True)
                    if all(other.name == "manager" or other.process.poll() is not None for other in processes):
                        return int(code)
                    continue
                if item.name != "manager":
                    stop_processes([other for other in processes if other is not item])
                    print(f"{item.name} exited with status {code}")
                    return int(code)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nstopping autoresearch processes")
        stop_processes(processes)
        return 130


def forwarded(values: list[str]) -> list[str]:
    return values[1:] if values[:1] == ["--"] else values


def cmd_launch(args: argparse.Namespace) -> int:
    exp = experiment_config.layout(args.experiment, args.experiment_root)
    if args.fresh:
        clear_experiment(exp, keep_worktrees=args.keep_worktrees, stop_timeout=args.stop_timeout)
    else:
        init_experiment(exp)

    if not args.no_frontend:
        print(f"dashboard: http://{args.host}:{args.port}/?journal={exp.name}", flush=True)
    print(f"experiment: {exp.name} ({exp.root})", flush=True)
    print("press Ctrl-C to stop", flush=True)

    processes = [start_manager(args, exp)]
    if not args.no_frontend:
        processes.append(start_frontend(args, exp))
    return wait_for_processes(processes)


def cmd_clean(args: argparse.Namespace) -> int:
    exp = experiment_config.layout(args.experiment, args.experiment_root)
    result = clear_runs.clear_runs(
        exp,
        dry_run=not args.yes,
        keep_worktrees=args.keep_worktrees,
        reinit=not args.no_reinit,
        stop_agents=not args.no_stop_agents,
        stop_timeout=args.stop_timeout,
        debug=args.debug,
    )
    import json

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    exp = experiment_config.layout(args.experiment, args.experiment_root)
    ns = argparse.Namespace(db=exp.team_db, worktree_root=exp.worktree_root, experiment_root=exp.root)
    return team_journal.cmd_status(ns)


def cmd_team(args: argparse.Namespace) -> int:
    return team_journal.main(forwarded(args.team_args))


def cmd_agent(args: argparse.Namespace) -> int:
    from autoresearch.backend import agent_runtime

    return agent_runtime.main(forwarded(args.agent_args))


def cmd_frontend(args: argparse.Namespace) -> int:
    cmd = [repo_python(), str(REPO_ROOT / "frontend" / "scripts" / "serve.py"), *forwarded(args.frontend_args)]
    return subprocess.run(cmd, cwd=str(REPO_ROOT / "frontend")).returncode


def cmd_board(args: argparse.Namespace) -> int:
    from autoresearch.backend import message_board

    return message_board.main(forwarded(args.board_args))


def cmd_memory(args: argparse.Namespace) -> int:
    from autoresearch.backend import research_memory

    return research_memory.main(forwarded(args.memory_args))


def add_experiment_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--experiment", help="experiment name under experiments/")
    parser.add_argument("--experiment-root", type=Path, help="path to an experiment directory")


def add_hidden_parser(subparsers: argparse._SubParsersAction, name: str) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, help=argparse.SUPPRESS)
    subparsers._choices_actions = [
        action for action in subparsers._choices_actions
        if action.dest != name
    ]
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run and manage autoresearch experiments.")
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="{launch,clean,status}")

    launch = sub.add_parser("launch", help="start the manager and dashboard")
    add_experiment_args(launch)
    launch.add_argument("--fresh", action="store_true", help="clear generated run state before launching")
    launch.add_argument("--keep-worktrees", action="store_true", help="do not delete worktrees when using --fresh")
    launch.add_argument("--stop-timeout", type=float, default=5.0, help=argparse.SUPPRESS)
    launch.add_argument("--agent-id", default="manager-main", help=argparse.SUPPRESS)
    launch.add_argument("--interval", type=float, default=5.0, help=argparse.SUPPRESS)
    launch.add_argument("--max-steps", type=int, help="stop the manager after this many manager cycles")
    launch.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    launch.add_argument("--no-apply-scale", action="store_true", help=argparse.SUPPRESS)
    launch.add_argument("--allow-idle-retire", action="store_true", help=argparse.SUPPRESS)
    launch.add_argument("--completion-stop-on-target", action="store_true", help="stop when the experiment workflow target is reached")
    launch.add_argument("--completion-plateau-steps", type=int, default=0, help="stop after this many manager cycles with the same best frontier")
    launch.add_argument("--completion-max-seconds", type=float, default=0.0, help="stop after this many seconds from the first agent start")
    launch.add_argument("--no-frontend", action="store_true")
    launch.add_argument("--host", default="127.0.0.1")
    launch.add_argument("--port", type=int, default=5176)
    launch.set_defaults(func=cmd_launch)

    clean = sub.add_parser("clean", help="clear generated run state")
    add_experiment_args(clean)
    clean.add_argument("--yes", action="store_true", help="actually delete; otherwise print a dry run")
    clean.add_argument("--keep-worktrees", action="store_true")
    clean.add_argument("--no-reinit", action="store_true")
    clean.add_argument("--no-stop-agents", action="store_true")
    clean.add_argument("--stop-timeout", type=float, default=5.0)
    clean.add_argument("--debug", action="store_true")
    clean.set_defaults(func=cmd_clean)

    status = sub.add_parser("status", help="show team status for an experiment")
    add_experiment_args(status)
    status.set_defaults(func=cmd_status)

    team = add_hidden_parser(sub, "team")
    team.add_argument("team_args", nargs=argparse.REMAINDER)
    team.set_defaults(func=cmd_team)

    agent = add_hidden_parser(sub, "agent")
    agent.add_argument("agent_args", nargs=argparse.REMAINDER)
    agent.set_defaults(func=cmd_agent)

    frontend = add_hidden_parser(sub, "frontend")
    frontend.add_argument("frontend_args", nargs=argparse.REMAINDER)
    frontend.set_defaults(func=cmd_frontend)

    board = add_hidden_parser(sub, "board")
    board.add_argument("board_args", nargs=argparse.REMAINDER)
    board.set_defaults(func=cmd_board)

    memory = add_hidden_parser(sub, "memory")
    memory.add_argument("memory_args", nargs=argparse.REMAINDER)
    memory.set_defaults(func=cmd_memory)

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
