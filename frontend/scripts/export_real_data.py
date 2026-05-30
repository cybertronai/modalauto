#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


DEFAULT_BASELINE = 0
DEFAULT_TARGET = 0
DEFAULT_PROBLEM = "experiment"
DEFAULT_METRIC = "score"
DEFAULT_DIRECTION = "minimize"

# Legacy aliases for older callers. Experiment-specific values come from
# experiments/<slug>/workflow.json.
BASELINE = DEFAULT_BASELINE
TARGET = DEFAULT_TARGET


def experiment_meta(journal: Path) -> dict:
    workflow_path = journal.parent / "workflow.json"
    exp_name = journal.parent.name or DEFAULT_PROBLEM
    if not workflow_path.exists():
        return {
            "direction": DEFAULT_DIRECTION,
            "metric": DEFAULT_METRIC,
            "problem": exp_name,
            "baseline": DEFAULT_BASELINE,
            "target": DEFAULT_TARGET,
            "domain": "custom",
            "visualizations": [],
        }
    w = json.loads(workflow_path.read_text())
    return {
        "direction": w.get("direction", DEFAULT_DIRECTION),
        "metric": w.get("primary_metric", DEFAULT_METRIC),
        "problem": w.get("description") or w.get("name") or exp_name,
        "baseline": w.get("baseline", DEFAULT_BASELINE),
        "target": w.get("target", DEFAULT_TARGET),
        "domain": w.get("domain", "custom"),
        "visualizations": w.get("visualizations", []),
    }


def is_maximize(meta: dict) -> bool:
    return str(meta.get("direction", DEFAULT_DIRECTION)).lower() == "maximize"


def parse_ts(value):
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def seconds(ts, start):
    if ts is None:
        return None
    return round((parse_ts(ts) - start).total_seconds(), 3)


def load_json(value, default):
    try:
        return json.loads(value) if value else default
    except json.JSONDecodeError:
        return default


def fit_bin(score, best):
    if score is None:
        return None
    span = max(1e-9, BASELINE - best)
    return max(0, min(6, round(((BASELINE - score) / span) * 6)))


def fit_bin_meta(score, best, meta):
    if score is None:
        return None
    baseline = meta.get("baseline", DEFAULT_BASELINE)
    if is_maximize(meta):
        span = max(1e-9, (best - baseline)) if best is not None else 1
        return max(0, min(6, round(((score - baseline) / span) * 6)))
    span = max(1e-9, (baseline - best)) if best is not None else 1
    return max(0, min(6, round(((baseline - score) / span) * 6)))


def bucketize(raw):
    if not raw:
        return None
    buckets = {
        "mul": int(raw.get("mul_reads") or raw.get("mul") or 0),
        "add": int(raw.get("add_reads") or raw.get("add") or 0),
        "copy": int(raw.get("copy_reads") or raw.get("copy") or 0),
        "load": int(raw.get("ops") or raw.get("load") or 0),
        "store": int(raw.get("output_reads") or raw.get("store") or 0),
    }
    return buckets if any(buckets.values()) else None


def score_from_verification(ver, fallback=None):
    if not ver:
        return fallback
    raw = load_json(ver["buckets_json"], {})
    if isinstance(raw, dict) and raw.get("score_float") is not None:
        return raw["score_float"]
    return ver.get("official_score") if ver.get("official_score") is not None else fallback


def normalize_display_parents(nodes):
    """Expose displayParent without inventing lineage beyond explicit parent ids."""
    roots = [
        node for node in nodes
        if not node.get("parent") and node.get("outcome") == "accept" and node.get("tVerified") is not None
    ]
    roots.sort(key=lambda node: (node.get("tVerified") or 0, node.get("tProposed") or 0, node["id"]))
    initial_root = roots[0]["id"] if roots else None
    for node in nodes:
        node["rawParent"] = node.get("parent")
        if node.get("parent"):
            node["displayParent"] = node.get("parent")
        elif initial_root and node["id"] != initial_root:
            node["displayParent"] = initial_root
        else:
            node["displayParent"] = None


