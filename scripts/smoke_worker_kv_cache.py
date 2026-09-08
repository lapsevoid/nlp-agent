"""Run an opt-in real-Provider smoke check for the Worker KV cache prefix."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from typing import Any
from uuid import uuid4

from core.model_runtime.factory import ModelFactory
from core.model_runtime.reporters import (
    InMemoryModelUsageReporter,
    ModelUsageReporterSlot,
)
from core.model_runtime.usage import UsageAttributionContext, bind_usage_attribution
from server.tools.worker_tool import _build_worker_initial_messages


def _attribution() -> UsageAttributionContext:
    suffix = uuid4().hex
    return UsageAttributionContext(
        request_id=f"kv-cache-smoke-{suffix}",
        user_id="kv-cache-smoke",
        workspace_id="kv-cache-smoke",
        conversation_id=f"kv-cache-smoke-{suffix}",
        turn_id=suffix,
        purpose="worker",
    )


def _measured_usage(
    reporter: InMemoryModelUsageReporter,
    *,
    event_index: int,
) -> dict[str, Any]:
    attempts = reporter.events[event_index:]
    succeeded = [event for event in attempts if event[2].status == "succeeded"]
    if len(succeeded) != 1:
        raise AssertionError(
            f"expected one successful Provider attempt, got {len(succeeded)}"
        )
    invocation, usage, outcome = succeeded[0]
    if usage.source != "provider":
        raise AssertionError(f"usage is not Provider-measured: {usage.source}")
    if usage.input_tokens <= 0:
        raise AssertionError("Provider did not return positive input token usage")
    return {
        "provider": invocation.identity.provider,
        "model": invocation.identity.provider_model,
        "attempts": len(attempts),
        "finish_reason": outcome.finish_reason,
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "cache_hit_rate": usage.cached_input_tokens / usage.input_tokens,
    }


async def _run(wait_s: float) -> dict[str, Any]:
    reporter = InMemoryModelUsageReporter()
    factory = ModelFactory.from_settings()
    factory.reporter_slot = ModelUsageReporterSlot(reporter, required=True)
    if not factory.profile_available("deepseek"):
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    first_messages = _build_worker_initial_messages(
        "KV cache smoke profile A.",
        "Reply with exactly WARMED.",
        current_time="2026-09-08 08:00:00 Tuesday",
    )
    second_messages = _build_worker_initial_messages(
        "KV cache smoke profile B.",
        "Reply with exactly DISCOVERED.",
        current_time="2026-09-08 09:00:00 Tuesday",
    )
    third_messages = _build_worker_initial_messages(
        "KV cache smoke profile C.",
        "Reply with exactly HIT.",
        current_time="2026-09-08 10:00:00 Tuesday",
    )
    first_prefix = str(first_messages[0].content)
    second_prefix = str(second_messages[0].content)
    third_prefix = str(third_messages[0].content)
    if not first_prefix == second_prefix == third_prefix:
        raise AssertionError("Worker stable prefixes differ before Provider calls")

    worker = factory.build_profile_role("deepseek", "worker")
    first_index = len(reporter.events)
    with bind_usage_attribution(_attribution()):
        await worker.ainvoke(first_messages)
    first_usage = _measured_usage(reporter, event_index=first_index)

    if wait_s:
        await asyncio.sleep(wait_s)

    second_index = len(reporter.events)
    with bind_usage_attribution(_attribution()):
        await worker.ainvoke(second_messages)
    second_usage = _measured_usage(reporter, event_index=second_index)

    if wait_s:
        await asyncio.sleep(wait_s)

    third_index = len(reporter.events)
    with bind_usage_attribution(_attribution()):
        await worker.ainvoke(third_messages)
    third_usage = _measured_usage(reporter, event_index=third_index)
    if third_usage["cached_input_tokens"] <= 0:
        raise AssertionError(
            "verification Provider response reported zero cached_input_tokens"
        )

    return {
        "prefix_sha256": hashlib.sha256(first_prefix.encode("utf-8")).hexdigest(),
        "prefix_characters": len(first_prefix),
        "warmup": first_usage,
        "prefix_discovery": second_usage,
        "verification": third_usage,
        "verified": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Make three real DeepSeek Worker calls with different runtime context "
            "and require a Provider-reported KV cache hit after prefix discovery."
        )
    )
    parser.add_argument(
        "--wait-s",
        type=float,
        default=3.0,
        help="Seconds to wait between Provider calls while cache prefixes persist.",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(max(0.0, args.wait_s))), indent=2))


if __name__ == "__main__":
    main()
