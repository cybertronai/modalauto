"""Local trace recording plus optional Raindrop Workshop forwarding.

The UI reads the SQLite records directly. When ``raindrop-ai`` is installed and
Raindrop is configured, the same spans are also mirrored through the SDK so
Workshop can display them live.
"""

from __future__ import annotations

import contextvars
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


TRACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_traces (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT,
    role            TEXT,
    kind            TEXT NOT NULL,
    item_id         TEXT,
    run_id          TEXT,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'ok', 'failed')),
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    duration_ms     INTEGER,
    workshop_url    TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    spans_json      TEXT NOT NULL DEFAULT '[]',
    error           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_traces_agent ON agent_traces(agent_id, started_at);
CREATE INDEX IF NOT EXISTS idx_agent_traces_item ON agent_traces(item_id, started_at);
CREATE INDEX IF NOT EXISTS idx_agent_traces_run ON agent_traces(run_id, started_at);
"""

_current_trace: contextvars.ContextVar["TraceRecorder | None"] = contextvars.ContextVar(
    "autoresearch_current_trace",
    default=None,
)
_current_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "autoresearch_current_span_id",
    default=None,
)
_raindrop = None
_raindrop_init_attempted = False
_raindrop_init_error: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_debugger_enabled() -> bool:
    value = os.environ.get("RAINDROP_LOCAL_DEBUGGER", "").strip()
    return bool(value) and value.lower() not in {"0", "false", "no", "off"}


def workshop_url() -> str | None:
    if not local_debugger_enabled():
        return None
    value = os.environ.get("RAINDROP_LOCAL_DEBUGGER", "").strip()
    if value.startswith(("http://", "https://")):
        return value.removesuffix("/v1/").removesuffix("/v1").rstrip("/")
    return "http://localhost:5899"


def _local_workshop_endpoint() -> str | None:
    if not local_debugger_enabled():
        return None
    value = os.environ.get("RAINDROP_LOCAL_DEBUGGER", "").strip()
    if value.startswith(("http://", "https://")):
        endpoint = value
    else:
        endpoint = "http://localhost:5899/v1/"
    endpoint = endpoint.rstrip("/")
    if not endpoint.endswith("/v1"):
        endpoint = f"{endpoint}/v1"
    return f"{endpoint}/"


def ensure_trace_tables(db: sqlite3.Connection) -> None:
    db.executescript(TRACE_SCHEMA)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path), timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def _jsonable(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return repr(value)[:240]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 4000 else value[:4000] + "...[truncated]"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        text = value[:1024].decode("utf-8", errors="replace")
        return text + ("...[truncated]" if len(value) > 1024 else "")
    if isinstance(value, dict):
        items = list(value.items())[:80]
        out = {str(k): _jsonable(v, depth=depth + 1) for k, v in items}
        if len(value) > len(items):
            out["__truncated__"] = len(value) - len(items)
        return out
    if isinstance(value, (list, tuple, set)):
        seq = list(value)[:80]
        out = [_jsonable(v, depth=depth + 1) for v in seq]
        if len(value) > len(seq):
            out.append({"__truncated__": len(value) - len(seq)})
        return out
    return repr(value)[:1000]


def _json_dumps(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))


def _tail(value: str | None, limit: int = 2000) -> str:
    if not value:
        return ""
    return value[-limit:] if len(value) > limit else value


def _maybe_init_raindrop():
    global _raindrop, _raindrop_init_attempted, _raindrop_init_error
    if _raindrop_init_attempted:
        return _raindrop
    _raindrop_init_attempted = True
    if not (local_debugger_enabled() or os.environ.get("RAINDROP_WRITE_KEY")):
        return None
    try:
        import raindrop.analytics as raindrop  # type: ignore
    except Exception as exc:  # noqa: BLE001
        _raindrop_init_error = f"raindrop import failed: {exc}"
        return None
    raw_write_key = (os.environ.get("RAINDROP_WRITE_KEY") or "").strip()
    local_endpoint = _local_workshop_endpoint()
    local_placeholder_keys = {"local", "local-debugger", "debug", "dummy", "test"}
    local_only = bool(local_endpoint) and (
        not raw_write_key or raw_write_key.lower() in local_placeholder_keys
    )
    write_key = raw_write_key or "local-debugger"
    init_kwargs = {
        "tracing_enabled": True,
        "bypass_otel_for_tools": True,
    }
    if local_only:
        init_kwargs.update({
            "endpoint": local_endpoint,
            "local_workshop_url": None,
        })
    elif local_endpoint:
        init_kwargs["local_workshop_url"] = local_endpoint
    try:
        try:
            raindrop.init(write_key, **init_kwargs)
        except TypeError:
            init_kwargs.pop("bypass_otel_for_tools", None)
            init_kwargs.pop("local_workshop_url", None)
            raindrop.init(write_key, **init_kwargs)
        _raindrop = raindrop
    except Exception as exc:  # noqa: BLE001
        _raindrop_init_error = f"raindrop init failed: {exc}"
        return None
    return _raindrop


class TraceSpan:
    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        self.metadata = dict(metadata or {})
        self.output: Any = None

    def set_output(self, output: Any) -> None:
        self.output = output

    def set_metadata(self, **metadata: Any) -> None:
        self.metadata.update(metadata)


class TraceRecorder:
    def __init__(
        self,
        *,
        db_path: Path,
        agent_id: str | None,
        role: str | None,
        kind: str,
        title: str,
        item_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.db_path = db_path
        self.id = "tr-" + uuid4().hex[:12]
        self.agent_id = agent_id
        self.role = role
        self.kind = kind
        self.title = title
        self.item_id = item_id
        self.run_id = run_id
        self.metadata = dict(metadata or {})
        self.spans: list[dict[str, Any]] = []
        self.status = "running"
        self.error: str | None = None
        self.started_at = now_iso()
        self.ended_at: str | None = None
        self.duration_ms: int | None = None
        self.workshop_url = workshop_url()
        self._start_perf = time.perf_counter()
        self._token = None
        self._interaction = None
        self._raindrop = None

    def __enter__(self) -> "TraceRecorder":
        self.metadata.setdefault("workshop_enabled", bool(self.workshop_url))
        self._start_raindrop()
        self._persist()
        self._token = _current_trace.set(self)
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc is not None:
            self.status = "failed"
            self.error = str(exc)
        elif self.status == "running":
            self.status = "ok"
        self.ended_at = now_iso()
        self.duration_ms = int((time.perf_counter() - self._start_perf) * 1000)
        self._finish_raindrop()
        self._persist()
        if self._token is not None:
            _current_trace.reset(self._token)
        return False

    def update(
        self,
        *,
        item_id: str | None = None,
        run_id: str | None = None,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if item_id is not None:
            self.item_id = item_id
        if run_id is not None:
            self.run_id = run_id
        if title is not None:
            self.title = title
        if metadata:
            self.metadata.update(metadata)
        self._persist()

    def add_span(
        self,
        *,
        span_id: str,
        parent_id: str | None,
        name: str,
        kind: str,
        started_at: str,
        duration_ms: int,
        metadata: dict[str, Any],
        output: Any = None,
        error: str | None = None,
    ) -> None:
        row = {
            "id": span_id,
            "parentId": parent_id,
            "name": name,
            "kind": kind,
            "startedAt": started_at,
            "durationMs": duration_ms,
            "status": "failed" if error else "ok",
            "metadata": _jsonable(metadata),
            "output": _jsonable(output),
            "error": error,
        }
        self.spans.append(row)
        self._record_raindrop_tool(row)
        self._persist()

    def _persist(self) -> None:
        try:
            db = _connect(self.db_path)
            ensure_trace_tables(db)
            stamp = now_iso()
            db.execute(
                """
                INSERT INTO agent_traces
                    (id, agent_id, role, kind, item_id, run_id, title, status,
                     started_at, ended_at, duration_ms, workshop_url,
                     metadata_json, spans_json, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    agent_id = excluded.agent_id,
                    role = excluded.role,
                    kind = excluded.kind,
                    item_id = excluded.item_id,
                    run_id = excluded.run_id,
                    title = excluded.title,
                    status = excluded.status,
                    ended_at = excluded.ended_at,
                    duration_ms = excluded.duration_ms,
                    workshop_url = excluded.workshop_url,
                    metadata_json = excluded.metadata_json,
                    spans_json = excluded.spans_json,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    self.id,
                    self.agent_id,
                    self.role,
                    self.kind,
                    self.item_id,
                    self.run_id,
                    self.title,
                    self.status,
                    self.started_at,
                    self.ended_at,
                    self.duration_ms,
                    self.workshop_url,
                    _json_dumps(self.metadata),
                    _json_dumps(self.spans),
                    self.error,
                    self.started_at,
                    stamp,
                ),
            )
            db.commit()
            db.close()
        except Exception:  # noqa: BLE001
            return

    def _start_raindrop(self) -> None:
        self._raindrop = _maybe_init_raindrop()
        if _raindrop_init_error:
            self.metadata.setdefault("raindrop_error", _raindrop_init_error)
        if self._raindrop is None:
            return
        try:
            self._interaction = self._raindrop.begin(
                user_id=self.agent_id or "autoresearch",
                event=self.kind,
                input=_json_dumps({
                    "title": self.title,
                    "agent_id": self.agent_id,
                    "role": self.role,
                    "item_id": self.item_id,
                    "run_id": self.run_id,
                }),
                convo_id=self.run_id or self.id,
                properties={
                    "trace_id": self.id,
                    "title": self.title,
                    "agent_id": self.agent_id,
                    "role": self.role,
                    "item_id": self.item_id,
                    "run_id": self.run_id,
                    **_jsonable(self.metadata),
                },
            )
            event_id = getattr(self._interaction, "id", None) or getattr(self._interaction, "event_id", None)
            if event_id:
                self.metadata["raindrop_event_id"] = event_id
        except Exception as exc:  # noqa: BLE001
            self.metadata["raindrop_error"] = str(exc)
            self._interaction = None

    def _record_raindrop_tool(self, row: dict[str, Any]) -> None:
        if self._interaction is None:
            return
        try:
            self._interaction.track_tool(
                name=str(row["name"]),
                input=row.get("metadata"),
                output=row.get("output"),
                duration_ms=row.get("durationMs") or 0,
                error=row.get("error"),
                properties={
                    "trace_id": self.id,
                    "span_id": row.get("id"),
                    "kind": row.get("kind"),
                    "parent_id": row.get("parentId"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            self.metadata["raindrop_tool_error"] = _tail(str(exc), 500)

    def _finish_raindrop(self) -> None:
        if self._interaction is None:
            return
        try:
            self._interaction.finish(output=_json_dumps({
                "trace_id": self.id,
                "status": self.status,
                "error": self.error,
                "spans": len(self.spans),
            }))
            if self._raindrop is not None:
                self._raindrop.flush()
        except Exception as exc:  # noqa: BLE001
            self.metadata["raindrop_finish_error"] = _tail(str(exc), 500)


def trace_step(args: Any, *, kind: str | None = None, title: str | None = None,
               metadata: dict[str, Any] | None = None) -> TraceRecorder:
    return TraceRecorder(
        db_path=Path(args.db),
        agent_id=getattr(args, "agent_id", None),
        role=getattr(args, "role", None),
        kind=kind or getattr(args, "role", "agent_step"),
        title=title or f"{getattr(args, 'role', 'agent')} step",
        metadata=metadata,
    )


def trace_run(
    *,
    db_path: Path,
    agent_id: str,
    role: str,
    kind: str,
    title: str,
    item_id: str | None = None,
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> TraceRecorder:
    return TraceRecorder(
        db_path=db_path,
        agent_id=agent_id,
        role=role,
        kind=kind,
        title=title,
        item_id=item_id,
        run_id=run_id,
        metadata=metadata,
    )


def update_trace(**kwargs: Any) -> None:
    rec = _current_trace.get()
    if rec is not None:
        rec.update(**kwargs)


def record_event(name: str, *, kind: str = "event", metadata: dict[str, Any] | None = None) -> None:
    rec = _current_trace.get()
    if rec is None:
        return
    rec.add_span(
        span_id=f"sp-{len(rec.spans) + 1:04d}",
        parent_id=_current_span_id.get(),
        name=name,
        kind=kind,
        started_at=now_iso(),
        duration_ms=0,
        metadata=metadata or {},
        output=None,
        error=None,
    )


@contextmanager
def span(name: str, *, kind: str = "task", metadata: dict[str, Any] | None = None):
    rec = _current_trace.get()
    handle = TraceSpan(metadata)
    if rec is None:
        yield handle
        return
    span_id = f"sp-{len(rec.spans) + 1:04d}"
    parent_id = _current_span_id.get()
    token = _current_span_id.set(span_id)
    started_at = now_iso()
    start_perf = time.perf_counter()
    error = None
    try:
        yield handle
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        raise
    finally:
        duration_ms = int((time.perf_counter() - start_perf) * 1000)
        rec.add_span(
            span_id=span_id,
            parent_id=parent_id,
            name=name,
            kind=kind,
            started_at=started_at,
            duration_ms=duration_ms,
            metadata=handle.metadata,
            output=handle.output,
            error=error,
        )
        _current_span_id.reset(token)