def family_from(context, summary):
    best = summary.get("best") if isinstance(summary, dict) else {}
    if isinstance(best, dict) and best.get("family"):
        return best["family"]
    if isinstance(summary, dict) and summary.get("family"):
        return summary["family"]
    impl = context.get("implementation") if isinstance(context, dict) else {}
    op = impl.get("operator") if isinstance(impl, dict) else None
    if op:
        return op
    return context.get("source") or "real_run"


def find_hypothesis_ref(value):
    if isinstance(value, dict):
        direct = value.get("hypothesis_id") or value.get("parent_hypothesis_id") or value.get("source_insight_id")
        if direct:
            return str(direct)
        for nested in value.values():
            found = find_hypothesis_ref(nested)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_hypothesis_ref(item)
            if found:
                return found
    return None


def detect_db(journal, db_filename=None):
    if db_filename:
        return journal / db_filename
    for name in ("team.db", "team_journal.db"):
        path = journal / name
        if path.exists():
            return path
    return journal / "team_journal.db"


def _resolve_artifact(journal, artifact_path):
    """Resolve a submission's stored artifact_path to a real file on disk,
    tolerating absolute paths from another checkout / relative paths."""
    if not artifact_path:
        return None
    p = Path(artifact_path)
    candidates = [p, journal / artifact_path, journal / "artifacts" / p.name]
    # also try matching the leaf dir name under this journal's artifacts/
    if p.parent.name:
        candidates.append(journal / "artifacts" / p.parent.name / p.name)
    for c in candidates:
        if c.exists():
            return c
    return None


def file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        return "image"
    if suffix in {".mp4", ".webm", ".mov", ".m4v"}:
        return "video"
    if suffix == ".json":
        return "json"
    if suffix in {".csv", ".tsv"}:
        return "table"
    if suffix in {".md", ".txt", ".ir", ".py", ".log"}:
        return "text"
    if suffix == ".npy":
        return "array"
    return "file"


def file_item(path: Path) -> dict:
    return {
        "name": path.name,
        "path": str(path),
        "kind": file_kind(path),
        "size": path.stat().st_size if path.exists() else None,
    }


def text_preview(path: Path, limit: int = 6000) -> str | None:
    if not path.exists() or file_kind(path) not in {"text", "json", "table"}:
        return None
    try:
        raw = path.read_text(errors="replace")
    except OSError:
        return None
    return raw[:limit] + ("\n..." if len(raw) > limit else "")


def body_from_npy(path: Path) -> dict | None:
    if path.suffix.lower() != ".npy" or not path.exists():
        return None
    try:
        import numpy as np
        arr = np.load(path)
    except Exception:
        return None
    if getattr(arr, "ndim", None) != 2 or arr.size > 400:
        return None
    legend = {"0": "empty", "1": "rigid", "2": "soft", "3": "horizontal actuator", "4": "vertical actuator"}
    glyph = {0: "E", 1: "R", 2: "S", 3: "H", 4: "V"}
    grid = [[int(v) for v in row] for row in arr.tolist()]
    text = "\n".join(" ".join(glyph.get(int(v), "?") for v in row) for row in grid)
    return {"grid": grid, "text": text, "legend": legend}


def artifact_details(journal: Path, artifact_path_value, summary: dict, raw_buckets: dict) -> dict | None:
    path = _resolve_artifact(journal, artifact_path_value)
    if not path:
        return None
    artifact_root = path.parent.parent if path.parent.name in {"bodies", "policies"} else path.parent
    wanted = [path]
    for name in ["summary.json", "candidates.csv", "best.txt", "best.npy"]:
        p = artifact_root / name
        if p.exists() and p not in wanted:
            wanted.append(p)
    stem = path.stem
    media_patterns = [
        f"{stem}.png", f"{stem}.jpg", f"{stem}.jpeg", f"{stem}.gif", f"{stem}.webp", f"{stem}.svg",
        f"{stem}.mp4", f"{stem}.webm", f"{stem}.mov", f"{stem}.m4v",
        f"viz/{stem}.png", f"viz/{stem}.jpg", f"viz/{stem}.jpeg", f"viz/{stem}.gif", f"viz/{stem}.webp", f"viz/{stem}.svg",
        f"viz/{stem}.mp4", f"viz/{stem}.webm", f"viz/{stem}.mov", f"viz/{stem}.m4v",
    ]
    for pattern in media_patterns:
        for p in sorted(artifact_root.glob(pattern)):
            if p.exists() and p not in wanted:
                wanted.append(p)

    body = body_from_npy(path)
    metrics = {}
    if isinstance(summary, dict):
        metrics.update({k: v for k, v in summary.items() if k.startswith("n_")})
    if isinstance(raw_buckets, dict):
        for k in ["score_float", "score_std", "score_min", "score_max", "n_voxels", "stochastic", "seeds"]:
            if k in raw_buckets:
                metrics[k] = raw_buckets[k]

    return {
        "path": str(path),
        "root": str(artifact_root),
        "name": path.name,
        "kind": "voxel_body" if body else file_kind(path),
        "files": [file_item(p) for p in wanted if p.exists()],
        "images": [file_item(p) for p in wanted if p.exists() and file_kind(p) == "image"],
        "videos": [file_item(p) for p in wanted if p.exists() and file_kind(p) == "video"],
        "body": body,
        "preview": text_preview(path),
        "metrics": metrics,
    }


