"""Structured NAT workflow and sandbox tool telemetry with correlation bindings."""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
import math
import time
from typing import Any, Literal


Outcome = Literal['ok', 'refused', 'error']


def percentile(values: list[float], quantile: float) -> float:
    """Compute a deterministic nearest-rank percentile on a non-empty list of timings."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return round(ordered[index], 3)


@dataclass(frozen=True)
class SpanRecord:
    """Single correlated execution span for a NAT workflow or sandbox tool."""

    operation: str
    outcome: Outcome
    duration_ms: float
    run_id: str | None = None
    sandbox_id: str | None = None
    actor: str | None = None
    delegation_hash: str | None = None
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class TelemetryCollector:
    """Bounded collector for NAT tool and workflow execution spans."""

    def __init__(self, *, max_records: int = 256) -> None:
        if max_records < 1:
            raise ValueError('max_records must be positive')
        self._records: deque[SpanRecord] = deque(maxlen=max_records)

    @property
    def records(self) -> tuple[SpanRecord, ...]:
        return tuple(self._records)

    def record(
        self,
        operation: str,
        outcome: Outcome,
        duration_ms: float,
        *,
        run_id: str | None = None,
        sandbox_id: str | None = None,
        actor: str | None = None,
        delegation_hash: str | None = None,
        request_id: str | None = None,
    ) -> SpanRecord:
        if not operation:
            raise ValueError('telemetry operation name required')
        span = SpanRecord(
            operation=operation,
            outcome=outcome,
            duration_ms=round(max(0.0, float(duration_ms)), 3),
            run_id=run_id,
            sandbox_id=sandbox_id,
            actor=actor,
            delegation_hash=delegation_hash,
            request_id=request_id,
        )
        self._records.append(span)
        return span

    @asynccontextmanager
    async def track(
        self,
        operation: str,
        *,
        run_id: str | None = None,
        sandbox_id: str | None = None,
        actor: str | None = None,
        delegation_hash: str | None = None,
        request_id: str | None = None,
    ) -> AsyncIterator[None]:
        started = time.perf_counter()
        outcome: Outcome = 'ok'
        try:
            yield
        except BaseException:
            outcome = 'error'
            raise
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self.record(
                operation,
                outcome,
                elapsed_ms,
                run_id=run_id,
                sandbox_id=sandbox_id,
                actor=actor,
                delegation_hash=delegation_hash,
                request_id=request_id,
            )

    def summary(self) -> dict[str, Any]:
        grouped: dict[str, list[SpanRecord]] = {}
        for item in self._records:
            grouped.setdefault(item.operation, []).append(item)
        operations: dict[str, dict[str, Any]] = {}
        for name, items in sorted(grouped.items()):
            durations = [item.duration_ms for item in items]
            operations[name] = {
                'count': len(items),
                'ok': sum(1 for item in items if item.outcome == 'ok'),
                'refused': sum(1 for item in items if item.outcome == 'refused'),
                'errors': sum(1 for item in items if item.outcome == 'error'),
                'p50_ms': percentile(durations, 0.50),
                'p95_ms': percentile(durations, 0.95),
            }
        return {
            'total_calls': len(self._records),
            'ok_calls': sum(1 for item in self._records if item.outcome == 'ok'),
            'refused_calls': sum(1 for item in self._records if item.outcome == 'refused'),
            'error_calls': sum(1 for item in self._records if item.outcome == 'error'),
            'operations': operations,
        }
