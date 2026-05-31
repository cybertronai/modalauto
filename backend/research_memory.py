#!/usr/bin/env python3
"""Research-paper memory for async researcher agents.

Inspired by Aria's split between saved items, agent extractions, user TLDRs,
and vector search. This version is deliberately local and simple: SQLite plus
JSON-encoded embeddings. Embeddings default to deterministic hashed vectors so
the system works without an API key; set OPENAI_API_KEY to swap in real
embeddings later if desired.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoresearch.backend import experiment_config


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = experiment_config.DEFAULT_RESEARCH_DB
ARXIV_API = "https://export.arxiv.org/api/query"
EMBED_DIMS = 384


SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id             TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    source_id      TEXT,
    url            TEXT,
    title          TEXT NOT NULL,
    authors        TEXT,
    abstract       TEXT,
    tags_json      TEXT NOT NULL DEFAULT '[]',
    status         TEXT NOT NULL DEFAULT 'inbox'
                   CHECK (status IN ('inbox', 'active', 'archived')),
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS paper_notes (
    id             TEXT PRIMARY KEY,
    paper_id       TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    agent_id       TEXT,
    tldr           TEXT,
    intuition      TEXT,
    empirics       TEXT,
    details        TEXT,
    key_claims_json TEXT NOT NULL DEFAULT '[]',
    relevance      TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_embeddings (
    paper_id       TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    model          TEXT NOT NULL,
    text_hash      TEXT NOT NULL,
    dims           INTEGER NOT NULL,
    vector_json    TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS researcher_tasks (
    id             TEXT PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'claimed', 'done', 'failed')),
    query          TEXT NOT NULL,
    reason         TEXT,
    claimed_by     TEXT,
    result_json    TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_source ON papers(source, source_id);
CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_notes_paper ON paper_notes(paper_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON researcher_tasks(status, created_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path), timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.row_factory = sqlite3.Row
    return db


def init_db(db_path: Path) -> None:
    db = connect(db_path)
    db.executescript(SCHEMA)
    db.commit()
    db.close()


def slug(s: str) -> str:
    out = []
    prev_dash = False
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-") or "untitled"


def next_id(db: sqlite3.Connection, prefix: str, table: str) -> str:
    row = db.execute(f"SELECT id FROM {table} WHERE id LIKE ? ORDER BY id DESC LIMIT 1", (f"{prefix}-%",)).fetchone()
    if row is None:
        return f"{prefix}-001"
    try:
        n = int(str(row["id"]).split("-")[-1])
    except ValueError:
        return f"{prefix}-001"
    return f"{prefix}-{n + 1:03d}"


def normalize_arxiv_id(value: str) -> str | None:
    value = value.strip().replace(".pdf", "")
    value = re.sub(r"^arxiv:", "", value, flags=re.I)
    value = re.sub(r"^(abs|pdf|html)/", "", value, flags=re.I)
    try:
        parsed = urllib.parse.urlparse(value)
        if parsed.netloc:
            parts = [p for p in parsed.path.split("/") if p]
            if parts and parts[0].lower() in {"abs", "pdf", "html"}:
                value = "/".join(parts[1:])
    except Exception:
        pass
    match = re.match(r"^((?:[a-z-]+(?:\.[A-Z]{2})?/\d{7})|(?:\d{4}\.\d{4,5}))(v\d+)?$", value, re.I)
    return f"{match.group(1)}{match.group(2) or ''}" if match else None


def arxiv_query(search: str, max_results: int) -> list[dict[str, Any]]:
    params = {"search_query": search, "start": "0", "max_results": str(max_results)}
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "autoresearch-researcher/0.1"})
    with urllib.request.urlopen(req, timeout=30) as res:
        xml = res.read()
    root = ET.fromstring(xml)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    out = []
    for entry in root.findall("atom:entry", ns):
        id_url = entry.findtext("atom:id", default="", namespaces=ns)
        title = " ".join(entry.findtext("atom:title", default="", namespaces=ns).split())
        abstract = " ".join(entry.findtext("atom:summary", default="", namespaces=ns).split())
        authors = ", ".join(
            name.text.strip()
            for name in entry.findall("atom:author/atom:name", ns)
            if name.text
        )
        arxiv_id = normalize_arxiv_id(id_url) or id_url.rsplit("/", 1)[-1]
        out.append({
            "source": "arxiv",
            "source_id": arxiv_id,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "title": title,
            "authors": authors,
            "abstract": abstract,
        })
    return out


def paper_text(paper: sqlite3.Row | dict[str, Any], note: sqlite3.Row | dict[str, Any] | None = None) -> str:
    parts = [
        paper["title"],
        paper["authors"] or "",
        paper["abstract"] or "",
    ]
    if note:
        parts.extend([
            note["tldr"] or "",
            note["intuition"] or "",
            note["empirics"] or "",
            note["details"] or "",
            note["relevance"] or "",
        ])
    return "\n\n".join(p for p in parts if p)


def hashed_embedding(text: str, dims: int = EMBED_DIMS) -> list[float]:
    vec = [0.0] * dims
    tokens = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    for token in tokens:
        h = int(hashlib.blake2b(token.encode(), digest_size=8).hexdigest(), 16)
        idx = h % dims
        sign = -1.0 if (h >> 8) & 1 else 1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def upsert_paper(db: sqlite3.Connection, paper: dict[str, Any], tags: list[str] | None = None) -> str:
    stamp = now()
    source = paper.get("source") or "manual"
    source_id = paper.get("source_id") or slug(paper["title"])[:80]
    existing = db.execute(
        "SELECT id FROM papers WHERE source = ? AND source_id = ?",
        (source, source_id),
    ).fetchone()
    paper_id = existing["id"] if existing else next_id(db, "paper", "papers")
    db.execute(
        """
        INSERT INTO papers
            (id, source, source_id, url, title, authors, abstract, tags_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, source_id) DO UPDATE SET
            url = excluded.url,
            title = excluded.title,
            authors = excluded.authors,
            abstract = excluded.abstract,
            tags_json = excluded.tags_json,
            updated_at = excluded.updated_at
        """,
        (
            paper_id,
            source,
            source_id,
            paper.get("url"),
            paper["title"],
            paper.get("authors"),
            paper.get("abstract"),
            json.dumps(tags or paper.get("tags") or []),
            stamp,
            stamp,
        ),
    )
    refresh_embedding(db, paper_id)
    return paper_id


def latest_note(db: sqlite3.Connection, paper_id: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM paper_notes WHERE paper_id = ? ORDER BY updated_at DESC LIMIT 1",
        (paper_id,),
    ).fetchone()


def refresh_embedding(db: sqlite3.Connection, paper_id: str) -> None:
    paper = db.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if paper is None:
        return
    note = latest_note(db, paper_id)
    text = paper_text(paper, note)
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    vector = hashed_embedding(text)
    db.execute(
        """
        INSERT INTO paper_embeddings (paper_id, model, text_hash, dims, vector_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(paper_id) DO UPDATE SET
            model = excluded.model,
            text_hash = excluded.text_hash,
            dims = excluded.dims,
            vector_json = excluded.vector_json,
            created_at = excluded.created_at
        """,
        (paper_id, "hashing-blake2b-384", text_hash, len(vector), json.dumps(vector), now()),
    )


def claim_research_task(db: sqlite3.Connection, agent_id: str) -> sqlite3.Row | None:
    row = db.execute(
        """
        SELECT * FROM researcher_tasks
        WHERE status = 'queued'
        ORDER BY created_at ASC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    stamp = now()
    db.execute(
        """
        UPDATE researcher_tasks
        SET status = 'claimed', claimed_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (agent_id, stamp, row["id"]),
    )
    return row


def complete_research_task(
    db: sqlite3.Connection,
    task_id: str,
    status: str,
    result: dict[str, Any],
) -> None:
    stamp = now()
    db.execute(
        """
        UPDATE researcher_tasks
        SET status = ?, result_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, json.dumps(result, sort_keys=True), stamp, task_id),
    )


