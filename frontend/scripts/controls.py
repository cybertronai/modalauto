from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autoresearch.backend import clear_runs, experiment_config, message_board


CONTROL_TABLES = ["branch_controls", "control_actions"]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def stop_run(journal, con: sqlite3.Connection, data: dict, stamp: str) -> dict:
    reason = str(data.get("reason") or "Stopped from dashboard").strip() or "Stopped from dashboard"
    exp = experiment_config.layout(root=journal.parent)
    exp.board_dir.mkdir(parents=True, exist_ok=True)
    message = {
        "id": f"msg-{uuid4().hex[:12]}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "from": "dashboard",
        "to": "all",
        "channel": "global",
        "kind": "stop",
        "body": reason,
        "payload": {"source": "dashboard", "reason": reason},
    }
    with message_board.channel_path(exp.board_dir, "global").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(message, sort_keys=True) + "\n")

    changed = con.execute(
        """
        UPDATE agents
        SET status = 'dead', current_item = NULL, last_heartbeat = ?, updated_at = ?
        WHERE status != 'dead'
        """,
        (stamp, stamp),
    ).rowcount
    con.execute(
        "INSERT INTO control_actions (kind, body, payload_json, created_at) VALUES (?, ?, ?, ?)",
        ("stop_run", reason, json.dumps({"message_id": message["id"], **data}, sort_keys=True), stamp),
    )
    process_result = clear_runs.stop_experiment_processes(exp, timeout=2.0, dry_run=False, debug=False)
    return {
        "messageId": message["id"],
        "agentsMarkedDead": changed,
        "processesMatched": process_result.get("matched", 0),
        "processesTerminated": len(process_result.get("terminated", [])),
        "processesKilled": len(process_result.get("killed", [])),
        "processesRemaining": len(process_result.get("remaining", [])),
    }


