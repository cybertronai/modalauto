from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import controls


CHANGELOG_NAME = "frontend_changelog.jsonl"
WATCH_TABLES = ["agents", "hypotheses", "submissions", "verifications", "manager_events"]
FRONTEND_WATCH_EXTS = {".html", ".css", ".js", ".jsx", ".ts", ".tsx"}


def ensure_frontend_hooks(con: sqlite3.Connection) -> None:
    controls.ensure_control_tables(con)
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
    for table in [*WATCH_TABLES, *controls.CONTROL_TABLES]:
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


def db_signature(journal: Path, detect_db) -> tuple[str, dict]:
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
        for table in [*WATCH_TABLES, *controls.CONTROL_TABLES]:
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


def frontend_signature(frontend_root: Path) -> str:
    parts = []
    for path in sorted(frontend_root.rglob("*")):
        if not path.is_file() or path.suffix not in FRONTEND_WATCH_EXTS:
            continue
        if any(part in {"dist", "node_modules"} for part in path.relative_to(frontend_root).parts):
            continue
        stat = path.stat()
        parts.append((str(path.relative_to(frontend_root)), stat.st_mtime_ns, stat.st_size))
    raw = json.dumps(parts, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def changelog_path(journal: Path) -> Path:
    return journal / CHANGELOG_NAME


def append_changelog_if_changed(journal: Path, detect_db, build_payload):
    db_hash, counts = db_signature(journal, detect_db)
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
                fh.seek(0, 2)
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


def read_changelog(journal: Path) -> list[dict]:
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
