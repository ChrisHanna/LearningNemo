"""NAT FunctionMiddleware for correlated workflow and tool execution telemetry."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
import json
import logging
import time
from typing import Any

from pydantic import BaseModel, Field

from task_agent.control.telemetry import Outcome, TelemetryCollector

BLOCK_MESSAGE = "I'm sorry, I can't help with that."

try:
    from nat.builder.builder import Builder
    from nat.cli.register_workflow import register_middleware
    from nat.data_models.middleware import FunctionMiddlewareBaseConfig
    from nat.middleware.function_middleware import FunctionMiddleware
    from nat.middleware.middleware import CallNext, CallNextStream, FunctionMiddlewareContext
    from task_agent.security.authorization import AuthenticationRequiredError, get_verified_principal
    _NAT_AVAILABLE = True
except ImportError:
    _NAT_AVAILABLE = False
    Builder = Any  # type: ignore[assignment,misc]
    CallNext = Any  # type: ignore[assignment,misc]
    CallNextStream = Any  # type: ignore[assignment,misc]
    FunctionMiddlewareContext = Any  # type: ignore[assignment,misc]

    class FunctionMiddlewareBaseConfig(BaseModel):  # type: ignore[no-redef]
        def __init_subclass__(cls, **kwargs: Any) -> None:
            super().__init_subclass__()

    class FunctionMiddleware:  # type: ignore[no-redef]
        def __init__(self, *, is_final: bool = False) -> None:
            self.is_final = is_final

    def register_middleware(*_args: Any, **_kwargs: Any):  # type: ignore[misc]
        def decorator(fn: Any) -> Any:
            return fn
        return decorator

    class AuthenticationRequiredError(PermissionError):  # type: ignore[no-redef]
        pass

    def get_verified_principal() -> Any:  # type: ignore[misc]
        raise AuthenticationRequiredError('unauthenticated')


logger = logging.getLogger(__name__)


class WorkflowTelemetryConfig(FunctionMiddlewareBaseConfig, name='workflow_telemetry'):
    """Configure correlated latency and outcome telemetry for a NAT workflow or tool."""

    max_records: int = Field(default=256, ge=1, le=4096)


class WorkflowTelemetryMiddleware(FunctionMiddleware):
    """Record sanitized invocation telemetry correlated with the verified request identity."""

    def __init__(
        self,
        config: WorkflowTelemetryConfig,
        *,
        collector: TelemetryCollector | None = None,
    ) -> None:
        super().__init__(is_final=False)
        self.collector = collector or TelemetryCollector(max_records=config.max_records)

    @staticmethod
    def _request_id() -> str:
        try:
            return str(get_verified_principal().request_id)
        except Exception:
            return 'unavailable'

    def _emit(self, operation: str, outcome: Outcome, duration_ms: float) -> None:
        request_id = self._request_id()
        span = self.collector.record(
            operation,
            outcome,
            duration_ms,
            request_id=request_id,
        )
        event = {'event': 'nat_workflow_telemetry', **span.to_dict()}
        log = logger.warning if outcome != 'ok' else logger.info
        log('nat_workflow_telemetry %s', json.dumps(event, separators=(',', ':')))

    async def function_middleware_invoke(
        self,
        *args: Any,
        call_next: CallNext,
        context: FunctionMiddlewareContext,
        **kwargs: Any,
    ) -> Any:
        operation = getattr(context, 'name', None) or '<workflow>'
        started = time.perf_counter()
        outcome: Outcome = 'ok'
        try:
            result = await call_next(*args, **kwargs)
            if result == BLOCK_MESSAGE:
                outcome = 'refused'
            return result
        except BaseException:
            outcome = 'error'
            raise
        finally:
            self._emit(operation, outcome, (time.perf_counter() - started) * 1000.0)

    async def function_middleware_stream(
        self,
        *args: Any,
        call_next: CallNextStream,
        context: FunctionMiddlewareContext,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        operation = getattr(context, 'name', None) or '<workflow>'
        started = time.perf_counter()
        outcome: Outcome = 'ok'
        try:
            async for chunk in call_next(*args, **kwargs):
                if chunk == BLOCK_MESSAGE:
                    outcome = 'refused'
                yield chunk
        except BaseException:
            outcome = 'error'
            raise
        finally:
            self._emit(operation, outcome, (time.perf_counter() - started) * 1000.0)


@register_middleware(config_type=WorkflowTelemetryConfig)
async def workflow_telemetry(
    config: WorkflowTelemetryConfig,
    _builder: Builder,
) -> AsyncGenerator[WorkflowTelemetryMiddleware, None]:
    yield WorkflowTelemetryMiddleware(config)
