#!/usr/bin/env python3
import argparse
import json
import mimetypes
import os
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import controls
from export_real_data import build_payload, detect_db, render_js, render_runs_js, node_trace
import journals as journal_store
import live_state


mimetypes.add_type("text/typescript; charset=utf-8", ".ts")
mimetypes.add_type("text/typescript; charset=utf-8", ".tsx")

AUTORESEARCH_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = AUTORESEARCH_ROOT
START_CWD = Path.cwd().resolve()
def discover_journals():
    return journal_store.discover_journals(AUTORESEARCH_ROOT, detect_db, START_CWD)


def request_query(handler):
    query = urlparse(handler.path).query
    if query:
        return query
    referer = handler.headers.get("Referer") or handler.headers.get("Referrer") or ""
    return urlparse(referer).query


def journal_from_request(handler):
    return journal_store.select_journal(request_query(handler), AUTORESEARCH_ROOT, detect_db, START_CWD)


def build_runs(journals):
    """Build a Compare run per journal that actually has tree nodes."""
    runs = []
    for journal in journals:
        payload = build_payload(journal)
        if not payload.get("nodes"):
            continue
        meta = payload["meta"]
        meta["label"] = journal_store.run_label(journal)
        best = meta.get("best")
        desc = f"{meta.get('totalNodes', 0)} hypotheses"
        if best is not None:
            desc += f" · best {best:,}"
        runs.append({"id": journal_store.journal_id(journal), "label": journal_store.run_label(journal), "desc": desc, "payload": payload})
    return runs


def build_tasks(journal_list, selected=None):
    return journal_store.build_tasks(journal_list, selected, build_payload)


def ensure_frontend_hooks(con):
    live_state.ensure_frontend_hooks(con)


def append_changelog_if_changed(journal):
    return live_state.append_changelog_if_changed(journal, detect_db, build_payload)


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


class AutoresearchHandler(SimpleHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            return

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
                        db_hash, counts = live_state.db_signature(journal, detect_db)
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
            last_hash = live_state.frontend_signature(Path(__file__).resolve().parents[1])
            last_heartbeat = time.time()
            try:
                while True:
                    now = time.time()
                    current_hash = live_state.frontend_signature(Path(__file__).resolve().parents[1])
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
                "selected": journal_store.journal_id(journal) if journal else None,
                "tasks": build_tasks(discover_journals(), journal),
            }, separators=(",", ":")).encode("utf-8"))
            return

        if path == "/api/meta":
            journal = journal_from_request(self)
            payload = {"journal": str(journal) if journal else None, "hash": None, "counts": {}}
            if journal:
                _, payload["hash"], payload["counts"] = append_changelog_if_changed(journal)
                payload["changelog"] = str(live_state.changelog_path(journal))
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
                payload["changelog"] = str(live_state.changelog_path(journal))
                payload["frames"] = live_state.read_changelog(journal)
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
        controls.handle_control_post(self, path, journal_from_request(self), detect_db, ensure_frontend_hooks)

    def end_headers(self):
        path = urlparse(self.path).path
        if path == "/" or path.endswith((".js", ".jsx", ".ts", ".tsx", ".css", ".html")):
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Serve the autoresearch dashboard.")
    parser.add_argument("--journal", type=Path, help="experiment journal directory to serve")
    parser.add_argument("--default-journal", help="journal id to show in the printed dashboard URL without pinning discovery")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5174")))
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.journal:
        os.environ["FRONTEND_JOURNAL"] = str(args.journal)
    os.chdir(Path(__file__).resolve().parents[1])
    server = ThreadingHTTPServer((args.host, args.port), AutoresearchHandler)
    journal = journal_store.pick_journal(AUTORESEARCH_ROOT, detect_db, START_CWD)
    suffix = f"?journal={args.default_journal}" if args.default_journal else ""
    print(f"Autoresearch server http://{args.host}:{args.port}/{suffix}", flush=True)
    print(f"Journal: {journal if journal else 'none found'}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