def cmd_init(args: argparse.Namespace) -> int:
    init_db(args.db)
    print(json.dumps({"db": str(args.db)}, indent=2))
    return 0


def cmd_fetch_arxiv(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    query = args.query
    if normalize_arxiv_id(query):
        query = f"id:{normalize_arxiv_id(query)}"
    papers = arxiv_query(query, args.max_results)
    rows = []
    for paper in papers:
        paper_id = upsert_paper(db, paper, args.tag)
        rows.append({"paper_id": paper_id, **paper})
    db.commit()
    db.close()
    print(json.dumps(rows, indent=2, sort_keys=True))
    return 0


def cmd_add_paper(args: argparse.Namespace) -> int:
    init_db(args.db)
    paper = {
        "source": args.source,
        "source_id": args.source_id or args.url or slug(args.title),
        "url": args.url,
        "title": args.title,
        "authors": args.authors,
        "abstract": args.abstract,
    }
    db = connect(args.db)
    paper_id = upsert_paper(db, paper, args.tag)
    db.commit()
    db.close()
    print(json.dumps({"paper_id": paper_id}, indent=2))
    return 0


def cmd_add_note(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    stamp = now()
    note_id = args.note_id or next_id(db, "note", "paper_notes")
    db.execute(
        """
        INSERT INTO paper_notes
            (id, paper_id, agent_id, tldr, intuition, empirics, details,
             key_claims_json, relevance, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note_id,
            args.paper_id,
            args.agent_id,
            args.tldr,
            args.intuition,
            args.empirics,
            args.details,
            args.key_claims_json,
            args.relevance,
            stamp,
            stamp,
        ),
    )
    refresh_embedding(db, args.paper_id)
    db.commit()
    db.close()
    print(json.dumps({"note_id": note_id}, indent=2))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    init_db(args.db)
    qvec = hashed_embedding(args.query)
    db = connect(args.db)
    rows = db.execute(
        """
        SELECT p.*, e.vector_json
        FROM papers p
        JOIN paper_embeddings e ON e.paper_id = p.id
        """
    ).fetchall()
    scored = []
    for row in rows:
        vec = json.loads(row["vector_json"])
        scored.append((cosine(qvec, vec), row))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, row in scored[:args.limit]:
        out.append({
            "score": round(score, 4),
            "paper_id": row["id"],
            "title": row["title"],
            "authors": row["authors"],
            "url": row["url"],
            "abstract": (row["abstract"] or "")[:500],
        })
    db.close()
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


def cmd_import_aria_csv(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    rows = []
    with args.csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            clean_row = {k.lstrip("\ufeff"): v for k, v in row.items()}
            title = clean_row.get("Name") or clean_row.get("title") or clean_row.get("Title")
            url = clean_row.get("URL") or clean_row.get("url") or clean_row.get("Link")
            tags = []
            raw_tags = clean_row.get("Tags") or clean_row.get("tags") or ""
            if raw_tags:
                tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
            if not title:
                continue
            paper = {
                "source": "aria_csv",
                "source_id": url or slug(title),
                "url": url,
                "title": title,
                "authors": clean_row.get("Author") or clean_row.get("Creator") or clean_row.get("authors"),
                "abstract": clean_row.get("Abstract") or clean_row.get("abstract"),
            }
            paper_id = upsert_paper(db, paper, tags)
            rows.append({"paper_id": paper_id, "title": title})
    db.commit()
    db.close()
    print(json.dumps({"imported": len(rows), "rows": rows[:20]}, indent=2, sort_keys=True))
    return 0


def cmd_add_task(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    stamp = now()
    task_id = args.task_id or next_id(db, "rtask", "researcher_tasks")
    db.execute(
        """
        INSERT INTO researcher_tasks
            (id, status, query, reason, created_at, updated_at)
        VALUES (?, 'queued', ?, ?, ?, ?)
        """,
        (task_id, args.query, args.reason or "", stamp, stamp),
    )
    db.commit()
    db.close()
    print(json.dumps({"task_id": task_id}, indent=2))
    return 0


def cmd_claim_task(args: argparse.Namespace) -> int:
    init_db(args.db)
    db = connect(args.db)
    row = claim_research_task(db, args.agent_id)
    db.commit()
    db.close()
    print(json.dumps({"claimed": dict(row) if row else None}, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", help="experiment name under experiments/")
    parser.add_argument("--experiment-root", type=Path, help="experiment directory containing journal/ and worktrees/")
    parser.add_argument("--db", type=Path)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.set_defaults(func=cmd_init)

    p_arxiv = sub.add_parser("fetch-arxiv")
    p_arxiv.add_argument("query")
    p_arxiv.add_argument("--max-results", type=int, default=3)
    p_arxiv.add_argument("--tag", action="append", default=[])
    p_arxiv.set_defaults(func=cmd_fetch_arxiv)

    p_add = sub.add_parser("add-paper")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--url")
    p_add.add_argument("--source", default="manual")
    p_add.add_argument("--source-id")
    p_add.add_argument("--authors")
    p_add.add_argument("--abstract")
    p_add.add_argument("--tag", action="append", default=[])
    p_add.set_defaults(func=cmd_add_paper)

    p_note = sub.add_parser("add-note")
    p_note.add_argument("--note-id")
    p_note.add_argument("--paper-id", required=True)
    p_note.add_argument("--agent-id")
    p_note.add_argument("--tldr")
    p_note.add_argument("--intuition")
    p_note.add_argument("--empirics")
    p_note.add_argument("--details")
    p_note.add_argument("--key-claims-json", default="[]")
    p_note.add_argument("--relevance")
    p_note.set_defaults(func=cmd_add_note)

    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=5)
    p_search.set_defaults(func=cmd_search)

    p_import = sub.add_parser("import-aria-csv")
    p_import.add_argument("csv_path", type=Path)
    p_import.set_defaults(func=cmd_import_aria_csv)

    p_task = sub.add_parser("add-task")
    p_task.add_argument("--task-id")
    p_task.add_argument("--query", required=True)
    p_task.add_argument("--reason")
    p_task.set_defaults(func=cmd_add_task)

    p_claim = sub.add_parser("claim-task")
    p_claim.add_argument("--agent-id", required=True)
    p_claim.set_defaults(func=cmd_claim_task)

    args = parser.parse_args(argv)
    exp = experiment_config.layout(args.experiment, args.experiment_root)
    args.experiment_root = exp.root
    args.db = (args.db or exp.research_db).expanduser().resolve()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
