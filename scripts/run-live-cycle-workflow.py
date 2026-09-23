#!/usr/bin/env python3
"""Run one approval-bound live cycle incident through the four trusted workers."""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import re
import ssl
import sys
import time
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any

from azure.identity import ManagedIdentityCredential

from task_agent.control.canonical import canonical_json
from task_agent.control.canonical import content_hash
from task_agent.control.canonical import subject_hash
from task_agent.control.models import ApprovalReceipt
from task_agent.control.models import ExecutionRecord
from task_agent.control.models import OperationReceipt
from task_agent.control.models import PlanContent
from task_agent.control.models import VerificationRecord
from task_agent.control.mssql_client import MssqlProcedureClient


HOST_PATTERN = re.compile(r"^[a-z0-9-]{2,63}(?:\.[a-z0-9-]{1,63})+\.azurecontainerapps\.io$")
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SERVICE_KEYS = {"diagnostic", "queryRunner", "remediation", "verifier"}
SAFE_QUERY_VERSION = "cycle-safe-v1"
UNSAFE_QUERY_VERSION = "cycle-unsafe-v1"
OPERATION_ID = "remediate.activate-cycle-safe-query-v1"
TARGET_RESOURCE = "lab.QueryVersions/cycle-safe-v1"
EXPECTED_SUMMARY = {
    "task_state": "completed",
    "plan_state": "consumed",
    "approval_state": "consumed",
    "execution_state": "succeeded",
    "verification_state": "passed",
    "query_run_state": "cancelled",
    "active_query_version": SAFE_QUERY_VERSION,
}


class LiveWorkflowError(RuntimeError):
    pass


def required_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise LiveWorkflowError("workflow_configuration_failed")
    return value


def structured_environment(name: str) -> dict[str, str]:
    try:
        value = json.loads(required_environment(name))
    except json.JSONDecodeError as error:
        raise LiveWorkflowError("workflow_configuration_failed") from error
    if not isinstance(value, dict) or set(value) != SERVICE_KEYS or not all(
        isinstance(key, str) and isinstance(item, str) and item for key, item in value.items()
    ):
        raise LiveWorkflowError("workflow_configuration_failed")
    return value


def validate_configuration(endpoints: dict[str, str], audiences: dict[str, str]) -> None:
    if not all(HOST_PATTERN.fullmatch(host) for host in endpoints.values()):
        raise LiveWorkflowError("workflow_endpoint_configuration_failed")
    if not all(UUID_PATTERN.fullmatch(value) for value in audiences.values()):
        raise LiveWorkflowError("workflow_audience_configuration_failed")


def request_json(
    host: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    attempts: int = 5,
) -> dict[str, Any]:
    body = None if payload is None else canonical_json(payload).encode("ascii")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    last_error: BaseException | None = None
    for attempt in range(attempts):
        connection = http.client.HTTPSConnection(
            host,
            timeout=120,
            context=ssl.create_default_context(),
        )
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            content = response.read(65537)
            if len(content) > 65536:
                raise LiveWorkflowError("worker_response_too_large")
            if response.status in {429, 502, 503, 504} and attempt + 1 < attempts:
                time.sleep(3)
                continue
            if not 200 <= response.status < 300:
                raise LiveWorkflowError(f"worker_http_{response.status}")
            value = json.loads(content.decode("utf-8"))
            if not isinstance(value, dict):
                raise LiveWorkflowError("worker_response_invalid")
            return value
        except (OSError, json.JSONDecodeError, http.client.HTTPException) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(3)
                continue
        finally:
            connection.close()
    raise LiveWorkflowError("worker_request_failed") from last_error


def worker_token(credential: ManagedIdentityCredential, audience: str) -> str:
    token = credential.get_token(f"api://{audience}/.default")
    if not isinstance(token.token, str) or not token.token:
        raise LiveWorkflowError("worker_token_failed")
    return token.token


def require_row(rows: tuple[dict[str, Any], ...], expected: dict[str, Any], category: str) -> None:
    if len(rows) != 1 or rows[0] != expected:
        raise LiveWorkflowError(category)


