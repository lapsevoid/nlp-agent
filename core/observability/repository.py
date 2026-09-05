"""SQLite WAL repository for traces, spans, events, and daily aggregates."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from core.observability.models import SpanRecord, TelemetryEnvelope, TelemetryEvent, TraceRecord
from core.observability.summary import build_telemetry_overview
from core.observability.traces import build_trace_group_page, trace_chain_identity


class TelemetryRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                PRAGMA busy_timeout=5000;
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                    session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    chain_id TEXT, chain_name TEXT, entrypoint TEXT,
                    workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    channel TEXT NOT NULL, source TEXT NOT NULL,
                    started_at TEXT NOT NULL, completed_at TEXT,
                    duration_ms INTEGER, ttft_ms INTEGER, status TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    usage_source TEXT NOT NULL DEFAULT 'none',
                    error_kind TEXT, error_message TEXT, attributes_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status, started_at DESC);
                CREATE TABLE IF NOT EXISTS spans (
                    span_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL,
                    parent_span_id TEXT, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    worker_id TEXT, kind TEXT NOT NULL, name TEXT NOT NULL,
                    started_at TEXT NOT NULL, completed_at TEXT, duration_ms INTEGER,
                    status TEXT NOT NULL, attempt INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    usage_source TEXT NOT NULL DEFAULT 'none',
                    error_kind TEXT, error_message TEXT, attributes_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_spans_kind ON spans(kind, started_at DESC);
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, level TEXT NOT NULL,
                    name TEXT NOT NULL, trace_id TEXT, span_id TEXT, session_id TEXT,
                    turn_id TEXT, worker_id TEXT, payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp DESC);
                CREATE TABLE IF NOT EXISTS daily_metrics (
                    day TEXT NOT NULL, component TEXT NOT NULL, name TEXT NOT NULL,
                    requests INTEGER NOT NULL DEFAULT 0, successes INTEGER NOT NULL DEFAULT 0,
                    errors INTEGER NOT NULL DEFAULT 0, duration_sum_ms INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_tokens INTEGER NOT NULL DEFAULT 0, total_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, component, name)
                );
                """
            )
            self._ensure_column("traces", "cache_miss_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("traces", "reasoning_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("traces", "chain_id", "TEXT")
            self._ensure_column("traces", "chain_name", "TEXT")
            self._ensure_column("traces", "entrypoint", "TEXT")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_traces_chain ON traces(chain_id, started_at DESC)"
            )
            self._ensure_column("spans", "cache_miss_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("spans", "reasoning_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("daily_metrics", "cache_miss_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("daily_metrics", "reasoning_tokens", "INTEGER NOT NULL DEFAULT 0")

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @staticmethod
    def _json(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    def write_batch(self, envelopes: Iterable[TelemetryEnvelope]) -> None:
        with self._lock, self._conn:
            for envelope in envelopes:
                if envelope.kind == "trace":
                    self._write_trace(envelope.payload)  # type: ignore[arg-type]
                elif envelope.kind == "span":
                    self._write_span(envelope.payload)  # type: ignore[arg-type]
                else:
                    self._write_event(envelope.payload)  # type: ignore[arg-type]

    def _write_trace(self, item: TraceRecord) -> None:
        u = item.usage
        self._conn.execute(
            """INSERT OR REPLACE INTO traces (
               trace_id,request_id,session_id,turn_id,chain_id,chain_name,entrypoint,
               workspace_id,user_id,channel,source,
               started_at,completed_at,duration_ms,ttft_ms,status,input_tokens,output_tokens,
               cached_tokens,total_tokens,usage_source,error_kind,error_message,attributes_json,
               cache_miss_tokens,reasoning_tokens
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item.trace_id, item.request_id, item.session_id, item.turn_id,
             item.chain_id, item.chain_name, item.entrypoint,
             item.workspace_id, item.user_id, item.channel, item.source,
             item.started_at.isoformat(), item.completed_at.isoformat() if item.completed_at else None,
             item.duration_ms, item.ttft_ms, item.status.value, u.input_tokens,
             u.output_tokens, u.cached_tokens, u.total_tokens, u.source,
             item.error_kind, item.error_message, self._json(item.attributes),
             u.cache_miss_tokens, u.reasoning_tokens),
        )

    def _write_span(self, item: SpanRecord) -> None:
        u = item.usage
        self._conn.execute(
            """INSERT OR REPLACE INTO spans (
               span_id,trace_id,parent_span_id,session_id,turn_id,worker_id,kind,name,
               started_at,completed_at,duration_ms,status,attempt,input_tokens,output_tokens,
               cached_tokens,total_tokens,usage_source,error_kind,error_message,attributes_json,
               cache_miss_tokens,reasoning_tokens
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item.span_id, item.trace_id, item.parent_span_id, item.session_id,
             item.turn_id, item.worker_id, item.kind.value, item.name,
             item.started_at.isoformat(), item.completed_at.isoformat() if item.completed_at else None,
             item.duration_ms, item.status.value, item.attempt, u.input_tokens,
             u.output_tokens, u.cached_tokens, u.total_tokens, u.source,
             item.error_kind, item.error_message, self._json(item.attributes),
             u.cache_miss_tokens, u.reasoning_tokens),
        )
        if item.completed_at is not None:
            day = item.completed_at.date().isoformat()
            metric_name = item.name
            if item.kind.value == "model" and item.attributes.get("model"):
                metric_name = f"{item.name}:{item.attributes['model']}"
            error = 1 if item.status.value in {"error", "timeout"} else 0
            success = 1 if item.status.value == "ok" else 0
            self._conn.execute(
                """INSERT INTO daily_metrics (
                   day,component,name,requests,successes,errors,duration_sum_ms,input_tokens,
                   output_tokens,cached_tokens,total_tokens,cache_miss_tokens,reasoning_tokens
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(day,component,name) DO UPDATE SET
                   requests=requests+1, successes=successes+excluded.successes,
                   errors=errors+excluded.errors, duration_sum_ms=duration_sum_ms+excluded.duration_sum_ms,
                   input_tokens=input_tokens+excluded.input_tokens,
                   output_tokens=output_tokens+excluded.output_tokens,
                   cached_tokens=cached_tokens+excluded.cached_tokens,
                   total_tokens=total_tokens+excluded.total_tokens,
                   cache_miss_tokens=cache_miss_tokens+excluded.cache_miss_tokens,
                   reasoning_tokens=reasoning_tokens+excluded.reasoning_tokens""",
                (day, item.kind.value, metric_name, 1, success, error, item.duration_ms or 0,
                 u.input_tokens, u.output_tokens, u.cached_tokens, u.total_tokens,
                 u.cache_miss_tokens, u.reasoning_tokens),
            )

    def _write_event(self, item: TelemetryEvent) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?,?)",
            (item.event_id, item.timestamp.isoformat(), item.level, item.name,
             item.trace_id, item.span_id, item.session_id, item.turn_id,
             item.worker_id, self._json(item.payload)),
        )

    def _rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, args).fetchall()]

    @staticmethod
    def _decode(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for row in rows:
            for key in ("attributes_json", "payload_json"):
                if key in row:
                    row[key.removesuffix("_json")] = json.loads(row.pop(key) or "{}")
        return rows

    def overview(self, days: int = 30) -> dict[str, Any]:
        return build_telemetry_overview(
            self._decode(self._rows("SELECT * FROM traces")),
            self._decode(self._rows("SELECT * FROM spans")),
            self._decode(self._rows("SELECT * FROM events")),
            days,
        )

    def list_traces(self, *, limit: int = 100, session_id: str | None = None,
                    status: str | None = None, user_id: str | None = None,
                    workspace_ids: frozenset[str] | None = None) -> list[dict[str, Any]]:
        clauses, args = [], []
        if session_id:
            clauses.append("session_id=?"); args.append(session_id)
        if status:
            clauses.append("status=?"); args.append(status)
        if user_id:
            clauses.append("user_id=?"); args.append(user_id)
        if workspace_ids and "*" not in workspace_ids:
            marks = ",".join("?" for _ in workspace_ids)
            clauses.append(f"workspace_id IN ({marks})")
            args.extend(sorted(workspace_ids))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return self._decode(self._rows(
            f"SELECT * FROM traces{where} ORDER BY started_at DESC LIMIT ?",
            (*args, min(max(1, limit), 500)),
        ))

    def trace_groups(
        self,
        *,
        days: int = 30,
        limit: int = 24,
        offset: int = 0,
        query: str | None = None,
        focus: str = "all",
    ) -> dict[str, Any]:
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
        rows = self._decode(self._rows(
            "SELECT * FROM traces WHERE started_at>=? ORDER BY started_at DESC", (since,)
        ))
        span_rows = self._decode(self._rows(
            "SELECT * FROM spans WHERE started_at>=? ORDER BY started_at", (since,)
        ))
        page = build_trace_group_page(
            rows, spans=span_rows, limit=limit, offset=offset, query=query, focus=focus
        )
        page["period_days"] = days
        return page

    def trace_group_detail(self, chain_id: str) -> dict[str, Any] | None:
        rows = self._decode(self._rows("SELECT * FROM traces ORDER BY started_at"))
        matching = [row for row in rows if trace_chain_identity(row)["chain_id"] == chain_id]
        if not matching:
            return None
        trace_ids = {str(row["trace_id"]) for row in matching}
        spans = self._decode(self._rows("SELECT * FROM spans ORDER BY started_at"))
        events = self._decode(self._rows("SELECT * FROM events ORDER BY timestamp"))
        matching_spans = [row for row in spans if str(row.get("trace_id")) in trace_ids]
        chain = build_trace_group_page(matching, spans=matching_spans, limit=1)["items"][0]
        return {
            "chain": chain,
            "traces": matching,
            "spans": matching_spans,
            "events": [row for row in events if str(row.get("trace_id")) in trace_ids],
        }

    def trace_detail(self, trace_id: str) -> dict[str, Any] | None:
        traces = self._decode(self._rows("SELECT * FROM traces WHERE trace_id=?", (trace_id,)))
        if not traces:
            return None
        return {
            "trace": traces[0],
            "spans": self._decode(self._rows(
                "SELECT * FROM spans WHERE trace_id=? ORDER BY started_at", (trace_id,))),
            "events": self._decode(self._rows(
                "SELECT * FROM events WHERE trace_id=? ORDER BY timestamp", (trace_id,))),
        }

    def usage(self, days: int = 30) -> list[dict[str, Any]]:
        since = (datetime.now(timezone.utc).date() - timedelta(days=max(1, days))).isoformat()
        return self._rows(
            "SELECT * FROM daily_metrics WHERE day>=? ORDER BY day,component,name", (since,)
        )

    def sessions(self, days: int = 30, limit: int = 100, *,
                 user_id: str | None = None,
                 workspace_ids: frozenset[str] | None = None) -> list[dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
        clauses = ["started_at>=?", "completed_at IS NOT NULL"]
        args: list[Any] = [since]
        if user_id:
            clauses.append("user_id=?"); args.append(user_id)
        if workspace_ids and "*" not in workspace_ids:
            marks = ",".join("?" for _ in workspace_ids)
            clauses.append(f"workspace_id IN ({marks})")
            args.extend(sorted(workspace_ids))
        where = " AND ".join(clauses)
        return self._rows(
            f"""SELECT session_id,workspace_id,user_id,channel,COUNT(*) turns,
                      SUM(CASE WHEN status IN ('error','timeout') THEN 1 ELSE 0 END) errors,
                      CAST(AVG(duration_ms) AS INTEGER) avg_duration_ms,
                      SUM(total_tokens) total_tokens,MAX(started_at) last_seen
               FROM traces WHERE {where}
               GROUP BY session_id,workspace_id,user_id,channel
               ORDER BY last_seen DESC LIMIT ?""",
            (*args, min(max(1, limit), 500)),
        )

    def recent_events(self, *, limit: int = 200, level: str | None = None,
                      trace_id: str | None = None) -> list[dict[str, Any]]:
        clauses, args = [], []
        if level:
            clauses.append("level=?"); args.append(level)
        if trace_id:
            clauses.append("trace_id=?"); args.append(trace_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return self._decode(self._rows(
            f"SELECT * FROM events{where} ORDER BY timestamp DESC LIMIT ?",
            (*args, min(max(1, limit), 1000)),
        ))

    def errors(self, days: int = 30, limit: int = 100) -> list[dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
        return self._rows(
            """SELECT COALESCE(error_kind,'unknown') error_kind,kind,name,
                      COUNT(*) count,MAX(started_at) last_seen,MAX(trace_id) sample_trace_id
               FROM spans WHERE started_at>=? AND status IN ('error','timeout')
               GROUP BY error_kind,kind,name ORDER BY count DESC,last_seen DESC LIMIT ?""",
            (since, min(max(1, limit), 500)),
        )

    def prune(self, trace_days: int = 30, event_days: int = 30) -> dict[str, int]:
        trace_days = max(1, trace_days)
        event_days = max(1, event_days)
        now = datetime.now(timezone.utc)
        trace_before = (now - timedelta(days=trace_days)).isoformat()
        event_before = (now - timedelta(days=event_days)).isoformat()
        trace_day = (now.date() - timedelta(days=trace_days)).isoformat()
        with self._lock, self._conn:
            old = [r[0] for r in self._conn.execute(
                "SELECT trace_id FROM traces WHERE started_at<?", (trace_before,)).fetchall()]
            spans = 0
            if old:
                marks = ",".join("?" for _ in old)
                spans = int(self._conn.execute(f"DELETE FROM spans WHERE trace_id IN ({marks})", old).rowcount or 0)
                self._conn.execute(f"DELETE FROM traces WHERE trace_id IN ({marks})", old)
            traces = len(old)
            events = int(self._conn.execute("DELETE FROM events WHERE timestamp<?", (event_before,)).rowcount or 0)
            daily_metrics = int(self._conn.execute("DELETE FROM daily_metrics WHERE day<?", (trace_day,)).rowcount or 0)
        return {"traces": traces, "spans": spans, "events": events, "daily_metrics": daily_metrics}

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._conn:
            trace_ids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT trace_id FROM traces WHERE session_id=?", (session_id,)
                ).fetchall()
            ]
            if trace_ids:
                marks = ",".join("?" for _ in trace_ids)
                self._conn.execute(
                    f"DELETE FROM spans WHERE trace_id IN ({marks})", trace_ids
                )
                self._conn.execute(
                    f"DELETE FROM events WHERE trace_id IN ({marks})", trace_ids
                )
            self._conn.execute("DELETE FROM traces WHERE session_id=?", (session_id,))

    def clear(self) -> dict[str, int]:
        """Remove every persisted observability record, including aggregates."""
        with self._lock, self._conn:
            counts = {
                table: int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("traces", "spans", "events", "daily_metrics")
            }
            # Spans and events may refer to traces, so clear dependants first.
            for table in ("spans", "events", "traces", "daily_metrics"):
                self._conn.execute(f"DELETE FROM {table}")
        return counts

    def health(self) -> dict[str, Any]:
        counts = {}
        with self._lock:
            for table in ("traces", "spans", "events"):
                counts[table] = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return {"database": str(self.path), "database_bytes": self.path.stat().st_size, **counts}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
