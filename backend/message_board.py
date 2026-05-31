#!/usr/bin/env python3
"""Filesystem message board for direct agent communication.

This is intentionally separate from the SQLite journal. The journal is durable
state; this board is lightweight coordination: handoffs, nudges, stop requests,
and manager scale actions. Messages are append-only JSONL files under
an experiment's `journal/messages/` directory.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from autoresearch.backend import experiment_config


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOARD = experiment_config.DEFAULT_BOARD


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def channel_path(board: Path, channel: str) -> Path:
    safe = channel.replace("/", "__")
    return board / f"{safe}.jsonl"


def ack_path(board: Path, agent_id: str) -> Path:
    return board / "acks" / f"{agent_id}.json"


def read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def load_acks(board: Path, agent_id: str) -> dict[str, int]:
    path = ack_path(board, agent_id)
    if not path.exists():
        return {}
    text = path.read_text().strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def save_acks(board: Path, agent_id: str, acks: dict[str, int]) -> None:
    path = ack_path(board, agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(acks, indent=2, sort_keys=True) + "\n")


def cmd_post(args: argparse.Namespace) -> int:
    args.board.mkdir(parents=True, exist_ok=True)
    message = {
        "id": args.message_id or f"msg-{uuid4().hex[:12]}",
        "created_at": now(),
        "from": args.sender,
        "to": args.to,
        "channel": args.channel,
        "kind": args.kind,
        "body": args.body,
        "payload": json.loads(args.payload_json),
    }
    path = channel_path(args.board, args.channel)
    with path.open("a") as f:
        f.write(json.dumps(message, sort_keys=True) + "\n")
    print(json.dumps(message, indent=2, sort_keys=True))
    return 0


def cmd_tail(args: argparse.Namespace) -> int:
    path = channel_path(args.board, args.channel)
    rows = read_jsonl(path)
    for row in rows[-args.limit:]:
        print(json.dumps(row, sort_keys=True))
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    args.board.mkdir(parents=True, exist_ok=True)
    acks = load_acks(args.board, args.agent_id)
    channels = args.channels or ["global", f"agent:{args.agent_id}"]
    out = []
    for channel in channels:
        rows = read_jsonl(channel_path(args.board, channel))
        start = acks.get(channel, 0)
        for idx, row in enumerate(rows[start:], start=start):
            to = row.get("to")
            if to in (None, "", args.agent_id, "all") or channel == f"agent:{args.agent_id}":
                item = dict(row)
                item["_channel_index"] = idx + 1
                out.append(item)
    for row in out[-args.limit:]:
        print(json.dumps(row, sort_keys=True))
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    args.board.mkdir(parents=True, exist_ok=True)
    acks = load_acks(args.board, args.agent_id)
    channels = args.channels or ["global", f"agent:{args.agent_id}"]
    for channel in channels:
        acks[channel] = len(read_jsonl(channel_path(args.board, channel)))
    save_acks(args.board, args.agent_id, acks)
    print(json.dumps({"agent_id": args.agent_id, "acks": acks}, indent=2, sort_keys=True))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    seen = 0
    deadline = time.time() + args.seconds if args.seconds else None
    while True:
        rows = read_jsonl(channel_path(args.board, args.channel))
        for row in rows[seen:]:
            print(json.dumps(row, sort_keys=True), flush=True)
        seen = len(rows)
        if deadline is not None and time.time() >= deadline:
            break
        time.sleep(args.interval)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", help="experiment name under experiments/")
    parser.add_argument("--experiment-root", type=Path, help="experiment directory containing journal/ and worktrees/")
    parser.add_argument("--board", type=Path)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_post = sub.add_parser("post")
    p_post.add_argument("--message-id")
    p_post.add_argument("--sender", required=True)
    p_post.add_argument("--to", default="all")
    p_post.add_argument("--channel", default="global")
    p_post.add_argument("--kind", default="note")
    p_post.add_argument("--body", default="")
    p_post.add_argument("--payload-json", default="{}")
    p_post.set_defaults(func=cmd_post)

    p_tail = sub.add_parser("tail")
    p_tail.add_argument("--channel", default="global")
    p_tail.add_argument("--limit", type=int, default=20)
    p_tail.set_defaults(func=cmd_tail)

    p_inbox = sub.add_parser("inbox")
    p_inbox.add_argument("--agent-id", required=True)
    p_inbox.add_argument("--channels", nargs="*")
    p_inbox.add_argument("--limit", type=int, default=50)
    p_inbox.set_defaults(func=cmd_inbox)

    p_ack = sub.add_parser("ack")
    p_ack.add_argument("--agent-id", required=True)
    p_ack.add_argument("--channels", nargs="*")
    p_ack.set_defaults(func=cmd_ack)

    p_watch = sub.add_parser("watch")
    p_watch.add_argument("--channel", default="global")
    p_watch.add_argument("--interval", type=float, default=1.0)
    p_watch.add_argument("--seconds", type=float)
    p_watch.set_defaults(func=cmd_watch)

    args = parser.parse_args(argv)
    exp = experiment_config.layout(args.experiment, args.experiment_root)
    args.experiment_root = exp.root
    args.board = (args.board or exp.board_dir).expanduser().resolve()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
