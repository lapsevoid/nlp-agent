"""Authenticated query boundary for Gateway observability APIs."""

from __future__ import annotations

import asyncio
from typing import Any

from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.observability.runtime import TelemetryRuntime, global_telemetry


class ObservabilityService:
    def __init__(self, runtime: TelemetryRuntime = global_telemetry) -> None:
        self.runtime = runtime

    @staticmethod
    def _require_admin(principal: AuthenticatedPrincipal) -> None:
        if not principal.is_admin:
            raise AccessDeniedError("administrator role is required")

    async def overview(
        self, principal: AuthenticatedPrincipal, days: int = 30
    ) -> dict[str, Any]:
        self._require_admin(principal)
        result = await asyncio.to_thread(self.runtime.repository.overview, days)
        return {**result, "runtime": self.runtime.health()}

    async def traces(
        self,
        principal: AuthenticatedPrincipal,
        *,
        limit: int = 100,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.runtime.repository.list_traces,
            limit=limit,
            session_id=session_id,
            status=status,
            user_id=None if principal.is_admin else principal.user_id,
            workspace_ids=None if principal.is_admin else principal.workspace_ids,
        )

    async def trace(
        self, principal: AuthenticatedPrincipal, trace_id: str
    ) -> dict[str, Any] | None:
        detail = await asyncio.to_thread(self.runtime.repository.trace_detail, trace_id)
        if detail is None:
            return None
        trace = detail["trace"]
        if not principal.is_admin and (
            trace.get("user_id") != principal.user_id
            or (
                "*" not in principal.workspace_ids
                and trace.get("workspace_id") not in principal.workspace_ids
            )
        ):
            raise AccessDeniedError(trace_id)
        return detail

    async def trace_groups(
        self,
        principal: AuthenticatedPrincipal,
        *,
        days: int = 30,
        limit: int = 24,
        offset: int = 0,
        query: str | None = None,
        focus: str = "all",
    ) -> dict[str, Any]:
        """Return problem-oriented chain summaries for all monitor users."""
        self._require_admin(principal)
        return await asyncio.to_thread(
            self.runtime.repository.trace_groups,
            days=days,
            limit=limit,
            offset=offset,
            query=query,
            focus=focus,
        )

    async def trace_group(
        self, principal: AuthenticatedPrincipal, chain_id: str
    ) -> dict[str, Any] | None:
        self._require_admin(principal)
        return await asyncio.to_thread(
            self.runtime.repository.trace_group_detail, chain_id
        )

    async def usage(
        self, principal: AuthenticatedPrincipal, days: int = 30
    ) -> list[dict[str, Any]]:
        self._require_admin(principal)
        return await asyncio.to_thread(self.runtime.repository.usage, days)

    async def system_usage(
        self,
        principal: AuthenticatedPrincipal,
        usage_reader: Any,
        days: int = 30,
    ) -> dict[str, Any]:
        """Read the canonical all-user token/credit ledger for the monitor."""
        self._require_admin(principal)
        return await asyncio.to_thread(usage_reader.system_snapshot, days=days)

    async def system_usage_users(
        self,
        principal: AuthenticatedPrincipal,
        usage_reader: Any,
        *,
        days: int = 30,
        limit: int = 12,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Read one bounded page from the administrator's all-user ledger."""
        self._require_admin(principal)
        return await asyncio.to_thread(
            usage_reader.system_user_page,
            days=days,
            limit=limit,
            offset=offset,
        )

    async def system_usage_trend(
        self,
        principal: AuthenticatedPrincipal,
        usage_reader: Any,
        *,
        window_minutes: int = 120,
        bucket_minutes: int = 5,
    ) -> dict[str, Any]:
        """Read the bounded five-minute all-user usage trend."""
        self._require_admin(principal)
        return await asyncio.to_thread(
            usage_reader.system_trend,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
        )

    async def events(
        self,
        principal: AuthenticatedPrincipal,
        *,
        limit: int = 200,
        level: str | None = None,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if trace_id:
            await self.trace(principal, trace_id)
        else:
            self._require_admin(principal)
        return await asyncio.to_thread(
            self.runtime.repository.recent_events,
            limit=limit,
            level=level,
            trace_id=trace_id,
        )

    async def errors(
        self, principal: AuthenticatedPrincipal, days: int = 30, limit: int = 100
    ) -> list[dict[str, Any]]:
        self._require_admin(principal)
        return await asyncio.to_thread(self.runtime.repository.errors, days, limit)

    async def health(self, principal: AuthenticatedPrincipal) -> dict[str, Any]:
        self._require_admin(principal)
        return await asyncio.to_thread(self.runtime.health)

    def subscribe(
        self, principal: AuthenticatedPrincipal, maxsize: int = 500
    ) -> asyncio.Queue[dict[str, Any]]:
        self._require_admin(principal)
        return self.runtime.subscribe(maxsize=maxsize)

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.runtime.unsubscribe(queue)


global_observability_service = ObservabilityService()
