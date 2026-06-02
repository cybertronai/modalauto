#!/usr/bin/env python3
"""Coordination journal for elastic autoresearch agent teams.

The scorer, verifier, and leaderboard artifacts stay outside this schema. This
database only coordinates work: hypotheses, submissions, agent leases, and the
manager's current scaling recommendation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from autoresearch.backend import experiment_config


REPO_ROOT = experiment_config.AUTORESEARCH_ROOT
DEFAULT_DB = experiment_config.DEFAULT_TEAM_DB
DEFAULT_WORKTREE_ROOT = experiment_config.DEFAULT_WORKTREE_ROOT
LEASE_SECONDS = 900
SCALE_ACTION_LOCK_SECONDS = 5


SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id            TEXT PRIMARY KEY,
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'paused', 'dead')),
    focus         TEXT,
    context_json  TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id             TEXT PRIMARY KEY,
    role           TEXT NOT NULL CHECK (role IN (
                       'creative_explorer',
                       'implementor',
                       'verifier',
                       'topline_manager',
                       'global_searcher',
                       'researcher',
                       'insight_generator',
                       'meta_agent'
                   )),
    team_id         TEXT REFERENCES teams(id),
    status          TEXT NOT NULL DEFAULT 'idle'
                    CHECK (status IN ('idle', 'working', 'paused', 'dead')),
    worktree_path   TEXT,
    current_item    TEXT,
    lease_expires   TEXT,
    last_heartbeat  TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id                  TEXT PRIMARY KEY,
    team_id              TEXT REFERENCES teams(id),
    proposer_agent_id    TEXT REFERENCES agents(id),
    parent_hypothesis_id TEXT REFERENCES hypotheses(id),
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN (
                            'queued',
                            'claimed',
                            'implemented',
                            'submitted',
                            'rejected',
                            'abandoned'
                        )),
    priority            INTEGER NOT NULL DEFAULT 0,
    title               TEXT NOT NULL,
    rationale           TEXT,
    expected_movement   TEXT,
    context_json        TEXT NOT NULL DEFAULT '{}',
    claimed_by          TEXT REFERENCES agents(id),
    lease_expires       TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id                    TEXT PRIMARY KEY,
    hypothesis_id          TEXT NOT NULL REFERENCES hypotheses(id),
    team_id                TEXT REFERENCES teams(id),
    implementor_agent_id   TEXT REFERENCES agents(id),
    status                 TEXT NOT NULL DEFAULT 'pending_verification'
                           CHECK (status IN (
                               'pending_verification',
                               'in_verification',
                               'verified',
                               'rejected',
                               'published'
                           )),
    artifact_path          TEXT NOT NULL,
    candidate_summary_json TEXT NOT NULL DEFAULT '{}',
    claimed_by             TEXT REFERENCES agents(id),
    lease_expires          TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verifications (
    id                TEXT PRIMARY KEY,
    submission_id     TEXT NOT NULL REFERENCES submissions(id),
    verifier_agent_id TEXT REFERENCES agents(id),
    semantic          TEXT NOT NULL CHECK (semantic IN ('ok', 'invalid')),
    official_score    INTEGER,
    buckets_json      TEXT NOT NULL DEFAULT '{}',
    decision          TEXT NOT NULL CHECK (decision IN ('accept', 'reject')),
    error             TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manager_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scale_action_locks (
    lock_key       TEXT PRIMARY KEY,
    owner_agent_id TEXT NOT NULL,
    lease_expires  TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS promoted_tools (
    id              TEXT PRIMARY KEY,
    creator_agent_id TEXT REFERENCES agents(id),
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'retired')),
    tool_path       TEXT NOT NULL,
    signature       TEXT,
    interface_json  TEXT NOT NULL DEFAULT '{}',
    score_hint      INTEGER,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS branch_controls (
    branch_id       TEXT PRIMARY KEY REFERENCES hypotheses(id),
    status          TEXT NOT NULL DEFAULT 'halted'
                    CHECK (status IN ('halted', 'active')),
    note            TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS control_actions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                 TEXT NOT NULL,
    source_hypothesis_id TEXT REFERENCES hypotheses(id),
    target_hypothesis_id TEXT REFERENCES hypotheses(id),
    body                 TEXT,
    payload_json         TEXT NOT NULL DEFAULT '{}',
    created_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agents_role_status ON agents(role, status);
CREATE INDEX IF NOT EXISTS idx_hyp_status_priority ON hypotheses(status, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_sub_status ON submissions(status, created_at);
CREATE INDEX IF NOT EXISTS idx_ver_submission ON verifications(submission_id);
CREATE INDEX IF NOT EXISTS idx_tools_status ON promoted_tools(status, created_at);
CREATE INDEX IF NOT EXISTS idx_tools_signature ON promoted_tools(signature, status);
CREATE INDEX IF NOT EXISTS idx_branch_controls_status ON branch_controls(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_control_actions_created ON control_actions(created_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def lease_deadline() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path), timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.row_factory = sqlite3.Row
    return db


def init_db(db_path: Path = DEFAULT_DB) -> None:
    db = connect(db_path)
    db.executescript(SCHEMA)
    migrate_agent_roles(db)
    migrate_promoted_tools(db)
    stamp = now()
    db.execute(
        """
        INSERT OR IGNORE INTO teams (id, focus, context_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "global",
            "shared frontier and radical search directions",
            "{}",
            stamp,
            stamp,
        ),
    )
    db.commit()
    db.close()