def _submission_artifact_dir(journal, artifact_path):
    art = _resolve_artifact(journal, artifact_path)
    if art:
        return art.parent if art.is_file() else art
    if not artifact_path:
        return journal / "artifacts"
    p = Path(artifact_path)
    if p.is_absolute():
        return p.parent if p.suffix else p
    return journal / "artifacts" / p.parent.name if p.parent.name else journal / "artifacts"


def _media_path(item):
    value = item.get("path") or item.get("href") or item.get("url") or item.get("file")
    return str(value) if value else None


def _media_candidates(context, summary):
    values = []
    for source in (context, summary):
        if not isinstance(source, dict):
            continue
        media = source.get("media")
        if isinstance(media, dict):
            if _media_path(media):
                values.append(media)
            else:
                for key, value in media.items():
                    if isinstance(value, dict):
                        values.append({"kind": key, **value})
                    elif value:
                        values.append({"kind": key, "path": value})
        elif isinstance(media, list):
            values.extend([item for item in media if isinstance(item, dict)])
        for key in ("timelapse", "timelapse_gif", "preview", "preview_image", "animation"):
            value = source.get(key)
            if value:
                values.append({"kind": "timelapse" if "timelapse" in key or key == "animation" else "preview", "path": value})
    return values


def _media_kind(item):
    kind = str(item.get("kind") or item.get("role") or item.get("name") or "preview").lower()
    if "gif" in kind or "time" in kind or "animation" in kind:
        return "timelapse"
    return kind


def _looks_like_local_media(path):
    if not path:
        return False
    if path.startswith(("http://", "https://", "data:")):
        return True
    return Path(path).suffix.lower() in {".gif", ".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"}


def _artifact_url(path: Path) -> str:
    return "/api/artifact?path=" + quote(str(path), safe="")


def _resolve_media_path(path, artifact_dir, journal):
    if not path or path.startswith(("http://", "https://", "data:")):
        return None
    p = Path(path)
    candidates = [p] if p.is_absolute() else [artifact_dir / p, journal / p]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            journal_root = journal.resolve()
            if resolved.exists() and resolved.is_file() and (resolved == journal_root or journal_root in resolved.parents):
                return resolved
        except OSError:
            continue
    return None


def extract_media(context, summary, artifact_path, journal, hyp_id):
    artifact_dir = _submission_artifact_dir(journal, artifact_path)
    out = {}
    for item in _media_candidates(context, summary):
        path = _media_path(item)
        if not _looks_like_local_media(path):
            continue
        kind = _media_kind(item)
        resolved = _resolve_media_path(path, artifact_dir, journal)
        if path.startswith(("http://", "https://", "data:")):
            src = path
        elif resolved:
            src = _artifact_url(resolved)
        else:
            continue
        out[kind] = {
            "kind": kind,
            "src": src,
            "label": item.get("label") or item.get("title") or kind,
            "path": str(resolved) if resolved else path,
            "mime": item.get("mime") or item.get("type"),
        }
    if "timelapse" not in out and artifact_dir:
        for name in ("timelapse.gif", "preview.gif", "run.gif", "timelapse.mp4", "preview.webm"):
            media_path = artifact_dir / name
            if media_path.exists():
                out["timelapse"] = {
                    "kind": "timelapse",
                    "src": _artifact_url(media_path.resolve()),
                    "label": "timelapse",
                    "path": str(media_path),
                    "mime": None,
                }
                break
    return out or None


