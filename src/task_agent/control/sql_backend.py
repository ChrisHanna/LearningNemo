"""Closed Azure SQL stored-procedure adapters for trusted workers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Literal
from typing import Protocol
from typing import TypeAlias

from task_agent.control.canonical import content_hash
from task_agent.control.models import ApprovalReceipt
from task_agent.control.models import OperationReceipt
from task_agent.control.models import Scalar
from task_agent.control.operations import OperationDeniedError
from task_agent.control.operations import RegisteredOperation


ProcedureName = Literal[
    'control.usp_admit_invoice_job',
    'control.usp_claim_invoice_job',
    'control.usp_finish_invoice_job',
    'control.usp_get_invoice_job',
    'control.usp_append_invoice_activity',
    'control.usp_read_invoice_activity',
    'control.usp_create_invoice_scenario',
    'ops.usp_diagnose_invoice_summary',
    'ops.usp_diagnose_invoice_batches',
    'ops.usp_verify_invoice_integrity',
    'control.usp_register_invoice_planning',
    'control.usp_admit_invoice_tool',
    'control.usp_record_invoice_evidence',
    'control.usp_record_invoice_plan',
    'control.usp_submit_invoice_plan',
    'control.usp_decide_invoice_plan',
    'control.usp_claim_invoice_execution',
    'ops.usp_execute_invoice_step',
    'control.usp_read_invoice_plans',
    'control.usp_review_invoice_plans',
    'control.usp_revoke_invoice_run',
    'control.usp_get_invoice_execution',
    'ops.usp_verify_invoice_run',
    'control.usp_complete_invoice_plan',
    'control.usp_get_invoice_evidence',
    "control.usp_record_readonly_analysis",
    "control.usp_get_readonly_analysis",
    "control.usp_propose_readonly_analysis",
    "control.usp_claim_human_execution",
    "control.usp_get_human_execution",
    "control.usp_reconcile_human_broker",
    "control.usp_record_human_execution_stage",
    "control.usp_complete_human_execution",
    "control.usp_record_human_investigation",
    "control.usp_begin_human_investigation",
    "control.usp_list_human_incidents",
    "control.usp_submit_human_plan",
    "control.usp_list_review_plans",
    "control.usp_decide_review_plan",
    "control.usp_initialize_cycle_demo",
    "control.usp_record_cycle_containment_plan",
    "control.usp_issue_approval",
    "control.usp_record_cycle_execution",
    "control.usp_record_cycle_verification",
    "control.usp_get_cycle_demo_summary",
    "ops.usp_get_diagnostic_snapshot",
    "ops.usp_register_query_run",
    "ops.usp_mark_query_running",
    "ops.usp_request_owned_query_cancel",
    "ops.usp_confirm_owned_query_cancelled",
    "ops.usp_mark_query_failed",
    "ops.usp_activate_cycle_safe_query",
    "ops.usp_verify_cycle_recovery",
]
SqlValue: TypeAlias = Scalar | datetime | None
SqlRow: TypeAlias = Mapping[str, SqlValue]


class SqlProcedureUnavailableError(RuntimeError):
    """Raised when the trusted database procedure boundary is unavailable."""


class SqlProcedureClient(Protocol):
    async def call(
        self,
        procedure: ProcedureName,
        parameters: Mapping[str, SqlValue],
    ) -> tuple[SqlRow, ...]:
        """Call one allowlisted stored procedure and return its rows."""


class OwnedQueryLeaseController(Protocol):
    async def start_owned(self, run_id: str) -> str:
        """Start the fixed demo query and return its unguessable process lease."""

    async def cancel_owned(self, run_id: str, lease_id: str) -> None:
        """Close the exact locally owned query connection or fail closed."""


def _one_row(
    rows: tuple[SqlRow, ...],
    expected_fields: set[str],
    operation: str,
) -> SqlRow:
    if len(rows) != 1 or set(rows[0]) != expected_fields:
        raise SqlProcedureUnavailableError(f"{operation} returned an invalid result contract")
    return rows[0]


class AzureSqlIncidentBackend:
    """Diagnostic and verification through fixed procedures only."""

    def __init__(self, client: SqlProcedureClient) -> None:
        self._client = client

    async def diagnostic_snapshot(self) -> tuple[dict[str, str], str]:
        rows = await self._client.call("ops.usp_get_diagnostic_snapshot", {})
        row = _one_row(
            rows,
            {"query_run_states_json", "active_query_version"},
            "diagnostic snapshot",
        )
        version = row["active_query_version"]
        states_json = row["query_run_states_json"]
        if not isinstance(version, str) or not isinstance(states_json, str):
            raise SqlProcedureUnavailableError("diagnostic snapshot returned invalid values")
        try:
            states = json.loads(states_json)
        except json.JSONDecodeError as error:
            raise SqlProcedureUnavailableError("diagnostic snapshot returned invalid JSON") from error
        if not isinstance(states, list) or not all(
            isinstance(item, dict)
            and set(item) == {"run_id", "state"}
            and isinstance(item["run_id"], str)
            and isinstance(item["state"], str)
            for item in states
        ):
            raise SqlProcedureUnavailableError("diagnostic snapshot state map is invalid")
        state_map = {item["run_id"]: item["state"] for item in states}
        if len(state_map) != len(states):
            raise SqlProcedureUnavailableError("diagnostic snapshot contains duplicate run IDs")
        return state_map, version

    async def verification_checks(self, expected_version: str) -> dict[str, bool]:
        rows = await self._client.call(
            "ops.usp_verify_cycle_recovery",
            {"safe_query_version": expected_version},
        )
        row = _one_row(
            rows,
            {"safe_query_version_active", "no_owned_query_running", "deterministic_result"},
            "cycle recovery verification",
        )
        if not all(type(row[name]) is bool for name in row):
            raise SqlProcedureUnavailableError("verification returned non-boolean checks")
        return {name: bool(value) for name, value in row.items()}


class AzureSqlQueryRunnerBackend:
    """Cooperatively cancel only a connection owned by this runner process."""

    def __init__(
        self,
        client: SqlProcedureClient,
        leases: OwnedQueryLeaseController,
    ) -> None:
        self._client = client
        self._leases = leases

    async def execute(
        self,
        operation: RegisteredOperation,
        parameters: dict[str, Scalar],
    ) -> OperationReceipt:
        if operation.operation_id == "demo.start-controlled-query-v1":
            run_id = str(parameters["run_id"])
            await self._leases.start_owned(run_id)
            return OperationReceipt(
                operation_id=operation.operation_id,
                result_code="owned_query_started",
                result={"run_id": run_id, "state": "running"},
            )
        if operation.operation_id != "contain.cancel-owned-query-v1":
            raise OperationDeniedError("SQL query runner operation is not registered")
        rows = await self._client.call(
            "ops.usp_request_owned_query_cancel",
            {"run_id": parameters["run_id"]},
        )
        row = _one_row(rows, {"run_id", "lease_id", "state"}, "query cancellation request")
        if (
            row["run_id"] != parameters["run_id"]
            or not isinstance(row["lease_id"], str)
            or row["state"] != "cancel_requested"
        ):
            raise OperationDeniedError("authoritative query cancellation was denied")
        lease_id = str(row["lease_id"])
        await self._leases.cancel_owned(str(parameters["run_id"]), lease_id)
        confirmation = await self._client.call(
            "ops.usp_confirm_owned_query_cancelled",
            {"run_id": parameters["run_id"], "lease_id": lease_id},
        )
        result = _one_row(
            confirmation,
            {"result_code", "run_id", "state"},
            "query cancellation confirmation",
        )
        if (
            result["result_code"] != "owned_query_cancelled"
            or result["run_id"] != parameters["run_id"]
            or result["state"] != "cancelled"
        ):
            raise OperationDeniedError("authoritative query cancellation confirmation was denied")
        return OperationReceipt(
            operation_id=operation.operation_id,
            result_code="owned_query_cancelled",
            result={"run_id": str(result["run_id"]), "state": "cancelled"},
        )


class AzureSqlRemediationAuthority:
    """Atomically validates approval, consumes its grant, and activates safe SQL."""

    def __init__(self, client: SqlProcedureClient) -> None:
        self._client = client

    async def execute(
        self,
        presented: ApprovalReceipt,
        operation: RegisteredOperation,
        parameters: dict[str, Scalar],
        *,
        now: datetime,
    ) -> OperationReceipt:
        if (
            operation.operation_id != "remediate.activate-cycle-safe-query-v1"
            or operation.worker != "remediation"
            or parameters != {"query_version": presented.safe_query_version}
        ):
            raise OperationDeniedError("SQL remediation binding differs")
        payload = presented.model_dump(mode="python", exclude={"receipt_hash"})
        if content_hash(payload) != presented.receipt_hash:
            raise OperationDeniedError("approval receipt hash differs")
        if presented.consumed_at is not None or presented.expires_at <= now:
            raise OperationDeniedError("approval is consumed or expired")
        rows = await self._client.call(
            "ops.usp_activate_cycle_safe_query",
            {
                "approval_id": presented.approval_id,
                "plan_id": presented.plan_id,
                "task_id": presented.task_id,
                "engagement_id": presented.engagement_id,
                "workspace_id": presented.workspace_id,
                "logical_agent_id": presented.logical_agent_id,
                "plan_hash": presented.plan_hash,
                "operation_id": presented.operation_id,
                "target_resource": presented.target_resource,
                "safe_query_version": presented.safe_query_version,
                "approved_by_hash": presented.approved_by_hash,
                "approved_at": presented.approved_at,
                "expires_at": presented.expires_at,
                "one_time_id": presented.one_time_id,
                "expected_approval_version": presented.version,
                "receipt_hash": presented.receipt_hash,
                "now_utc": now,
            },
        )
        row = _one_row(
            rows,
            {"result_code", "operation_id", "safe_query_version"},
            "atomic remediation",
        )
        if (
            row["result_code"] != "safe_query_activated"
            or row["operation_id"] != operation.operation_id
            or row["safe_query_version"] != presented.safe_query_version
        ):
            raise OperationDeniedError("authoritative SQL remediation was denied")
        return OperationReceipt(
            operation_id=operation.operation_id,
            result_code="safe_query_activated",
            result={"safe_query_version": presented.safe_query_version},
        )