def migrate_agent_roles(db: sqlite3.Connection) -> None:
    row = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'agents'").fetchone()
    if row is None or "meta_agent" in (row["sql"] or ""):
        return
    db.execute("ALTER TABLE agents RENAME TO agents_old")
    db.executescript("""
    CREATE TABLE agents (
        id             TEXT PRIMARY KEY,
        role           TEXT NOT NULL CHECK (role IN (
                           'creative_explorer',
                           'implementor',
                           'verifier',
                           'topline_manager',
                           'global_searcher',
                           'researcher',
                           'insight_generator',
                           'meta_agent'
                       )),
        team_id         TEXT REFERENCES teams(id),
        status          TEXT NOT NULL DEFAULT 'idle'
                        CHECK (status IN ('idle', 'working', 'paused', 'dead')),
        worktree_path   TEXT,
        current_item    TEXT,
        lease_expires   TEXT,
        last_heartbeat  TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );
    """)
    db.execute(
        """
        INSERT INTO agents
            (id, role, team_id, status, worktree_path, current_item,
             lease_expires, last_heartbeat, created_at, updated_at)
        SELECT id, role, team_id, status, worktree_path, current_item,
               lease_expires, last_heartbeat, created_at, updated_at
        FROM agents_old
        """
    )
    db.execute("DROP TABLE agents_old")
    db.execute("CREATE INDEX IF NOT EXISTS idx_agents_role_status ON agents(role, status)")


def migrate_promoted_tools(db: sqlite3.Connection) -> None:
    row = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'promoted_tools'").fetchone()
    if row is None:
        return
    if "signature" not in (row["sql"] or ""):
        db.execute("ALTER TABLE promoted_tools ADD COLUMN signature TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tools_signature ON promoted_tools(signature, status)")


def next_id(db: sqlite3.Connection, prefix: str, table: str) -> str:
    row = db.execute(f"SELECT id FROM {table} WHERE id LIKE ? ORDER BY id DESC LIMIT 1", (f"{prefix}-%",)).fetchone()
    if row is None:
        return f"{prefix}-001"
    try:
        n = int(str(row["id"]).split("-")[-1])
    except ValueError:
        return f"{prefix}-001"
    return f"{prefix}-{n + 1:03d}"


def row_counts(db: sqlite3.Connection, table: str, group_col: str) -> dict[str, int]:
    rows = db.execute(f"SELECT {group_col} AS k, COUNT(*) AS n FROM {table} GROUP BY {group_col}").fetchall()
    return {str(row["k"]): int(row["n"]) for row in rows}


VOLATILE_CONTEXT_KEYS = {
    "best_frontier",
    "recent_best",
    "recent_families",
    "parent_source",
}
IDEA_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "before", "by",
    "for", "from", "has", "have", "if", "in", "into", "is", "it", "more",
    "new", "of", "on", "or", "that", "the", "this", "to", "try", "use",
    "when", "with", "without", "test",
}
IDEA_SYNONYMS = {
    "cache": "reuse",
    "cached": "reuse",
    "caching": "reuse",
    "hold": "reuse",
    "holding": "reuse",
    "reuse": "reuse",
    "reusing": "reuse",
    "alias": "alias",
    "aliasing": "alias",
    "packed": "pack",
    "packing": "pack",
    "scratch": "scratch",
    "temporary": "scratch",
    "temp": "scratch",
    "tile": "tile",
    "tiled": "tile",
    "tiling": "tile",
    "panel": "panel",
    "panels": "panel",
    "schedule": "schedule",
    "schedules": "schedule",
    "loop": "loop",
    "loops": "loop",
    "address": "address",
    "addresses": "address",
    "read": "read",
    "reads": "read",
    "repeated": "read",
    "repeatedly": "read",
    "low": "low",
    "frontier": "frontier",
    "plateau": "plateau",
    "global": "global",
    "meta": "meta",
    "transfer": "transfer",
    "recombine": "transfer",
}