def node_trace(journal, hyp_id, n=16):
    """LIVE real execution trace for one hypothesis node — reads its best
    submission's real `best.ir` and runs the real scorer's `trace_run`.
    Returns None when there's no runnable artifact (UI falls back).
    No precompute / export: this is computed on demand per request."""
    db = detect_db(journal)
    if not db.exists():
        return None
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        subs = [dict(r) for r in con.execute(
            "select * from submissions where hypothesis_id = ?", (hyp_id,))]
        if not subs:
            return None
        ver_by_sub = {}
        for s in subs:
            vr = con.execute(
                "select * from verifications where submission_id = ?", (s["id"],)).fetchone()
            ver_by_sub[s["id"]] = dict(vr) if vr else None

        def sub_key(sub):
            ver = ver_by_sub.get(sub["id"])
            return (
                0 if ver and ver.get("official_score") is not None else 1,
                ver.get("official_score") if ver and ver.get("official_score") is not None else 10 ** 12,
                sub["created_at"],
            )
        sub = sorted(subs, key=sub_key)[0]
    finally:
        con.close()

    art = _resolve_artifact(journal, sub.get("artifact_path"))
    if not art:
        return None
    try:
        ir = art.read_text(encoding="utf-8")
    except OSError:
        return None

    # Use the experiment's own scorer (single source of truth for the cost
    # model + simulator); fall back gracefully if it can't be imported.
    import sys as _sys
    exp_root = journal.parent  # experiments/<name>/journal -> experiments/<name>
    if str(exp_root) not in _sys.path:
        _sys.path.insert(0, str(exp_root))
    try:
        from matmul.matmul import trace_run  # type: ignore
    except Exception as exc:
        return {"ok": False, "error": f"scorer unavailable: {exc}"}
    tr = trace_run(ir, n)
    tr["node"] = hyp_id
    tr["candidate"] = sub.get("artifact_path", "").rsplit("/", 1)[-1]
    return tr


