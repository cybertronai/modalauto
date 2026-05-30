#!/usr/bin/env python3
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from export_real_data import build_payload, detect_db, render_js, render_runs_js, node_trace


AUTORESEARCH_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = AUTORESEARCH_ROOT
DEFAULT_AUTORESEARCH = AUTORESEARCH_ROOT
START_CWD = Path.cwd().resolve()
CHANGELOG_NAME = "frontend_changelog.jsonl"
WATCH_TABLES = ["agents", "hypotheses", "submissions", "verifications", "manager_events"]
CONTROL_TABLES = ["branch_controls", "control_actions"]
FRONTEND_WATCH_EXTS = {".html", ".css", ".js", ".jsx"}


def configured_journal_path():
    configured = os.environ.get("FRONTEND_JOURNAL")
    if not configured:
        return None
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = START_CWD / path
    return path.resolve()


def pick_journal():
    configured = configured_journal_path()
    if configured:
        return configured
    candidates = [
        p for p in (DEFAULT_AUTORESEARCH / "experiments").glob("*/journal")
        if detect_db(p).exists()
    ]
    if not candidates:
        return None
    return max(candidates, key=journal_mtime_ns)


def journal_id(journal):
    return journal.parent.name if journal.name == "journal" else journal.name


def journal_from_request(handler):
    q = parse_qs(urlparse(handler.path).query)
    selected = (q.get("journal") or q.get("task") or [None])[0]
    if selected:
        for journal in discover_journals():
            if journal_id(journal) == selected or journal.name == selected or journal.parent.name == selected:
                return journal
        return None
    return pick_journal()


def journal_mtime_ns(journal):
    newest = 0
    db = detect_db(journal)
    for suffix in ["", "-wal", "-shm"]:
        path = Path(str(db) + suffix)
        if path.exists():
            newest = max(newest, path.stat().st_mtime_ns)
    return newest


def discover_journals():
    """All experiment journal dirs with a team_journal.db, newest first.
    Honors FRONTEND_JOURNAL to pin a single journal (matches pick_journal)."""
    configured = configured_journal_path()
    if configured:
        return [configured] if detect_db(configured).exists() else []
    candidates = [
        p for p in (DEFAULT_AUTORESEARCH / "experiments").glob("*/journal")
        if detect_db(p).exists()
    ]
    return sorted(candidates, key=journal_mtime_ns, reverse=True)


def run_label(journal):
    """Human label from an experiment journal path."""
    name = journal.parent.name if journal.name == "journal" else journal.name
    name = re.sub(r"_?\d{8}T\d{6}$", "", name)
    return name.replace("_", " ").replace("-", " ").title() if name else "Main Run"


def build_runs(journals):
    """Build a Compare run per journal that actually has tree nodes."""
    runs = []
    for journal in journals:
        payload = build_payload(journal)
        if not payload.get("nodes"):
            continue
        meta = payload["meta"]
        meta["label"] = run_label(journal)
        best = meta.get("best")
        desc = f"{meta.get('totalNodes', 0)} hypotheses"
        if best is not None:
            desc += f" · best {best:,}"
        runs.append({"id": journal_id(journal), "label": run_label(journal), "desc": desc, "payload": payload})
    return runs


def build_tasks(journals, selected=None):
    tasks = []
    for journal in journals:
        payload = build_payload(journal)
        meta = payload.get("meta", {})
        best = meta.get("best")
        desc = f"{meta.get('totalNodes', 0)} hypotheses"
        if best is not None:
            desc += f" · best {best:,}"
        jid = journal_id(journal)
        tasks.append({
            "id": jid,
            "label": run_label(journal),
            "desc": desc,
            "journal": str(journal),
            "selected": bool(selected and journal.resolve() == selected.resolve()),
        })
    return tasks


def db_signature(journal):
    db = detect_db(journal)
    counts = {}
    version = 0
    media = []
    artifacts = journal / "artifacts"
    if artifacts.exists():
        for path in sorted(artifacts.glob("**/*.gif")):
            try:
                stat = path.stat()
            except OSError:
                continue
            media.append([str(path.relative_to(journal)), stat.st_size, stat.st_mtime_ns])
    try:
        con = sqlite3.connect(db)
        ensure_frontend_hooks(con)
        version = con.execute("select version from frontend_state where id = 1").fetchone()[0]
        for table in [*WATCH_TABLES, *CONTROL_TABLES]:
            counts[table] = con.execute(f"select count(*) from {table}").fetchone()[0]
        con.close()
    except sqlite3.Error as exc:
        counts["error"] = str(exc)
    raw = json.dumps({
        "journal": str(journal),
        "counts": counts,
        "media": media,
        "version": version,
    }, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16], counts


