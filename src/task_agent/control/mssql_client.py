"""Managed-identity Microsoft SQL client with a closed procedure catalog."""

from __future__ import annotations

import asyncio
import time
import ipaddress
import re
import socket
import uuid
from collections.abc import Callable
from collections.abc import Mapping
from datetime import UTC
from datetime import datetime
from typing import Any

from task_agent.control.sql_backend import ProcedureName
from task_agent.control.sql_backend import SqlProcedureUnavailableError
from task_agent.control.sql_backend import SqlRow
from task_agent.control.sql_backend import SqlValue
from task_agent.control.invoice_catalog import PROCEDURES as INVOICE_PROCEDURES


SERVER_PATTERN = re.compile(r"^[a-z0-9-]{1,63}\.database\.windows\.net$")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
PROCEDURES: dict[ProcedureName, tuple[str, tuple[str, ...]]] = {
    **INVOICE_PROCEDURES,
    "control.usp_record_readonly_analysis": ("EXEC control.usp_record_readonly_analysis @sponsor_hash=?, @analysis_json=?", ("sponsor_hash", "analysis_json")),
    "control.usp_get_readonly_analysis": ("EXEC control.usp_get_readonly_analysis @sponsor_hash=?", ("sponsor_hash",)),
    "control.usp_propose_readonly_analysis": ("EXEC control.usp_propose_readonly_analysis @analysis_id=?, @sponsor_hash=?, @evidence_hash=?, @plan_json=?", ("analysis_id", "sponsor_hash", "evidence_hash", "plan_json")),
    "control.usp_reconcile_human_broker": (
        "EXEC control.usp_reconcile_human_broker @plan_id=?, @sponsor_hash=?", ("plan_id", "sponsor_hash"),
    ),
    "control.usp_get_human_execution": (
        "EXEC control.usp_get_human_execution @plan_id=?, @sponsor_hash=?", ("plan_id", "sponsor_hash"),
    ),
    "control.usp_begin_human_investigation": (
        "EXEC control.usp_begin_human_investigation @task_id=?, @engagement_id=?, @workspace_id=?, @logical_agent_id=?, "
        "@request_id=?, @sponsor_hash=?, @run_id=?, @plan_id=?, @expires_at=?, @now_utc=?",
        ("task_id", "engagement_id", "workspace_id", "logical_agent_id", "request_id", "sponsor_hash", "run_id", "plan_id", "expires_at", "now_utc"),
    ),
    "control.usp_claim_human_execution": (
        "EXEC control.usp_claim_human_execution @plan_id = ?, @expected_plan_hash = ?, @expected_plan_version = ?, @sponsor_hash = ?, @execution_id = ?",
        ("plan_id", "expected_plan_hash", "expected_plan_version", "sponsor_hash", "execution_id"),
    ),
    "control.usp_record_human_execution_stage": (
        "EXEC control.usp_record_human_execution_stage @execution_id = ?, @sponsor_hash = ?, @plan_hash = ?, @stage = ?, @receipt_json = ?, @receipt_hash = ?",
        ("execution_id", "sponsor_hash", "plan_hash", "stage", "receipt_json", "receipt_hash"),
    ),
    "control.usp_complete_human_execution": (
        "EXEC control.usp_complete_human_execution @execution_id = ?, @sponsor_hash = ?, @plan_hash = ?",
        ("execution_id", "sponsor_hash", "plan_hash"),
    ),
    "control.usp_record_human_investigation": (
        "EXEC control.usp_record_human_investigation @plan_json = ?, @run_id = ?, @expected_task_version = ?, "
        "@diagnosis_receipt_hash = ?, @containment_receipt_hash = ?, @diagnosis_json = ?, @containment_json = ?",
        ("plan_json", "run_id", "expected_task_version", "diagnosis_receipt_hash", "containment_receipt_hash", "diagnosis_json", "containment_json"),
    ),
    "control.usp_list_human_incidents": ("EXEC control.usp_list_human_incidents @sponsor_hash = ?", ("sponsor_hash",)),
    "control.usp_submit_human_plan": (
        "EXEC control.usp_submit_human_plan @plan_id = ?, @sponsor_hash = ?, @expected_plan_hash = ?, @expected_investigation_version = ?",
        ("plan_id", "sponsor_hash", "expected_plan_hash", "expected_investigation_version"),
    ),
    "control.usp_list_review_plans": ("EXEC control.usp_list_review_plans", ()),
    "control.usp_decide_review_plan": (
        "EXEC control.usp_decide_review_plan @plan_id = ?, @expected_plan_hash = ?, "
        "@expected_plan_version = ?, @reviewer_hash = ?, @decision = ?, @receipt_json = ?",
        ("plan_id", "expected_plan_hash", "expected_plan_version", "reviewer_hash", "decision", "receipt_json"),
    ),
    "control.usp_initialize_cycle_demo": (
        "EXEC control.usp_initialize_cycle_demo "
        "@task_id = ?, @title = ?, @engagement_id = ?, @workspace_id = ?, "
        "@sponsor_subject_hash = ?, @logical_agent_id = ?, @policy_hash = ?, "
        "@expires_at = ?, @now_utc = ?",
        (
            "task_id",
            "title",
            "engagement_id",
            "workspace_id",
            "sponsor_subject_hash",
            "logical_agent_id",
            "policy_hash",
            "expires_at",
            "now_utc",
        ),
    ),
    "control.usp_record_cycle_containment_plan": (
        "EXEC control.usp_record_cycle_containment_plan "
        "@task_id = ?, @engagement_id = ?, @workspace_id = ?, @logical_agent_id = ?, "
        "@run_id = ?, @plan_id = ?, @plan_hash = ?, @operation_id = ?, "
        "@target_resource = ?, @safe_query_version = ?, @rollback_version = ?, "
        "@parameters_json = ?, @created_by_hash = ?, @now_utc = ?",
        (
            "task_id",
            "engagement_id",
            "workspace_id",
            "logical_agent_id",
            "run_id",
            "plan_id",
            "plan_hash",
            "operation_id",
            "target_resource",
            "safe_query_version",
            "rollback_version",
            "parameters_json",
            "created_by_hash",
            "now_utc",
        ),
    ),
    "control.usp_issue_approval": (
        "EXEC control.usp_issue_approval "
        "@approval_id = ?, @plan_id = ?, @task_id = ?, @engagement_id = ?, "
        "@workspace_id = ?, @logical_agent_id = ?, @plan_hash = ?, @operation_id = ?, "
        "@target_resource = ?, @safe_query_version = ?, @approved_by_hash = ?, "
        "@approved_at = ?, @expires_at = ?, @one_time_id = ?, @receipt_hash = ?",
        (
            "approval_id",
            "plan_id",
            "task_id",
            "engagement_id",
            "workspace_id",
            "logical_agent_id",
            "plan_hash",
            "operation_id",
            "target_resource",
            "safe_query_version",
            "approved_by_hash",
            "approved_at",
            "expires_at",
            "one_time_id",
            "receipt_hash",
        ),
    ),
    "control.usp_record_cycle_execution": (
        "EXEC control.usp_record_cycle_execution "
        "@execution_id = ?, @task_id = ?, @plan_id = ?, @approval_id = ?, "
        "@plan_hash = ?, @safe_query_version = ?, @workspace_id = ?, "
        "@triggered_by_hash = ?, @result_code = ?, @receipt_hash = ?, "
        "@started_at = ?, @completed_at = ?",
        (
            "execution_id",
            "task_id",
            "plan_id",
            "approval_id",
            "plan_hash",
            "safe_query_version",
            "workspace_id",
            "triggered_by_hash",
            "result_code",
            "receipt_hash",
            "started_at",
            "completed_at",
        ),
    ),
    "control.usp_record_cycle_verification": (
        "EXEC control.usp_record_cycle_verification "
        "@verification_id = ?, @task_id = ?, @execution_id = ?, @plan_hash = ?, "
        "@safe_query_version = ?, @verification_profile = ?, @workspace_id = ?, "
        "@checks_json = ?, @receipt_hash = ?, @verified_at = ?",
        (
            "verification_id",
            "task_id",
            "execution_id",
            "plan_hash",
            "safe_query_version",
            "verification_profile",
            "workspace_id",
            "checks_json",
            "receipt_hash",
            "verified_at",
        ),
    ),
    "control.usp_get_cycle_demo_summary": (
        "EXEC control.usp_get_cycle_demo_summary @task_id = ?, @run_id = ?",
        ("task_id", "run_id"),
    ),
    "ops.usp_get_diagnostic_snapshot": (
        "EXEC ops.usp_get_diagnostic_snapshot",
        (),
    ),
    "ops.usp_register_query_run": (
        "EXEC ops.usp_register_query_run "
        "@run_id = ?, @lease_id = ?, @owner_instance = ?, @watchdog_deadline = ?",
        ("run_id", "lease_id", "owner_instance", "watchdog_deadline"),
    ),
    "ops.usp_mark_query_running": (
        "EXEC ops.usp_mark_query_running @run_id = ?, @lease_id = ?",
        ("run_id", "lease_id"),
    ),
    "ops.usp_request_owned_query_cancel": (
        "EXEC ops.usp_request_owned_query_cancel @run_id = ?",
        ("run_id",),
    ),
    "ops.usp_confirm_owned_query_cancelled": (
        "EXEC ops.usp_confirm_owned_query_cancelled @run_id = ?, @lease_id = ?",
        ("run_id", "lease_id"),
    ),
    "ops.usp_mark_query_failed": (
        "EXEC ops.usp_mark_query_failed @run_id = ?, @lease_id = ?, @failure_code = ?",
        ("run_id", "lease_id", "failure_code"),
    ),
    "ops.usp_activate_cycle_safe_query": (
        "EXEC ops.usp_activate_cycle_safe_query "
        "@approval_id = ?, @plan_id = ?, @task_id = ?, @engagement_id = ?, "
        "@workspace_id = ?, @logical_agent_id = ?, @plan_hash = ?, @operation_id = ?, "
        "@target_resource = ?, @safe_query_version = ?, @approved_by_hash = ?, "
        "@approved_at = ?, @expires_at = ?, @one_time_id = ?, "
        "@expected_approval_version = ?, @receipt_hash = ?, @now_utc = ?",
        (
            "approval_id",
            "plan_id",
            "task_id",
            "engagement_id",
            "workspace_id",
            "logical_agent_id",
            "plan_hash",
            "operation_id",
            "target_resource",
            "safe_query_version",
            "approved_by_hash",
            "approved_at",
            "expires_at",
            "one_time_id",
            "expected_approval_version",
            "receipt_hash",
            "now_utc",
        ),
    ),
    "ops.usp_verify_cycle_recovery": (
        "EXEC ops.usp_verify_cycle_recovery @safe_query_version = ?",
        ("safe_query_version",),
    ),
}