def build_payload(journal, db_filename=None):
    db = detect_db(journal, db_filename)
    if not db.exists():
        raise SystemExit(f"missing {db}")

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    exp_meta = experiment_meta(journal)

    all_times = []
    for table in ["agents", "hypotheses", "submissions", "verifications", "manager_events"]:
        for row in con.execute(f"select created_at from {table}"):
            all_times.append(parse_ts(row["created_at"]))
    if not all_times:
        return {
            "meta": {
                "baseline": exp_meta["baseline"],
                "target": exp_meta["target"],
                "best": None,
                "bestNode": None,
                "gap": None,
                "tMax": 1,
                "tNow": 1,
                "totalNodes": 0,
                "excludedTreeItems": 0,
                "problem": exp_meta["problem"],
                "metric": exp_meta["metric"],
                "direction": exp_meta["direction"],
                "domain": exp_meta["domain"],
                "source": str(journal),
                "sourceDb": db.name,
                "seed": journal.name,
                "startedAt": datetime.now(timezone.utc).isoformat(),
                "visualizations": exp_meta["visualizations"],
            },
            "nodes": [],
            "hypotheses": [],
            "agents": [],
            "events": [],
            "series": [{"t": 0, "best": None}, {"t": 1, "best": None}],
        }
    start = min(all_times)

    agents = []
    for row in con.execute("select * from agents order by created_at, id"):
        retired = row["updated_at"] if row["status"] == "dead" else None
        agents.append({
            "id": row["id"],
            "role": row["role"],
            "spawnedAt": seconds(row["created_at"], start),
            "retiredAt": seconds(retired, start) if retired else None,
            "status": row["status"],
            "currentItem": row["current_item"],
        })

    hyp_rows = {r["id"]: dict(r) for r in con.execute("select * from hypotheses")}
    halted_branches = {
        r["branch_id"]: dict(r)
        for r in con.execute("select * from branch_controls where status = 'halted'")
    } if con.execute("select name from sqlite_master where type = 'table' and name = 'branch_controls'").fetchone() else {}
    sub_rows = [dict(r) for r in con.execute("select * from submissions order by created_at, id")]
    ver_by_sub = {r["submission_id"]: dict(r) for r in con.execute("select * from verifications")}
    subs_by_hyp = {}
    for sub in sub_rows:
        subs_by_hyp.setdefault(sub["hypothesis_id"], []).append(sub)
    agent_role_by_id = {a["id"]: a["role"] for a in agents}
    hypothesis_items = []

    tree_excluded_roles = set()
    excluded_tree_items = 0
    transfer_edges = []
    first_verified = con.execute(
        """
        SELECT h.id
        FROM verifications v
        JOIN submissions s ON s.id = v.submission_id
        JOIN hypotheses h ON h.id = s.hypothesis_id
        WHERE v.decision = 'accept' AND v.official_score IS NOT NULL
        ORDER BY v.created_at ASC, h.id
        LIMIT 1
        """
    ).fetchone()
    first_verified_hyp_id = first_verified["id"] if first_verified else None

    # One display node per executable candidate hypothesis, using its best/first verified submission if present.
    nodes = []
    for hyp_id, hyp in sorted(hyp_rows.items(), key=lambda item: (item[1]["created_at"], item[0])):
        subs = subs_by_hyp.get(hyp_id, [])
        def sub_key(sub):
            ver = ver_by_sub.get(sub["id"])
            score = score_from_verification(ver)
            ordered_score = -score if score is not None and is_maximize(exp_meta) else score
            return (
                0 if score is not None else 1,
                ordered_score if ordered_score is not None else 10**12,
                sub["created_at"],
            )
        sub = sorted(subs, key=sub_key)[0] if subs else None
        ver = ver_by_sub.get(sub["id"]) if sub else None
        context = load_json(hyp["context_json"], {})
        evolution = context.get("evolution") if isinstance(context.get("evolution"), dict) else {}
        summary = load_json(sub["candidate_summary_json"], {}) if sub else {}
        best = summary.get("best") if isinstance(summary, dict) else {}
        if not isinstance(best, dict):
            best = {}

        raw_buckets = load_json(ver["buckets_json"], {}) if ver else best
        score = score_from_verification(ver, best.get("score"))
        decision = ver.get("decision") if ver else None
        semantic = ver.get("semantic") if ver else None
        buckets = bucketize(raw_buckets)
        family = family_from(context, summary)
        candidate = best.get("name") or summary.get("name") or family or hyp["id"]
        artifact = {"details": artifact_details(journal, sub.get("artifact_path"), summary, raw_buckets)} if sub else {}
        media = extract_media(context, summary, sub["artifact_path"] if sub else None, journal, hyp_id)
        proposer_role = next((a["role"] for a in agents if a["id"] == hyp["proposer_agent_id"]), "creative_explorer")
        is_transfer = evolution.get("event") == "horizontal_transfer"
        if is_transfer:
            transfer_edges.append({
                "id": f"transfer-{hyp_id}",
                "to": hyp_id,
                "donor": evolution.get("donor_hypothesis_id"),
                "recipient": evolution.get("recipient_hypothesis_id") or hyp["parent_hypothesis_id"],
                "donorFamily": evolution.get("donor_family"),
                "recipientFamily": evolution.get("recipient_family"),
                "t": seconds(hyp["created_at"], start),
                "transferred": evolution.get("transferred"),
            })
        hypothesis_items.append({
            "id": hyp_id,
            "parent": hyp["parent_hypothesis_id"],
            "status": hyp["status"],
            "title": hyp["title"],
            "proposer": hyp["proposer_agent_id"],
            "proposerRole": proposer_role,
            "createdAt": seconds(hyp["created_at"], start),
            "updatedAt": seconds(hyp["updated_at"], start),
            "claimedBy": hyp["claimed_by"],
            "hasSubmission": sub is not None,
            "hasVerification": ver is not None,
            "inTree": ver is not None and proposer_role not in tree_excluded_roles,
            "halted": hyp_id in halted_branches,
            "isTransfer": is_transfer,
            "evolution": evolution,
            "rationale": hyp["rationale"],
            "expectedMovement": hyp["expected_movement"],
            "media": media,
        })
        if ver is None:
            excluded_tree_items += 1
            continue
        if proposer_role in tree_excluded_roles and not is_transfer and hyp_id != first_verified_hyp_id:
            excluded_tree_items += 1
            continue
        inferred_parent = hyp["parent_hypothesis_id"]
        if is_transfer:
            inferred_parent = evolution.get("recipient_hypothesis_id") or inferred_parent
        if inferred_parent == hyp_id:
            inferred_parent = None
        impl = context.get("implementation") if isinstance(context, dict) else {}
        context_parent = find_hypothesis_ref({
            "best_frontier": context.get("best_frontier") if isinstance(context, dict) else None,
            "implementation_parent": impl.get("best_frontier") if isinstance(impl, dict) else None,
            "source_capability": impl.get("source_capability") if isinstance(impl, dict) else None,
            "base_capability": impl.get("base_capability") if isinstance(impl, dict) else None,
            "source_insight_id": impl.get("source_insight_id") if isinstance(impl, dict) else None,
        })
        if context_parent == hyp_id or context_parent == inferred_parent:
            context_parent = None
        claimed_at = hyp["updated_at"] if hyp["claimed_by"] else sub["created_at"] if sub else None

        nodes.append({
            "id": hyp_id,
            "parent": inferred_parent,
            "contextParent": context_parent,
            "gen": 0,
            "family": family,
            "title": hyp["title"],
            "candidate": candidate,
            "proposer": hyp["proposer_agent_id"],
            "proposerRole": proposer_role,
            "tProposed": seconds(hyp["created_at"], start),
            "tClaimed": seconds(claimed_at, start) if claimed_at else None,
            "impl": sub["implementor_agent_id"] if sub else hyp["claimed_by"],
            "tSubmitted": seconds(sub["created_at"], start) if sub else None,
            "verifier": ver["verifier_agent_id"] if ver else sub["claimed_by"] if sub else None,
            "tVerified": seconds(ver["created_at"], start) if ver else None,
            "outcome": "accept" if decision == "accept" else "reject" if decision == "reject" else None,
            "semantic": semantic,
            "score": score,
            "subId": sub["id"] if sub else None,
            "verId": ver["id"] if ver else None,
            "buckets": buckets,
            "artifact": artifact,
            "fit": None,
            "abandoned": hyp["status"] == "abandoned",
            "rationale": hyp["rationale"],
            "expectedMovement": hyp["expected_movement"],
            "isTransfer": is_transfer,
            "halted": hyp_id in halted_branches,
            "evolution": evolution,
            "media": media,
        })

    normalize_display_parents(nodes)

    by_id = {n["id"]: n for n in nodes}
    def gen_of(node):
        parent = by_id.get(node.get("displayParent") or node["parent"])
        return gen_of(parent) + 1 if parent else 0
    for n in nodes:
        n["gen"] = gen_of(n)

    verified_scores = [n["score"] for n in nodes if n["outcome"] == "accept" and n["score"] is not None]
    if verified_scores:
        best_score = max(verified_scores) if is_maximize(exp_meta) else min(verified_scores)
    else:
        best_score = None
    fit_baseline = best_score if best_score is not None else exp_meta["baseline"]
    for n in nodes:
        n["fit"] = fit_bin_meta(n["score"], fit_baseline, exp_meta)
    best_candidates = [
        n for n in nodes
        if best_score is not None and n["score"] == best_score and n["outcome"] == "accept"
    ]
    best_candidates.sort(key=lambda n: n.get("tVerified") or -1, reverse=True)
    best_node = best_candidates[0] if best_candidates else None
    if best_node:
        best_node["isFrontier"] = True
    node_ids = {n["id"] for n in nodes}
    transfer_edges = [
        edge for edge in transfer_edges
        if edge.get("to") in node_ids
        and edge.get("donor") in node_ids
        and (edge.get("recipient") in node_ids or edge.get("recipient") is None)
    ]

    events = []
    def ev(t, kind, agent, node_id, text, **extra):
        role = next((a["role"] for a in agents if a["id"] == agent), None)
        row = {"t": t, "kind": kind, "agent": agent, "role": role, "nodeId": node_id, "text": text}
        row.update(extra)
        events.append(row)

    for a in agents:
        ev(a["spawnedAt"], "spawn", a["id"], None, f"started {a['role'].replace('_', ' ')}", spawnRole=a["role"])
    for n in nodes:
        ev(n["tProposed"], "proposed", n["proposer"], n["id"], n["title"])
        if n["tClaimed"] is not None and n["impl"]:
            ev(n["tClaimed"], "claimed", n["impl"], n["id"], "claimed hypothesis")
        if n["tSubmitted"] is not None and n["impl"]:
            ev(n["tSubmitted"], "submitted", n["impl"], n["id"], "candidate submitted", score=n["score"])
        if n["tVerified"] is not None and n["verifier"]:
            ev(n["tVerified"], "verified", n["verifier"], n["id"], n["outcome"] or "verified", score=n["score"], decision=n["outcome"], semantic=n["semantic"])

    for row in con.execute("select * from manager_events order by created_at, id"):
        payload = load_json(row["payload_json"], {})
        ev(seconds(row["created_at"], start), "scale", payload.get("agent_id") or "manager", None, row["kind"], payload=payload)

    events.sort(key=lambda e: (e["t"], e["kind"]))
    t_max = max([e["t"] for e in events] + [0]) + 5
    t_now = t_max

    def frontier_at(t):
        b = None
        maximize = is_maximize(exp_meta)
        for n in nodes:
            if n["outcome"] == "accept" and n["score"] is not None and n["tVerified"] is not None and n["tVerified"] <= t:
                if b is None:
                    b = n["score"]
                else:
                    b = max(b, n["score"]) if maximize else min(b, n["score"])
        return b

    series = [{"t": (i / 60) * t_max, "best": frontier_at((i / 60) * t_max)} for i in range(61)]
    gap = None
    if best_score is not None:
        gap = (exp_meta["target"] - best_score) if is_maximize(exp_meta) else (best_score - exp_meta["target"])
    payload = {
        "meta": {
            "baseline": exp_meta["baseline"],
            "target": exp_meta["target"],
            "best": best_score,
            "bestNode": best_node["id"] if best_node else None,
            "gap": gap,
            "tMax": t_max,
            "tNow": t_now,
            "totalNodes": len(nodes),
            "excludedTreeItems": excluded_tree_items,
            "haltedBranches": len(halted_branches),
            "problem": exp_meta["problem"],
            "metric": exp_meta["metric"],
            "direction": exp_meta["direction"],
            "domain": exp_meta["domain"],
            "source": str(journal),
            "sourceDb": db.name,
            "seed": journal.name,
            "startedAt": start.isoformat(),
            "visualizations": exp_meta["visualizations"],
        },
        "nodes": nodes,
        "transferEdges": transfer_edges,
        "hypotheses": hypothesis_items,
        "agents": agents,
        "events": events,
        "series": series,
    }

    return payload


