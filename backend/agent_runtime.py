#!/usr/bin/env python3
"""Autonomous agent runtime for autoresearch experiments.

Each process owns one role and one worktree. Agents communicate through the
message board, persist durable work to the team journal, and heartbeat every
cycle so the manager can recover stale claims.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from autoresearch.backend import experiment_config
from autoresearch.backend import message_board
from autoresearch.backend import research_memory
from autoresearch.backend import team_journal
from autoresearch.experiments.matmul.matmul import matmul
from autoresearch.experiments.matmul.loop import buckets, verify_general


REPO_ROOT = experiment_config.AUTORESEARCH_ROOT
ROLE_ALIASES = {
    "creative-explorer": "creative_explorer",
    "topline-manager": "topline_manager",
    "global-searcher": "global_searcher",
}
ROLES = {
    "creative_explorer",
    "implementor",
    "verifier",
    "topline_manager",
    "global_searcher",
    "researcher",
    "insight_generator",
    "meta_agent",
}


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def normalize_role(role: str) -> str:
    role = ROLE_ALIASES.get(role, role)
    if role not in ROLES:
        raise SystemExit(f"unknown role: {role}")
    return role


def agent_prefix(role: str) -> str:
    return {
        "creative_explorer": "explorer",
        "implementor": "impl",
        "verifier": "verifier",
        "topline_manager": "manager",
        "global_searcher": "global",
        "researcher": "researcher",
        "insight_generator": "insight",
        "meta_agent": "meta",
    }[role]


def default_agent_id(role: str) -> str:
    return f"{agent_prefix(role)}-{uuid4().hex[:8]}"


def fresh_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def runner_command(args: argparse.Namespace, run_id: str, hyp_path: Path) -> list[str]:
    workflow = experiment_config.load_workflow(args.workflow_path)
    runner = workflow.get("runner", {}) if isinstance(workflow, dict) else {}
    command = runner.get("command") or "experiments/matmul/loop.py"
    command_path = Path(command)
    if not command_path.is_absolute():
        command_path = REPO_ROOT / command_path
    python_executable = sys.executable
    venv = workflow.get("venv") if isinstance(workflow, dict) else None
    if venv:
        candidate = Path(str(venv)).expanduser() / "bin" / "python"
        if candidate.exists():
            python_executable = str(candidate)
    base_args = experiment_config.render_workflow_args(runner.get("args", []), args.experiment_layout)
    return [
        python_executable,
        str(command_path),
        *base_args,
        "--run-id",
        run_id,
        "--hypothesis-json",
        str(hyp_path),
        "--journal-root",
        str(args.journal_root),
        "--verify-cases",
        "4",
        "--verify-top",
        "3",
    ]


def connect_team(args: argparse.Namespace):
    if not args.db.exists():
        team_journal.init_db(args.db)
    return team_journal.connect(args.db)


def post(board: Path, sender: str, channel: str, kind: str, body: str = "",
         payload: dict | None = None, to: str = "all") -> None:
    board.mkdir(parents=True, exist_ok=True)
    msg = {
        "id": f"msg-{uuid4().hex[:12]}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "from": sender,
        "to": to,
        "channel": channel,
        "kind": kind,
        "body": body,
        "payload": payload or {},
    }
    with message_board.channel_path(board, channel).open("a") as f:
        f.write(json.dumps(msg, sort_keys=True) + "\n")


def unread_messages(board: Path, agent_id: str) -> list[dict[str, object]]:
    board.mkdir(parents=True, exist_ok=True)
    acks = message_board.load_acks(board, agent_id)
    channels = ["global", "manager-actions", f"agent:{agent_id}"]
    out = []
    for channel in channels:
        rows = message_board.read_jsonl(message_board.channel_path(board, channel))
        start = acks.get(channel, 0)
        for idx, row in enumerate(rows[start:], start=start):
            to = row.get("to")
            if to in (None, "", "all", agent_id) or channel == f"agent:{agent_id}":
                item = dict(row)
                item["_channel_index"] = idx + 1
                out.append(item)
    return out


def ack_messages(board: Path, agent_id: str) -> None:
    channels = ["global", "manager-actions", f"agent:{agent_id}"]
    acks = message_board.load_acks(board, agent_id)
    for channel in channels:
        acks[channel] = len(message_board.read_jsonl(message_board.channel_path(board, channel)))
    message_board.save_acks(board, agent_id, acks)


def should_stop(args: argparse.Namespace) -> bool:
    stop = False
    for msg in unread_messages(args.board, args.agent_id):
        if msg.get("kind") in {"stop", "retire", "pause"}:
            stop = True
    ack_messages(args.board, args.agent_id)
    return stop


def recent_global_stop(board: Path, window_seconds: int) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    rows = message_board.read_jsonl(message_board.channel_path(board, "global"))
    for msg in rows:
        if msg.get("kind") not in {"stop", "retire", "pause"}:
            continue
        created = parse_message_time(msg.get("created_at"))
        if created is not None and created >= cutoff:
            return True
    return False


def parse_message_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def action_counts(actions: list[dict[str, object]]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for action in actions:
        if action.get("skipped"):
            continue
        key = (str(action.get("action")), str(action.get("role")))
        counts[key] = counts.get(key, 0) + 1
    return counts


def recent_peer_intent_counts(args: argparse.Namespace, window_seconds: int = 5) -> dict[tuple[str, str], int]:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    counts: dict[tuple[str, str], int] = {}
    rows = message_board.read_jsonl(message_board.channel_path(args.board, "manager-actions"))
    for msg in rows:
        if msg.get("from") == args.agent_id or msg.get("kind") not in {"scale_intent", "scale_applied"}:
            continue
        created = parse_message_time(msg.get("created_at"))
        if created is None or created < cutoff:
            continue
        payload = msg.get("payload") or {}
        actions = payload.get("actions") if msg.get("kind") == "scale_intent" else payload.get("applied")
        if not isinstance(actions, list):
            continue
        for key, n in action_counts(actions).items():
            counts[key] = counts.get(key, 0) + n
    return counts


def subtract_peer_intents(actions: list[dict[str, object]], peer_counts: dict[tuple[str, str], int]) -> list[dict[str, object]]:
    remaining = dict(peer_counts)
    out = []
    for action in actions:
        key = (str(action.get("action")), str(action.get("role")))
        if remaining.get(key, 0) > 0:
            remaining[key] -= 1
            continue
        out.append(action)
    return out


def action_lock_key(action: dict[str, object], slot: int) -> str:
    return f"scale:{action.get('action')}:{action.get('role')}:{slot}"


def register_agent(args: argparse.Namespace) -> Path:
    db = connect_team(args)
    stamp = team_journal.now()
    worktree = args.worktree or args.worktree_root / args.agent_id
    worktree.mkdir(parents=True, exist_ok=True)
    db.execute(
        """
        INSERT OR REPLACE INTO agents
            (id, role, team_id, status, worktree_path, lease_expires,
             last_heartbeat, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            args.agent_id,
            args.role,
            args.team_id,
            "idle",
            str(worktree),
            team_journal.lease_deadline(),
            stamp,
            stamp,
            stamp,
        ),
    )
    db.commit()
    db.close()
    return worktree


