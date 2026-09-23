"""Registered-operation catalog and deterministic local incident backend."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from task_agent.control.models import OperationReceipt
from task_agent.control.models import Scalar


class OperationDeniedError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParameterRule:
    value_type: type
    pattern: re.Pattern[str] | None = None

    def validate(self, name: str, value: Scalar) -> None:
        if type(value) is not self.value_type:
            raise OperationDeniedError(f"invalid parameter type: {name}")
        if self.pattern is not None and (
            not isinstance(value, str) or self.pattern.fullmatch(value) is None
        ):
            raise OperationDeniedError(f"invalid parameter value: {name}")


@dataclass(frozen=True)
class RegisteredOperation:
    operation_id: str
    parameters: dict[str, ParameterRule]
    worker: str


OPERATION_CATALOG = {
    "demo.start-controlled-query-v1": RegisteredOperation(
        operation_id="demo.start-controlled-query-v1",
        parameters={
            "run_id": ParameterRule(str, re.compile(r"^run-[a-z0-9]{8,64}$")),
        },
        worker="query-runner",
    ),
    "contain.cancel-owned-query-v1": RegisteredOperation(
        operation_id="contain.cancel-owned-query-v1",
        parameters={
            "run_id": ParameterRule(str, re.compile(r"^run-[a-z0-9]{8,64}$")),
        },
        worker="query-runner",
    ),
    "remediate.activate-cycle-safe-query-v1": RegisteredOperation(
        operation_id="remediate.activate-cycle-safe-query-v1",
        parameters={
            "query_version": ParameterRule(str, re.compile(r"^cycle-safe-v[0-9]+$")),
        },
        worker="remediation",
    ),
}

PROHIBITED_PARAMETER_NAMES = {"command", "connection_string", "session_id", "sql", "token", "url"}


def validate_operation(operation_id: str, parameters: dict[str, Scalar]) -> RegisteredOperation:
    operation = OPERATION_CATALOG.get(operation_id)
    if operation is None:
        raise OperationDeniedError("operation is not registered")
    supplied = set(parameters)
    if supplied & PROHIBITED_PARAMETER_NAMES:
        raise OperationDeniedError("raw privileged parameter is prohibited")
    if supplied != set(operation.parameters):
        raise OperationDeniedError("operation parameter names differ from the catalog")
    for name, rule in operation.parameters.items():
        rule.validate(name, parameters[name])
    return operation


class OperationExecutor(Protocol):
    async def execute(
        self,
        operation: RegisteredOperation,
        parameters: dict[str, Scalar],
    ) -> OperationReceipt: ...


class InMemoryIncidentBackend:
    """Deterministic stand-in for later Azure SQL stored-procedure adapters."""

    def __init__(self) -> None:
        self.query_runs: dict[str, str] = {}
        self.active_query_version = "cycle-unsafe-v1"

    def start_query(self, run_id: str) -> None:
        if re.fullmatch(r"run-[a-z0-9]{8,64}", run_id) is None:
            raise OperationDeniedError("invalid query run identifier")
        self.query_runs[run_id] = "running"

    async def execute(
        self,
        operation: RegisteredOperation,
        parameters: dict[str, Scalar],
    ) -> OperationReceipt:
        if operation.operation_id == "demo.start-controlled-query-v1":
            run_id = str(parameters["run_id"])
            if run_id in self.query_runs:
                raise OperationDeniedError("query run already exists")
            self.start_query(run_id)
            return OperationReceipt(
                operation_id=operation.operation_id,
                result_code="owned_query_started",
                result={"run_id": run_id, "state": "running"},
            )
        if operation.operation_id == "contain.cancel-owned-query-v1":
            run_id = str(parameters["run_id"])
            if self.query_runs.get(run_id) != "running":
                raise OperationDeniedError("query run is not owned and running")
            self.query_runs[run_id] = "cancelled"
            return OperationReceipt(
                operation_id=operation.operation_id,
                result_code="owned_query_cancelled",
                result={"run_id": run_id, "state": "cancelled"},
            )
        if operation.operation_id == "remediate.activate-cycle-safe-query-v1":
            version = str(parameters["query_version"])
            self.active_query_version = version
            return OperationReceipt(
                operation_id=operation.operation_id,
                result_code="safe_query_activated",
                result={"safe_query_version": version},
            )
        raise OperationDeniedError("operation has no deterministic executor")

    async def diagnostic_snapshot(self) -> tuple[dict[str, str], str]:
        return dict(self.query_runs), self.active_query_version

    async def verification_checks(self, expected_version: str) -> dict[str, bool]:
        return {
            "safe_query_version_active": self.active_query_version == expected_version,
            "no_owned_query_running": all(state != "running" for state in self.query_runs.values()),
        }