"""MySQL-backed telemetry repository used by production runtime."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from collections.abc import Iterable
from typing import Any

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.dialects.mysql import insert

from core.observability.models import TelemetryEnvelope
from core.observability.summary import build_telemetry_overview
from core.observability.traces import build_trace_group_page, trace_chain_identity
from server.infrastructure.mysql.models import ObservabilityRecordModel


def _record_key(kind: str, payload: dict[str, Any]) -> str:
    """Select the envelope's own identity so sibling spans never overwrite."""
    field = {"trace": "trace_id", "span": "span_id", "event": "event_id"}.get(kind)
    if field is None:
        raise ValueError(f"unsupported telemetry envelope kind: {kind}")
    value = payload.get(field)
    if not value:
        raise ValueError(f"telemetry {kind} is missing {field}")
    return str(value)


class MySQLTelemetryRepository:
    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url.replace("mysql+aiomysql://", "mysql+pymysql://"), pool_pre_ping=True)

    def write_batch(self, envelopes: Iterable[TelemetryEnvelope]) -> None:
        with self._engine.begin() as connection:
            for envelope in envelopes:
                payload = envelope.model_dump(mode="json")
                item = payload.get("payload", {})
                key = _record_key(envelope.kind, item)
                statement = insert(ObservabilityRecordModel).values(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"nlp-agent-observability:{envelope.kind}:{key}")), kind=envelope.kind, record_key=key,
                    trace_id=item.get("trace_id"), session_id=item.get("session_id"),
                    turn_id=item.get("turn_id"), status=item.get("status"), payload_json=payload,
                ).on_duplicate_key_update(payload_json=payload, status=item.get("status"))
                connection.execute(statement)

    def clear(self) -> dict[str, int]:
        with self._engine.begin() as connection:
            counts = {kind: int(connection.scalar(select(func.count()).select_from(ObservabilityRecordModel).where(ObservabilityRecordModel.kind == kind)) or 0) for kind in ("trace", "span", "event")}
            daily_metrics = int(connection.scalar(select(func.count()).select_from(ObservabilityRecordModel).where(ObservabilityRecordModel.kind == "span")) or 0)
            connection.execute(delete(ObservabilityRecordModel))
        return {"traces": counts["trace"], "spans": counts["span"], "events": counts["event"], "daily_metrics": daily_metrics}

    def health(self) -> dict[str, Any]:
        with self._engine.connect() as connection:
            count = int(connection.scalar(select(func.count()).select_from(ObservabilityRecordModel)) or 0)
        with self._engine.connect() as connection:
            counts = {kind: int(connection.scalar(select(func.count()).select_from(ObservabilityRecordModel).where(ObservabilityRecordModel.kind == kind)) or 0) for kind in ("trace", "span", "event")}
        return {"database": "mysql", "records": count, "traces": counts["trace"], "spans": counts["span"], "events": counts["event"]}

    def _rows(self, kind: str, *, since: datetime | None = None) -> list[dict[str, Any]]:
        statement = select(ObservabilityRecordModel.payload_json).where(
            ObservabilityRecordModel.kind == kind
        )
        if since is not None:
            statement = statement.where(ObservabilityRecordModel.created_at >= since)
        statement = statement.order_by(ObservabilityRecordModel.created_at.desc())
        with self._engine.connect() as connection:
            rows = connection.execute(statement).all()
        output = []
        for row in rows:
            item = row[0].get("payload", {})
            if kind in {"trace", "span"}:
                item = {**item, **(item.get("usage") or {})}
            output.append(item)
        return output

    def overview(self, days: int = 30) -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        return build_telemetry_overview(
            self._rows("trace", since=since),
            self._rows("span", since=since),
            self._rows("event", since=since),
            days,
        )

    def list_traces(self, *, limit: int = 100, session_id: str | None = None, status: str | None = None, user_id: str | None = None, workspace_ids: frozenset[str] | None = None) -> list[dict[str, Any]]:
        rows = self._rows("trace")
        return [row for row in rows if (not session_id or row.get("session_id") == session_id) and (not status or row.get("status") == status) and (not user_id or row.get("user_id") == user_id) and (not workspace_ids or row.get("workspace_id") in workspace_ids)][:limit]

    def trace_groups(self, *, days: int = 30, limit: int = 24, offset: int = 0, query: str | None = None, focus: str = "all") -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        traces = self._rows("trace", since=since)
        spans = self._rows("span", since=since)
        page = build_trace_group_page(
            traces,
            spans=spans,
            limit=limit,
            offset=offset,
            query=query,
            focus=focus,
        )
        page["period_days"] = days
        return page

    def trace_group_detail(self, chain_id: str) -> dict[str, Any] | None:
        traces = [row for row in self._rows("trace") if trace_chain_identity(row)["chain_id"] == chain_id]
        if not traces:
            return None
        trace_ids = {str(row["trace_id"]) for row in traces}
        spans = [row for row in self._rows("span") if str(row.get("trace_id")) in trace_ids]
        chain = build_trace_group_page(traces, spans=spans, limit=1)["items"][0]
        return {
            "chain": chain,
            "traces": traces,
            "spans": spans,
            "events": [row for row in self._rows("event") if str(row.get("trace_id")) in trace_ids],
        }

    def recent_events(self, *, limit: int = 200, level: str | None = None, trace_id: str | None = None) -> list[dict[str, Any]]:
        return [row for row in self._rows("event") if (not level or row.get("level") == level) and (not trace_id or row.get("trace_id") == trace_id)][:limit]

    def trace_detail(self, trace_id: str) -> dict[str, Any] | None:
        traces = [row for row in self._rows("trace") if row.get("trace_id") == trace_id]
        if not traces: return None
        return {"trace": traces[0], "spans": [row for row in self._rows("span") if row.get("trace_id") == trace_id], "events": [row for row in self._rows("event") if row.get("trace_id") == trace_id]}

    def errors(self, days: int = 30, limit: int = 100) -> list[dict[str, Any]]:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        return build_telemetry_overview(
            [], self._rows("span", since=since), [], days
        )["error_groups"][:limit]

    def usage(self, days: int = 30) -> list[dict[str, Any]]:
        since = (
            datetime.now(timezone.utc).date() - timedelta(days=max(1, days))
        ).isoformat()
        metrics: dict[tuple[str, str, str], dict[str, Any]] = {}
        token_fields = (
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "cache_miss_tokens",
            "reasoning_tokens",
            "total_tokens",
        )

        for row in self._rows("span"):
            completed_at = row.get("completed_at")
            if not completed_at:
                continue

            day = str(completed_at)[:10]
            if day < since:
                continue

            component = str(row.get("kind") or "unknown")
            name = str(row.get("name") or "unknown")
            attributes = row.get("attributes") or {}
            if component == "model" and attributes.get("model"):
                name = f"{name}:{attributes['model']}"

            key = (day, component, name)
            metric = metrics.setdefault(
                key,
                {
                    "day": day,
                    "component": component,
                    "name": name,
                    "requests": 0,
                    "successes": 0,
                    "errors": 0,
                    "duration_sum_ms": 0,
                    **{field: 0 for field in token_fields},
                },
            )

            status = row.get("status")
            metric["requests"] += 1
            metric["successes"] += int(status == "ok")
            metric["errors"] += int(status in {"error", "timeout"})
            metric["duration_sum_ms"] += int(row.get("duration_ms") or 0)
            for field in token_fields:
                metric[field] += int(row.get(field, 0) or 0)

        return [metrics[key] for key in sorted(metrics)]

    def prune(self, trace_days: int = 30, event_days: int = 30) -> dict[str, int]:
        """Delete expired telemetry without touching the quota usage ledger."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        trace_before = now - timedelta(days=max(1, trace_days))
        event_before = now - timedelta(days=max(1, event_days))

        with self._engine.begin() as connection:
            spans = connection.execute(
                delete(ObservabilityRecordModel).where(
                    ObservabilityRecordModel.kind == "span",
                    ObservabilityRecordModel.created_at < trace_before,
                )
            )
            traces = connection.execute(
                delete(ObservabilityRecordModel).where(
                    ObservabilityRecordModel.kind == "trace",
                    ObservabilityRecordModel.created_at < trace_before,
                )
            )
            events = connection.execute(
                delete(ObservabilityRecordModel).where(
                    ObservabilityRecordModel.kind == "event",
                    ObservabilityRecordModel.created_at < event_before,
                )
            )
        return {
            "traces": max(0, int(traces.rowcount or 0)),
            "spans": max(0, int(spans.rowcount or 0)),
            "events": max(0, int(events.rowcount or 0)),
            "daily_metrics": 0,
        }

    def delete_session(self, session_id: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(delete(ObservabilityRecordModel).where(ObservabilityRecordModel.session_id == session_id))

    def close(self) -> None:
        self._engine.dispose()

    def flush(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        # Query endpoints remain safe during the migration window; new telemetry is
        # durable in MySQL even when no historical rows exist.
        if name in {"overview", "list_traces", "trace_detail", "usage", "recent_events", "errors"}:
            return lambda *args, **kwargs: [] if name != "overview" else {"period_days": kwargs.get("days", 30), "requests": 0, "successes": 0, "errors": 0, "error_rate": 0.0, "latency_ms": {"p50": None, "p90": None, "p95": None, "p99": None}, "ttft_ms": {"p50": None, "p90": None, "p95": None, "p99": None}, "tokens": {}}
        raise AttributeError(name)