# Shared adapter: turns a JSON payload into a full "world" object (attaches the
# derive-at-time helper fns the UI calls). Used for both the single dashboard
# world (window.APP) and each Compare run (window.EVO_RUNS).
WORLD_FACTORY_JS = r"""
  function appWorld(payload) {
    const maximize = String(payload.meta.direction || 'minimize').toLowerCase() === 'maximize';
    function statusAt(n, T) {
      if (T < n.tProposed) return 'unborn';
      if (n.abandoned) return T > n.tProposed + 18 ? 'abandoned' : 'queued';
      if (n.tClaimed == null || T < n.tClaimed) return 'queued';
      if (n.tSubmitted == null || T < n.tSubmitted) return 'claimed';
      if (n.tVerified == null || T < n.tVerified) return 'submitted';
      return n.outcome === 'accept' ? 'verified' : 'rejected';
    }
    function bornCount(T) { return payload.nodes.filter((n) => n.tProposed <= T).length; }
    function frontierAt(T) {
      let best = null;
      payload.nodes.forEach((n) => {
        if (n.outcome === 'accept' && n.score != null && n.tVerified != null && n.tVerified <= T) {
          best = best == null ? n.score : (maximize ? Math.max(best, n.score) : Math.min(best, n.score));
        }
      });
      return best;
    }
    function fitBin(score) {
      if (score == null) return null;
      const best = payload.meta.best == null ? payload.meta.baseline : payload.meta.best;
      if (maximize) {
        const span = Math.max(1e-9, best - payload.meta.baseline);
        return Math.max(0, Math.min(6, Math.round(((score - payload.meta.baseline) / span) * 6)));
      }
      const span = Math.max(1e-9, payload.meta.baseline - best);
      return Math.max(0, Math.min(6, Math.round(((payload.meta.baseline - score) / span) * 6)));
    }
    function agentActivity(T) {
      const out = {};
      payload.agents.forEach((a) => {
        const alive = a.spawnedAt <= T && (a.retiredAt == null || a.retiredAt > T);
        out[a.id] = { id: a.id, role: a.role, alive, status: 'idle', item: null, kind: null };
      });
      payload.nodes.forEach((n) => {
        if (n.impl && n.tClaimed != null && n.tClaimed <= T && (n.tSubmitted == null || T < n.tSubmitted)) {
          const o = out[n.impl]; if (o && o.alive) { o.status = 'working'; o.item = n.id; o.kind = 'building ' + n.candidate; }
        }
        if (n.verifier && n.tSubmitted != null && n.tSubmitted <= T && (n.tVerified == null || T < n.tVerified)) {
          const o = out[n.verifier]; if (o && o.alive) { o.status = 'working'; o.item = n.id; o.kind = 'verifying ' + (n.subId || 'submission'); }
        }
        if (n.proposer && T >= n.tProposed - 2.5 && T < n.tProposed) {
          const o = out[n.proposer]; if (o && o.alive && o.status === 'idle') { o.status = 'working'; o.item = n.id; o.kind = 'proposing hypothesis'; }
        }
      });
      payload.events.forEach((e) => {
        if (e.t > T || T - e.t >= 4 || !e.agent || !out[e.agent]) return;
        const o = out[e.agent];
        if (!o.alive || o.status === 'working') return;
        if (e.kind === 'scale') { o.status = 'working'; o.kind = 'planning scale'; }
        if (e.kind === 'spawn') { o.status = 'working'; o.kind = 'starting'; }
      });
      return out;
    }
    payload.fns = { statusAt, bornCount, agentActivity, frontierAt, fitBin };
    return payload;
  }
"""


