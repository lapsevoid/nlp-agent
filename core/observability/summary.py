"""Low-cardinality operational summaries built from persisted telemetry rows."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any, Iterable


LEGACY_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "cache_miss_tokens",
    "reasoning_tokens",
    "total_tokens",
)

_FAILURE_STATUSES = {"error", "timeout", "cancelled", "denied"}
_ERROR_STATUSES = {"error", "timeout"}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    if not value:
        return None
    try:
        return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _in_period(row: dict[str, Any], since: datetime, *, event: bool = False) -> bool:
    keys = ("timestamp",) if event else ("completed_at", "started_at")
    timestamp = next((_parse_datetime(row.get(key)) for key in keys if row.get(key)), None)
    return timestamp is not None and timestamp >= since


def _usage_value(row: dict[str, Any], field: str) -> int:
    value = row.get(field)
    if value is None:
        value = (row.get("usage") or {}).get(field, 0)
    return int(value or 0)


def _percentile(values: Iterable[int], percentile: float) -> int | None:
    ordered = sorted(values)
    if not ordered:
        return None
    # Use nearest-rank semantics for operational tail metrics. Interpolating
    # between two samples can make a rare slow request disappear from P90/P95/
    # P99, which is the opposite of what an incident dashboard needs.
    rank = max(1, ceil(len(ordered) * percentile))
    return ordered[min(len(ordered) - 1, rank - 1)]


def _dimension_counts(values: Iterable[Any]) -> list[dict[str, Any]]:
    counts = Counter(str(value or "unknown") for value in values)
    return [
        {"value": value, "requests": count}
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _token_totals(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    return {
        field: sum(_usage_value(row, field) for row in rows)
        for field in LEGACY_TOKEN_FIELDS
    }


def _aggregate_rows(rows: list[dict[str, Any]], *, key_name: str, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(str(row.get(item) or "unknown") for item in keys)
        item = groups.setdefault(
            key,
            {
                key_name: key[0] if len(key) == 1 else None,
                **{item: value for item, value in zip(keys, key)},
                "requests": 0,
                "successes": 0,
                "errors": 0,
                "failed_requests": 0,
                "retries": 0,
                "duration_sum_ms": 0,
                "total_tokens": 0,
            },
        )
        status = str(row.get("status") or "unknown")
        item["requests"] += 1
        item["successes"] += int(status == "ok")
        item["errors"] += int(status in _ERROR_STATUSES)
        item["failed_requests"] += int(status in _FAILURE_STATUSES)
        item["retries"] += int(int(row.get("attempt") or 1) > 1)
        item["duration_sum_ms"] += int(row.get("duration_ms") or 0)
        item["total_tokens"] += _usage_value(row, "total_tokens")

    result = []
    for item in groups.values():
        item["error_rate"] = item["errors"] / item["requests"] if item["requests"] else 0.0
        item["failure_rate"] = item["failed_requests"] / item["requests"] if item["requests"] else 0.0
        item["avg_duration_ms"] = round(item["duration_sum_ms"] / item["requests"]) if item["requests"] else 0
        item.pop("duration_sum_ms", None)
        result.append(item)
    return sorted(result, key=lambda item: (-item["requests"], tuple(str(item.get(key, "")) for key in keys)))


def _user_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("user_id") or "unknown")].append(row)

    result = []
    for user_id, user_rows in groups.items():
        errors = sum(str(row.get("status") or "") in _ERROR_STATUSES for row in user_rows)
        requests = len(user_rows)
        timestamps = [
            _parse_datetime(row.get("completed_at") or row.get("started_at"))
            for row in user_rows
        ]
        last_seen = max((value for value in timestamps if value is not None), default=None)
        result.append(
            {
                "user_id": user_id,
                "requests": requests,
                "successes": sum(row.get("status") == "ok" for row in user_rows),
                "errors": errors,
                "error_rate": errors / requests if requests else 0.0,
                "total_tokens": sum(_usage_value(row, "total_tokens") for row in user_rows),
                "avg_duration_ms": round(
                    sum(int(row.get("duration_ms") or 0) for row in user_rows) / requests
                )
                if requests
                else 0,
                "workspaces": sorted({str(row.get("workspace_id") or "unknown") for row in user_rows}),
                "last_seen": last_seen.isoformat() if last_seen else None,
            }
        )
    return sorted(result, key=lambda item: (-item["requests"], item["user_id"]))


def _model_rows(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    models: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        if str(span.get("kind") or "") != "model":
            continue
        attributes = span.get("attributes") or {}
        provider = str(
            attributes.get("provider")
            or attributes.get("provider_name")
            or "unknown"
        )
        provider_model = str(
            attributes.get("provider_model")
            or attributes.get("model")
            or attributes.get("model_name")
            or "unknown"
        )
        profile = str(attributes.get("model_profile") or attributes.get("preset") or "unknown")
        models[(provider, provider_model, profile)].append(span)

    result = []
    for (provider, provider_model, profile), rows in models.items():
        errors = sum(str(row.get("status") or "") in _ERROR_STATUSES for row in rows)
        result.append(
            {
                "provider": provider,
                "provider_model": provider_model,
                "model_profile": profile,
                "requests": len(rows),
                "successes": sum(row.get("status") == "ok" for row in rows),
                "errors": errors,
                "error_rate": errors / len(rows) if rows else 0.0,
                "retries": sum(int(row.get("attempt") or 1) > 1 for row in rows),
                "total_tokens": sum(_usage_value(row, "total_tokens") for row in rows),
                "avg_duration_ms": round(
                    sum(int(row.get("duration_ms") or 0) for row in rows) / len(rows)
                )
                if rows
                else 0,
            }
        )
    return sorted(result, key=lambda item: (-item["requests"], item["provider_model"]))


def _error_groups(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        if str(span.get("status") or "") not in _FAILURE_STATUSES:
            continue
        key = (
            str(span.get("error_kind") or "unknown"),
            str(span.get("kind") or "unknown"),
            str(span.get("name") or "unknown"),
        )
        groups[key].append(span)

    result = []
    for (error_kind, kind, name), rows in groups.items():
        timestamps = [
            _parse_datetime(row.get("completed_at") or row.get("started_at"))
            for row in rows
        ]
        last_seen = max((value for value in timestamps if value is not None), default=None)
        result.append(
            {
                "error_kind": error_kind,
                "kind": kind,
                "name": name,
                "count": len(rows),
                "last_seen": last_seen.isoformat() if last_seen else None,
                "sample_trace_id": str(rows[0].get("trace_id") or ""),
            }
        )
    return sorted(
        result,
        key=lambda item: (item["count"], item["last_seen"] or ""),
        reverse=True,
    )


def build_telemetry_overview(
    traces: Iterable[dict[str, Any]],
    spans: Iterable[dict[str, Any]],
    events: Iterable[dict[str, Any]],
    days: int = 30,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build an all-user overview without exposing high-cardinality IDs as tags.

    Trace rows represent logical requests; span rows represent component attempts.
    Keeping those two populations separate prevents retries from inflating the
    user-facing request/error rate while still making retry/fallback pressure
    visible in component and model tables.
    """

    end = _utc(now or datetime.now(timezone.utc))
    since = end - timedelta(days=max(1, days))
    trace_rows = [
        row for row in traces if row.get("completed_at") and _in_period(row, since)
    ]
    span_rows = [
        row for row in spans if row.get("completed_at") and _in_period(row, since)
    ]
    event_rows = [row for row in events if _in_period(row, since, event=True)]
    statuses = Counter(str(row.get("status") or "unknown") for row in trace_rows)
    errors = sum(row.get("status") in _ERROR_STATUSES for row in trace_rows)
    failed_requests = sum(row.get("status") in _FAILURE_STATUSES for row in trace_rows)
    durations = [int(row["duration_ms"]) for row in trace_rows if row.get("duration_ms") is not None]
    ttfts = [int(row["ttft_ms"]) for row in trace_rows if row.get("ttft_ms") is not None]
    timestamps = [
        _parse_datetime(row.get("completed_at") or row.get("started_at"))
        for row in trace_rows
    ]
    latest_trace = max((value for value in timestamps if value is not None), default=None)
    event_timestamps = [
        _parse_datetime(row.get("timestamp"))
        for row in event_rows
    ]
    latest_event = max((value for value in event_timestamps if value is not None), default=None)
    span_kind_rows = _aggregate_rows(span_rows, key_name="kind", keys=("kind",))
    component_rows = _aggregate_rows(span_rows, key_name="name", keys=("kind", "name"))
    for row in component_rows:
        row["label"] = f"{row['kind']} · {row['name']}"
        row.pop("kind", None)

    return {
        "period_days": max(1, days),
        "from": since.isoformat(),
        "to": end.isoformat(),
        "requests": len(trace_rows),
        "successes": statuses.get("ok", 0),
        "errors": errors,
        "failed_requests": failed_requests,
        "error_rate": errors / len(trace_rows) if trace_rows else 0.0,
        "failure_rate": failed_requests / len(trace_rows) if trace_rows else 0.0,
        "latency_ms": {
            "p50": _percentile(durations, 0.50),
            "p90": _percentile(durations, 0.90),
            "p95": _percentile(durations, 0.95),
            "p99": _percentile(durations, 0.99),
        },
        "ttft_ms": {
            "p50": _percentile(ttfts, 0.50),
            "p90": _percentile(ttfts, 0.90),
            "p95": _percentile(ttfts, 0.95),
            "p99": _percentile(ttfts, 0.99),
        },
        "tokens": _token_totals(trace_rows),
        "active_users": len({str(row.get("user_id") or "unknown") for row in trace_rows}),
        "active_workspaces": len(
            {str(row.get("workspace_id") or "unknown") for row in trace_rows}
        ),
        "active_sessions": len(
            {str(row.get("session_id") or "unknown") for row in trace_rows}
        ),
        "status_breakdown": [
            {"value": value, "requests": count}
            for value, count in sorted(statuses.items(), key=lambda item: (-item[1], item[0]))
        ],
        "tags": {
            "channels": _dimension_counts(row.get("channel") for row in trace_rows),
            "sources": _dimension_counts(row.get("source") for row in trace_rows),
            "span_kinds": _dimension_counts(row.get("kind") for row in span_rows),
        },
        "component_spans": component_rows,
        "span_kinds": span_kind_rows,
        "models": _model_rows(span_rows),
        "error_groups": _error_groups(span_rows),
        "events_by_level": dict(
            sorted(Counter(str(row.get("level") or "unknown") for row in event_rows).items())
        ),
        "event_names": _dimension_counts(row.get("name") for row in event_rows),
        "top_users": _user_summary(trace_rows),
        "latest_trace_at": latest_trace.isoformat() if latest_trace else None,
        "latest_event_at": latest_event.isoformat() if latest_event else None,
    }
