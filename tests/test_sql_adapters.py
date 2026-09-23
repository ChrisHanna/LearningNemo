from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest

from task_agent.control.canonical import content_hash
from task_agent.control.models import ApprovalReceipt
from task_agent.control.operations import OperationDeniedError
from task_agent.control.operations import validate_operation
from task_agent.control.sql_backend import AzureSqlIncidentBackend
from task_agent.control.sql_backend import AzureSqlQueryRunnerBackend
from task_agent.control.sql_backend import AzureSqlRemediationAuthority
from task_agent.control.sql_backend import SqlProcedureUnavailableError


NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


class FakeProcedureClient:
    def __init__(self, rows_by_procedure):
        self.rows_by_procedure = rows_by_procedure
        self.calls = []

    async def call(self, procedure, parameters):
        self.calls.append((procedure, dict(parameters)))
        return tuple(self.rows_by_procedure[procedure])


class FakeLeaseController:
    def __init__(self) -> None:
        self.calls = []

    async def cancel_owned(self, run_id, lease_id):
        self.calls.append((run_id, lease_id))

    async def start_owned(self, run_id):
        self.calls.append((run_id, "started"))
        return "lease-abcdefgh"


def approval() -> ApprovalReceipt:
    data = {
        "approval_id": "approval-00000001",
        "plan_id": "plan-00000001",
        "task_id": "task-incident",
        "engagement_id": "engagement-alice",
        "workspace_id": "workspace-alice",
        "logical_agent_id": "agent-runner-alice",
        "plan_hash": "a" * 64,
        "operation_id": "remediate.activate-cycle-safe-query-v1",
        "target_resource": "azure-sql:controlled-lab",
        "safe_query_version": "cycle-safe-v1",
        "approved_by_hash": "b" * 64,
        "approved_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
        "one_time_id": "grant-00000001",
        "consumed_at": None,
        "version": 1,
    }
    return ApprovalReceipt(**data, receipt_hash=content_hash(data))


async def test_sql_diagnostic_uses_fixed_procedure_and_parses_state() -> None:
    client = FakeProcedureClient(
        {
            "ops.usp_get_diagnostic_snapshot": [
                {
                    "query_run_states_json": json.dumps(
                        [{"run_id": "run-abcdefgh", "state": "running"}]
                    ),
                    "active_query_version": "cycle-unsafe-v1",
                }
            ]
        }
    )
    backend = AzureSqlIncidentBackend(client)

    states, version = await backend.diagnostic_snapshot()

    assert states == {"run-abcdefgh": "running"}
    assert version == "cycle-unsafe-v1"
    assert client.calls == [("ops.usp_get_diagnostic_snapshot", {})]


async def test_sql_containment_accepts_only_owned_cancel_procedure() -> None:
    leases = FakeLeaseController()
    client = FakeProcedureClient(
        {
            "ops.usp_request_owned_query_cancel": [
                {
                    "run_id": "run-abcdefgh",
                    "lease_id": "lease-abcdefgh",
                    "state": "cancel_requested",
                }
            ],
            "ops.usp_confirm_owned_query_cancelled": [
                {
                    "result_code": "owned_query_cancelled",
                    "run_id": "run-abcdefgh",
                    "state": "cancelled",
                }
            ],
        }
    )
    backend = AzureSqlQueryRunnerBackend(client, leases)
    operation = validate_operation(
        "contain.cancel-owned-query-v1",
        {"run_id": "run-abcdefgh"},
    )

    receipt = await backend.execute(operation, {"run_id": "run-abcdefgh"})

    assert receipt.result_code == "owned_query_cancelled"
    assert [call[0] for call in client.calls] == [
        "ops.usp_request_owned_query_cancel",
        "ops.usp_confirm_owned_query_cancelled",
    ]
    assert leases.calls == [("run-abcdefgh", "lease-abcdefgh")]


async def test_sql_query_start_uses_only_owned_controller() -> None:
    leases = FakeLeaseController()
    client = FakeProcedureClient({})
    backend = AzureSqlQueryRunnerBackend(client, leases)
    operation = validate_operation("demo.start-controlled-query-v1", {"run_id": "run-abcdefgh"})

    receipt = await backend.execute(operation, {"run_id": "run-abcdefgh"})

    assert receipt.result_code == "owned_query_started"
    assert receipt.result == {"run_id": "run-abcdefgh", "state": "running"}
    assert leases.calls == [("run-abcdefgh", "started")]
    assert client.calls == []


async def test_sql_remediation_sends_exact_approval_binding_to_one_procedure() -> None:
    record = approval()
    client = FakeProcedureClient(
        {
            "ops.usp_activate_cycle_safe_query": [
                {
                    "result_code": "safe_query_activated",
                    "operation_id": record.operation_id,
                    "safe_query_version": record.safe_query_version,
                }
            ]
        }
    )
    authority = AzureSqlRemediationAuthority(client)
    operation = validate_operation(record.operation_id, {"query_version": record.safe_query_version})

    receipt = await authority.execute(
        record,
        operation,
        {"query_version": record.safe_query_version},
        now=NOW,
    )

    assert receipt.result_code == "safe_query_activated"
    procedure, parameters = client.calls[0]
    assert procedure == "ops.usp_activate_cycle_safe_query"
    assert set(parameters) == {
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
    }
    assert "sql" not in parameters and "command" not in parameters


async def test_sql_verifier_requires_exact_boolean_contract() -> None:
    client = FakeProcedureClient(
        {
            "ops.usp_verify_cycle_recovery": [
                {
                    "safe_query_version_active": True,
                    "no_owned_query_running": True,
                    "deterministic_result": True,
                }
            ]
        }
    )
    checks = await AzureSqlIncidentBackend(client).verification_checks("cycle-safe-v1")
    assert all(checks.values())

    client.rows_by_procedure["ops.usp_verify_cycle_recovery"] = [
        {
            "safe_query_version_active": 1,
            "no_owned_query_running": True,
            "deterministic_result": True,
        }
    ]
    with pytest.raises(SqlProcedureUnavailableError, match="non-boolean"):
        await AzureSqlIncidentBackend(client).verification_checks("cycle-safe-v1")


async def test_sql_remediation_rejects_operation_substitution_before_database() -> None:
    record = approval()
    client = FakeProcedureClient({})
    authority = AzureSqlRemediationAuthority(client)
    operation = validate_operation(
        "contain.cancel-owned-query-v1",
        {"run_id": "run-abcdefgh"},
    )

    with pytest.raises(OperationDeniedError, match="binding differs"):
        await authority.execute(
            record,
            operation,
            {"run_id": "run-abcdefgh"},
            now=NOW,
        )
    assert client.calls == []