def render_js(payload):
    adapter = (
        "(function () {\n"
        + WORLD_FACTORY_JS
        + "  window.APP = appWorld(__PAYLOAD__);\n"
        + "})();\n"
    )
    return adapter.replace("__PAYLOAD__", json.dumps(payload, separators=(",", ":")))


def render_runs_js(runs):
    """runs: list of dicts {id, label, desc, payload}.
    Emits window.EVO_RUNS (each a full world) for the Compare page, mirroring
    the mock runs.js shape so compare.jsx is data-source agnostic."""
    items = []
    for r in runs:
        item = "    { id: __ID__, label: __LABEL__, desc: __DESC__, world: appWorld(__PAYLOAD__) }"
        item = item.replace("__ID__", json.dumps(r["id"]))
        item = item.replace("__LABEL__", json.dumps(r["label"]))
        item = item.replace("__DESC__", json.dumps(r.get("desc", "")))
        item = item.replace("__PAYLOAD__", json.dumps(r["payload"], separators=(",", ":")))
        items.append(item)
    body = ",\n".join(items)
    return (
        "(function () {\n"
        + WORLD_FACTORY_JS
        + "  var RUNS = [\n" + body + "\n  ];\n"
        + "  window.EVO_RUNS = RUNS;\n"
        + "  window.EVO_RUN_BY_ID = {}; RUNS.forEach(function (r) { window.EVO_RUN_BY_ID[r.id] = r; });\n"
        + "  if (!window.APP && RUNS.length) window.APP = RUNS[0].world;\n"
        + "})();\n"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("journal", type=Path)
    parser.add_argument("--out", type=Path, default=Path("real-data.js"))
    parser.add_argument("--db-name")
    args = parser.parse_args()

    payload = build_payload(args.journal, args.db_name)
    args.out.write_text(render_js(payload), encoding="utf-8")
    nodes = payload["nodes"]
    events = payload["events"]
    db = detect_db(args.journal, args.db_name)
    print(f"wrote {args.out} from {db} ({len(nodes)} hypotheses, {len(events)} events)")


if __name__ == "__main__":
    main()
