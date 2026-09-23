"""Authoritative approval lookup and one-time consumption boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from task_agent.control.canonical import content_hash
from task_agent.control.models import ApprovalReceipt
from task_agent.control.models import OperationReceipt
from task_agent.control.models import Scalar
from task_agent.control.operations import OperationDeniedError
from task_agent.control.operations import OperationExecutor
from task_agent.control.operations import RegisteredOperation


class ApprovalAuthority(Protocol):
    async def consume(self, presented: ApprovalReceipt, *, now: datetime) -> ApprovalReceipt:
        """Atomically validate and consume an authoritative approval."""


class UnavailableApprovalAuthority:
    async def consume(self, presented: ApprovalReceipt, *, now: datetime) -> ApprovalReceipt:
        del presented, now
        raise OperationDeniedError("authoritative approval repository is unavailable")


class RemediationAuthority(Protocol):
    async def execute(
        self,
        presented: ApprovalReceipt,
        operation: RegisteredOperation,
        parameters: dict[str, Scalar],
        *,
        now: datetime,
    ) -> OperationReceipt:
        """Atomically consume approval and execute its registered operation."""


class UnavailableRemediationAuthority:
    async def execute(
        self,
        presented: ApprovalReceipt,
        operation: RegisteredOperation,
        parameters: dict[str, Scalar],
        *,
        now: datetime,
    ) -> OperationReceipt:
        del presented, operation, parameters, now
        raise OperationDeniedError("authoritative remediation repository is unavailable")


class InMemoryRemediationAuthority:
    """Atomic test authority; deployed runtimes must use the Azure SQL adapter."""

    def __init__(
        self,
        approvals: Iterable[ApprovalReceipt],
        executor: OperationExecutor,
    ) -> None:
        records = tuple(approvals)
        self._approvals = {approval.approval_id: approval for approval in records}
        if len(self._approvals) != len(records):
            raise ValueError("approval identifiers must be unique")
        one_time_ids = {approval.one_time_id for approval in records}
        if len(one_time_ids) != len(records):
            raise ValueError("one-time grant identifiers must be unique")
        self._executor = executor
        self._consumed_approvals: set[str] = set()
        self._consumed_grants: set[str] = set()
        self._lock = asyncio.Lock()

    async def execute(
        self,
        presented: ApprovalReceipt,
        operation: RegisteredOperation,
        parameters: dict[str, Scalar],
        *,
        now: datetime,
    ) -> OperationReceipt:
        async with self._lock:
            stored = self._approvals.get(presented.approval_id)
            if stored is None or stored != presented:
                raise OperationDeniedError("approval does not match the authoritative repository")
            payload = stored.model_dump(mode="python", exclude={"receipt_hash"})
            if content_hash(payload) != stored.receipt_hash:
                raise OperationDeniedError("authoritative approval receipt hash differs")
            if stored.consumed_at is not None or stored.approval_id in self._consumed_approvals:
                raise OperationDeniedError("authoritative approval was already consumed")
            if stored.one_time_id in self._consumed_grants:
                raise OperationDeniedError("authoritative one-time grant was replayed")
            if stored.expires_at <= now:
                raise OperationDeniedError("authoritative approval expired")
            if (
                operation.operation_id != stored.operation_id
                or parameters.get("query_version") != stored.safe_query_version
            ):
                raise OperationDeniedError("authoritative approval binding differs")
            receipt = await self._executor.execute(operation, parameters)
            self._consumed_approvals.add(stored.approval_id)
            self._consumed_grants.add(stored.one_time_id)
            return receipt


class InMemoryApprovalAuthority:
    """Deterministic test authority; deployed runtimes must use external persistence."""

    def __init__(self, approvals: Iterable[ApprovalReceipt]) -> None:
        records = tuple(approvals)
        self._approvals = {approval.approval_id: approval for approval in records}
        if len(self._approvals) != len(records):
            raise ValueError("approval identifiers must be unique")
        one_time_ids = {approval.one_time_id for approval in records}
        if len(one_time_ids) != len(records):
            raise ValueError("one-time grant identifiers must be unique")
        self._consumed_approvals: set[str] = set()
        self._consumed_grants: set[str] = set()
        self._lock = asyncio.Lock()

    async def consume(self, presented: ApprovalReceipt, *, now: datetime) -> ApprovalReceipt:
        async with self._lock:
            stored = self._approvals.get(presented.approval_id)
            if stored is None or stored != presented:
                raise OperationDeniedError("approval does not match the authoritative repository")
            payload = stored.model_dump(mode="python", exclude={"receipt_hash"})
            if content_hash(payload) != stored.receipt_hash:
                raise OperationDeniedError("authoritative approval receipt hash differs")
            if stored.consumed_at is not None:
                raise OperationDeniedError("authoritative approval was already consumed")
            if stored.expires_at <= now:
                raise OperationDeniedError("authoritative approval expired")
            if (
                stored.approval_id in self._consumed_approvals
                or stored.one_time_id in self._consumed_grants
            ):
                raise OperationDeniedError("authoritative one-time grant was replayed")
            self._consumed_approvals.add(stored.approval_id)
            self._consumed_grants.add(stored.one_time_id)
            return stored.model_copy(deep=True)