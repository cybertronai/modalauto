#!/usr/bin/env python3
"""Live JSON API for autoresearch journals used by the frontend."""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

AUTORESEARCH_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = AUTORESEARCH_ROOT
if str(AUTORESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(AUTORESEARCH_ROOT))

from autoresearch.backend import experiment_config


FRONTEND_SCRIPTS = AUTORESEARCH_ROOT / "frontend" / "scripts"
if str(FRONTEND_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(FRONTEND_SCRIPTS))

from export_real_data import build_payload, detect_db, node_trace  # noqa: E402


def journal_mtime_ns(journal: Path) -> int:
    newest = 0
    db = detect_db(journal)
    for suffix in ["", "-wal", "-shm"]:
        path = Path(str(db) + suffix)
        if path.exists():
            newest = max(newest, path.stat().st_mtime_ns)
    return newest


def pick_journal(configured: Path | None = None) -> Path | None:
    if configured:
        path = configured.expanduser().resolve()
        return path if detect_db(path).exists() else None
    env = os.environ.get("FRONTEND_JOURNAL")
    if env:
        path = Path(env).expanduser().resolve()
        return path if detect_db(path).exists() else None
    candidates = [path for path in (AUTORESEARCH_ROOT / "experiments").glob("*/journal") if detect_db(path).exists()]
    return max(candidates, key=journal_mtime_ns) if candidates else None


def journal_id(journal: Path) -> str:
    return journal.parent.name if journal.name == "journal" else journal.name


def run_label(journal: Path) -> str:
    name = journal_id(journal)
    return name.replace("_", " ").replace("-", " ").title() if name else "Main Run"


def discover_journals(configured: Path | None = None) -> list[Path]:
    if configured:
        journal = configured.expanduser().resolve()
        return [journal] if detect_db(journal).exists() else []
    env = os.environ.get("FRONTEND_JOURNAL")
    if env:
        journal = Path(env).expanduser().resolve()
        return [journal] if detect_db(journal).exists() else []
    candidates = [path for path in (AUTORESEARCH_ROOT / "experiments").glob("*/journal") if detect_db(path).exists()]
    return sorted(candidates, key=journal_mtime_ns, reverse=True)


def select_journal(query: str, configured: Path | None = None) -> Path | None:
    selected = (parse_qs(query).get("journal") or parse_qs(query).get("task") or [None])[0]
    if selected:
        for journal in discover_journals(configured):
            if selected in {journal_id(journal), journal.name, journal.parent.name}:
                return journal
        return None
    return pick_journal(configured)


def build_tasks(journals: list[Path], selected: Path | None = None) -> list[dict]:
    tasks = []
    for journal in journals:
        payload = build_payload(journal)
        meta = payload.get("meta", {})
        best = meta.get("best")
        desc = f"{meta.get('totalNodes', 0)} hypotheses"
        if best is not None:
            desc += f" · best {best:,}"
        tasks.append({
            "id": journal_id(journal),
            "label": run_label(journal),
            "desc": desc,
            "journal": str(journal),
            "selected": bool(selected and journal.resolve() == selected.resolve()),
        })
    return tasks


class Handler(BaseHTTPRequestHandler):
    journal: Path | None = None

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path not in {"/api/data", "/api/status", "/api/tasks", "/api/trace"}:
            self.send_json({"error": "not_found"}, 404)
            return
        journal = select_journal(parsed.query, self.journal)
        if path == "/api/tasks":
            self.send_json({"selected": journal_id(journal) if journal else None, "tasks": build_tasks(discover_journals(self.journal), journal)})
            return
        if not journal:
            self.send_json({"journal": None, "payload": None, "error": "no_journal"})
            return
        if path == "/api/status":
            self.send_json({"journal": str(journal), "db": str(detect_db(journal))})
            return
        if path == "/api/trace":
            from urllib.parse import parse_qs
            node_id = (parse_qs(parsed.query).get("node") or [None])[0]
            if not node_id:
                self.send_json({"ok": False, "error": "missing node"}, 400)
                return
            tr = node_trace(journal, node_id)
            self.send_json(tr or {"ok": False, "error": "no_artifact"})
            return
        payload = build_payload(journal)
        self.send_json({"journal": str(journal), "db": str(detect_db(journal)), "payload": payload})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", help="experiment name under experiments/")
    parser.add_argument("--experiment-root", type=Path, help="experiment directory containing journal/ and worktrees/")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("FRONTEND_API_PORT", "5175")))
    args = parser.parse_args(argv)
    if not args.journal and (args.experiment or args.experiment_root):
        args.journal = experiment_config.layout(args.experiment, args.experiment_root).journal_dir
    Handler.journal = args.journal
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Frontend API http://{args.host}:{args.port}/api/data")
    print(f"Journal: {pick_journal(args.journal) or 'none found'}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
