from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from task_agent.control.canonical import content_hash
from task_agent.control.models import ApprovalReceipt


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "run_live_cycle_workflow",
    ROOT / "scripts" / "run-live-cycle-workflow.py",
)
assert spec is not None and spec.loader is not None
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


async def test_live_cycle_runner_binds_approval_and_final_sql_state(monkeypatch) -> None:
    endpoints = {
        "diagnostic": "diagnostic.example.eastus.azurecontainerapps.io",
        "queryRunner": "query-runner.example.eastus.azurecontainerapps.io",
        "remediation": "remediation.example.eastus.azurecontainerapps.io",
        "verifier": "verifier.example.eastus.azurecontainerapps.io",
    }
    audiences = {
        "diagnostic": "11111111-1111-4111-8111-111111111111",
        "queryRunner": "22222222-2222-4222-8222-222222222222",
        "remediation": "33333333-3333-4333-8333-333333333333",
        "verifier": "44444444-4444-4444-8444-444444444444",
    }
    monkeypatch.setenv("LEARNINGNEMO_WORKER_ENDPOINTS", workflow.canonical_json(endpoints))
    monkeypatch.setenv("LEARNINGNEMO_WORKER_AUDIENCES", workflow.canonical_json(audiences))
    monkeypatch.setenv("LEARNINGNEMO_SQL_SERVER", "sql-example.database.windows.net")
    monkeypatch.setenv("LEARNINGNEMO_SQL_DATABASE", "learningnemo")
    monkeypatch.setenv("AZURE_CLIENT_ID", "55555555-5555-4555-8555-555555555555")
    monkeypatch.setenv("LEARNINGNEMO_APPROVER_HASH", "a" * 64)
    monkeypatch.setenv("LEARNINGNEMO_OPERATOR_HASH", "b" * 64)

    sql_calls = []

    class FakeSqlClient:
        def __init__(self, **options):
            assert options["application_name"] == "LearningNeMoLiveControl"

        async def call(self, procedure, parameters):
            sql_calls.append((procedure, parameters))
            if procedure == "control.usp_initialize_cycle_demo":
                return ({"result_code": "cycle_demo_initialized", "task_state": "open"},)
            if procedure == "control.usp_record_cycle_containment_plan":
                return ({
                    "result_code": "cycle_demo_contained",
                    "task_state": "contained",
                    "plan_state": "awaiting_approval",
                },)
            if procedure == "control.usp_get_cycle_demo_summary":
                return (dict(workflow.EXPECTED_SUMMARY),)
            return ()

    class FakeCredential:
        def __init__(self, **options):
            assert options["client_id"] == "55555555-5555-4555-8555-555555555555"
            self.closed = False

        def get_token(self, scope):
            assert scope.startswith("api://") and scope.endswith("/.default")
            return SimpleNamespace(token="opaque-token")

        def close(self):
            self.closed = True

    request_calls = []

    def fake_request(host, method, path, **options):
        request_calls.append((host, method, path, options))
        if path == "/readyz":
            return {"status": "ready"}
        if path == "/v1/query-runs/start":
            return {
                "operation_id": "demo.start-controlled-query-v1",
                "result_code": "owned_query_started",
                "result": {"run_id": options["payload"]["run_id"], "state": "running"},
            }
        if path == "/v1/diagnostics/current":
            run_id = next(
                call[3]["payload"]["run_id"]
                for call in request_calls
                if call[2] == "/v1/query-runs/start"
            )
            return {
                "active_query_version": workflow.UNSAFE_QUERY_VERSION,
                "query_run_states": {run_id: "running"},
            }
        if path == "/v1/query-runs/cancel":
            return {
                "operation_id": "contain.cancel-owned-query-v1",
                "result_code": "owned_query_cancelled",
                "result": {"run_id": options["payload"]["run_id"], "state": "cancelled"},
            }
        if path == "/v1/remediations/execute":
            approval = ApprovalReceipt.model_validate(options["payload"]["approval"])
            assert approval.receipt_hash == content_hash(
                approval.model_dump(mode="python", exclude={"receipt_hash"})
            )
            return {
                "operation_id": workflow.OPERATION_ID,
                "result_code": "safe_query_activated",
                "result": {"query_version": workflow.SAFE_QUERY_VERSION},
            }
        if path == "/v1/verifications/cycle-recovery":
            return {
                "passed": True,
                "checks": {
                    "safe_query_version_active": True,
                    "no_owned_query_running": True,
                    "deterministic_result": True,
                },
            }
        raise AssertionError(path)

    monkeypatch.setattr(workflow, "MssqlProcedureClient", FakeSqlClient)
    monkeypatch.setattr(workflow, "ManagedIdentityCredential", FakeCredential)
    monkeypatch.setattr(workflow, "request_json", fake_request)

    await workflow.run_workflow()

    assert [name for name, _parameters in sql_calls] == [
        "control.usp_initialize_cycle_demo",
        "control.usp_record_cycle_containment_plan",
        "control.usp_issue_approval",
        "control.usp_record_cycle_execution",
        "control.usp_record_cycle_verification",
        "control.usp_get_cycle_demo_summary",
    ]
    assert [path for _host, _method, path, _options in request_calls if path != "/readyz"] == [
        "/v1/query-runs/start",
        "/v1/diagnostics/current",
        "/v1/query-runs/cancel",
        "/v1/remediations/execute",
        "/v1/verifications/cycle-recovery",
    ]