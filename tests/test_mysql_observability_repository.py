from datetime import datetime, timezone

from core.observability.mysql_repository import MySQLTelemetryRepository, _record_key


def test_observability_record_key_keeps_siblings_in_one_trace_distinct():
    trace = {"trace_id": "trace-1", "span_id": "span-1", "event_id": "event-1"}

    assert _record_key("trace", trace) == "trace-1"
    assert _record_key("span", trace) == "span-1"
    assert _record_key("event", trace) == "event-1"
    assert _record_key("span", {**trace, "span_id": "span-2"}) != _record_key("span", trace)


def test_prune_deletes_expired_trace_span_and_event_rows():
    class Result:
        def __init__(self, rowcount):
            self.rowcount = rowcount

    class Connection:
        def __init__(self):
            self.statements = []
            self.rowcounts = iter((4, 2, 3))

        def execute(self, statement):
            self.statements.append(statement)
            return Result(next(self.rowcounts))

    class Begin:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, *_args):
            return None

    class Engine:
        def __init__(self):
            self.connection = Connection()

        def begin(self):
            return Begin(self.connection)

    repository = object.__new__(MySQLTelemetryRepository)
    repository._engine = Engine()

    assert repository.prune(trace_days=30, event_days=14) == {
        "traces": 2,
        "spans": 4,
        "events": 3,
        "daily_metrics": 0,
    }
    assert len(repository._engine.connection.statements) == 3


def test_usage_aggregates_span_rows_for_monitor_contract(monkeypatch) -> None:
    repository = object.__new__(MySQLTelemetryRepository)
    day = datetime.now(timezone.utc).date().isoformat()
    rows = [
        {
            "completed_at": f"{day}T10:00:00+00:00",
            "kind": "model",
            "name": "coordinator.model",
            "attributes": {"model": "test-model"},
            "status": "ok",
            "duration_ms": 125,
            "input_tokens": 12,
            "output_tokens": 3,
            "cached_tokens": 2,
            "cache_miss_tokens": 10,
            "reasoning_tokens": 1,
            "total_tokens": 15,
        },
        {
            "completed_at": f"{day}T11:00:00+00:00",
            "kind": "model",
            "name": "coordinator.model",
            "attributes": {"model": "test-model"},
            "status": "error",
            "duration_ms": 75,
            "input_tokens": 8,
            "output_tokens": 2,
            "cached_tokens": 0,
            "cache_miss_tokens": 8,
            "reasoning_tokens": 0,
            "total_tokens": 10,
        },
    ]

    monkeypatch.setattr(repository, "_rows", lambda kind: rows if kind == "span" else [])

    assert repository.usage() == [
        {
            "day": day,
            "component": "model",
            "name": "coordinator.model:test-model",
            "requests": 2,
            "successes": 1,
            "errors": 1,
            "duration_sum_ms": 200,
            "input_tokens": 20,
            "output_tokens": 5,
            "cached_tokens": 2,
            "cache_miss_tokens": 18,
            "reasoning_tokens": 1,
            "total_tokens": 25,
        }
    ]
