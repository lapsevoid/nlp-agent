"""Seed synthetic monitoring data for local dashboard review.

The script is deliberately scoped to localhost MySQL and to rows carrying the
``monitor-demo-v1`` marker.  It never touches real users, prompts, or secrets.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, delete
from sqlalchemy.dialects.mysql import insert

from configs.settings import settings
from core.observability.models import (
    SpanKind,
    SpanRecord,
    SpanStatus,
    TelemetryEnvelope,
    TelemetryEvent,
    TokenUsage,
    TraceRecord,
)
from server.infrastructure.mysql.models import ObservabilityRecordModel
from server.quota.models import UsageEventModel


MARKER = "monitor-demo-v1"
PREFIX = f"{MARKER}-"


def _id(kind: str, index: int) -> str:
    return f"{PREFIX}{kind}-{index:03d}-{uuid.uuid4().hex[:8]}"


def _utc(day_offset: int, minute: int) -> datetime:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return now - timedelta(days=day_offset, minutes=minute)


def _usage(index: int) -> TokenUsage:
    input_tokens = 700 + index * 41
    output_tokens = 180 + index * 17
    cached = 120 if index % 3 == 0 else 0
    reasoning = 90 if index % 4 == 0 else 0
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached,
        cache_miss_tokens=max(0, input_tokens - cached),
        reasoning_tokens=reasoning,
        total_tokens=input_tokens + output_tokens + reasoning,
        source="provider",
    )


def _build_observability(count: int = 96) -> tuple[list[TelemetryEnvelope], list[dict]]:
    envelopes: list[TelemetryEnvelope] = []
    usage_rows: list[dict] = []
    users = ["demo-user-alice", "demo-user-bob", "demo-user-charlie", "demo-user-diana"]
    workspaces = ["demo-workspace-lab", "demo-workspace-prod"]
    models = [
        ("openai", "gpt-5.4-mini"),
        ("openai", "gpt-5.4"),
        ("anthropic", "claude-sonnet-4"),
    ]
    channels = ["web", "api", "cli", "sandbox"]

    for index in range(count):
        user = users[index % len(users)]
        workspace = workspaces[index % len(workspaces)]
        provider, model_name = models[index % len(models)]
        is_timeout = index % 19 == 7
        is_error = not is_timeout and index % 17 in {4, 13}
        has_retry = is_timeout or is_error
        status = SpanStatus.TIMEOUT if is_timeout else SpanStatus.ERROR if is_error else SpanStatus.OK
        trace_id = _id("trace", index)
        session_id = f"{PREFIX}session-{index % 8:02d}"
        turn_id = f"{PREFIX}turn-{index:03d}"
        started = _utc(index % 8, index * 7)
        duration = 280 + index * 33
        usage = _usage(index)
        attributes = {
            "demo_seed_id": MARKER,
            "model": model_name,
            "provider": provider,
            "request_type": "chat_completion" if index % 3 else "tool_augmented_chat",
            "retry_count": 1 if has_retry else 0,
        }
        trace = TraceRecord(
            trace_id=trace_id,
            request_id=_id("request", index),
            session_id=session_id,
            turn_id=turn_id,
            workspace_id=workspace,
            user_id=user,
            channel=channels[index % len(channels)],
            source="demo",
            started_at=started,
            completed_at=started + timedelta(milliseconds=duration),
            duration_ms=duration,
            ttft_ms=80 + index * 9,
            status=status,
            usage=usage,
            error_kind="provider_timeout" if status == SpanStatus.TIMEOUT else "upstream_5xx" if status == SpanStatus.ERROR else None,
            error_message="Synthetic demo failure" if status != SpanStatus.OK else None,
            attributes=attributes,
        )
        envelopes.append(TelemetryEnvelope(kind="trace", payload=trace))

        root_span_id = _id("span", index * 3)
        model_span_id = _id("span", index * 3 + 1)
        tool_span_id = _id("span", index * 3 + 2)
        root = SpanRecord(
            trace_id=trace_id, span_id=root_span_id, session_id=session_id,
            turn_id=turn_id, kind=SpanKind.GATEWAY, name="gateway.request",
            started_at=started, completed_at=started + timedelta(milliseconds=duration),
            duration_ms=duration, status=status, attributes={"demo_seed_id": MARKER},
        )
        model = SpanRecord(
            trace_id=trace_id, span_id=model_span_id, parent_span_id=root_span_id,
            session_id=session_id, turn_id=turn_id, kind=SpanKind.MODEL,
            name="model.invoke", started_at=started + timedelta(milliseconds=30),
            completed_at=started + timedelta(milliseconds=duration - 20),
            duration_ms=max(1, duration - 50), status=status, attempt=2 if has_retry else 1,
            usage=usage, error_kind=trace.error_kind, error_message=trace.error_message,
            attributes={"demo_seed_id": MARKER, "provider": provider, "model": model_name},
        )
        tool = SpanRecord(
            trace_id=trace_id, span_id=tool_span_id, parent_span_id=root_span_id,
            session_id=session_id, turn_id=turn_id, worker_id=f"{PREFIX}worker-{index % 3}",
            kind=SpanKind.TOOL, name="tool.search" if index % 2 else "tool.rerank",
            started_at=started + timedelta(milliseconds=10),
            completed_at=started + timedelta(milliseconds=duration // 3),
            duration_ms=max(1, duration // 3), status=SpanStatus.OK,
            attributes={"demo_seed_id": MARKER, "cache_hit": index % 2 == 0},
        )
        envelopes.extend(TelemetryEnvelope(kind="span", payload=item) for item in (root, model, tool))

        event_specs = [("request.completed", "info" if status == SpanStatus.OK else "error")]
        if has_retry:
            event_specs.insert(0, ("request.retry", "warning"))
        if index % 11 == 3:
            event_specs.append(("request.slow", "warning"))
        if index % 23 == 9:
            event_specs.append(("provider.rate_limited", "error"))
        if index % 5 == 0:
            event_specs.append(("queue.backpressure", "warning"))
        for event_index, (name, level) in enumerate(event_specs):
            event = TelemetryEvent(
                event_id=_id("event", index * 3 + event_index),
                timestamp=started + timedelta(milliseconds=duration), level=level,
                name=name, trace_id=trace_id, span_id=model_span_id,
                session_id=session_id, turn_id=turn_id,
                worker_id=f"{PREFIX}worker-{index % 3}",
                payload={"demo_seed_id": MARKER, "provider": provider, "model": model_name},
            )
            envelopes.append(TelemetryEnvelope(kind="event", payload=event))

        operation_id = f"{PREFIX}operation-{index:03d}"
        usage_rows.append({
            "id": str(uuid.uuid4()), "operation_id": operation_id,
            "reservation_id": f"{PREFIX}reservation-{index:03d}", "user_id": user,
            "workspace_id": workspace, "conversation_id": session_id,
            "turn_id": turn_id, "worker_id": f"{PREFIX}worker-{index % 3}",
            "parent_operation_id": None, "purpose": "chat", "provider": provider,
            "provider_model": model_name, "provider_response_id": f"{PREFIX}response-{index:03d}",
            "model_profile": "balanced", "preset": "demo", "route": "primary",
            "pricing_key": f"{provider}:{model_name}" if index % 6 else None,
            "attempt": 2 if has_retry else 1, "fallback_index": 1 if index % 29 == 19 else 0,
            "outcome_status": "timeout" if status == SpanStatus.TIMEOUT else "error" if status == SpanStatus.ERROR else "completed",
            "finish_reason": "stop" if status == SpanStatus.OK else None,
            "error_kind": trace.error_kind, "input_tokens": usage.input_tokens,
            "cached_input_tokens": usage.cached_tokens, "cache_write_input_tokens": 0,
            "output_tokens": usage.output_tokens, "reasoning_output_tokens": usage.reasoning_tokens,
            "total_tokens": usage.total_tokens, "usage_source": "provider",
            "usage_status": "priced" if index % 6 else "unpriced",
            "pricing_version": "demo-2026-01", "credits_micro": None if index % 6 else 0,
            "raw_usage_json": {"demo_seed_id": MARKER, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
            "dedupe_key": f"{PREFIX}dedupe-{index:03d}", "idempotency_key": f"{PREFIX}idem-{index:03d}",
            "started_at": started, "occurred_at": started + timedelta(milliseconds=duration),
            "created_at": started + timedelta(milliseconds=duration), "archived_at": None, "archive_batch_id": None,
        })
    for index in range(max(3, count // 24)):
        timestamp = _utc(index, index * 11 + 3)
        envelopes.append(TelemetryEnvelope(kind="event", payload=TelemetryEvent(
            event_id=_id("system-event", index), timestamp=timestamp, level="warning",
            name="telemetry.backpressure", worker_id=f"{PREFIX}worker-{index % 3}",
            payload={
                "demo_seed_id": MARKER, "component": "telemetry", "operation": "event_ingest",
                "queue_size": 420 + index * 95, "queue_capacity": 5000,
            },
        )))
    return envelopes, usage_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic monitoring data into local MySQL")
    parser.add_argument("--clear", action="store_true", help="remove only monitor-demo-v1 rows")
    parser.add_argument("--count", type=int, default=96, help="number of synthetic logical requests to create (default: 96)")
    args = parser.parse_args()
    if args.count < 1:
        raise SystemExit("--count must be at least 1")
    database_url = settings.NLP_AGENT_DATABASE_URL.strip()
    if not database_url or not any(host in database_url for host in ("127.0.0.1", "localhost")):
        raise SystemExit("Refusing to seed: NLP_AGENT_DATABASE_URL must point to localhost")

    engine = create_engine(database_url.replace("mysql+aiomysql://", "mysql+pymysql://"), pool_pre_ping=True)
    envelopes, usage_rows = _build_observability(count=args.count)
    with engine.begin() as connection:
        connection.execute(delete(ObservabilityRecordModel).where(ObservabilityRecordModel.record_key.like(f"{PREFIX}%")))
        connection.execute(delete(UsageEventModel).where(UsageEventModel.operation_id.like(f"{PREFIX}%")))
        if not args.clear:
            for envelope in envelopes:
                payload = envelope.model_dump(mode="json")
                item = payload["payload"]
                key = str(
                    item.get("event_id")
                    if envelope.kind == "event"
                    else item.get("span_id")
                    if envelope.kind == "span"
                    else item.get("trace_id")
                )
                connection.execute(insert(ObservabilityRecordModel).values(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"nlp-agent:{envelope.kind}:{key}")),
                    kind=envelope.kind, record_key=key, trace_id=item.get("trace_id"),
                    session_id=item.get("session_id"), turn_id=item.get("turn_id"),
                    status=item.get("status"), payload_json=payload,
                ))
            connection.execute(insert(UsageEventModel), usage_rows)
    engine.dispose()
    print("cleared monitor-demo-v1 rows")
    if not args.clear:
        print(f"seeded {len(envelopes)} observability records and {len(usage_rows)} usage events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
