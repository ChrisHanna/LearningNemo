from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from task_agent.control.approval import InMemoryRemediationAuthority
from task_agent.control.canonical import content_hash
from task_agent.control.models import ApprovalReceipt
from task_agent.control.operations import InMemoryIncidentBackend
from task_agent.control.operations import OperationDeniedError
from task_agent.control.runtime import create_app
from task_agent.control.runtime import create_deployed_app
from task_agent.control.runtime import settings_from_environment


NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)
ALLOWED_HEADERS = {"X-MS-CLIENT-PRINCIPAL-ID": "control-workload"}


def approval_payload(*, expires_at: datetime | None = None) -> dict[str, object]:
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
        "expires_at": expires_at or NOW + timedelta(minutes=15),
        "one_time_id": "grant-00000001",
        "consumed_at": None,
        "version": 1,
    }
    approval = ApprovalReceipt(**data, receipt_hash=content_hash(data))
    return approval.model_dump(mode="json")


def remediation_request(*, approval: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "approval": approval or approval_payload(),
        "expected_plan_hash": "a" * 64,
        "one_time_id": "grant-00000001",
        "operation_id": "remediate.activate-cycle-safe-query-v1",
        "parameters": {"query_version": "cycle-safe-v1"},
    }


def remediation_authority(
    backend: InMemoryIncidentBackend,
    payload: dict[str, object] | None = None,
) -> InMemoryRemediationAuthority:
    return InMemoryRemediationAuthority(
        [ApprovalReceipt.model_validate(payload or approval_payload())],
        backend,
    )


class FailOnceExecutor:
    def __init__(self, backend: InMemoryIncidentBackend) -> None:
        self._backend = backend
        self.calls = 0

    async def execute(self, operation, parameters):
        self.calls += 1
        if self.calls == 1:
            raise OperationDeniedError("simulated database transaction failure")
        return await self._backend.execute(operation, parameters)


def test_all_worker_modes_expose_only_health_and_readiness_without_auth() -> None:
    for mode in ("diagnostic", "query-runner", "remediation", "verifier"):
        client = TestClient(create_app(mode=mode, allowed_callers=frozenset({"control-workload"})))
        assert client.get("/healthz").json() == {"status": "healthy"}
        assert client.get("/readyz").json() == {"status": "ready", "mode": mode}
        assert client.get("/openapi.json").status_code == 404


def test_worker_endpoint_requires_platform_identity_header() -> None:
    client = TestClient(
        create_app(mode="diagnostic", allowed_callers=frozenset({"control-workload"}))
    )

    missing = client.get("/v1/diagnostics/current")
    wrong = client.get(
        "/v1/diagnostics/current",
        headers={"X-MS-CLIENT-PRINCIPAL-ID": "unapproved-workload"},
    )

    assert missing.status_code == 401
    assert missing.json() == {"detail": "workload_authentication_required"}
    assert wrong.status_code == 403
    assert wrong.json() == {"detail": "workload_caller_denied"}


def test_runtime_mode_does_not_register_other_worker_routes() -> None:
    client = TestClient(
        create_app(mode="diagnostic", allowed_callers=frozenset({"control-workload"}))
    )

    assert client.post(
        "/v1/remediations/execute",
        headers=ALLOWED_HEADERS,
        json=remediation_request(),
    ).status_code == 404


def test_query_runner_cancels_only_an_owned_running_query() -> None:
    backend = InMemoryIncidentBackend()
    backend.start_query("run-abcdefgh")
    client = TestClient(
        create_app(
            mode="query-runner",
            allowed_callers=frozenset({"control-workload"}),
            backend=backend,
        )
    )

    allowed = client.post(
        "/v1/query-runs/cancel",
        headers=ALLOWED_HEADERS,
        json={"run_id": "run-abcdefgh"},
    )
    replay = client.post(
        "/v1/query-runs/cancel",
        headers=ALLOWED_HEADERS,
        json={"run_id": "run-abcdefgh"},
    )

    assert allowed.status_code == 200
    assert allowed.json()["result_code"] == "owned_query_cancelled"
    assert replay.status_code == 409
    assert replay.json() == {"detail": "containment_denied"}


def test_query_runner_starts_only_registered_demo_query() -> None:
    backend = InMemoryIncidentBackend()
    client = TestClient(
        create_app(
            mode="query-runner",
            allowed_callers=frozenset({"control-workload"}),
            backend=backend,
        )
    )

    started = client.post(
        "/v1/query-runs/start",
        headers=ALLOWED_HEADERS,
        json={"run_id": "run-abcdefgh"},
    )
    replay = client.post(
        "/v1/query-runs/start",
        headers=ALLOWED_HEADERS,
        json={"run_id": "run-abcdefgh"},
    )

    assert started.status_code == 200
    assert started.json()["result_code"] == "owned_query_started"
    assert replay.status_code == 409


def test_deployed_runtime_requires_and_constructs_sql_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "LEARNINGNEMO_SERVICE_MODE": "diagnostic",
        "LEARNINGNEMO_ALLOWED_CALLER_IDS": "control-workload",
        "LEARNINGNEMO_SQL_SERVER": "sql-example.database.windows.net",
        "LEARNINGNEMO_SQL_DATABASE": "learningnemo",
        "AZURE_CLIENT_ID": "11111111-1111-4111-8111-111111111111",
        "CONTAINER_APP_REPLICA_NAME": "replica-1",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = settings_from_environment()
    client = TestClient(create_deployed_app(settings))

    assert settings.sql_server == "sql-example.database.windows.net"
    assert client.get("/healthz").status_code == 200