async def run_workflow() -> None:
    endpoints = structured_environment("LEARNINGNEMO_WORKER_ENDPOINTS")
    audiences = structured_environment("LEARNINGNEMO_WORKER_AUDIENCES")
    validate_configuration(endpoints, audiences)
    server = required_environment("LEARNINGNEMO_SQL_SERVER")
    database = required_environment("LEARNINGNEMO_SQL_DATABASE")
    client_id = required_environment("AZURE_CLIENT_ID")
    approver_hash = required_environment("LEARNINGNEMO_APPROVER_HASH")
    operator_hash = required_environment("LEARNINGNEMO_OPERATOR_HASH")
    if not re.fullmatch(r"[0-9a-f]{64}", approver_hash) or not re.fullmatch(r"[0-9a-f]{64}", operator_hash):
        raise LiveWorkflowError("workflow_subject_hash_failed")
    if approver_hash == operator_hash:
        raise LiveWorkflowError("workflow_separation_of_duties_failed")
    uuid.UUID(client_id)

    credential = ManagedIdentityCredential(client_id=client_id)
    sql = MssqlProcedureClient(
        server=server,
        database=database,
        client_id=client_id,
        application_name="LearningNeMoLiveControl",
        query_timeout_seconds=30,
    )
    try:
        for key in sorted(SERVICE_KEYS):
            ready = request_json(endpoints[key], "GET", "/readyz", attempts=10)
            if ready.get("status") != "ready":
                raise LiveWorkflowError("worker_not_ready")
        print("PASS live_workflow_workers_ready")

        suffix = uuid.uuid4().hex[:16]
        task_id = f"task-{suffix}"
        engagement_id = f"engagement-{suffix}"
        workspace_id = f"workspace-{suffix}"
        logical_agent_id = f"agent-{suffix}"
        run_id = f"run-{suffix}"
        plan_id = f"plan-{suffix}"
        approval_id = f"approval-{suffix}"
        one_time_id = f"grant-{uuid.uuid4().hex}"
        execution_id = f"execution-{suffix}"
        verification_id = f"verification-{suffix}"
        now = datetime.now(UTC).replace(microsecond=0)
        context_expiry = now + timedelta(minutes=30)
        sponsor_hash = subject_hash("learningnemo-live-sponsor")
        policy_hash = content_hash({"policy": "cycle-demo-v1", "workspace_id": workspace_id})
        rows = await sql.call(
            "control.usp_initialize_cycle_demo",
            {
                "task_id": task_id,
                "title": "Controlled cycle recovery demonstration",
                "engagement_id": engagement_id,
                "workspace_id": workspace_id,
                "sponsor_subject_hash": sponsor_hash,
                "logical_agent_id": logical_agent_id,
                "policy_hash": policy_hash,
                "expires_at": context_expiry,
                "now_utc": now,
            },
        )
        require_row(rows, {"result_code": "cycle_demo_initialized", "task_state": "open"}, "workflow_initialize_failed")
        print("PASS live_workflow_initialized")

        query_token = worker_token(credential, audiences["queryRunner"])
        started = request_json(
            endpoints["queryRunner"],
            "POST",
            "/v1/query-runs/start",
            token=query_token,
            payload={"run_id": run_id},
        )
        if started.get("result_code") != "owned_query_started" or (started.get("result") or {}).get("state") != "running":
            raise LiveWorkflowError("workflow_query_start_failed")
        diagnostic = request_json(
            endpoints["diagnostic"],
            "GET",
            "/v1/diagnostics/current",
            token=worker_token(credential, audiences["diagnostic"]),
        )
        if (
            diagnostic.get("active_query_version") != UNSAFE_QUERY_VERSION
            or (diagnostic.get("query_run_states") or {}).get(run_id) != "running"
        ):
            raise LiveWorkflowError("workflow_diagnosis_failed")
        print("PASS live_workflow_diagnosed")

        cancelled = request_json(
            endpoints["queryRunner"],
            "POST",
            "/v1/query-runs/cancel",
            token=query_token,
            payload={"run_id": run_id},
        )
        if cancelled.get("result_code") != "owned_query_cancelled" or (cancelled.get("result") or {}).get("state") != "cancelled":
            raise LiveWorkflowError("workflow_containment_failed")
        print("PASS live_workflow_contained")

        plan_content = PlanContent(
            task_id=task_id,
            engagement_id=engagement_id,
            workspace_id=workspace_id,
            logical_agent_id=logical_agent_id,
            operation_id=OPERATION_ID,
            target_resource=TARGET_RESOURCE,
            safe_query_version=SAFE_QUERY_VERSION,
            rollback_version=UNSAFE_QUERY_VERSION,
            parameters={"query_version": SAFE_QUERY_VERSION},
        )
        plan_hash = content_hash(plan_content)
        rows = await sql.call(
            "control.usp_record_cycle_containment_plan",
            {
                "task_id": task_id,
                "engagement_id": engagement_id,
                "workspace_id": workspace_id,
                "logical_agent_id": logical_agent_id,
                "run_id": run_id,
                "plan_id": plan_id,
                "plan_hash": plan_hash,
                "operation_id": OPERATION_ID,
                "target_resource": TARGET_RESOURCE,
                "safe_query_version": SAFE_QUERY_VERSION,
                "rollback_version": UNSAFE_QUERY_VERSION,
                "parameters_json": canonical_json(plan_content.parameters),
                "created_by_hash": operator_hash,
                "now_utc": datetime.now(UTC).replace(microsecond=0),
            },
        )
        require_row(
            rows,
            {"result_code": "cycle_demo_contained", "task_state": "contained", "plan_state": "awaiting_approval"},
            "workflow_plan_failed",
        )

        approved_at = datetime.now(UTC).replace(microsecond=0)
        approval_data = {
            "approval_id": approval_id,
            "plan_id": plan_id,
            "task_id": task_id,
            "engagement_id": engagement_id,
            "workspace_id": workspace_id,
            "logical_agent_id": logical_agent_id,
            "plan_hash": plan_hash,
            "operation_id": OPERATION_ID,
            "target_resource": TARGET_RESOURCE,
            "safe_query_version": SAFE_QUERY_VERSION,
            "approved_by_hash": approver_hash,
            "approved_at": approved_at,
            "expires_at": approved_at + timedelta(minutes=10),
            "one_time_id": one_time_id,
            "consumed_at": None,
            "version": 1,
        }
        approval = ApprovalReceipt(**approval_data, receipt_hash=content_hash(approval_data))
        await sql.call(
            "control.usp_issue_approval",
            approval.model_dump(mode="python", exclude={"consumed_at", "version"}),
        )
        print("PASS live_workflow_approved")

        remediation_started = datetime.now(UTC).replace(microsecond=0)
        remediation = request_json(
            endpoints["remediation"],
            "POST",
            "/v1/remediations/execute",
            token=worker_token(credential, audiences["remediation"]),
            payload={
                "approval": approval.model_dump(mode="json"),
                "expected_plan_hash": plan_hash,
                "one_time_id": one_time_id,
                "operation_id": OPERATION_ID,
                "parameters": {"query_version": SAFE_QUERY_VERSION},
            },
        )
        operation_receipt = OperationReceipt.model_validate(remediation)
        if operation_receipt.result_code != "safe_query_activated":
            raise LiveWorkflowError("workflow_remediation_failed")
        remediation_completed = datetime.now(UTC).replace(microsecond=0)
        execution_data = {
            "execution_id": execution_id,
            "task_id": task_id,
            "plan_id": plan_id,
            "approval_id": approval_id,
            "plan_hash": plan_hash,
            "safe_query_version": SAFE_QUERY_VERSION,
            "workspace_id": workspace_id,
            "triggered_by_hash": operator_hash,
            "state": "succeeded",
            "operation_receipt": operation_receipt,
            "started_at": remediation_started,
            "completed_at": remediation_completed,
        }
        execution = ExecutionRecord(**execution_data, receipt_hash=content_hash(execution_data))
        await sql.call(
            "control.usp_record_cycle_execution",
            {
                "execution_id": execution.execution_id,
                "task_id": execution.task_id,
                "plan_id": execution.plan_id,
                "approval_id": execution.approval_id,
                "plan_hash": execution.plan_hash,
                "safe_query_version": execution.safe_query_version,
                "workspace_id": execution.workspace_id,
                "triggered_by_hash": execution.triggered_by_hash,
                "result_code": execution.operation_receipt.result_code,
                "receipt_hash": execution.receipt_hash,
                "started_at": execution.started_at,
                "completed_at": execution.completed_at,
            },
        )
        print("PASS live_workflow_remediated")

        verification_result = request_json(
            endpoints["verifier"],
            "POST",
            "/v1/verifications/cycle-recovery",
            token=worker_token(credential, audiences["verifier"]),
            payload={"safe_query_version": SAFE_QUERY_VERSION},
        )
        checks = verification_result.get("checks")
        if verification_result.get("passed") is not True or not isinstance(checks, dict) or not all(
            value is True for value in checks.values()
        ):
            raise LiveWorkflowError("workflow_verification_failed")
        verified_at = datetime.now(UTC).replace(microsecond=0)
        verification_data = {
            "verification_id": verification_id,
            "task_id": task_id,
            "execution_id": execution_id,
            "plan_hash": plan_hash,
            "safe_query_version": SAFE_QUERY_VERSION,
            "verification_profile": "cycle-recovery-v1",
            "workspace_id": workspace_id,
            "state": "passed",
            "checks": checks,
            "verified_at": verified_at,
        }
        verification = VerificationRecord(
            **verification_data,
            receipt_hash=content_hash(verification_data),
        )
        await sql.call(
            "control.usp_record_cycle_verification",
            {
                "verification_id": verification.verification_id,
                "task_id": verification.task_id,
                "execution_id": verification.execution_id,
                "plan_hash": verification.plan_hash,
                "safe_query_version": verification.safe_query_version,
                "verification_profile": verification.verification_profile,
                "workspace_id": verification.workspace_id,
                "checks_json": canonical_json(verification.checks),
                "receipt_hash": verification.receipt_hash,
                "verified_at": verification.verified_at,
            },
        )
        summary = await sql.call(
            "control.usp_get_cycle_demo_summary",
            {"task_id": task_id, "run_id": run_id},
        )
        require_row(summary, EXPECTED_SUMMARY, "workflow_summary_failed")
        print("PASS live_workflow_verified")
        print("PASS live_workflow_completed")
    finally:
        credential.close()


def main() -> int:
    try:
        asyncio.run(run_workflow())
        return 0
    except Exception as error:
        category = str(error) if isinstance(error, LiveWorkflowError) else "live_workflow_failed"
        print(f"FAIL {category}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())