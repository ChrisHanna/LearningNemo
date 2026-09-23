"""FastAPI boundary for one least-authority trusted worker mode."""

from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import Literal

import uvicorn
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Header
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from task_agent.control.approval import RemediationAuthority
from task_agent.control.approval import UnavailableRemediationAuthority
from task_agent.control.canonical import content_hash
from task_agent.control.models import ApprovalReceipt
from task_agent.control.models import Scalar
from task_agent.control.mssql_client import MssqlProcedureClient
from task_agent.control.mssql_owned_query import MssqlOwnedQueryController
from task_agent.control.operations import InMemoryIncidentBackend
from task_agent.control.operations import OperationDeniedError
from task_agent.control.operations import validate_operation
from task_agent.control.sql_backend import AzureSqlIncidentBackend
from task_agent.control.sql_backend import AzureSqlQueryRunnerBackend
from task_agent.control.sql_backend import AzureSqlRemediationAuthority
from task_agent.control.sql_backend import SqlProcedureUnavailableError
from task_agent.control.workers import DiagnosticWorker
from task_agent.control.workers import QueryRunnerWorker
from task_agent.control.workers import VerifierWorker


ServiceMode = Literal["diagnostic", "query-runner", "remediation", "verifier"]


@dataclass(frozen=True)
class RuntimeSettings:
    mode: ServiceMode
    allowed_callers: frozenset[str]
    sql_server: str
    sql_database: str
    client_id: str
    instance_id: str


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryCancelRequest(StrictRequest):
    run_id: str = Field(pattern=r"^run-[a-z0-9]{8,64}$")


class QueryStartRequest(StrictRequest):
    run_id: str = Field(pattern=r"^run-[a-z0-9]{8,64}$")


class RemediationRequest(StrictRequest):
    approval: ApprovalReceipt
    expected_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    one_time_id: str = Field(min_length=1, max_length=128)
    operation_id: Literal["remediate.activate-cycle-safe-query-v1"]
    parameters: dict[str, Scalar]


class VerificationRequest(StrictRequest):
    safe_query_version: str = Field(pattern=r"^cycle-safe-v[0-9]+$")


class WorkloadAuthorizer:
    """Defense-in-depth allowlist for headers injected by Container Apps auth."""

    def __init__(self, allowed_callers: frozenset[str]) -> None:
        if not allowed_callers or any(not caller for caller in allowed_callers):
            raise ValueError("at least one trusted workload caller is required")
        self._allowed_callers = allowed_callers

    async def authorize(
        self,
        principal_id: str | None = Header(default=None, alias="X-MS-CLIENT-PRINCIPAL-ID"),
    ) -> None:
        if principal_id is None:
            raise HTTPException(status_code=401, detail="workload_authentication_required")
        if principal_id not in self._allowed_callers:
            raise HTTPException(status_code=403, detail="workload_caller_denied")


def verify_broker_request(request: RemediationRequest, now: datetime) -> None:
    approval = request.approval
    receipt_payload = approval.model_dump(mode="python", exclude={"receipt_hash"})
    if content_hash(receipt_payload) != approval.receipt_hash:
        raise OperationDeniedError("approval receipt hash differs")
    if approval.consumed_at is not None:
        raise OperationDeniedError("approval receipt was already consumed")
    if approval.expires_at <= now:
        raise OperationDeniedError("approval receipt expired")
    if (
        request.expected_plan_hash != approval.plan_hash
        or request.one_time_id != approval.one_time_id
        or request.operation_id != approval.operation_id
    ):
        raise OperationDeniedError("approval binding differs")
    if request.parameters.get("query_version") != approval.safe_query_version:
        raise OperationDeniedError("approved query version differs")
    validate_operation(request.operation_id, request.parameters)


