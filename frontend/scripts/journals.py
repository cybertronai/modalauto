from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import parse_qs


def configured_journal_path(start_cwd: Path | None = None) -> Path | None:
    configured = os.environ.get("FRONTEND_JOURNAL")
    if not configured:
        return None
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = (start_cwd or Path.cwd()) / path
    return path.resolve()


def journal_mtime_ns(journal: Path, detect_db) -> int:
    newest = 0
    db = detect_db(journal)
    for suffix in ["", "-wal", "-shm"]:
        path = Path(str(db) + suffix)
        if path.exists():
            newest = max(newest, path.stat().st_mtime_ns)
    return newest


def discover_journals(root: Path, detect_db, start_cwd: Path | None = None) -> list[Path]:
    configured = configured_journal_path(start_cwd)
    if configured:
        return [configured] if detect_db(configured).exists() else []
    candidates = [path for path in (root / "experiments").glob("*/journal") if detect_db(path).exists()]
    return sorted(candidates, key=lambda path: journal_mtime_ns(path, detect_db), reverse=True)


def pick_journal(root: Path, detect_db, start_cwd: Path | None = None) -> Path | None:
    journals = discover_journals(root, detect_db, start_cwd)
    return journals[0] if journals else None


def journal_id(journal: Path) -> str:
    return journal.parent.name if journal.name == "journal" else journal.name


def run_label(journal: Path) -> str:
    name = re.sub(r"_?\d{8}T\d{6}$", "", journal_id(journal))
    return name.replace("_", " ").replace("-", " ").title() if name else "Main Run"


def select_journal(query: str, root: Path, detect_db, start_cwd: Path | None = None) -> Path | None:
    selected = (parse_qs(query).get("journal") or parse_qs(query).get("task") or [None])[0]
    if selected:
        for journal in discover_journals(root, detect_db, start_cwd):
            if selected in {journal_id(journal), journal.name, journal.parent.name}:
                return journal
        return None
    return pick_journal(root, detect_db, start_cwd)


def build_tasks(journals: list[Path], selected: Path | None, build_payload) -> list[dict]:
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
