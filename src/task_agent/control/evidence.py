"""Append-only in-memory evidence ledger with a deterministic hash chain."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from task_agent.control.canonical import content_hash
from task_agent.control.models import EvidenceEvent
from task_agent.control.models import EvidenceLevel
from task_agent.control.models import Scalar


class EvidenceLedger:
    def __init__(self) -> None:
        self._events: list[EvidenceEvent] = []
        self._lock = asyncio.Lock()

    async def append(
        self,
        *,
        event_id: str,
        timestamp: datetime,
        event_type: str,
        level: EvidenceLevel,
        request_id: str,
        resource: str,
        decision: str,
        reason_code: str,
        details: dict[str, Scalar] | None = None,
        **correlation: Any,
    ) -> EvidenceEvent:
        async with self._lock:
            sequence = len(self._events) + 1
            previous_hash = self._events[-1].event_hash if self._events else ""
            payload = {
                "sequence": sequence,
                "event_id": event_id,
                "timestamp": timestamp,
                "event_type": event_type,
                "level": level,
                "request_id": request_id,
                "resource": resource,
                "decision": decision,
                "reason_code": reason_code,
                "details": details or {},
                "previous_hash": previous_hash,
                **correlation,
            }
            draft = EvidenceEvent(**payload, event_hash="0" * 64)
            normalized = draft.model_dump(mode="python", exclude={"event_hash"})
            event = draft.model_copy(update={"event_hash": content_hash(normalized)})
            self._events.append(event)
            return event.model_copy(deep=True)

    async def list(self) -> list[EvidenceEvent]:
        async with self._lock:
            return [event.model_copy(deep=True) for event in self._events]

    async def validate(self) -> bool:
        async with self._lock:
            previous_hash = ""
            for index, event in enumerate(self._events, start=1):
                payload = event.model_dump(mode="python", exclude={"event_hash"})
                if event.sequence != index or event.previous_hash != previous_hash:
                    return False
                if content_hash(payload) != event.event_hash:
                    return False
                previous_hash = event.event_hash
            return True