def ensure_control_tables(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS branch_controls (
            branch_id       TEXT PRIMARY KEY REFERENCES hypotheses(id),
            status          TEXT NOT NULL DEFAULT 'halted'
                            CHECK (status IN ('halted', 'active')),
            note            TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS control_actions (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            kind                 TEXT NOT NULL,
            source_hypothesis_id TEXT REFERENCES hypotheses(id),
            target_hypothesis_id TEXT REFERENCES hypotheses(id),
            body                 TEXT,
            payload_json         TEXT NOT NULL DEFAULT '{}',
            created_at           TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_branch_controls_status ON branch_controls(status, updated_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_control_actions_created ON control_actions(created_at)")


def read_json_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def hypothesis_exists(con: sqlite3.Connection, hyp_id: str) -> bool:
    return con.execute("SELECT id FROM hypotheses WHERE id = ?", (hyp_id,)).fetchone() is not None


def halted_ancestors(con: sqlite3.Connection, hyp_id: str) -> list[str]:
    seen = set()
    current = hyp_id
    halted = []
    while current and current not in seen:
        seen.add(current)
        row = con.execute(
            """
            SELECT h.parent_hypothesis_id, bc.status
            FROM hypotheses h
            LEFT JOIN branch_controls bc ON bc.branch_id = h.id AND bc.status = 'halted'
            WHERE h.id = ?
            """,
            (current,),
        ).fetchone()
        if row is None:
            break
        if row["status"] == "halted":
            halted.append(current)
        current = row["parent_hypothesis_id"]
    return halted


def next_control_hyp_id(con: sqlite3.Connection) -> str:
    n = con.execute("SELECT COUNT(*) AS n FROM hypotheses WHERE id LIKE 'user-hyp-%'").fetchone()["n"]
    while True:
        n += 1
        hyp_id = f"user-hyp-{n:04d}"
        if not hypothesis_exists(con, hyp_id):
            return hyp_id


def insert_control_hypothesis(
    con: sqlite3.Connection,
    *,
    title: str,
    rationale: str,
    movement: str,
    parent_id: str | None,
    priority: int,
    context: dict,
) -> str:
    hyp_id = next_control_hyp_id(con)
    stamp = now_iso()
    con.execute(
        """
        INSERT INTO hypotheses
            (id, team_id, proposer_agent_id, parent_hypothesis_id, priority, title, rationale,
             expected_movement, context_json, created_at, updated_at)
        VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hyp_id,
            "global",
            parent_id,
            int(priority),
            title[:180] or "User control hypothesis",
            rationale,
            movement,
            json.dumps(context, sort_keys=True),
            stamp,
            stamp,
        ),
    )
    return hyp_id


def handle_control_post(handler, path: str, journal, detect_db, ensure_hooks) -> None:
    if not journal:
        handler.send_json(404, {"ok": False, "error": "no journal DB found"})
        return
    con = None
    try:
        data = read_json_body(handler)
        con = sqlite3.connect(detect_db(journal))
        con.row_factory = sqlite3.Row
        ensure_hooks(con)
        stamp = now_iso()

        if path == "/api/control/stop-run":
            result = stop_run(journal, con, data, stamp)
            con.commit()
            handler.send_json(200, {"ok": True, **result})
            return

        if path == "/api/control/halt":
            node_id = str(data.get("nodeId") or "")
            if not node_id or not hypothesis_exists(con, node_id):
                raise ValueError("nodeId must reference an existing hypothesis")
            note = str(data.get("note") or "")
            con.execute(
                """
                INSERT INTO branch_controls (branch_id, status, note, created_at, updated_at)
                VALUES (?, 'halted', ?, ?, ?)
                ON CONFLICT(branch_id) DO UPDATE SET
                    status = 'halted',
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (node_id, note, stamp, stamp),
            )
            con.execute(
                "INSERT INTO control_actions (kind, target_hypothesis_id, body, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                ("halt_branch", node_id, note, json.dumps(data, sort_keys=True), stamp),
            )
            con.commit()
            handler.send_json(200, {"ok": True, "branchId": node_id, "status": "halted"})
            return

        if path == "/api/control/unhalt":
            node_id = str(data.get("nodeId") or "")
            if not node_id or not hypothesis_exists(con, node_id):
                raise ValueError("nodeId must reference an existing hypothesis")
            con.execute(
                """
                INSERT INTO branch_controls (branch_id, status, note, created_at, updated_at)
                VALUES (?, 'active', NULL, ?, ?)
                ON CONFLICT(branch_id) DO UPDATE SET
                    status = 'active',
                    updated_at = excluded.updated_at
                """,
                (node_id, stamp, stamp),
            )
            con.execute(
                "INSERT INTO control_actions (kind, target_hypothesis_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                ("unhalt_branch", node_id, json.dumps(data, sort_keys=True), stamp),
            )
            con.commit()
            handler.send_json(200, {"ok": True, "branchId": node_id, "status": "active"})
            return

        if path == "/api/control/inject":
            node_id = str(data.get("nodeId") or "") or None
            mode = str(data.get("mode") or "branch")
            text = str(data.get("text") or "").strip()
            if not text:
                raise ValueError("text is required")
            if mode == "open":
                node_id = None
            elif not node_id or not hypothesis_exists(con, node_id):
                raise ValueError("nodeId must reference an existing hypothesis for branch injection")
            if node_id and halted_ancestors(con, node_id):
                raise ValueError("cannot inject into a halted branch")
            hyp_id = insert_control_hypothesis(
                con,
                title=("User injected branch" if node_id else "User open hypothesis"),
                rationale=text,
                movement="User-injected information should be prioritized by implementors.",
                parent_id=node_id,
                priority=int(data.get("priority") or 60),
                context={
                    "source": "user_control",
                    "control": "inject_text",
                    "mode": mode,
                    "text": text,
                    "implementation": {
                        "operator": "enumerate_schedule_family",
                        "user_instruction": text,
                    },
                },
            )
            con.execute(
                "INSERT INTO control_actions (kind, target_hypothesis_id, body, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                ("inject_text", node_id, text, json.dumps({"created_hypothesis_id": hyp_id, **data}, sort_keys=True), stamp),
            )
            con.commit()
            handler.send_json(200, {"ok": True, "hypothesisId": hyp_id})
            return

        if path == "/api/control/transfer":
            source_id = str(data.get("sourceId") or "")
            target_id = str(data.get("targetId") or "")
            if not source_id or not target_id:
                raise ValueError("sourceId and targetId are required")
            if source_id == target_id:
                raise ValueError("sourceId and targetId must differ")
            if not hypothesis_exists(con, source_id) or not hypothesis_exists(con, target_id):
                raise ValueError("sourceId and targetId must reference existing hypotheses")
            if halted_ancestors(con, target_id):
                raise ValueError("cannot transfer into a halted destination branch")
            note = str(data.get("note") or "")
            source = con.execute("SELECT title, context_json FROM hypotheses WHERE id = ?", (source_id,)).fetchone()
            target = con.execute("SELECT title FROM hypotheses WHERE id = ?", (target_id,)).fetchone()
            source_context = json.loads(source["context_json"] or "{}")
            source_impl = source_context.get("implementation") if isinstance(source_context, dict) else {}
            if not isinstance(source_impl, dict):
                source_impl = {}
            hyp_id = insert_control_hypothesis(
                con,
                title=f"User gene transfer: {source_id[:10]} -> {target_id[:10]}",
                rationale=note or f"Transfer implementation structure from {source['title']} into {target['title']}.",
                movement="Recombine source branch information into the selected destination branch.",
                parent_id=target_id,
                priority=int(data.get("priority") or 70),
                context={
                    "source": "user_control",
                    "control": "gene_transfer",
                    "implementation": {
                        **source_impl,
                        "operator": source_impl.get("operator") or "enumerate_schedule_family",
                        "transfer_from": source_id,
                        "transfer_to": target_id,
                        "user_note": note,
                    },
                    "evolution": {
                        "event": "horizontal_transfer",
                        "donor_hypothesis_id": source_id,
                        "recipient_hypothesis_id": target_id,
                        "reason": note or "manual gene transfer",
                    },
                },
            )
            con.execute(
                """
                INSERT INTO control_actions
                    (kind, source_hypothesis_id, target_hypothesis_id, body, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("gene_transfer", source_id, target_id, note, json.dumps({"created_hypothesis_id": hyp_id, **data}, sort_keys=True), stamp),
            )
            con.commit()
            handler.send_json(200, {"ok": True, "hypothesisId": hyp_id})
            return

        handler.send_json(404, {"ok": False, "error": "unknown control endpoint"})
    except Exception as exc:
        handler.send_json(400, {"ok": False, "error": str(exc)})
    finally:
        if con is not None:
            con.close()