def frontend_signature():
    root = Path(__file__).resolve().parents[1]
    parts = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in FRONTEND_WATCH_EXTS:
            continue
        if any(part in {"dist", "node_modules"} for part in path.relative_to(root).parts):
            continue
        stat = path.stat()
        parts.append((str(path.relative_to(root)), stat.st_mtime_ns, stat.st_size))
    raw = json.dumps(parts, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def ensure_frontend_hooks(con):
    ensure_control_tables(con)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS frontend_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
        """
    )
    con.execute(
        """
        INSERT OR IGNORE INTO frontend_state (id, version, updated_at)
        VALUES (1, 0, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        """
    )
    for table in [*WATCH_TABLES, *CONTROL_TABLES]:
        for op, event in [("ai", "INSERT"), ("au", "UPDATE"), ("ad", "DELETE")]:
            con.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS frontend_{table}_{op}
                AFTER {event} ON {table}
                BEGIN
                    UPDATE frontend_state
                    SET version = version + 1,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    WHERE id = 1;
                END
                """
            )
    con.commit()


def ensure_control_tables(con):
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


def changelog_path(journal):
    return journal / CHANGELOG_NAME


def append_changelog_if_changed(journal):
    db_hash, counts = db_signature(journal)
    payload = build_payload(journal)
    path = changelog_path(journal)
    if not any(counts.get(table, 0) for table in WATCH_TABLES):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return payload, db_hash, counts
    last_hash = None
    if path.exists():
        try:
            with path.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                pos = fh.tell()
                buf = b""
                while pos > 0 and b"\n" not in buf[:-1]:
                    step = min(4096, pos)
                    pos -= step
                    fh.seek(pos)
                    buf = fh.read(step) + buf
                lines = [line for line in buf.splitlines() if line.strip()]
                if lines:
                    last_hash = json.loads(lines[-1])["hash"]
        except (OSError, json.JSONDecodeError, KeyError):
            last_hash = None
    if last_hash != db_hash:
        record = {
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "hash": db_hash,
            "counts": counts,
            "payload": payload,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    return payload, db_hash, counts


def read_changelog(journal):
    path = changelog_path(journal)
    frames = []
    if not path.exists():
        return frames
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Keep the replay payload useful but bounded for polling.
            counts = frame.get("counts", {})
            if not any(counts.get(table, 0) for table in WATCH_TABLES):
                continue
            frames.append({
                "captured_at": frame.get("captured_at"),
                "hash": frame.get("hash"),
                "counts": counts,
                "meta": (frame.get("payload") or {}).get("meta", {}),
            })
    return frames


def artifact_path_from_query(journal, raw_path):
    if not journal or not raw_path:
        return None
    try:
        target = Path(raw_path).expanduser().resolve()
        target.relative_to(journal.resolve())
    except (OSError, ValueError):
        return None
    return target if target.exists() and target.is_file() else None


def artifact_path_from_any_journal(raw_path):
    for journal in discover_journals():
        target = artifact_path_from_query(journal, raw_path)
        if target:
            return target
    return None


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_json_body(handler):
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


def hypothesis_exists(con, hyp_id):
    row = con.execute("SELECT id FROM hypotheses WHERE id = ?", (hyp_id,)).fetchone()
    return row is not None


def halted_ancestors(con, hyp_id):
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


def next_control_hyp_id(con):
    n = con.execute("SELECT COUNT(*) AS n FROM hypotheses WHERE id LIKE 'user-hyp-%'").fetchone()["n"]
    while True:
        n += 1
        hyp_id = f"user-hyp-{n:04d}"
        if not hypothesis_exists(con, hyp_id):
            return hyp_id


def insert_control_hypothesis(con, *, title, rationale, movement, parent_id, priority, context):
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


class AutoresearchHandler(SimpleHTTPRequestHandler):
    def end_no_cache_headers(self, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/experiments/"):
            try:
                target = (REPO_ROOT / path.lstrip("/")).resolve()
                target.relative_to((REPO_ROOT / "experiments").resolve())
            except (OSError, ValueError):
                self.send_error(404)
                return
            if not target.exists() or not target.is_file():
                self.send_error(404)
                return
            self.end_no_cache_headers(mimetypes.guess_type(str(target))[0] or "application/octet-stream")
            self.wfile.write(target.read_bytes())
            return

        if path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            last_hash = None
            last_heartbeat = 0.0
            try:
                while True:
                    journal = journal_from_request(self)
                    now = time.time()
                    if journal and detect_db(journal).exists():
                        db_hash, counts = db_signature(journal)
                        if db_hash != last_hash:
                            payload = {
                                "journal": str(journal),
                                "hash": db_hash,
                                "counts": counts,
                            }
                            self.wfile.write(b"event: change\n")
                            self.wfile.write(f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8"))
                            self.wfile.flush()
                            last_hash = db_hash
                            last_heartbeat = now
                        elif now - last_heartbeat >= 15:
                            self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                            last_heartbeat = now
                    elif last_hash is not None:
                        self.wfile.write(b"event: missing\n")
                        self.wfile.write(b"data: {\"journal\":null}\n\n")
                        self.wfile.flush()
                        last_hash = None
                        last_heartbeat = now
                    time.sleep(1)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        if path == "/api/dev-events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            last_hash = frontend_signature()
            last_heartbeat = time.time()
            try:
                while True:
                    now = time.time()
                    current_hash = frontend_signature()
                    if current_hash != last_hash:
                        payload = {"hash": current_hash}
                        self.wfile.write(b"event: reload\n")
                        self.wfile.write(f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        last_hash = current_hash
                        last_heartbeat = now
                    elif now - last_heartbeat >= 15:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        last_heartbeat = now
                    time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        if path == "/real-data.js":
            journal = journal_from_request(self)
            self.end_no_cache_headers("text/javascript; charset=utf-8")
            if not journal:
                self.wfile.write(b"console.warn('Autoresearch: no team_journal.db found; using mock data');\n")
                return
            try:
                payload, _, _ = append_changelog_if_changed(journal)
                self.wfile.write(render_js(payload).encode("utf-8"))
            except Exception as exc:
                msg = json.dumps(f"Autoresearch real-data load failed: {exc}")
                self.wfile.write(f"console.error({msg});\n".encode("utf-8"))
            return

        if path == "/real-runs.js":
            self.end_no_cache_headers("text/javascript; charset=utf-8")
            try:
                journals = discover_journals()
                runs = build_runs(journals)
                if not runs and not journals:
                    self.wfile.write(b"console.warn('Autoresearch: no populated journals; using mock runs');\n")
                    return
                self.wfile.write(render_runs_js(runs).encode("utf-8"))
            except Exception as exc:
                msg = json.dumps(f"Autoresearch real-runs load failed: {exc}")
                self.wfile.write(f"console.error({msg});\n".encode("utf-8"))
            return

        if path == "/api/tasks":
            journal = journal_from_request(self)
            self.end_no_cache_headers("application/json; charset=utf-8")
            self.wfile.write(json.dumps({
                "selected": journal_id(journal) if journal else None,
                "tasks": build_tasks(discover_journals(), journal),
            }, separators=(",", ":")).encode("utf-8"))
            return

        if path == "/api/meta":
            journal = journal_from_request(self)
            payload = {"journal": str(journal) if journal else None, "hash": None, "counts": {}}
            if journal:
                _, payload["hash"], payload["counts"] = append_changelog_if_changed(journal)
                payload["changelog"] = str(changelog_path(journal))
            self.end_no_cache_headers("application/json; charset=utf-8")
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        if path == "/api/data":
            journal = journal_from_request(self)
            self.end_no_cache_headers("application/json; charset=utf-8")
            if not journal:
                self.wfile.write(json.dumps({"journal": None, "payload": None}).encode("utf-8"))
                return
            try:
                payload, db_hash, counts = append_changelog_if_changed(journal)
                self.wfile.write(json.dumps({
                    "journal": str(journal),
                    "db": str(detect_db(journal)),
                    "hash": db_hash,
                    "counts": counts,
                    "payload": payload,
                }, separators=(",", ":")).encode("utf-8"))
            except Exception as exc:
                self.wfile.write(json.dumps({"journal": str(journal), "error": str(exc)}).encode("utf-8"))
            return

        if path == "/api/changelog":
            journal = journal_from_request(self)
            payload = {"journal": str(journal) if journal else None, "frames": []}
            if journal:
                append_changelog_if_changed(journal)
                payload["changelog"] = str(changelog_path(journal))
                payload["frames"] = read_changelog(journal)
            self.end_no_cache_headers("application/json; charset=utf-8")
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        if path == "/api/trace":
            # LIVE real run-playback trace for one node: reads its best.ir and
            # runs the experiment's real simulator on demand. ?node=<hyp id>
            # &journal=<id> (optional, to target a specific Compare run).
            q = parse_qs(urlparse(self.path).query)
            node_id = (q.get("node") or [None])[0]
            journal = None
            jsel = (q.get("journal") or [None])[0]
            if jsel:
                journal = next((j for j in discover_journals() if j.name == jsel
                                or j.parent.name == jsel), None)
            journal = journal or journal_from_request(self)
            self.end_no_cache_headers("application/json; charset=utf-8")
            if not journal or not node_id:
                self.wfile.write(json.dumps({"ok": False, "error": "missing journal or node"}).encode("utf-8"))
                return
            try:
                tr = node_trace(journal, node_id)
                self.wfile.write(json.dumps(tr or {"ok": False, "error": "no_artifact"},
                                            separators=(",", ":")).encode("utf-8"))
            except Exception as exc:
                self.wfile.write(json.dumps({"ok": False, "error": str(exc)}).encode("utf-8"))
            return

        if path == "/api/artifact":
            q = parse_qs(urlparse(self.path).query)
            target = artifact_path_from_any_journal((q.get("path") or [""])[0])
            if not target:
                self.send_error(404)
                return
            self.end_no_cache_headers(mimetypes.guess_type(str(target))[0] or "application/octet-stream")
            self.wfile.write(target.read_bytes())
            return

        super().do_GET()

    def send_json(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/control/"):
            self.send_error(404)
            return
        journal = journal_from_request(self)
        if not journal:
            self.send_json(404, {"ok": False, "error": "no journal DB found"})
            return
        db = detect_db(journal)
        try:
            data = read_json_body(self)
            con = sqlite3.connect(db)
            con.row_factory = sqlite3.Row
            ensure_frontend_hooks(con)
            stamp = now_iso()
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
                self.send_json(200, {"ok": True, "branchId": node_id, "status": "halted"})
            elif path == "/api/control/unhalt":
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
                self.send_json(200, {"ok": True, "branchId": node_id, "status": "active"})
            elif path == "/api/control/inject":
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
                priority = int(data.get("priority") or 60)
                hyp_id = insert_control_hypothesis(
                    con,
                    title=("User injected branch" if node_id else "User open hypothesis"),
                    rationale=text,
                    movement="User-injected information should be prioritized by implementors.",
                    parent_id=node_id,
                    priority=priority,
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
                self.send_json(200, {"ok": True, "hypothesisId": hyp_id})
            elif path == "/api/control/transfer":
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
                priority = int(data.get("priority") or 70)
                hyp_id = insert_control_hypothesis(
                    con,
                    title=f"User gene transfer: {source_id[:10]} -> {target_id[:10]}",
                    rationale=note or f"Transfer implementation structure from {source['title']} into {target['title']}.",
                    movement="Recombine source branch information into the selected destination branch.",
                    parent_id=target_id,
                    priority=priority,
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
                self.send_json(200, {"ok": True, "hypothesisId": hyp_id})
            else:
                self.send_json(404, {"ok": False, "error": "unknown control endpoint"})
            con.close()
        except Exception as exc:
            try:
                con.close()
            except Exception:
                pass
            self.send_json(400, {"ok": False, "error": str(exc)})

    def end_headers(self):
        if self.path.endswith((".js", ".jsx", ".css", ".html")):
            self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()


def main():
    port = int(os.environ.get("PORT", "5174"))
    os.chdir(Path(__file__).resolve().parents[1])
    server = ThreadingHTTPServer(("127.0.0.1", port), AutoresearchHandler)
    journal = pick_journal()
    print(f"Autoresearch server http://127.0.0.1:{port}/")
    print(f"Journal: {journal if journal else 'none found'}")
    server.serve_forever()


if __name__ == "__main__":
    main()