def test_deployed_runtime_fails_closed_without_sql_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEARNINGNEMO_SERVICE_MODE", "verifier")
    monkeypatch.setenv("LEARNINGNEMO_ALLOWED_CALLER_IDS", "control-workload")
    for name in ("LEARNINGNEMO_SQL_SERVER", "LEARNINGNEMO_SQL_DATABASE", "AZURE_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="SQL runtime settings"):
        settings_from_environment()


def test_remediation_requires_exact_approval_and_consumes_grant_once() -> None:
    backend = InMemoryIncidentBackend()
    client = TestClient(
        create_app(
            mode="remediation",
            allowed_callers=frozenset({"control-workload"}),
            backend=backend,
            clock=lambda: NOW,
            remediation_authority=remediation_authority(backend),
        )
    )
    request = remediation_request()

    allowed = client.post(
        "/v1/remediations/execute",
        headers=ALLOWED_HEADERS,
        json=request,
    )
    replay = client.post(
        "/v1/remediations/execute",
        headers=ALLOWED_HEADERS,
        json=request,
    )

    assert allowed.status_code == 200
    assert allowed.json()["result_code"] == "safe_query_activated"
    assert backend.active_query_version == "cycle-safe-v1"
    assert replay.status_code == 409
    assert replay.json() == {"detail": "remediation_denied"}


def test_remediation_rejects_tampered_or_expired_approval() -> None:
    valid = approval_payload()
    backend = InMemoryIncidentBackend()
    client = TestClient(
        create_app(
            mode="remediation",
            allowed_callers=frozenset({"control-workload"}),
            clock=lambda: NOW,
            remediation_authority=remediation_authority(backend, valid),
        )
    )
    tampered = dict(valid)
    tampered["safe_query_version"] = "cycle-safe-v2"
    expired = approval_payload(expires_at=NOW - timedelta(seconds=1))

    tampered_response = client.post(
        "/v1/remediations/execute",
        headers=ALLOWED_HEADERS,
        json=remediation_request(approval=tampered),
    )
    expired_response = client.post(
        "/v1/remediations/execute",
        headers=ALLOWED_HEADERS,
        json=remediation_request(approval=expired),
    )

    assert tampered_response.status_code == 409
    assert expired_response.status_code == 409


def test_remediation_fails_closed_without_authoritative_repository() -> None:
    backend = InMemoryIncidentBackend()
    initial_query_version = backend.active_query_version
    client = TestClient(
        create_app(
            mode="remediation",
            allowed_callers=frozenset({"control-workload"}),
            backend=backend,
            clock=lambda: NOW,
        )
    )

    response = client.post(
        "/v1/remediations/execute",
        headers=ALLOWED_HEADERS,
        json=remediation_request(),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "remediation_denied"}
    assert backend.active_query_version == initial_query_version


def test_failed_operation_does_not_consume_authoritative_grant() -> None:
    backend = InMemoryIncidentBackend()
    approval = ApprovalReceipt.model_validate(approval_payload())
    executor = FailOnceExecutor(backend)
    authority = InMemoryRemediationAuthority([approval], executor)
    client = TestClient(
        create_app(
            mode="remediation",
            allowed_callers=frozenset({"control-workload"}),
            backend=backend,
            clock=lambda: NOW,
            remediation_authority=authority,
        )
    )
    request = remediation_request()

    failed = client.post("/v1/remediations/execute", headers=ALLOWED_HEADERS, json=request)
    succeeded = client.post("/v1/remediations/execute", headers=ALLOWED_HEADERS, json=request)
    replay = client.post("/v1/remediations/execute", headers=ALLOWED_HEADERS, json=request)

    assert failed.status_code == 409
    assert succeeded.status_code == 200
    assert replay.status_code == 409
    assert executor.calls == 2


def test_strict_request_schema_rejects_arbitrary_sql() -> None:
    client = TestClient(
        create_app(
            mode="remediation",
            allowed_callers=frozenset({"control-workload"}),
            clock=lambda: NOW,
        )
    )
    request = remediation_request()
    request["sql"] = "UPDATE control.Tasks SET state = 'completed'"

    response = client.post(
        "/v1/remediations/execute",
        headers=ALLOWED_HEADERS,
        json=request,
    )

    assert response.status_code == 422


def test_verifier_reports_independent_checks_without_mutation() -> None:
    backend = InMemoryIncidentBackend()
    backend.active_query_version = "cycle-safe-v1"
    client = TestClient(
        create_app(
            mode="verifier",
            allowed_callers=frozenset({"control-workload"}),
            backend=backend,
        )
    )

    response = client.post(
        "/v1/verifications/cycle-recovery",
        headers=ALLOWED_HEADERS,
        json={"safe_query_version": "cycle-safe-v1"},
    )

    assert response.status_code == 200
    assert response.json()["passed"] is True
    assert response.json()["checks"] == {
        "safe_query_version_active": True,
        "no_owned_query_running": True,
    }