def build_connection_string(
    *,
    server: str,
    database: str,
    client_id: str,
    application_name: str,
) -> str:
    if SERVER_PATTERN.fullmatch(server) is None:
        raise ValueError("SQL server hostname is invalid")
    if NAME_PATTERN.fullmatch(database) is None or NAME_PATTERN.fullmatch(application_name) is None:
        raise ValueError("SQL database or application name is invalid")
    uuid.UUID(client_id)
    return (
        f"Server=tcp:{server},1433;"
        f"Database={database};"
        "Encrypt=strict;"
        "TrustServerCertificate=no;"
        "ConnectRetryCount=3;"
        "ConnectRetryInterval=10;"
        "MultiSubnetFailover=Yes;"
    )


def managed_identity_connect(
    connection_string: str,
    client_id: str,
    *,
    timeout_seconds: int,
    connect: Callable[..., Any] | None = None,
    credential_factory: Callable[..., Any] | None = None,
    resolver: Callable[..., Any] = socket.getaddrinfo,
    server: str | None = None,
) -> Any:
    uuid.UUID(client_id)
    if server is not None:
        require_private_sql_resolution(server, resolver=resolver)
    if credential_factory is None:
        from azure.identity import ManagedIdentityCredential

        credential_factory = ManagedIdentityCredential
    if connect is None:
        import mssql_python

        connect = mssql_python.connect
    credential = credential_factory(client_id=client_id)
    try:
        return connect(
            connection_string,
            token_provider=credential,
            timeout=timeout_seconds,
        )
    except Exception:
        close = getattr(credential, "close", None)
        if callable(close):
            close()
        raise


