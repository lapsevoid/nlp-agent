import subprocess
import sys
from pathlib import Path

from scripts.seed_monitor_demo import MARKER, _build_observability


def test_demo_seed_builds_dense_multi_user_observability_data():
    envelopes, usage_rows = _build_observability(count=96)

    traces = [envelope for envelope in envelopes if envelope.kind == "trace"]
    spans = [envelope for envelope in envelopes if envelope.kind == "span"]
    events = [envelope for envelope in envelopes if envelope.kind == "event"]

    assert len(traces) == 96
    assert len(spans) == 96 * 3
    assert len(events) >= 96
    assert len(usage_rows) == 96
    assert {row["user_id"] for row in usage_rows} == {
        "demo-user-alice",
        "demo-user-bob",
        "demo-user-charlie",
        "demo-user-diana",
    }
    assert len({row["provider_model"] for row in usage_rows}) == 3
    assert {row["raw_usage_json"]["demo_seed_id"] for row in usage_rows} == {MARKER}
    assert {row["outcome_status"] for row in usage_rows} >= {"completed", "error", "timeout"}


def test_demo_seed_script_supports_direct_project_root_invocation():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/seed_monitor_demo.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "Seed synthetic monitoring data" in result.stdout