def heartbeat(args: argparse.Namespace, status: str | None = None) -> None:
    db = connect_team(args)
    team_journal.heartbeat_agent(db, args.agent_id, status)
    db.commit()
    db.close()


def finish(args: argparse.Namespace, status: str = "dead") -> None:
    db = connect_team(args)
    stamp = team_journal.now()
    db.execute(
        """
        UPDATE agents
        SET status = ?, current_item = NULL, last_heartbeat = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, stamp, stamp, args.agent_id),
    )
    db.commit()
    db.close()


def frontier_parent_id(best: dict | None) -> str | None:
    if not isinstance(best, dict):
        return None
    value = best.get("hypothesis_id")
    return str(value) if value else None


def branch_is_halted(db, hyp_id: str | None) -> bool:
    if not hyp_id:
        return False
    has_table = db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'branch_controls'"
    ).fetchone()
    if has_table is None:
        return False
    seen: set[str] = set()
    current = hyp_id
    while current and current not in seen:
        seen.add(current)
        row = db.execute(
            """
            SELECT h.parent_hypothesis_id, bc.status
            FROM hypotheses h
            LEFT JOIN branch_controls bc ON bc.branch_id = h.id AND bc.status = 'halted'
            WHERE h.id = ?
            """,
            (current,),
        ).fetchone()
        if row is None:
            return False
        if row["status"] == "halted":
            return True
        current = row["parent_hypothesis_id"]
    return False


def usable_frontier_parent(db, best: dict | None) -> str | None:
    parent = frontier_parent_id(best)
    return None if branch_is_halted(db, parent) else parent


def parse_json_object(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def successful_donors(db, limit: int = 12) -> list[dict]:
    rows = db.execute(
        """
        SELECT h.id AS hypothesis_id, h.title, h.context_json,
               s.id AS submission_id, s.candidate_summary_json,
               v.official_score
        FROM verifications v
        JOIN submissions s ON s.id = v.submission_id
        JOIN hypotheses h ON h.id = s.hypothesis_id
        WHERE v.decision = 'accept' AND v.official_score IS NOT NULL
        ORDER BY v.official_score ASC, v.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    donors = []
    for row in rows:
        context = parse_json_object(row["context_json"])
        summary = parse_json_object(row["candidate_summary_json"])
        implementation = context.get("implementation") if isinstance(context.get("implementation"), dict) else {}
        best = summary.get("best") if isinstance(summary.get("best"), dict) else {}
        donors.append({
            "hypothesis_id": row["hypothesis_id"],
            "title": row["title"],
            "submission_id": row["submission_id"],
            "official_score": row["official_score"],
            "family": str(best.get("family") or "unknown"),
            "candidate": str(best.get("name") or ""),
            "implementation": implementation,
            "summary_best": best,
        })
    return donors


def implementation_signature(implementation: dict) -> dict:
    return {
        "operator": implementation.get("operator"),
        "tool_kind": implementation.get("tool_kind")
        or (implementation.get("base_capability") or {}).get("tool_kind")
        if isinstance(implementation.get("base_capability"), dict)
        else None,
        "reuse_goal": implementation.get("reuse_goal"),
        "loop_order": implementation.get("loop_order"),
        "tile": implementation.get("tile"),
        "low_address_roles": implementation.get("low_address_roles"),
    }


def transfer_payload(parent_hypothesis_id: str | None, donors: list[dict], dominant_family: str) -> dict | None:
    if len(donors) < 2:
        return None
    recipient = next((item for item in donors if item["hypothesis_id"] == parent_hypothesis_id), donors[0])
    donor = next(
        (
            item
            for item in donors
            if item["hypothesis_id"] != recipient["hypothesis_id"]
            and (
                item["family"] != recipient["family"]
                or implementation_signature(item["implementation"]) != implementation_signature(recipient["implementation"])
            )
        ),
        None,
    )
    if donor is None:
        return None
    donor_impl = dict(donor["implementation"])
    recipient_impl = recipient["implementation"] if isinstance(recipient["implementation"], dict) else {}
    merged_impl = dict(donor_impl)
    merged_impl.setdefault("operator", donor_impl.get("operator") or recipient_impl.get("operator") or "enumerate_schedule_family")
    merged_impl["transfer_from"] = donor["hypothesis_id"]
    merged_impl["transfer_to"] = recipient["hypothesis_id"]
    merged_impl["recipient_frontier"] = implementation_signature(recipient_impl)
    merged_impl["dominant_family"] = dominant_family
    return {
        "implementation": merged_impl,
        "evolution": {
            "event": "horizontal_transfer",
            "donor_hypothesis_id": donor["hypothesis_id"],
            "recipient_hypothesis_id": recipient["hypothesis_id"],
            "donor_family": donor["family"],
            "recipient_family": recipient["family"],
            "donor_score": donor["official_score"],
            "recipient_score": recipient["official_score"],
            "transferred": implementation_signature(donor_impl),
            "reason": "recombine a distinct successful branch into the current frontier during plateau",
        },
    }


def propose_hypothesis(args: argparse.Namespace, radical: bool = False) -> dict[str, str]:
    db = connect_team(args)
    existing = db.execute("SELECT COUNT(*) AS n FROM hypotheses").fetchone()["n"]
    best = team_journal.best_frontier(db)
    parent_hypothesis_id = usable_frontier_parent(db, best) if existing else None
    if radical and args.allow_seeded_strategies:
        templates = [
            (
                "Reorient to sA-cache schedule",
                "The current panel loop is plateaued because it does not make the hottest A read cost 1.",
                "Use a concrete sA-cache generator with B scratch lanes and low-address accumulators.",
                "sa_cache",
            ),
            (
                "Reorient to dead-input/output reuse",
                "The best prior direction reuses dead A/B storage for output cells and reduces final output read cost.",
                "Use a concrete dead-input-output packed generator.",
                "dead_io",
            ),
            (
                "Global reorientation batch",
                "Run the strongest non-baseline strategy set when the frontier has not improved.",
                "Compare sA-cache and dead-input-output reuse in one fast batch.",
                "global_reorient",
            ),
        ]
    elif radical:
        templates = [
            (
                "Search hot-read scratch layouts from first principles",
                "The scorer rewards repeatedly-read values at low addresses. Try a loop order that reuses one input value across several multiplies and gives that live value the cheapest address.",
                "Improve mul/copy read cost by moving a high-reuse temporary into the lowest scratch address.",
                {
                    "operator": "schedule_from_reasoning",
                    "loop_order": ["i_block", "j_block", "k", "i_inner", "j_inner"],
                    "tile": {"i": 8, "j": 4},
                    "low_address_roles": ["single_input_cache", "multiply_tmp", "other_input_strip", "accumulators"],
                    "reuse_goal": "hold one A value while sweeping several B columns",
                },
            ),
            (
                "Search liveness-safe output aliasing from first principles",
                "Some input cells become dead before final output emission; test conservative aliasing candidates generated locally.",
                "Reduce output storage/read cost without copying a prior packed scheme.",
                {
                    "operator": "schedule_from_reasoning",
                    "loop_order": ["i_block", "j_block", "k", "j_inner", "i_inner"],
                    "tile": {"i": 4, "j": 8},
                    "low_address_roles": ["other_input_cache", "multiply_tmp", "input_strip", "accumulators"],
                    "reuse_goal": "hold one B value while sweeping several A rows",
                },
            ),
            (
                "Global scratch schedule reorientation",
                "The baseline panel loop is plateaued; run broad local schedule enumeration over cache dimensions and low-address scratch shapes.",
                "Find a better schedule through generic parameterized enumeration.",
                {
                    "operator": "enumerate_schedule_family",
                    "tiles": [{"i": 8, "j": 4}, {"i": 4, "j": 8}, {"i": 4, "j": 4}],
                    "reuse_goals": ["hold_A_sweep_B", "hold_B_sweep_A"],
                    "low_address_roles": ["input_cache", "multiply_tmp", "strip", "accumulators"],
                },
            ),
        ]
    else:
        templates = [
            (
                "Enumerate a new rectangular panel family",
                "Small panel schedules are cheap to test and expose copy/mul/add bucket tradeoffs.",
                "Find a panel shape with lower total verified score than current blind baseline.",
                {"operator": "enumerate_panels"},
            ),
            (
                "Test a low-address single-input cache from local frontier",
                "The local frontier has high repeated operand reads; test a generic single-input cache schedule.",
                "Try holding one input value across multiple products.",
                {
                    "operator": "schedule_from_reasoning",
                    "loop_order": ["i_block", "j_block", "k", "i_inner", "j_inner"],
                    "tile": {"i": 4, "j": 4},
                    "low_address_roles": ["single_input_cache", "multiply_tmp", "other_input_strip", "accumulators"],
                    "reuse_goal": "hold one A value while sweeping several B columns",
                },
            ),
        ]
    title, rationale, movement, plan = templates[existing % len(templates)]
    strategy = plan if isinstance(plan, str) else "reasoned"
    operator_payload = {"strategy": strategy} if isinstance(plan, str) else plan
    hyp_id = fresh_id("hyp")
    stamp = team_journal.now()
    db.execute(
        """
        INSERT INTO hypotheses
            (id, team_id, proposer_agent_id, parent_hypothesis_id, priority, title, rationale,
             expected_movement, context_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hyp_id,
            args.team_id,
            args.agent_id,
            parent_hypothesis_id,
            10 if radical else 5,
            title,
            rationale,
            movement,
            json.dumps({
                "radical": radical,
                "source": args.role,
                "implementation": operator_payload,
                "parent_source": "best_frontier" if parent_hypothesis_id else None,
                "best_frontier": best,
            }),
            stamp,
            stamp,
        ),
    )
    db.commit()
    db.close()
    post(args.board, args.agent_id, "global", "hypothesis", title, {"hypothesis_id": hyp_id})
    return {"hypothesis_id": hyp_id, "title": title, "strategy": strategy}


def run_creative_step(args: argparse.Namespace) -> None:
    heartbeat(args, "working")
    result = propose_hypothesis(args, radical=False)
    post(args.board, args.agent_id, "manager-actions", "update", "creative hypothesis queued", result)
    heartbeat(args, "idle")


def run_global_searcher_step(args: argparse.Namespace) -> None:
    heartbeat(args, "working")
    result = propose_hypothesis(args, radical=True)
    post(args.board, args.agent_id, "global", "radical_hypothesis", result["title"], result)
    heartbeat(args, "idle")


def run_insight_generator_step(args: argparse.Namespace) -> None:
    heartbeat(args, "working")
    db = connect_team(args)
    best = team_journal.best_frontier(db)
    parent_hypothesis_id = usable_frontier_parent(db, best)
    recent = db.execute(
        """
        SELECT h.title, h.context_json, s.candidate_summary_json
        FROM submissions s
        JOIN hypotheses h ON h.id = s.hypothesis_id
        ORDER BY s.created_at DESC
        LIMIT 20
        """
    ).fetchall()
    families: dict[str, int] = {}
    scores: list[int] = []
    for row in recent:
        try:
            summary = json.loads(row["candidate_summary_json"])
            family = str(summary.get("best", {}).get("family", "unknown"))
            score = int(summary.get("best", {}).get("score", 0))
        except Exception:
            continue
        families[family] = families.get(family, 0) + 1
        if score:
            scores.append(score)
    hyp_id = fresh_id("hyp")
    stamp = team_journal.now()
    insight = {
        "operator": "enumerate_panels",
        "insight": "If many recent submissions share the same family/score, force broader loop-order and address-layout reasoning before more implementation.",
        "best_frontier": best,
        "recent_families": families,
        "recent_best": min(scores) if scores else None,
    }
    db.execute(
        """
        INSERT INTO hypotheses
            (id, team_id, proposer_agent_id, parent_hypothesis_id, priority, title, rationale,
             expected_movement, context_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hyp_id,
            args.team_id,
            args.agent_id,
            parent_hypothesis_id,
            7,
            "Insight: plateau requires new loop/address heuristic",
            "Analyze recent submissions and add a simplification heuristic without committing to one named solution.",
            "Push global search away from duplicate families and toward reusable value/address-cost heuristics.",
            json.dumps({"source": args.role, "implementation": insight, "parent_source": "best_frontier"}),
            stamp,
            stamp,
        ),
    )
    db.commit()
    db.close()
    post(args.board, args.agent_id, "global", "insight", "plateau heuristic generated", {
        "hypothesis_id": hyp_id,
        "recent_families": families,
        "best_frontier": best,
    })
    heartbeat(args, "idle")


def build_meta_tool(args: argparse.Namespace, operator: dict[str, object]) -> tuple[str, Path]:
    tool_id = fresh_id("tool")
    tool_dir = args.worktree_root / args.agent_id / "tools" / tool_id
    tool_dir.mkdir(parents=True, exist_ok=True)
    tool_path = tool_dir / "generate_hypothesis.py"
    tool_kind = str(operator.get("tool_kind") or "hot_read_schedule")
    if tool_kind == "alternate_representation":
        tool_source = '''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capability-json", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    capability = json.loads(args.capability_json.read_text())
    payload = {
        "operator": "enumerate_schedule_family",
        "generated_by": "meta_tool",
        "source_capability": capability,
        "tiles": [{"i": 4, "j": 8}, {"i": 8, "j": 2}, {"i": 2, "j": 8}],
        "reuse_goals": ["hold_B_sweep_A", "change_loop_order", "avoid_dominant_family"],
        "low_address_roles": ["alternate_input_cache", "temporary_product", "accumulators"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n")
    print(json.dumps({"out": str(args.out), "operator": payload["operator"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    elif tool_kind == "reuse_axis_swap":
        tool_source = '''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capability-json", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    capability = json.loads(args.capability_json.read_text())
    payload = {
        "operator": "schedule_from_reasoning",
        "generated_by": "meta_tool",
        "source_capability": capability,
        "loop_order": ["j_block", "i_block", "k", "j_inner", "i_inner"],
        "tile": {"i": 4, "j": 8},
        "reuse_goal": "hold one B value while sweeping several A rows",
        "low_address_roles": ["most_reused_live_value", "temporary_product", "small_operand_strip", "accumulators"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n")
    print(json.dumps({"out": str(args.out), "operator": payload["operator"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    elif tool_kind == "layout_mutation":
        tool_source = '''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capability-json", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    capability = json.loads(args.capability_json.read_text())
    payload = {
        "operator": "enumerate_schedule_family",
        "generated_by": "meta_tool",
        "source_capability": capability,
        "tiles": [{"i": 2, "j": 8}, {"i": 8, "j": 2}, {"i": 4, "j": 4}],
        "reuse_goals": ["minimize_tmp_read_cost", "move_accumulators_lower", "shuffle_bulk_layout"],
        "low_address_roles": ["temporary_product", "accumulators", "input_cache"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n")
    print(json.dumps({"out": str(args.out), "operator": payload["operator"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    elif tool_kind == "liveness_reuse_probe":
        tool_source = '''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capability-json", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    capability = json.loads(args.capability_json.read_text())
    payload = {
        "operator": "enumerate_schedule_family",
        "generated_by": "meta_tool",
        "source_capability": capability,
        "tiles": [{"i": 4, "j": 8}, {"i": 8, "j": 4}],
        "reuse_goals": ["detect_dead_storage", "probe_output_aliasing", "reduce_output_read_cost"],
        "low_address_roles": ["dead_storage_candidates", "accumulators", "temporary_product"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n")
    print(json.dumps({"out": str(args.out), "operator": payload["operator"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    elif tool_kind == "verification_relief":
        tool_source = '''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capability-json", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    capability = json.loads(args.capability_json.read_text())
    payload = {
        "operator": "enumerate_panels",
        "generated_by": "meta_tool",
        "source_capability": capability,
        "tool_note": "verification backlog present; emit cheap conservative baseline candidate",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n")
    print(json.dumps({"out": str(args.out), "operator": payload["operator"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    else:
        tool_source = '''#!/usr/bin/env python3
"""Generated local meta tool.

Reads a generic capability spec and emits a concrete hypothesis JSON for the
domain implementor. This file is intentionally local to one run/worktree.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capability-json", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    capability = json.loads(args.capability_json.read_text())
    diversity = capability.get("diversity", {})
    dominant = diversity.get("dominant_family")
    # Generic transformation rule: if one family dominates, ask the
    # implementor to generate a different representation that changes reuse
    # and address placement. Domain code decides how to instantiate it.
    payload = {
        "operator": "schedule_from_reasoning",
        "generated_by": "meta_tool",
        "source_capability": capability,
        "avoid_family": dominant,
        "loop_order": ["i_block", "j_block", "k", "i_inner", "j_inner"],
        "tile": {"i": 8, "j": 4},
        "reuse_goal": "hold a frequently reused live value while sweeping the other operand",
        "low_address_roles": [
            "most_reused_live_value",
            "temporary_product",
            "small_operand_strip",
            "accumulators"
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n")
    print(json.dumps({"out": str(args.out), "operator": payload["operator"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    tool_path.write_text(tool_source)
    tool_path.chmod(0o755)
    (tool_dir / "capability.json").write_text(json.dumps(operator, indent=2, sort_keys=True) + "\n")
    return tool_id, tool_path


def tool_signature(operator: dict[str, object]) -> str:
    payload = {
        "tool_kind": operator.get("tool_kind"),
        "proposed_capability": operator.get("proposed_capability"),
        "interface": operator.get("interface"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def choose_tool_kind(
    db,
    dominant_family: str,
    bottlenecks: list[str],
    best_score: int,
) -> str:
    active = db.execute(
        """
        SELECT interface_json, score_hint
        FROM promoted_tools
        WHERE status = 'active'
        ORDER BY created_at DESC
        LIMIT 12
        """
    ).fetchall()
    seen: set[str] = set()
    plateau_same_score = 0
    for row in active:
        try:
            interface = json.loads(row["interface_json"])
            kind = str(interface.get("tool_kind") or "hot_read_schedule")
        except Exception:
            kind = "hot_read_schedule"
        seen.add(kind)
        if row["score_hint"] == best_score:
            plateau_same_score += 1
    exploration_order = [
        "hot_read_schedule",
        "alternate_representation",
        "reuse_axis_swap",
        "layout_mutation",
        "liveness_reuse_probe",
        "verification_relief",
    ]
    if "verification_backlog" in bottlenecks and plateau_same_score < 1:
        return "verification_relief"
    if "frontier_plateau_with_low_result_diversity" in bottlenecks or dominant_family != "none":
        for kind in exploration_order:
            if kind not in seen:
                return kind
        idx = plateau_same_score % len(exploration_order)
        return exploration_order[idx]
    return next((kind for kind in exploration_order if kind not in seen), "hot_read_schedule")


def retire_duplicate_tools(db, signature: str, keep_tool_id: str | None = None) -> int:
    if not signature:
        return 0
    params: tuple[object, ...]
    if keep_tool_id:
        params = (signature, keep_tool_id)
        db.execute(
            """
            UPDATE promoted_tools
            SET status = 'retired'
            WHERE signature = ? AND id != ? AND status = 'active'
            """,
            params,
        )
    else:
        db.execute(
            "UPDATE promoted_tools SET status = 'retired' WHERE signature = ? AND status = 'active'",
            (signature,),
        )
    return db.total_changes


def run_meta_agent_step(args: argparse.Namespace) -> None:
    heartbeat(args, "working")
    db = connect_team(args)
    best = team_journal.best_frontier(db)
    parent_hypothesis_id = usable_frontier_parent(db, best)
    recent = db.execute(
        """
        SELECT h.title, h.context_json, s.status AS submission_status,
               s.candidate_summary_json, v.official_score, v.decision
        FROM submissions s
        JOIN hypotheses h ON h.id = s.hypothesis_id
        LEFT JOIN verifications v ON v.submission_id = s.id
        ORDER BY s.created_at DESC
        LIMIT 40
        """
    ).fetchall()
    families: dict[str, int] = {}
    scores: list[int] = []
    invalid = 0
    for row in recent:
        try:
            summary = json.loads(row["candidate_summary_json"])
            family = str(summary.get("best", {}).get("family", "unknown"))
            score = int(summary.get("best", {}).get("score", 0))
        except Exception:
            continue
        families[family] = families.get(family, 0) + 1
        if score:
            scores.append(score)
        if row["decision"] == "reject":
            invalid += 1

    state = team_journal.counts(db)
    best_score = int(best.get("official_score") or min(scores or [0]) or 0)
    recent_best = min(scores) if scores else None
    dominant_family, dominant_count = max(families.items(), key=lambda item: item[1], default=("none", 0))
    plateau = bool(scores) and recent_best == best_score and dominant_count >= max(5, len(recent) // 2)
    pending_verification = int(state.get("submissions", {}).get("pending_verification", 0))
    queued_hypotheses = int(state.get("hypotheses", {}).get("queued", 0))

    bottlenecks = []
    if plateau:
        bottlenecks.append("frontier_plateau_with_low_result_diversity")
    if pending_verification >= 8:
        bottlenecks.append("verification_backlog")
    if queued_hypotheses < 3:
        bottlenecks.append("hypothesis_supply_low")
    if invalid >= 3:
        bottlenecks.append("invalid_candidate_rate")
    if not bottlenecks:
        bottlenecks.append("monitoring_no_major_bottleneck")
    tool_kind = choose_tool_kind(db, dominant_family, bottlenecks, best_score)

    operator = {
        "operator": "meta_capability_spec",
        "tool_kind": tool_kind,
        "created_by": args.role,
        "goal": "improve top-line frontier or loop throughput",
        "observed_bottlenecks": bottlenecks,
        "topline": {
            "best_score": best_score,
            "recent_best": recent_best,
            "pending_verification": pending_verification,
            "queued_hypotheses": queued_hypotheses,
        },
        "diversity": {
            "recent_families": families,
            "dominant_family": dominant_family,
            "dominant_count": dominant_count,
        },
        "proposed_capability": (
            "create a new generic operator that changes the representation or search neighborhood "
            "when recent accepted submissions are dominated by one family"
        ),
        "interface": {
            "inputs": ["best_artifact", "recent_scores", "recent_families", "hypothesis_context", "research_notes"],
            "outputs": ["candidate_generator_or_ir_transform", "candidate_summary", "operator_limits"],
        },
        "prototype_plan": [
            "inspect the best candidate and score buckets",
            "identify the hottest repeated reads or most expensive storage class",
            "construct a small reversible transform or generator that changes that bottleneck",
            "test against semantic verifier before publishing",
        ],
        "best_frontier": best,
    }
    tool_id = None
    tool_path = None
    signature = tool_signature(operator)
    duplicate = db.execute(
        "SELECT id FROM promoted_tools WHERE signature = ? AND status = 'active' LIMIT 1",
        (signature,),
    ).fetchone()
    if plateau:
        if duplicate is None:
            tool_id, tool_path = build_meta_tool(args, operator)
            operator["promoted_tool"] = {"tool_id": tool_id, "tool_path": str(tool_path), "signature": signature}
        else:
            operator["duplicate_tool_id"] = duplicate["id"]
    hyp_id = fresh_id("hyp")
    stamp = team_journal.now()
    title = "Meta-capability: escape plateau" if plateau else "Meta-capability: monitor loop bottleneck"
    db.execute(
        """
        INSERT INTO hypotheses
            (id, team_id, proposer_agent_id, parent_hypothesis_id, priority, title, rationale,
             expected_movement, context_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hyp_id,
            args.team_id,
            args.agent_id,
            parent_hypothesis_id,
            12,
            title,
            "Analyze top-line loop dynamics and propose the next generic capability without task-specific solution names.",
            "Increase search diversity or unblock the current throughput bottleneck.",
            json.dumps({"source": args.role, "implementation": operator}),
            stamp,
            stamp,
        ),
    )
    if tool_id and tool_path:
        retire_duplicate_tools(db, signature, keep_tool_id=tool_id)
        db.execute(
            """
            INSERT INTO promoted_tools
                (id, creator_agent_id, tool_path, signature, interface_json, score_hint, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = 'active',
                tool_path = excluded.tool_path,
                signature = excluded.signature,
                interface_json = excluded.interface_json,
                score_hint = excluded.score_hint,
                updated_at = excluded.updated_at
            """,
            (
                tool_id,
                args.agent_id,
                str(tool_path),
                signature,
                json.dumps(operator, sort_keys=True),
                best_score or None,
                stamp,
                stamp,
            ),
        )
        tool_hyp_id = fresh_id("hyp")
        tool_impl = {
            "operator": "promoted_tool",
            "tool_id": tool_id,
            "tool_path": str(tool_path),
            "base_capability": operator,
        }
        db.execute(
            """
            INSERT INTO hypotheses
                (id, team_id, proposer_agent_id, parent_hypothesis_id, priority, title, rationale,
                 expected_movement, context_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tool_hyp_id,
                args.team_id,
                args.agent_id,
                hyp_id,
                14,
                "Use promoted meta tool to escape plateau",
                "A meta-agent generated a local tool from top-line plateau analysis; test it as a candidate generator.",
                "Increase result diversity and attempt a frontier improvement.",
                json.dumps({"source": args.role, "implementation": tool_impl}),
                stamp,
                stamp,
            ),
        )
    transfer_hyp_id = None
    transfer = transfer_payload(parent_hypothesis_id, successful_donors(db), dominant_family) if plateau else None
    if transfer:
        transfer_hyp_id = fresh_id("hyp")
        donor_id = transfer["evolution"]["donor_hypothesis_id"]
        recipient_id = transfer["evolution"]["recipient_hypothesis_id"]
        db.execute(
            """
            INSERT INTO hypotheses
                (id, team_id, proposer_agent_id, parent_hypothesis_id, priority, title, rationale,
                 expected_movement, context_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transfer_hyp_id,
                args.team_id,
                args.agent_id,
                recipient_id,
                13,
                "Horizontal transfer: recombine successful branch",
                "A distinct accepted branch has useful implementation structure; transfer that gene into the current frontier branch.",
                "Increase diversity without injecting seeded task-specific strategy names.",
                json.dumps({
                    "source": args.role,
                    "implementation": transfer["implementation"],
                    "evolution": transfer["evolution"],
                }),
                stamp,
                stamp,
            ),
        )
        post(args.board, args.agent_id, "evolution", "gene_transfer", "horizontal transfer hypothesis queued", {
            "hypothesis_id": transfer_hyp_id,
            "donor_hypothesis_id": donor_id,
            "recipient_hypothesis_id": recipient_id,
            "donor_family": transfer["evolution"]["donor_family"],
            "recipient_family": transfer["evolution"]["recipient_family"],
            "transferred": transfer["evolution"]["transferred"],
        })
    db.commit()
    db.close()
    post(args.board, args.agent_id, "global", "meta_operator", "meta operator hypothesis generated", {
        "hypothesis_id": hyp_id,
        "operator": operator["operator"],
        "bottlenecks": bottlenecks,
        "dominant_family": dominant_family,
        "tool_id": tool_id,
        "tool_kind": tool_kind,
        "signature": signature,
        "duplicate_tool_id": operator.get("duplicate_tool_id"),
        "transfer_hypothesis_id": transfer_hyp_id,
    })
    heartbeat(args, "idle")


def claim_hypothesis(args: argparse.Namespace):
    db = connect_team(args)
    row = db.execute(
        """
        SELECT * FROM hypotheses
        WHERE status = 'queued'
          AND (? IS NULL OR team_id = ? OR team_id = 'global')
        ORDER BY priority DESC, created_at ASC
        LIMIT 1
        """,
        (args.team_id, args.team_id),
    ).fetchone()
    if row is None:
        db.close()
        return None
    stamp = team_journal.now()
    db.execute(
        """
        UPDATE hypotheses
        SET status = 'claimed', claimed_by = ?, lease_expires = ?, updated_at = ?
        WHERE id = ?
        """,
        (args.agent_id, team_journal.lease_deadline(), stamp, row["id"]),
    )
    db.execute(
        "UPDATE agents SET status = 'working', current_item = ?, last_heartbeat = ?, updated_at = ? WHERE id = ?",
        (row["id"], stamp, stamp, args.agent_id),
    )
    db.commit()
    db.close()
    return dict(row)


def run_implementor_step(args: argparse.Namespace) -> None:
    hyp = claim_hypothesis(args)
    if hyp is None:
        heartbeat(args, "idle")
        return
    try:
        hyp_context = json.loads(hyp.get("context_json") or "{}")
    except json.JSONDecodeError:
        hyp_context = {}
    implementation = hyp_context.get("implementation") if isinstance(hyp_context.get("implementation"), dict) else {}
    strategy = str(implementation.get("strategy") or hyp_context.get("strategy") or "baseline")
    seeded = {"sa_cache", "dead_io", "global_reorient"}
    if strategy in seeded and not args.allow_seeded_strategies:
        strategy = "baseline"
    run_id = f"{args.agent_id}_{hyp['id']}_{utc_stamp()}"
    hyp_path = args.worktree_root / args.agent_id / f"{run_id}.hypothesis.json"
    hyp_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(implementation or {"operator": "enumerate_panels"})
    payload["strategy"] = strategy
    if payload.get("operator") == "promoted_tool":
        tool_path = Path(str(payload.get("tool_path") or ""))
        capability_path = hyp_path.with_suffix(".capability.json")
        capability_path.write_text(json.dumps(payload.get("base_capability") or payload, indent=2, sort_keys=True) + "\n")
        proc_tool = subprocess.run(
            [
                sys.executable,
                str(tool_path),
                "--capability-json",
                str(capability_path),
                "--out",
                str(hyp_path),
            ],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            timeout=min(args.step_timeout, 30),
        )
        if proc_tool.returncode != 0:
            hyp_path.write_text(json.dumps({"operator": "enumerate_panels"}, indent=2, sort_keys=True) + "\n")
            post(args.board, args.agent_id, "global", "promoted_tool_failed", proc_tool.stderr[-1000:], {
                "hypothesis_id": hyp["id"],
                "tool_path": str(tool_path),
            })
    else:
        hyp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    cmd = runner_command(args, run_id, hyp_path)
    prior_candidates = []
    db = connect_team(args)
    for row in db.execute("SELECT candidate_summary_json FROM submissions ORDER BY created_at DESC LIMIT 200"):
        try:
            summary = json.loads(row["candidate_summary_json"] or "{}")
        except json.JSONDecodeError:
            continue
        name = (summary.get("best") or {}).get("name") if isinstance(summary, dict) else None
        if name:
            prior_candidates.append(str(name))
    db.close()
    if prior_candidates:
        cmd.extend(["--avoid-candidates-json", json.dumps(sorted(set(prior_candidates)))])
    if args.disable_meta_operator:
        cmd.append("--disable-meta-operator")
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), text=True, capture_output=True, timeout=args.step_timeout)
    worktree = Path(hyp.get("worktree_path") or args.worktree_root / args.agent_id)
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / f"{run_id}.stdout.json").write_text(proc.stdout)
    if proc.stderr:
        (worktree / f"{run_id}.stderr.log").write_text(proc.stderr)
    if proc.returncode != 0:
        db = connect_team(args)
        stamp = team_journal.now()
        db.execute("UPDATE hypotheses SET status = 'abandoned', updated_at = ? WHERE id = ?", (stamp, hyp["id"]))
        db.execute(
            "UPDATE agents SET status = 'idle', current_item = NULL, last_heartbeat = ?, updated_at = ? WHERE id = ?",
            (stamp, stamp, args.agent_id),
        )
        db.commit()
        db.close()
        post(args.board, args.agent_id, "global", "implementor_failed", proc.stderr[-1000:], {"hypothesis_id": hyp["id"]})
        return
    result = json.loads(proc.stdout)
    artifact_dir = Path(result["artifact_dir"])
    best_ir = artifact_dir / "best.ir"
    summary_path = artifact_dir / "summary.json"
    summary = json.loads(summary_path.read_text())
    db = connect_team(args)
    stamp = team_journal.now()
    sub_id = fresh_id("sub")
    db.execute(
        """
        INSERT INTO submissions
            (id, hypothesis_id, team_id, implementor_agent_id, artifact_path,
             candidate_summary_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sub_id,
            hyp["id"],
            hyp["team_id"],
            args.agent_id,
            str(best_ir),
            json.dumps(summary, sort_keys=True),
            stamp,
            stamp,
        ),
    )
    db.execute("UPDATE hypotheses SET status = 'submitted', updated_at = ? WHERE id = ?", (stamp, hyp["id"]))
    db.execute(
        "UPDATE agents SET status = 'idle', current_item = NULL, last_heartbeat = ?, updated_at = ? WHERE id = ?",
        (stamp, stamp, args.agent_id),
    )
    db.commit()
    db.close()
    post(args.board, args.agent_id, "global", "submission", "candidate submitted", {
        "hypothesis_id": hyp["id"],
        "submission_id": sub_id,
        "score": summary.get("best", {}).get("score"),
        "strategy": strategy,
        "artifact_path": str(best_ir),
    })


def claim_submission(args: argparse.Namespace):
    db = connect_team(args)
    row = db.execute(
        """
        SELECT * FROM submissions
        WHERE status = 'pending_verification'
        ORDER BY created_at ASC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        db.close()
        return None
    stamp = team_journal.now()
    db.execute(
        """
        UPDATE submissions
        SET status = 'in_verification', claimed_by = ?, lease_expires = ?, updated_at = ?
        WHERE id = ?
        """,
        (args.agent_id, team_journal.lease_deadline(), stamp, row["id"]),
    )
    db.execute(
        "UPDATE agents SET status = 'working', current_item = ?, last_heartbeat = ?, updated_at = ? WHERE id = ?",
        (row["id"], stamp, stamp, args.agent_id),
    )
    db.commit()
    db.close()
    return dict(row)


def run_verifier_step(args: argparse.Namespace) -> None:
    sub = claim_submission(args)
    if sub is None:
        heartbeat(args, "idle")
        return
    path = Path(sub["artifact_path"])
    semantic = "invalid"
    score = None
    error = ""
    bucket_json = "{}"
    try:
        ir = path.read_text()
        ok, message = verify_general(ir, cases=8, seed=20260530)
        if not ok:
            raise ValueError(message)
        score = matmul.score_16x16(ir)
        semantic = "ok"
        bucket_json = json.dumps(buckets(ir), sort_keys=True)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    db = connect_team(args)
    stamp = team_journal.now()
    ver_id = fresh_id("ver")
    decision = "accept" if semantic == "ok" and score is not None else "reject"
    db.execute(
        """
        INSERT INTO verifications
            (id, submission_id, verifier_agent_id, semantic, official_score,
             buckets_json, decision, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ver_id, sub["id"], args.agent_id, semantic, score, bucket_json, decision, error, stamp),
    )
    db.execute(
        "UPDATE submissions SET status = ?, updated_at = ? WHERE id = ?",
        ("verified" if decision == "accept" else "rejected", stamp, sub["id"]),
    )
    db.execute(
        "UPDATE agents SET status = 'idle', current_item = NULL, last_heartbeat = ?, updated_at = ? WHERE id = ?",
        (stamp, stamp, args.agent_id),
    )
    db.commit()
    db.close()
    post(args.board, args.agent_id, "global", "verification", decision, {
        "submission_id": sub["id"],
        "verification_id": ver_id,
        "score": score,
        "error": error,
    })


def run_researcher_step(args: argparse.Namespace) -> None:
    heartbeat(args, "working")
    research_memory.init_db(args.research_db)
    db = research_memory.connect(args.research_db)
    task = research_memory.claim_research_task(db, args.agent_id)
    if task is None:
        queries = [
            "communication avoiding matrix multiplication",
            "memory efficient matrix multiplication schedule",
            "register allocation liveness optimization",
        ]
        query = queries[int(time.time()) % len(queries)]
    else:
        query = task["query"]
    rows = []
    try:
        for paper in research_memory.arxiv_query(query, max_results=2):
            paper_id = research_memory.upsert_paper(db, {**paper, "tags": ["matmul", "researcher"]}, ["matmul", "researcher"])
            rows.append({"paper_id": paper_id, "title": paper["title"]})
        if task is not None:
            research_memory.complete_research_task(db, task["id"], "done", {"papers": rows})
        db.commit()
        post(args.board, args.agent_id, "global", "research_update", query, {
            "papers": rows,
            "task_id": task["id"] if task else None,
        })
    except Exception as exc:  # noqa: BLE001
        if task is not None:
            research_memory.complete_research_task(db, task["id"], "failed", {"error": str(exc)})
            db.commit()
        post(args.board, args.agent_id, "global", "research_failed", str(exc), {"query": query})
    finally:
        db.close()
    heartbeat(args, "idle")


def active_agents_by_role(db, role: str, exclude: str | None = None) -> list[dict[str, object]]:
    rows = db.execute(
        """
        SELECT * FROM agents
        WHERE role = ? AND status IN ('idle', 'working')
        ORDER BY status = 'idle' DESC, last_heartbeat ASC
        """,
        (role,),
    ).fetchall()
    out = [dict(row) for row in rows]
    if exclude:
        out = [row for row in out if row["id"] != exclude]
    return out


def spawn_agent(args: argparse.Namespace, role: str) -> str:
    agent_id = default_agent_id(role)
    log_dir = args.worktree_root / "launcher-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout = (log_dir / f"{agent_id}.out.log").open("a")
    stderr = (log_dir / f"{agent_id}.err.log").open("a")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "bin" / "autoresearch-agent"),
        role,
        "--experiment-root",
        str(args.experiment_root),
        "--agent-id",
        agent_id,
        "--db",
        str(args.db),
        "--board",
        str(args.board),
        "--worktree-root",
        str(args.worktree_root),
        "--research-db",
        str(args.research_db),
        "--journal-root",
        str(args.journal_root),
        "--workflow",
        str(args.workflow_path),
        "--interval",
        str(args.interval),
        "--step-timeout",
        str(args.step_timeout),
        "--intent-window-seconds",
        str(args.intent_window_seconds),
    ]
    if args.allow_seeded_strategies:
        cmd.append("--allow-seeded-strategies")
    if args.disable_meta_operator:
        cmd.append("--disable-meta-operator")
    subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=stdout, stderr=stderr, start_new_session=True)
    return agent_id


def retire_agent(args: argparse.Namespace, role: str) -> str | None:
    db = connect_team(args)
    candidates = active_agents_by_role(db, role, exclude=args.agent_id)
    db.close()
    if not candidates:
        return None
    target = str(candidates[0]["id"])
    post(args.board, args.agent_id, f"agent:{target}", "stop", "retire requested by manager", {"role": role}, to=target)
    return target


def run_manager_step(args: argparse.Namespace) -> None:
    heartbeat(args, "working")
    db = connect_team(args)
    stale = team_journal.requeue_stale(db, args.stale_seconds)
    team_journal.cleanup_scale_action_locks(db)
    state = team_journal.counts(db)
    state["best_frontier"] = team_journal.best_frontier(db)
    plan = team_journal.scale_plan(state, allow_idle_retire=args.allow_idle_retire)
    peer_counts = recent_peer_intent_counts(args, window_seconds=args.intent_window_seconds)
    intended_actions = subtract_peer_intents(plan["actions"], peer_counts)
    db.execute(
        "INSERT INTO manager_events (kind, payload_json, created_at) VALUES (?, ?, ?)",
        (
            "manager_step",
            json.dumps({
                "stale": stale,
                "plan": plan,
                "peer_intents": {f"{k[0]}:{k[1]}": v for k, v in peer_counts.items()},
                "intended_actions": intended_actions,
                "state": state,
            }, sort_keys=True),
            team_journal.now(),
        ),
    )
    db.commit()
    db.close()

    post(args.board, args.agent_id, "manager-actions", "scale_intent", "manager intent", {
        "actions": intended_actions,
        "peer_intents": {f"{k[0]}:{k[1]}": v for k, v in peer_counts.items()},
        "signals": plan.get("signals", {}),
    })

    applied = []
    if args.apply_scale:
        slots: dict[tuple[str, str], int] = {}
        for action in intended_actions:
            if should_stop(args):
                applied.append({"action": action["action"], "role": action["role"], "skipped": "stop_requested"})
                break
            role = action["role"]
            key = (str(action["action"]), str(role))
            slot = slots.get(key, 0)
            slots[key] = slot + 1
            db = connect_team(args)
            acquired = team_journal.try_acquire_scale_action_lock(db, action_lock_key(action, slot), args.agent_id)
            db.commit()
            db.close()
            if not acquired:
                applied.append({"action": action["action"], "role": role, "skipped": "lock_held", "slot": slot})
                continue
            if action["action"] == "spawn":
                spawned = spawn_agent(args, role)
                applied.append({"action": "spawn", "role": role, "agent_id": spawned, "slot": slot})
            elif action["action"] == "retire":
                retired = retire_agent(args, role)
                applied.append({"action": "retire", "role": role, "agent_id": retired, "slot": slot})

    payload = {
        "stale": stale,
        "plan": plan,
        "intended_actions": intended_actions,
        "applied": applied,
        "best_frontier": state.get("best_frontier"),
    }
    post(args.board, args.agent_id, "manager-actions", "scale_applied", "manager applied", {"applied": applied})
    post(args.board, args.agent_id, "manager-actions", "scale_plan", "manager step complete", payload)
    heartbeat(args, "idle")


def run_step(args: argparse.Namespace) -> None:
    if args.role == "creative_explorer":
        run_creative_step(args)
    elif args.role == "global_searcher":
        run_global_searcher_step(args)
    elif args.role == "implementor":
        run_implementor_step(args)
    elif args.role == "verifier":
        run_verifier_step(args)
    elif args.role == "researcher":
        run_researcher_step(args)
    elif args.role == "insight_generator":
        run_insight_generator_step(args)
    elif args.role == "meta_agent":
        run_meta_agent_step(args)
    elif args.role == "topline_manager":
        run_manager_step(args)
    else:
        raise SystemExit(f"unhandled role: {args.role}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=sorted(ROLES | set(ROLE_ALIASES)))
    parser.add_argument("--experiment", help="experiment name under experiments/")
    parser.add_argument("--experiment-root", type=Path, help="experiment directory containing journal/ and worktrees/")
    parser.add_argument("--agent-id")
    parser.add_argument("--team-id", default="global")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--research-db", type=Path)
    parser.add_argument("--board", type=Path)
    parser.add_argument("--journal-root", type=Path)
    parser.add_argument("--worktree-root", type=Path)
    parser.add_argument("--workflow", type=Path)
    parser.add_argument("--worktree", type=Path)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--step-timeout", type=float, default=240.0)
    parser.add_argument("--stale-seconds", type=int, default=team_journal.LEASE_SECONDS)
    parser.add_argument("--intent-window-seconds", type=int, default=5)
    parser.add_argument("--startup-stop-window-seconds", type=int, default=10)
    parser.add_argument("--allow-idle-retire", action="store_true")
    parser.add_argument("--apply-scale", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-seeded-strategies", action="store_true")
    parser.add_argument("--disable-meta-operator", action="store_true")
    args = parser.parse_args(argv)

    exp = experiment_config.layout(args.experiment, args.experiment_root)
    args.experiment_layout = exp
    args.experiment_root = exp.root
    args.db = (args.db or exp.team_db).expanduser().resolve()
    args.research_db = (args.research_db or exp.research_db).expanduser().resolve()
    args.board = (args.board or exp.board_dir).expanduser().resolve()
    args.journal_root = (args.journal_root or exp.journal_dir).expanduser().resolve()
    args.worktree_root = (args.worktree_root or exp.worktree_root).expanduser().resolve()
    args.workflow_path = (args.workflow or exp.workflow_path).expanduser().resolve()
    if args.worktree:
        args.worktree = args.worktree.expanduser().resolve()

    args.role = normalize_role(args.role)
    args.agent_id = args.agent_id or default_agent_id(args.role)
    register_agent(args)
    post(args.board, args.agent_id, "global", "agent_started", args.role, {"agent_id": args.agent_id})
    if recent_global_stop(args.board, args.startup_stop_window_seconds):
        finish(args, "dead")
        post(args.board, args.agent_id, "global", "agent_stopped", "recent stop message on startup", {"agent_id": args.agent_id})
        return 0
    ack_messages(args.board, args.agent_id)

    steps = 0
    try:
        while True:
            if should_stop(args):
                finish(args, "dead")
                post(args.board, args.agent_id, "global", "agent_stopped", "stop message received", {"agent_id": args.agent_id})
                return 0
            heartbeat(args)
            run_step(args)
            steps += 1
            if args.once or (args.max_steps is not None and steps >= args.max_steps):
                finish(args, "dead")
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        finish(args, "dead")
        return 130
    except Exception as exc:  # noqa: BLE001
        post(args.board, args.agent_id, "global", "agent_error", str(exc), {"role": args.role})
        finish(args, "dead")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