def create_app(
    *,
    mode: ServiceMode,
    allowed_callers: frozenset[str],
    backend: InMemoryIncidentBackend | AzureSqlIncidentBackend | AzureSqlQueryRunnerBackend | None = None,
    clock: Callable[[], datetime] | None = None,
    remediation_authority: RemediationAuthority | None = None,
    shutdown: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    incident_backend = backend or InMemoryIncidentBackend()
    authorizer = WorkloadAuthorizer(allowed_callers)
    now = clock or (lambda: datetime.now(UTC))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if shutdown is not None:
            await shutdown()

    app = FastAPI(
        title=f"LearningNeMo {mode}",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(SqlProcedureUnavailableError)
    async def sql_unavailable(
        _request: Request,
        _error: SqlProcedureUnavailableError,
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "sql_boundary_unavailable"})

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/readyz", include_in_schema=False)
    async def ready() -> dict[str, str]:
        return {"status": "ready", "mode": mode}

    if mode == "diagnostic":
        worker = DiagnosticWorker(incident_backend)

        @app.get("/v1/diagnostics/current", dependencies=[Depends(authorizer.authorize)])
        async def diagnostics() -> dict[str, object]:
            package = await worker.collect()
            return {
                "query_run_states": package.query_run_states,
                "active_query_version": package.active_query_version,
            }

    elif mode == "query-runner":
        worker = QueryRunnerWorker(incident_backend)

        @app.post("/v1/query-runs/start", dependencies=[Depends(authorizer.authorize)])
        async def start_query(request: QueryStartRequest) -> dict[str, object]:
            try:
                operation = validate_operation(
                    "demo.start-controlled-query-v1",
                    {"run_id": request.run_id},
                )
                receipt = await worker.start(operation, {"run_id": request.run_id})
                return receipt.model_dump(mode="json")
            except OperationDeniedError as error:
                raise HTTPException(status_code=409, detail="query_start_denied") from error

        @app.post("/v1/query-runs/cancel", dependencies=[Depends(authorizer.authorize)])
        async def cancel_query(request: QueryCancelRequest) -> dict[str, object]:
            try:
                operation = validate_operation(
                    "contain.cancel-owned-query-v1",
                    {"run_id": request.run_id},
                )
                receipt = await worker.contain(operation, {"run_id": request.run_id})
                return receipt.model_dump(mode="json")
            except OperationDeniedError as error:
                raise HTTPException(status_code=409, detail="containment_denied") from error

    elif mode == "remediation":
        authority = remediation_authority or UnavailableRemediationAuthority()

        @app.post("/v1/remediations/execute", dependencies=[Depends(authorizer.authorize)])
        async def execute_remediation(request: RemediationRequest) -> dict[str, object]:
            try:
                current_time = now()
                verify_broker_request(request, current_time)
                operation = validate_operation(request.operation_id, request.parameters)
                receipt = await authority.execute(
                    request.approval,
                    operation,
                    request.parameters,
                    now=current_time,
                )
                return receipt.model_dump(mode="json")
            except OperationDeniedError as error:
                raise HTTPException(status_code=409, detail="remediation_denied") from error

    else:
        worker = VerifierWorker(incident_backend)

        @app.post("/v1/verifications/cycle-recovery", dependencies=[Depends(authorizer.authorize)])
        async def verify(request: VerificationRequest) -> dict[str, object]:
            checks = await worker.verify(request.safe_query_version)
            return {"passed": bool(checks) and all(checks.values()), "checks": checks}

    return app


def settings_from_environment() -> RuntimeSettings:
    mode = os.getenv("LEARNINGNEMO_SERVICE_MODE", "")
    if mode not in {"diagnostic", "query-runner", "remediation", "verifier"}:
        raise RuntimeError("LEARNINGNEMO_SERVICE_MODE is invalid")
    allowed = frozenset(
        value.strip()
        for value in os.getenv("LEARNINGNEMO_ALLOWED_CALLER_IDS", "").split(",")
        if value.strip()
    )
    if not allowed:
        raise RuntimeError("LEARNINGNEMO_ALLOWED_CALLER_IDS is required")
    sql_server = os.getenv("LEARNINGNEMO_SQL_SERVER", "")
    sql_database = os.getenv("LEARNINGNEMO_SQL_DATABASE", "")
    client_id = os.getenv("AZURE_CLIENT_ID", "")
    instance_id = os.getenv("CONTAINER_APP_REPLICA_NAME") or os.getenv("HOSTNAME", "")
    if not all((sql_server, sql_database, client_id, instance_id)):
        raise RuntimeError("deployed SQL runtime settings are required")
    return RuntimeSettings(
        mode=mode,
        allowed_callers=allowed,
        sql_server=sql_server,
        sql_database=sql_database,
        client_id=client_id,
        instance_id=instance_id,
    )


def create_deployed_app(settings: RuntimeSettings) -> FastAPI:
    client = MssqlProcedureClient(
        server=settings.sql_server,
        database=settings.sql_database,
        client_id=settings.client_id,
        application_name=f"LearningNeMo{settings.mode.replace('-', '').title()}",
    )
    if settings.mode == "diagnostic":
        return create_app(
            mode=settings.mode,
            allowed_callers=settings.allowed_callers,
            backend=AzureSqlIncidentBackend(client),
        )
    if settings.mode == "query-runner":
        controller = MssqlOwnedQueryController(
            lifecycle_client=client,
            server=settings.sql_server,
            database=settings.sql_database,
            client_id=settings.client_id,
            owner_instance=settings.instance_id,
        )
        return create_app(
            mode=settings.mode,
            allowed_callers=settings.allowed_callers,
            backend=AzureSqlQueryRunnerBackend(client, controller),
            shutdown=controller.close,
        )
    if settings.mode == "remediation":
        return create_app(
            mode=settings.mode,
            allowed_callers=settings.allowed_callers,
            remediation_authority=AzureSqlRemediationAuthority(client),
        )
    return create_app(
        mode=settings.mode,
        allowed_callers=settings.allowed_callers,
        backend=AzureSqlIncidentBackend(client),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    settings = settings_from_environment()
    uvicorn.run(create_deployed_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()