def require_private_sql_resolution(
    server: str,
    *,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> tuple[str, ...]:
    if SERVER_PATTERN.fullmatch(server) is None:
        raise RuntimeError("sql_dns_hostname_invalid")
    try:
        records = resolver(server, 1433, family=socket.AF_INET, type=socket.SOCK_STREAM)
        addresses = tuple(sorted({str(record[4][0]) for record in records}))
    except OSError as error:
        raise RuntimeError("sql_private_dns_resolution_failed") from error
    if len(addresses) != 1:
        raise RuntimeError("sql_private_dns_resolution_failed")
    try:
        parsed = ipaddress.ip_address(addresses[0])
        if parsed.version != 4 or not any(parsed in network for network in PRIVATE_NETWORKS):
            raise RuntimeError("sql_dns_resolved_public_address")
    except ValueError as error:
        raise RuntimeError("sql_private_dns_resolution_failed") from error
    return addresses


def _normalize(value: SqlValue) -> SqlValue:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


class MssqlProcedureClient:
    """Execute only fixed stored procedure statements with managed identity."""

    def __init__(
        self,
        *,
        server: str,
        database: str,
        client_id: str,
        application_name: str,
        connect: Callable[[str], Any] | None = None,
        query_timeout_seconds: int = 15,
        connection_timeout_seconds: int | None = None,
        connection_attempts: int = 1,
    ) -> None:
        if not 1 <= query_timeout_seconds <= 30:
            raise ValueError("SQL procedure timeout must be between 1 and 30 seconds")
        if connection_timeout_seconds is not None and not 1 <= connection_timeout_seconds <= 30:
            raise ValueError("SQL connection timeout must be between 1 and 30 seconds")
        if type(connection_attempts) is not int or not 1 <= connection_attempts <= 3:
            raise ValueError("SQL connection attempts must be between 1 and 3")
        self._connection_string = build_connection_string(
            server=server,
            database=database,
            client_id=client_id,
            application_name=application_name,
        )
        self._server = server
        self._connect = connect
        self._client_id = client_id
        self._query_timeout_seconds = query_timeout_seconds
        self._connection_timeout_seconds = connection_timeout_seconds or query_timeout_seconds
        self._connection_attempts = connection_attempts

    def _driver_connect(self, connection_string: str) -> Any:
        if self._connect is not None:
            return self._connect(connection_string)
        return managed_identity_connect(
            connection_string,
            self._client_id,
            timeout_seconds=self._connection_timeout_seconds,
            server=self._server,
        )

    def _call_sync(
        self,
        procedure: ProcedureName,
        parameters: Mapping[str, SqlValue],
    ) -> tuple[SqlRow, ...]:
        statement, order = PROCEDURES[procedure]
        if set(parameters) != set(order):
            raise SqlProcedureUnavailableError("stored procedure parameter contract differs")
        values = tuple(_normalize(parameters[name]) for name in order)
        try:
            for attempt in range(self._connection_attempts):
                try:
                    opened = self._driver_connect(self._connection_string)
                    break
                except Exception:
                    if attempt == self._connection_attempts - 1:
                        raise
                    time.sleep(10)
            with opened as connection:
                connection.autocommit = True
                with connection.cursor() as cursor:
                    if hasattr(cursor, "timeout"):
                        cursor.timeout = self._query_timeout_seconds
                    cursor.execute(statement, *values)
                    description = cursor.description or ()
                    columns = tuple(
                        str(getattr(column, "name", None) or column[0])
                        for column in description
                    )
                    rows = tuple(
                        {column: value for column, value in zip(columns, row, strict=True)}
                        for row in cursor.fetchall()
                    ) if columns else ()
                return rows
        except SqlProcedureUnavailableError:
            raise
        except Exception as error:
            raise SqlProcedureUnavailableError("trusted SQL procedure call failed") from error

    async def call(
        self,
        procedure: ProcedureName,
        parameters: Mapping[str, SqlValue],
    ) -> tuple[SqlRow, ...]:
        if procedure not in PROCEDURES:
            raise SqlProcedureUnavailableError("stored procedure is not registered")
        return await asyncio.to_thread(self._call_sync, procedure, parameters)