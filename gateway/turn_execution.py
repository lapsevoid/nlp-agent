"""In-process execution implementation behind the turn-dispatch port."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from gateway.contracts import GatewayEventType, TurnStatus
from core.agent_runtime import configured_budget
from core.rbac import Permission
from gateway.dispatch import TurnTask
from gateway.engine import AgentEngine
from gateway.state import TurnExecutionState
from core.model_runtime.usage import UsageAttributionContext, bind_usage_attribution
from server.quota.contracts import FinishTurn


EventSink = Callable[[str, str, GatewayEventType, dict], Awaitable[None]]
_EXERCISE_RESULT_RE = re.compile(r"<!--\s*exercise-result:\s*(\{.*?\})\s*-->", re.DOTALL)
_GUIDED_RESULT_RE = re.compile(r"<!--\s*guided-result:\s*(\{.*?\})\s*-->", re.DOTALL)
_CANCEL_DRAIN_TIMEOUT_S = 0.25


class TurnExecutionTimeoutError(TimeoutError):
    """Raised when a turn workflow does not settle within its deadline."""

    def __init__(self, timeout_s: float) -> None:
        super().__init__(f"turn execution exceeded {timeout_s:g}s")
        self.timeout_s = timeout_s


def _extract_result(pattern: re.Pattern[str], text: str) -> tuple[str, dict[str, Any] | None]:
    match = pattern.search(text)
    if match is None:
        return text.strip(), None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return pattern.sub("", text).strip(), None
    return pattern.sub("", text).strip(), value if isinstance(value, dict) else None


class InProcessTurnExecutor:
    """Runs and finalizes turn work for the memory dispatcher."""

    def __init__(
        self,
        engine: AgentEngine,
        repository: TurnExecutionState,
        emit: EventSink,
        on_turn_completed: Callable[[str], None] | None = None,
        *,
        turn_timeout_s: float | None = None,
    ) -> None:
        self._engine = engine
        self._repository = repository
        self._emit = emit
        self._on_turn_completed = on_turn_completed
        self._abandoned_tasks: set[asyncio.Task[Any]] = set()
        self._turn_timeout_s = float(
            turn_timeout_s
            if turn_timeout_s is not None
            else configured_budget("coordinator").max_duration_s
        )
        if self._turn_timeout_s <= 0:
            raise ValueError("turn_timeout_s must be greater than zero")
        parameters = inspect.signature(engine.run_turn).parameters
        parameter_count = len(parameters)
        self._accepts_learning = parameter_count >= 6
        self._accepts_teaching_materials = parameter_count >= 7
        self._accepts_model_profile = "model_profile" in parameters

    async def run(self, task: TurnTask, execution_context: Any | None = None) -> None:
        await asyncio.to_thread(self._repository.update_turn, task.turn_id, TurnStatus.RUNNING)
        await self._emit(task.turn_id, task.context.session_id, GatewayEventType.TURN_STARTED, {"status": TurnStatus.RUNNING.value})
        quota_heartbeat_task: asyncio.Task[None] | None = None
        try:
            service = getattr(self._repository, "quota_service", None)
            if service is not None and task.reservation_id is not None:
                started = await asyncio.to_thread(
                    service.begin_reservation,
                    task.reservation_id,
                )
                if not started:
                    raise RuntimeError("quota reservation is no longer active")
                quota_heartbeat_task = asyncio.create_task(
                    self._quota_heartbeat(task.reservation_id, service),
                    name=f"quota-lease:{task.turn_id}",
                )
            # A fenced Worker passes its freshly-resolved authorization context.
            # Re-check immediately before the model/tool execution boundary.
            if execution_context is not None and hasattr(execution_context, "require"):
                execution_context.require(Permission.AGENT_TURN_SUBMIT)
            attribution = UsageAttributionContext(
                request_id=task.turn_id,
                user_id=task.context.user_id,
                workspace_id=task.context.workspace_id,
                conversation_id=task.context.session_id,
                turn_id=task.turn_id,
                reservation_id=task.reservation_id,
                worker_id=getattr(execution_context, "worker_id", None),
                purpose=(
                    "worker"
                    if getattr(execution_context, "worker_id", None)
                    else "coordinator"
                ),
            )
            with bind_usage_attribution(attribution):
                final_text, updated = await self._run_turn_with_timeout(task)
                updated_status = getattr(updated, "status", TurnStatus.COMPLETED)
                if updated_status != TurnStatus.COMPLETED:
                    await self._emit_terminal_for_state(task, updated)
                    await self._finish_quota(task)
                    return
        except asyncio.CancelledError:
            await self._engine.cancel_turn(task.context, task.turn_id)
            updated = await asyncio.to_thread(self._repository.update_turn, task.turn_id, TurnStatus.CANCELLED)
            if getattr(updated, "status", TurnStatus.CANCELLED) == TurnStatus.CANCELLED:
                await asyncio.to_thread(
                    self._repository.ensure_event,
                    turn_id=task.turn_id,
                    session_id=task.context.session_id,
                    event_type=GatewayEventType.TURN_CANCELLED,
                    payload={"status": TurnStatus.CANCELLED.value},
                )
            await self._finish_quota(task)
            raise
        except Exception as error:
            await self._converge_failed_turn(task, error)
            return
        finally:
            if quota_heartbeat_task is not None:
                quota_heartbeat_task.cancel()
                await asyncio.gather(quota_heartbeat_task, return_exceptions=True)
        await self._finish_quota(task)
        await self._emit(task.turn_id, task.context.session_id, GatewayEventType.MESSAGE_COMPLETED, {"content": final_text})
        await self._emit(task.turn_id, task.context.session_id, GatewayEventType.TURN_COMPLETED, {"status": TurnStatus.COMPLETED.value, "content": final_text})
        if self._on_turn_completed is not None:
            self._on_turn_completed(task.context.session_id)

    async def _converge_failed_turn(
        self, task: TurnTask, error: BaseException
    ) -> None:
        updated = await asyncio.to_thread(
            self._repository.update_turn,
            task.turn_id,
            TurnStatus.FAILED,
            error_kind=type(error).__name__,
            error_message=str(error),
        )
        await self._emit_terminal_for_state(task, updated, fallback_error=error)
        await self._finish_quota(task)

    async def _emit_terminal_for_state(
        self,
        task: TurnTask,
        updated: Any,
        *,
        fallback_error: BaseException | None = None,
    ) -> None:
        updated_status = getattr(updated, "status", TurnStatus.FAILED)
        if updated_status == TurnStatus.FAILED:
            error_kind = getattr(updated, "error_kind", None) or (
                type(fallback_error).__name__
                if fallback_error is not None
                else "turn_failed"
            )
            error_message = getattr(updated, "error_message", None) or str(
                fallback_error or "turn execution failed"
            )
            await self._emit(
                task.turn_id,
                task.context.session_id,
                GatewayEventType.TURN_FAILED,
                {
                    "status": TurnStatus.FAILED.value,
                    "error_kind": error_kind,
                    "message": error_message[:500],
                },
            )
        elif updated_status == TurnStatus.CANCELLED:
            await asyncio.to_thread(
                self._repository.ensure_event,
                turn_id=task.turn_id,
                session_id=task.context.session_id,
                event_type=GatewayEventType.TURN_CANCELLED,
                payload={"status": TurnStatus.CANCELLED.value},
            )
        elif updated_status == TurnStatus.COMPLETED:
            final_text = getattr(updated, "final_text", "") or ""
            await self._emit(
                task.turn_id,
                task.context.session_id,
                GatewayEventType.MESSAGE_COMPLETED,
                {"content": final_text},
            )
            await self._emit(
                task.turn_id,
                task.context.session_id,
                GatewayEventType.TURN_COMPLETED,
                {"status": TurnStatus.COMPLETED.value, "content": final_text},
            )

    async def _run_turn_workflow(self, task: TurnTask) -> tuple[str, Any]:
        final_text = await self._run_engine(task)
        final_text, exercise_state = await self._finalize_learning(task, final_text)
        updated = await asyncio.to_thread(
            self._repository.update_turn,
            task.turn_id,
            TurnStatus.COMPLETED,
            final_text=final_text,
            exercise_state=exercise_state,
        )
        return final_text, updated

    async def _run_turn_with_timeout(self, task: TurnTask) -> tuple[str, Any]:
        execution = asyncio.create_task(
            self._run_turn_workflow(task), name=f"turn-workflow:{task.turn_id}"
        )
        try:
            done, _pending = await asyncio.wait(
                {execution}, timeout=self._turn_timeout_s
            )
        except asyncio.CancelledError:
            await self._cancel_and_drain(task, execution)
            raise
        if done:
            return execution.result()
        await self._cancel_and_drain(task, execution)
        raise TurnExecutionTimeoutError(self._turn_timeout_s)

    async def _cancel_and_drain(
        self, task: TurnTask, execution: asyncio.Task[Any]
    ) -> None:
        if not execution.done():
            execution.cancel()
        cancellation = asyncio.create_task(
            self._engine.cancel_turn(task.context, task.turn_id),
            name=f"turn-cancel:{task.turn_id}",
        )
        done, pending = await asyncio.wait(
            {execution, cancellation}, timeout=_CANCEL_DRAIN_TIMEOUT_S
        )
        for pending_task in pending:
            pending_task.cancel()
            self._detach_task(pending_task)
        for completed_task in done:
            self._consume_task(completed_task)

    def _detach_task(self, task: asyncio.Task[Any]) -> None:
        self._abandoned_tasks.add(task)
        task.add_done_callback(self._consume_task)

    def _consume_task(self, task: asyncio.Task[Any]) -> None:
        self._abandoned_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            return

    async def _finish_quota(self, task: TurnTask) -> None:
        service = getattr(self._repository, "quota_service", None)
        if service is None or task.reservation_id is None:
            return
        await asyncio.to_thread(
            service.finish_turn,
            FinishTurn(
                reservation_id=task.reservation_id,
                turn_id=task.turn_id,
                idempotency_key=f"turn-finished:{task.turn_id}",
            ),
        )

    async def _quota_heartbeat(self, reservation_id: str, service: Any) -> None:
        interval = max(1.0, float(getattr(service, "lease_seconds", 300)) / 3)
        while True:
            await asyncio.sleep(interval)
            active = await asyncio.to_thread(service.heartbeat, reservation_id)
            if not active:
                return

    async def _run_engine(self, task: TurnTask) -> str:
        kwargs: dict[str, Any] = {}
        if self._accepts_learning:
            kwargs.update(
                learning_context=task.learning_context,
                learning_progress=task.learning_progress,
                exercise_state=task.exercise_state,
            )
        if self._accepts_teaching_materials:
            kwargs["teaching_materials"] = task.teaching_materials
        if self._accepts_model_profile:
            kwargs["model_profile"] = task.model_profile
        return await self._engine.run_turn(
            task.context, task.turn_id, task.content, **kwargs
        )

    async def _finalize_learning(self, task: TurnTask, final_text: str) -> tuple[str, object]:
        if task.guided_session_id is not None:
            final_text, guided_result = _extract_result(_GUIDED_RESULT_RE, final_text)
            await asyncio.to_thread(self._repository.advance_guided_session, task.guided_session_id, tutor_message=final_text, known_concepts=(guided_result or {}).get("known_concepts"), misconceptions=(guided_result or {}).get("misconceptions"), completed=(guided_result or {}).get("status") == "completed")
            await asyncio.to_thread(self._repository.update_turn_guided_status, task.turn_id, status="completed" if (guided_result or {}).get("status") == "completed" else "active")
        exercise_state = task.exercise_state
        if task.exercise_session_id is not None and exercise_state is not None:
            final_text, result = _extract_result(_EXERCISE_RESULT_RE, final_text)
            try:
                if exercise_state.status == "idle":
                    question = str((result or {}).get("question") or final_text).strip()
                    await asyncio.to_thread(self._repository.record_exercise_question, task.exercise_session_id, question)
                    exercise_state = await asyncio.to_thread(self._repository.exercise_state, task.exercise_session_id)
                elif exercise_state.status == "awaiting_answer" and result and result.get("kind") == "grading":
                    matches = result.get("matches")
                    if not isinstance(matches, list):
                        raise ValueError("grading result must contain rubric matches")
                    exercise_state = await asyncio.to_thread(self._repository.grade_exercise_answer, task.exercise_session_id, answer=task.content, matches=matches, feedback=str(result.get("feedback") or ""))
            except ValueError:
                pass
        return final_text, exercise_state
