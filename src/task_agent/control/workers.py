"""Least-authority worker interfaces for the trusted execution domain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from task_agent.control.models import OperationReceipt
from task_agent.control.models import Scalar
from task_agent.control.operations import OperationDeniedError
from task_agent.control.operations import OperationExecutor
from task_agent.control.operations import RegisteredOperation


@dataclass(frozen=True)
class DiagnosticPackage:
    query_run_states: dict[str, str]
    active_query_version: str


class DiagnosticBackend(Protocol):
    async def diagnostic_snapshot(self) -> tuple[dict[str, str], str]: ...


class VerificationBackend(Protocol):
    async def verification_checks(self, expected_version: str) -> dict[str, bool]: ...


class DiagnosticWorker:
    def __init__(self, backend: DiagnosticBackend) -> None:
        self._backend = backend

    async def collect(self) -> DiagnosticPackage:
        query_runs, version = await self._backend.diagnostic_snapshot()
        if not isinstance(query_runs, dict) or not isinstance(version, str):
            raise RuntimeError("diagnostic backend returned an invalid result")
        return DiagnosticPackage(
            query_run_states={str(key): str(value) for key, value in query_runs.items()},
            active_query_version=version,
        )


class QueryRunnerWorker:
    def __init__(self, executor: OperationExecutor) -> None:
        self._executor = executor

    async def start(
        self,
        operation: RegisteredOperation,
        parameters: dict[str, Scalar],
    ) -> OperationReceipt:
        if operation.worker != "query-runner" or not operation.operation_id.startswith("demo."):
            raise OperationDeniedError("query runner accepts registered demo starts only")
        return await self._executor.execute(operation, parameters)

    async def contain(
        self,
        operation: RegisteredOperation,
        parameters: dict[str, Scalar],
    ) -> OperationReceipt:
        if operation.worker != "query-runner" or not operation.operation_id.startswith("contain."):
            raise OperationDeniedError("query runner accepts containment operations only")
        return await self._executor.execute(operation, parameters)


class RemediationBroker:
    def __init__(self, executor: OperationExecutor) -> None:
        self._executor = executor

    async def execute(
        self,
        operation: RegisteredOperation,
        parameters: dict[str, Scalar],
    ) -> OperationReceipt:
        if operation.worker != "remediation" or not operation.operation_id.startswith("remediate."):
            raise OperationDeniedError("remediation broker accepts remediation operations only")
        return await self._executor.execute(operation, parameters)


class VerifierWorker:
    def __init__(self, backend: VerificationBackend) -> None:
        self._backend = backend

    async def verify(self, expected_version: str) -> dict[str, bool]:
        checks = await self._backend.verification_checks(expected_version)
        if not isinstance(checks, dict) or not all(
            isinstance(name, str) and isinstance(value, bool)
            for name, value in checks.items()
        ):
            raise RuntimeError("verification backend returned an invalid result")
        return checks