def parse_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def stable_context(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): stable_context(v)
            for k, v in sorted(value.items())
            if k not in VOLATILE_CONTEXT_KEYS
        }
    if isinstance(value, list):
        return [stable_context(item) for item in value]
    return value


def normalized_title(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return " ".join(idea_tokens(title))


def idea_tokens(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, dict):
            tokens.update(idea_tokens(*value.values()))
            continue
        if isinstance(value, list):
            tokens.update(idea_tokens(*value))
            continue
        for raw in re.findall(r"[a-z0-9]+", str(value).lower()):
            if raw in IDEA_STOPWORDS or len(raw) <= 1:
                continue
            tokens.add(IDEA_SYNONYMS.get(raw, raw))
    return tokens


def idea_token_set(title: str, rationale: str, expected_movement: str, context_json: str | dict[str, Any] | None) -> set[str]:
    context = context_json if isinstance(context_json, dict) else parse_json_object(context_json)
    implementation = context.get("implementation") if isinstance(context, dict) else {}
    if not isinstance(implementation, dict):
        implementation = {}
    focused_impl = {
        "operator": implementation.get("operator"),
        "strategy": implementation.get("strategy"),
        "tool_kind": implementation.get("tool_kind"),
        "reuse_goal": implementation.get("reuse_goal"),
        "low_address_roles": implementation.get("low_address_roles"),
        "loop_order": implementation.get("loop_order"),
        "evolution": context.get("evolution") if isinstance(context, dict) else None,
    }
    return idea_tokens(title, rationale, expected_movement, focused_impl)


def idea_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def same_general_idea(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    overlap = a & b
    core = {"reuse", "alias", "scratch", "tile", "panel", "schedule", "address", "transfer", "meta", "output", "dead"}
    if len(overlap) >= 5 and idea_similarity(a, b) >= 0.34 and len(overlap & core) >= 2:
        return True
    return len(overlap & core) >= 3 and len(overlap) >= 6 and idea_similarity(a, b) >= 0.20


def hypothesis_signature(title: str, context_json: str | dict[str, Any] | None) -> str:
    context = context_json if isinstance(context_json, dict) else parse_json_object(context_json)
    implementation = context.get("implementation") if isinstance(context, dict) else None
    if isinstance(implementation, dict) and implementation:
        payload = {
            "implementation": stable_context(implementation),
        }
    else:
        payload = {"title": normalized_title(title)}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]


def queued_hypothesis_signature(row: sqlite3.Row) -> str:
    return hypothesis_signature(str(row["title"]), row["context_json"])


def queued_hypothesis_idea(row: sqlite3.Row) -> set[str]:
    return idea_token_set(
        str(row["title"] or ""),
        str(row["rationale"] or ""),
        str(row["expected_movement"] or ""),
        row["context_json"],
    )


def find_matching_hypothesis(db: sqlite3.Connection, signature: str, idea: set[str]) -> sqlite3.Row | None:
    rows = db.execute(
        """
        SELECT *
        FROM hypotheses
        WHERE status IN ('queued', 'claimed', 'submitted', 'implemented')
        ORDER BY
            CASE status
                WHEN 'queued' THEN 0
                WHEN 'claimed' THEN 1
                WHEN 'submitted' THEN 2
                ELSE 3
            END,
            priority DESC,
            created_at ASC
        """
    ).fetchall()
    for row in rows:
        if queued_hypothesis_signature(row) == signature:
            return row
    for row in rows:
        if same_general_idea(queued_hypothesis_idea(row), idea):
            return row
    return None


def merge_duplicate_hypothesis(
    db: sqlite3.Connection,
    existing: sqlite3.Row,
    *,
    proposer_agent_id: str | None,
    priority: int,
    rationale: str,
    expected_movement: str,
    context_json: str,
    stamp: str,
) -> dict[str, Any]:
    context = parse_json_object(existing["context_json"])
    duplicate = parse_json_object(context_json)
    merged = context.setdefault("merged_duplicates", [])
    if isinstance(merged, list):
        merged.append({
            "proposer_agent_id": proposer_agent_id,
            "rationale": rationale,
            "expected_movement": expected_movement,
            "context": duplicate,
            "merged_at": stamp,
        })
    if existing["status"] == "queued":
        db.execute(
            """
            UPDATE hypotheses
            SET priority = MAX(priority, ?),
                rationale = CASE WHEN length(COALESCE(rationale, '')) >= length(?) THEN rationale ELSE ? END,
                expected_movement = CASE WHEN length(COALESCE(expected_movement, '')) >= length(?) THEN expected_movement ELSE ? END,
                context_json = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                priority,
                rationale,
                rationale,
                expected_movement,
                expected_movement,
                json.dumps(context, sort_keys=True),
                stamp,
                existing["id"],
            ),
        )
        status = "merged"
    else:
        status = "duplicate"
    return {"hypothesis_id": existing["id"], "status": status}


def hypothesis_backlog_cap(agent_counts: dict[str, int] | None = None) -> int:
    agent_counts = agent_counts or {}
    implementors = int(agent_counts.get("implementor", 0))
    generators = (
        int(agent_counts.get("creative_explorer", 0))
        + int(agent_counts.get("global_searcher", 0))
        + int(agent_counts.get("insight_generator", 0))
        + int(agent_counts.get("meta_agent", 0))
    )
    return min(50, max(8, implementors * 6 + generators * 3))


def prune_queued_hypotheses(
    db: sqlite3.Connection,
    *,
    max_total: int | None = None,
    max_per_signature: int = 1,
) -> dict[str, int]:
    if max_total is None:
        max_total = hypothesis_backlog_cap(active_agent_counts(db))
    stamp = now()
    rows = db.execute(
        """
        SELECT *
        FROM hypotheses
        WHERE status = 'queued'
        ORDER BY priority DESC, created_at ASC
        """
    ).fetchall()
    keep: set[str] = set()
    abandon: set[str] = set()
    per_sig: dict[str, int] = {}
    kept_ideas: list[set[str]] = []
    for row in rows:
        sig = queued_hypothesis_signature(row)
        idea = queued_hypothesis_idea(row)
        redundant_idea = any(same_general_idea(existing, idea) for existing in kept_ideas)
        if len(keep) < max_total and per_sig.get(sig, 0) < max_per_signature and not redundant_idea:
            keep.add(row["id"])
            per_sig[sig] = per_sig.get(sig, 0) + 1
            kept_ideas.append(idea)
        else:
            abandon.add(row["id"])
    for hyp_id in abandon:
        db.execute(
            """
            UPDATE hypotheses
            SET status = 'abandoned',
                rationale = COALESCE(rationale, '') || '\n\n[autoresearch] pruned redundant queued hypothesis',
                updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (stamp, hyp_id),
        )
    return {"kept": len(keep), "abandoned": len(abandon), "cap": max_total}


def queue_hypothesis(
    db: sqlite3.Connection,
    *,
    hypothesis_id: str,
    team_id: str,
    proposer_agent_id: str | None,
    parent_hypothesis_id: str | None,
    priority: int,
    title: str,
    rationale: str = "",
    expected_movement: str = "",
    context_json: str = "{}",
    prune: bool = True,
) -> dict[str, Any]:
    stamp = now()
    signature = hypothesis_signature(title, context_json)
    idea = idea_token_set(title, rationale, expected_movement, context_json)
    existing = find_matching_hypothesis(db, signature, idea)
    if existing is not None:
        result = merge_duplicate_hypothesis(
            db,
            existing,
            proposer_agent_id=proposer_agent_id,
            priority=priority,
            rationale=rationale,
            expected_movement=expected_movement,
            context_json=context_json,
            stamp=stamp,
        )
        if prune:
            result["pruned"] = prune_queued_hypotheses(db)
        result["signature"] = signature
        return result
    db.execute(
        """
        INSERT INTO hypotheses
            (id, team_id, proposer_agent_id, parent_hypothesis_id, priority, title, rationale,
             expected_movement, context_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hypothesis_id,
            team_id,
            proposer_agent_id,
            parent_hypothesis_id,
            priority,
            title,
            rationale,
            expected_movement,
            context_json,
            stamp,
            stamp,
        ),
    )
    result = {"hypothesis_id": hypothesis_id, "status": "queued", "signature": signature}
    if prune:
        result["pruned"] = prune_queued_hypotheses(db)
    return result


def active_agent_counts(db: sqlite3.Connection) -> dict[str, int]:
    rows = db.execute(
        """
        SELECT role, COUNT(*) AS n
        FROM agents
        WHERE status IN ('idle', 'working')
        GROUP BY role
        """
    ).fetchall()
    return {str(row["role"]): int(row["n"]) for row in rows}


def counts(db: sqlite3.Connection) -> dict[str, Any]:
    return {
        "agents": active_agent_counts(db),
        "hypotheses": row_counts(db, "hypotheses", "status"),
        "submissions": row_counts(db, "submissions", "status"),
        "teams": row_counts(db, "teams", "status"),
    }


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def scale_plan(state: dict[str, Any], allow_idle_retire: bool = False) -> dict[str, Any]:
    hypotheses = state.get("hypotheses", {})
    submissions = state.get("submissions", {})
    active = state.get("agents", {})
    backlog_cap = hypothesis_backlog_cap({str(k): int(v) for k, v in active.items()})
    queued_hyp = int(hypotheses.get("queued", 0))
    claimed_hyp = int(hypotheses.get("claimed", 0))
    pending_sub = int(submissions.get("pending_verification", 0))
    in_verification = int(submissions.get("in_verification", 0))
    total_backlog = queued_hyp + claimed_hyp + pending_sub + in_verification

    # Simple elastic policy. These numbers are intentionally conservative:
    # verification should clear quickly; exploration expands when hypothesis
    # supply is low; implementation expands when hypothesis supply is high.
    idle = total_backlog == 0
    manager_need = 1
    if total_backlog >= 24 or pending_sub >= 12:
        manager_need = 2
    supply_saturated = queued_hyp >= backlog_cap
    if allow_idle_retire and idle:
        desired = {
            "topline_manager": manager_need,
            "global_searcher": 1,
            "creative_explorer": 1,
            "implementor": 0,
            "verifier": 0,
            "researcher": 1,
            "insight_generator": 0,
            "meta_agent": 0,
        }
    else:
        desired = {
            "topline_manager": manager_need,
            "global_searcher": 1 if supply_saturated else clamp(2 + (1 if queued_hyp < 3 else 0), 2, 4),
            "creative_explorer": 0 if supply_saturated else clamp(1 + math.ceil(max(0, 6 - queued_hyp) / 3), 1, 4),
            "implementor": clamp(math.ceil((queued_hyp + claimed_hyp) / 3), 0, 8),
            "verifier": clamp(math.ceil((pending_sub + in_verification) / 3), 0, 6),
            "researcher": 2,
            "insight_generator": 0 if supply_saturated else 1,
            "meta_agent": 0 if supply_saturated else 1,
        }
    deltas = {role: desired[role] - int(active.get(role, 0)) for role in desired}
    actions = []
    for role, delta in deltas.items():
        if delta > 0:
            for _ in range(delta):
                actions.append({"action": "spawn", "role": role})
        elif delta < 0:
            if role == "topline_manager" and not allow_idle_retire:
                continue
            for _ in range(-delta):
                actions.append({"action": "retire", "role": role})
    return {
        "desired": desired,
        "active": active,
        "deltas": deltas,
        "actions": actions,
        "signals": {
            "queued_hypotheses": queued_hyp,
            "claimed_hypotheses": claimed_hyp,
            "pending_submissions": pending_sub,
            "in_verification": in_verification,
            "total_backlog": total_backlog,
            "hypothesis_backlog_cap": backlog_cap,
            "hypothesis_supply_saturated": supply_saturated,
            "idle": idle,
        },
    }


def heartbeat_agent(db: sqlite3.Connection, agent_id: str, status: str | None = None) -> None:
    stamp = now()
    if status:
        db.execute(
            """
            UPDATE agents
            SET last_heartbeat = ?, lease_expires = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (stamp, lease_deadline(), status, stamp, agent_id),
        )
    else:
        db.execute(
            """
            UPDATE agents
            SET last_heartbeat = ?, lease_expires = ?, updated_at = ?
            WHERE id = ?
            """,
            (stamp, lease_deadline(), stamp, agent_id),
        )


def try_acquire_scale_action_lock(
    db: sqlite3.Connection,
    lock_key: str,
    owner_agent_id: str,
    ttl_seconds: int = SCALE_ACTION_LOCK_SECONDS,
) -> bool:
    stamp_dt = datetime.now(timezone.utc)
    stamp = stamp_dt.isoformat()
    expires = (stamp_dt + timedelta(seconds=ttl_seconds)).isoformat()
    row = db.execute(
        "SELECT owner_agent_id, lease_expires FROM scale_action_locks WHERE lock_key = ?",
        (lock_key,),
    ).fetchone()
    lease = parse_time(row["lease_expires"]) if row else None
    if row is not None and lease is not None and lease > stamp_dt and row["owner_agent_id"] != owner_agent_id:
        return False
    db.execute(
        """
        INSERT INTO scale_action_locks (lock_key, owner_agent_id, lease_expires, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(lock_key) DO UPDATE SET
            owner_agent_id = excluded.owner_agent_id,
            lease_expires = excluded.lease_expires,
            updated_at = excluded.updated_at
        """,
        (lock_key, owner_agent_id, expires, stamp),
    )
    return True


def cleanup_scale_action_locks(db: sqlite3.Connection) -> int:
    stamp = now()
    db.execute("DELETE FROM scale_action_locks WHERE lease_expires < ?", (stamp,))
    return db.total_changes


def requeue_stale(db: sqlite3.Connection, stale_seconds: int) -> dict[str, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
    lease_cutoff = datetime.now(timezone.utc)
    stamp = now()
    changed = {"hypotheses": 0, "submissions": 0, "agents": 0}

    stale_hypotheses = []
    for row in db.execute("SELECT id, lease_expires FROM hypotheses WHERE status = 'claimed'").fetchall():
        lease = parse_time(row["lease_expires"])
        if lease is None or lease < lease_cutoff:
            stale_hypotheses.append(row["id"])
    for hyp_id in stale_hypotheses:
        db.execute(
            """
            UPDATE hypotheses
            SET status = 'queued', claimed_by = NULL, lease_expires = NULL, updated_at = ?
            WHERE id = ?
            """,
            (stamp, hyp_id),
        )
    changed["hypotheses"] = len(stale_hypotheses)

    stale_submissions = []
    for row in db.execute("SELECT id, lease_expires FROM submissions WHERE status = 'in_verification'").fetchall():
        lease = parse_time(row["lease_expires"])
        if lease is None or lease < lease_cutoff:
            stale_submissions.append(row["id"])
    for sub_id in stale_submissions:
        db.execute(
            """
            UPDATE submissions
            SET status = 'pending_verification', claimed_by = NULL, lease_expires = NULL, updated_at = ?
            WHERE id = ?
            """,
            (stamp, sub_id),
        )
    changed["submissions"] = len(stale_submissions)

    stale_agents = []
    for row in db.execute("SELECT id, last_heartbeat FROM agents WHERE status IN ('idle', 'working')").fetchall():
        beat = parse_time(row["last_heartbeat"])
        if beat is None or beat < cutoff:
            stale_agents.append(row["id"])
    for agent_id in stale_agents:
        db.execute(
            """
            UPDATE agents
            SET status = 'dead', current_item = NULL, updated_at = ?
            WHERE id = ?
            """,
            (stamp, agent_id),
        )
    changed["agents"] = len(stale_agents)
    return changed


def best_frontier(db: sqlite3.Connection, maximize: bool = False) -> dict[str, Any]:
    direction = "DESC" if maximize else "ASC"
    row = db.execute(
        f"""
        SELECT v.official_score, v.submission_id, s.hypothesis_id, s.artifact_path,
               h.title, v.created_at
        FROM verifications v
        JOIN submissions s ON s.id = v.submission_id
        JOIN hypotheses h ON h.id = s.hypothesis_id
        WHERE v.decision = 'accept' AND v.official_score IS NOT NULL
        ORDER BY v.official_score {direction}, v.created_at DESC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else {}


def latest_promoted_tool(db: sqlite3.Connection) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT * FROM promoted_tools
        WHERE status = 'active'
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None


def cmd_init(args: argparse.Namespace) -> int:
    init_db(args.db)
    args.worktree_root.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"db": str(args.db), "worktree_root": str(args.worktree_root)}, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    state = counts(db)
    workflow = experiment_config.load_workflow(args.experiment_root / "workflow.json")
    state["best_frontier"] = best_frontier(
        db,
        maximize=str(workflow.get("direction", "")).lower() == "maximize",
    )
    db.close()
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


def cmd_scale_plan(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    state = counts(db)
    plan = scale_plan(state, allow_idle_retire=args.allow_idle_retire)
    db.execute(
        "INSERT INTO manager_events (kind, payload_json, created_at) VALUES (?, ?, ?)",
        ("scale_plan", json.dumps(plan, sort_keys=True), now()),
    )
    db.commit()
    db.close()
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def cmd_register_agent(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    stamp = now()
    agent_id = args.agent_id or next_id(db, args.role, "agents")
    worktree_path = args.worktree or str(args.worktree_root / agent_id)
    Path(worktree_path).mkdir(parents=True, exist_ok=True)
    db.execute(
        """
        INSERT OR REPLACE INTO agents
            (id, role, team_id, status, worktree_path, lease_expires, last_heartbeat, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (agent_id, args.role, args.team_id, "idle", worktree_path, lease_deadline(), stamp, stamp, stamp),
    )
    db.commit()
    db.close()
    print(json.dumps({"agent_id": agent_id, "role": args.role, "worktree_path": worktree_path}, indent=2))
    return 0


def cmd_add_hypothesis(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    hyp_id = args.hypothesis_id or next_id(db, "hyp", "hypotheses")
    result = queue_hypothesis(
        db,
        hypothesis_id=hyp_id,
        team_id=args.team_id,
        proposer_agent_id=args.proposer_agent_id,
        parent_hypothesis_id=args.parent_hypothesis_id,
        priority=args.priority,
        title=args.title,
        rationale=args.rationale or "",
        expected_movement=args.expected_movement or "",
        context_json=args.context_json,
    )
    db.commit()
    db.close()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_claim_hypothesis(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
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
        print(json.dumps({"claimed": None}, indent=2))
        return 0
    stamp = now()
    db.execute(
        """
        UPDATE hypotheses
        SET status = 'claimed', claimed_by = ?, lease_expires = ?, updated_at = ?
        WHERE id = ?
        """,
        (args.agent_id, lease_deadline(), stamp, row["id"]),
    )
    db.execute(
        "UPDATE agents SET status = 'working', current_item = ?, last_heartbeat = ?, updated_at = ? WHERE id = ?",
        (row["id"], stamp, stamp, args.agent_id),
    )
    db.commit()
    db.close()
    print(json.dumps({"claimed": dict(row)}, indent=2, sort_keys=True))
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    hyp = db.execute("SELECT * FROM hypotheses WHERE id = ?", (args.hypothesis_id,)).fetchone()
    if hyp is None:
        raise SystemExit(f"unknown hypothesis: {args.hypothesis_id}")
    stamp = now()
    sub_id = args.submission_id or next_id(db, "sub", "submissions")
    db.execute(
        """
        INSERT INTO submissions
            (id, hypothesis_id, team_id, implementor_agent_id, artifact_path,
             candidate_summary_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sub_id,
            args.hypothesis_id,
            hyp["team_id"],
            args.agent_id,
            args.artifact_path,
            args.candidate_summary_json,
            stamp,
            stamp,
        ),
    )
    db.execute(
        "UPDATE hypotheses SET status = 'submitted', updated_at = ? WHERE id = ?",
        (stamp, args.hypothesis_id),
    )
    db.execute(
        "UPDATE agents SET status = 'idle', current_item = NULL, last_heartbeat = ?, updated_at = ? WHERE id = ?",
        (stamp, stamp, args.agent_id),
    )
    db.commit()
    db.close()
    print(json.dumps({"submission_id": sub_id}, indent=2))
    return 0


def cmd_claim_submission(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
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
        print(json.dumps({"claimed": None}, indent=2))
        return 0
    stamp = now()
    db.execute(
        """
        UPDATE submissions
        SET status = 'in_verification', claimed_by = ?, lease_expires = ?, updated_at = ?
        WHERE id = ?
        """,
        (args.agent_id, lease_deadline(), stamp, row["id"]),
    )
    db.execute(
        "UPDATE agents SET status = 'working', current_item = ?, last_heartbeat = ?, updated_at = ? WHERE id = ?",
        (row["id"], stamp, stamp, args.agent_id),
    )
    db.commit()
    db.close()
    print(json.dumps({"claimed": dict(row)}, indent=2, sort_keys=True))
    return 0


def cmd_record_verification(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    sub = db.execute("SELECT * FROM submissions WHERE id = ?", (args.submission_id,)).fetchone()
    if sub is None:
        raise SystemExit(f"unknown submission: {args.submission_id}")
    stamp = now()
    ver_id = args.verification_id or next_id(db, "ver", "verifications")
    decision = "accept" if args.semantic == "ok" and args.official_score is not None else "reject"
    db.execute(
        """
        INSERT INTO verifications
            (id, submission_id, verifier_agent_id, semantic, official_score,
             buckets_json, decision, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ver_id,
            args.submission_id,
            args.agent_id,
            args.semantic,
            args.official_score,
            args.buckets_json,
            decision,
            args.error or "",
            stamp,
        ),
    )
    db.execute(
        "UPDATE submissions SET status = ?, updated_at = ? WHERE id = ?",
        ("verified" if decision == "accept" else "rejected", stamp, args.submission_id),
    )
    db.execute(
        "UPDATE agents SET status = 'idle', current_item = NULL, last_heartbeat = ?, updated_at = ? WHERE id = ?",
        (stamp, stamp, args.agent_id),
    )
    db.commit()
    db.close()
    print(json.dumps({"verification_id": ver_id, "decision": decision}, indent=2))
    return 0


def cmd_set_agent_status(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    stamp = now()
    db.execute(
        """
        UPDATE agents
        SET status = ?, current_item = CASE WHEN ? IN ('dead', 'paused') THEN NULL ELSE current_item END,
            updated_at = ?, last_heartbeat = ?
        WHERE id = ?
        """,
        (args.status, args.status, stamp, stamp, args.agent_id),
    )
    db.commit()
    changed = db.total_changes
    db.close()
    print(json.dumps({"agent_id": args.agent_id, "status": args.status, "changed": changed}, indent=2))
    return 0


def cmd_heartbeat(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    heartbeat_agent(db, args.agent_id, args.status)
    db.commit()
    db.close()
    print(json.dumps({"agent_id": args.agent_id, "heartbeat": now()}, indent=2))
    return 0


def cmd_requeue_stale(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    changed = requeue_stale(db, args.stale_seconds)
    db.execute(
        "INSERT INTO manager_events (kind, payload_json, created_at) VALUES (?, ?, ?)",
        ("requeue_stale", json.dumps(changed, sort_keys=True), now()),
    )
    db.commit()
    db.close()
    print(json.dumps(changed, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", help="experiment name under experiments/")
    parser.add_argument("--experiment-root", type=Path, help="experiment directory containing journal/ and worktrees/")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--worktree-root", type=Path)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.set_defaults(func=cmd_init)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=cmd_status)

    p_scale = sub.add_parser("scale-plan")
    p_scale.add_argument("--allow-idle-retire", action="store_true")
    p_scale.set_defaults(func=cmd_scale_plan)

    p_agent = sub.add_parser("register-agent")
    p_agent.add_argument("role", choices=[
        "creative_explorer",
        "implementor",
        "verifier",
        "topline_manager",
        "global_searcher",
        "researcher",
        "insight_generator",
        "meta_agent",
    ])
    p_agent.add_argument("--agent-id")
    p_agent.add_argument("--team-id", default="global")
    p_agent.add_argument("--worktree")
    p_agent.set_defaults(func=cmd_register_agent)

    p_hyp = sub.add_parser("add-hypothesis")
    p_hyp.add_argument("--hypothesis-id")
    p_hyp.add_argument("--team-id", default="global")
    p_hyp.add_argument("--proposer-agent-id")
    p_hyp.add_argument("--parent-hypothesis-id")
    p_hyp.add_argument("--priority", type=int, default=0)
    p_hyp.add_argument("--title", required=True)
    p_hyp.add_argument("--rationale")
    p_hyp.add_argument("--expected-movement")
    p_hyp.add_argument("--context-json", default="{}")
    p_hyp.set_defaults(func=cmd_add_hypothesis)

    p_claim_hyp = sub.add_parser("claim-hypothesis")
    p_claim_hyp.add_argument("--agent-id", required=True)
    p_claim_hyp.add_argument("--team-id")
    p_claim_hyp.set_defaults(func=cmd_claim_hypothesis)

    p_submit = sub.add_parser("submit")
    p_submit.add_argument("--submission-id")
    p_submit.add_argument("--agent-id", required=True)
    p_submit.add_argument("--hypothesis-id", required=True)
    p_submit.add_argument("--artifact-path", required=True)
    p_submit.add_argument("--candidate-summary-json", default="{}")
    p_submit.set_defaults(func=cmd_submit)

    p_claim_sub = sub.add_parser("claim-submission")
    p_claim_sub.add_argument("--agent-id", required=True)
    p_claim_sub.set_defaults(func=cmd_claim_submission)

    p_ver = sub.add_parser("record-verification")
    p_ver.add_argument("--verification-id")
    p_ver.add_argument("--agent-id", required=True)
    p_ver.add_argument("--submission-id", required=True)
    p_ver.add_argument("--semantic", choices=["ok", "invalid"], required=True)
    p_ver.add_argument("--official-score", type=int)
    p_ver.add_argument("--buckets-json", default="{}")
    p_ver.add_argument("--error")
    p_ver.set_defaults(func=cmd_record_verification)

    p_status_agent = sub.add_parser("set-agent-status")
    p_status_agent.add_argument("--agent-id", required=True)
    p_status_agent.add_argument("--status", choices=["idle", "working", "paused", "dead"], required=True)
    p_status_agent.set_defaults(func=cmd_set_agent_status)

    p_heartbeat = sub.add_parser("heartbeat")
    p_heartbeat.add_argument("--agent-id", required=True)
    p_heartbeat.add_argument("--status", choices=["idle", "working", "paused", "dead"])
    p_heartbeat.set_defaults(func=cmd_heartbeat)

    p_requeue = sub.add_parser("requeue-stale")
    p_requeue.add_argument("--stale-seconds", type=int, default=LEASE_SECONDS)
    p_requeue.set_defaults(func=cmd_requeue_stale)

    args = parser.parse_args(argv)
    exp = experiment_config.layout(args.experiment, args.experiment_root)
    args.experiment_root = exp.root
    args.db = (args.db or exp.team_db).expanduser().resolve()
    args.worktree_root = (args.worktree_root or exp.worktree_root).expanduser().resolve()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
