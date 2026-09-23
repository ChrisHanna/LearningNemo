"""Deterministic workflow enforcing approval, replay, and completion rules."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from datetime import timedelta

from task_agent.control.canonical import content_hash
from task_agent.control.canonical import subject_hash
from task_agent.control.evidence import EvidenceLedger
from task_agent.control.models import ApprovalReceipt
from task_agent.control.models import DiagnosisRecord
from task_agent.control.models import ExecutionRecord
from task_agent.control.models import PlanContent
from task_agent.control.models import ResolutionPlan
from task_agent.control.models import Scalar
from task_agent.control.models import TaskRecord
from task_agent.control.models import VerificationRecord
from task_agent.control.operations import InMemoryIncidentBackend
from task_agent.control.operations import OperationDeniedError
from task_agent.control.operations import validate_operation
from task_agent.control.workers import DiagnosticWorker
from task_agent.control.workers import QueryRunnerWorker
from task_agent.control.workers import RemediationBroker
from task_agent.control.workers import VerifierWorker


class WorkflowError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TrustedWorkflowService:
    """In-memory reference service; Azure SQL will later replace persistence only."""

    def __init__(
        self,
        *,
        backend: InMemoryIncidentBackend | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self.backend = backend or InMemoryIncidentBackend()
        self.diagnostic_worker = DiagnosticWorker(self.backend)
        self.query_runner = QueryRunnerWorker(self.backend)
        self.remediation_broker = RemediationBroker(self.backend)
        self.verifier = VerifierWorker(self.backend)
        self.evidence = EvidenceLedger()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid.uuid4().hex}")
        self._tasks: dict[str, TaskRecord] = {}
        self._diagnoses: dict[str, DiagnosisRecord] = {}
        self._plans: dict[str, ResolutionPlan] = {}
        self._approvals: dict[str, ApprovalReceipt] = {}
        self._executions: dict[str, ExecutionRecord] = {}
        self._verifications: dict[str, VerificationRecord] = {}
        self._lock = asyncio.Lock()

    async def _event(
        self,
        *,
        event_type: str,
        request_id: str,
        resource: str,
        decision: str,
        reason_code: str,
        actor_subject: str | None = None,
        details: dict[str, Scalar] | None = None,
        **correlation: object,
    ) -> None:
        await self.evidence.append(
            event_id=self._id_factory("event"),
            timestamp=self._clock(),
            event_type=event_type,
            level="observed",
            request_id=request_id,
            resource=resource,
            decision=decision,
            reason_code=reason_code,
            actor_hash=subject_hash(actor_subject) if actor_subject else None,
            details=details,
            **correlation,
        )

    async def _deny(
        self,
        *,
        code: str,
        message: str,
        event_type: str,
        request_id: str,
        resource: str,
        actor_subject: str | None = None,
        **correlation: object,
    ) -> None:
        await self._event(
            event_type=event_type,
            request_id=request_id,
            resource=resource,
            decision="deny",
            reason_code=code,
            actor_subject=actor_subject,
            **correlation,
        )
        raise WorkflowError(code, message)

    async def create_task(self, *, task_id: str, title: str, request_id: str) -> TaskRecord:
        record = TaskRecord(task_id=task_id, title=title)
        async with self._lock:
            if task_id in self._tasks:
                await self._deny(
                    code="task_exists",
                    message="task already exists",
                    event_type="task_create",
                    request_id=request_id,
                    resource=f"task:{task_id}",
                    task_id=task_id,
                )
            self._tasks[task_id] = record
            await self._event(
                event_type="task_create",
                request_id=request_id,
                resource=f"task:{task_id}",
                decision="allow",
                reason_code="task_created",
                task_id=task_id,
            )
            return record.model_copy(deep=True)

    async def get_task(self, task_id: str) -> TaskRecord:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise WorkflowError("task_not_found", "task not found")
            return task.model_copy(deep=True)

    async def record_diagnosis(
        self,
        *,
        task_id: str,
        engagement_id: str,
        workspace_id: str,
        logical_agent_id: str,
        summary: str,
        evidence_refs: tuple[str, ...],
        request_id: str,
    ) -> DiagnosisRecord:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                await self._deny(
                    code="task_not_found",
                    message="task not found",
                    event_type="diagnosis_record",
                    request_id=request_id,
                    resource=f"task:{task_id}",
                    task_id=task_id,
                )
            diagnosis = DiagnosisRecord(
                diagnosis_id=self._id_factory("diagnosis"),
                task_id=task_id,
                engagement_id=engagement_id,
                workspace_id=workspace_id,
                logical_agent_id=logical_agent_id,
                summary=summary,
                evidence_refs=evidence_refs,
                created_at=self._clock(),
            )
            self._diagnoses[diagnosis.diagnosis_id] = diagnosis
            await self._event(
                event_type="diagnosis_record",
                request_id=request_id,
                resource=f"task:{task_id}",
                decision="allow",
                reason_code="diagnosis_recorded",
                task_id=task_id,
                engagement_id=engagement_id,
                workspace_id=workspace_id,
                logical_agent_id=logical_agent_id,
                details={"evidence_count": len(evidence_refs)},
            )
            return diagnosis.model_copy(deep=True)

    async def contain_owned_query(
        self,
        *,
        task_id: str,
        run_id: str,
        operator_subject: str,
        expected_task_version: int,
        request_id: str,
    ) -> TaskRecord:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                await self._deny(
                    code="task_not_found",
                    message="task not found",
                    event_type="containment",
                    request_id=request_id,
                    resource=f"task:{task_id}",
                    actor_subject=operator_subject,
                    task_id=task_id,
                )
            if task.version != expected_task_version:
                await self._deny(
                    code="stale_task_version",
                    message="task version changed",
                    event_type="containment",
                    request_id=request_id,
                    resource=f"task:{task_id}",
                    actor_subject=operator_subject,
                    task_id=task_id,
                )
            if task.state != "open":
                await self._deny(
                    code="task_not_open",
                    message="task is not open",
                    event_type="containment",
                    request_id=request_id,
                    resource=f"task:{task_id}",
                    actor_subject=operator_subject,
                    task_id=task_id,
                )
            try:
                operation = validate_operation("contain.cancel-owned-query-v1", {"run_id": run_id})
                receipt = await self.query_runner.contain(operation, {"run_id": run_id})
            except OperationDeniedError as error:
                await self._deny(
                    code="containment_denied",
                    message=str(error),
                    event_type="containment",
                    request_id=request_id,
                    resource=f"query-run:{run_id}",
                    actor_subject=operator_subject,
                    task_id=task_id,
                )
            updated = task.model_copy(update={"state": "contained", "version": task.version + 1})
            self._tasks[task_id] = updated
            await self._event(
                event_type="containment",
                request_id=request_id,
                resource=f"query-run:{run_id}",
                decision="allow",
                reason_code=receipt.result_code,
                actor_subject=operator_subject,
                task_id=task_id,
                details={"operation_id": receipt.operation_id},
            )
            return updated.model_copy(deep=True)

    async def propose_plan(
        self,
        *,
        content: PlanContent,
        created_by_subject: str,
        diagnosis_id: str,
        request_id: str,
    ) -> ResolutionPlan:
        async with self._lock:
            task = self._tasks.get(content.task_id)
            diagnosis = self._diagnoses.get(diagnosis_id)
            if task is None or diagnosis is None:
                await self._deny(
                    code="plan_context_missing",
                    message="task or diagnosis is missing",
                    event_type="plan_propose",
                    request_id=request_id,
                    resource=f"task:{content.task_id}",
                    actor_subject=created_by_subject,
                    task_id=content.task_id,
                )
            if task.state != "contained":
                await self._deny(
                    code="task_not_contained",
                    message="task must be contained before permanent remediation",
                    event_type="plan_propose",
                    request_id=request_id,
                    resource=f"task:{content.task_id}",
                    actor_subject=created_by_subject,
                    task_id=content.task_id,
                )
            context_matches = (
                diagnosis.task_id == content.task_id
                and diagnosis.engagement_id == content.engagement_id
                and diagnosis.workspace_id == content.workspace_id
                and diagnosis.logical_agent_id == content.logical_agent_id
            )
            if not context_matches:
                await self._deny(
                    code="diagnosis_context_mismatch",
                    message="diagnosis context does not match the plan",
                    event_type="plan_propose",
                    request_id=request_id,
                    resource=f"task:{content.task_id}",
                    actor_subject=created_by_subject,
                    task_id=content.task_id,
                )
            try:
                operation = validate_operation(content.operation_id, content.parameters)
            except OperationDeniedError as error:
                await self._deny(
                    code="operation_denied",
                    message=str(error),
                    event_type="plan_propose",
                    request_id=request_id,
                    resource=f"task:{content.task_id}",
                    actor_subject=created_by_subject,
                    task_id=content.task_id,
                )
            if operation.worker != "remediation" or content.parameters.get("query_version") != content.safe_query_version:
                await self._deny(
                    code="plan_scope_mismatch",
                    message="plan operation or safe query version differs from the catalog",
                    event_type="plan_propose",
                    request_id=request_id,
                    resource=f"task:{content.task_id}",
                    actor_subject=created_by_subject,
                    task_id=content.task_id,
                )
            plan_hash = content_hash(content)
            plan = ResolutionPlan(
                plan_id=self._id_factory("plan"),
                content=content,
                plan_hash=plan_hash,
                state="awaiting_approval",
                created_by_hash=subject_hash(created_by_subject),
                created_at=self._clock(),
            )
            self._plans[plan.plan_id] = plan
            await self._event(
                event_type="plan_propose",
                request_id=request_id,
                resource=f"plan:{plan.plan_id}",
                decision="allow",
                reason_code="plan_awaiting_approval",
                actor_subject=created_by_subject,
                task_id=content.task_id,
                engagement_id=content.engagement_id,
                workspace_id=content.workspace_id,
                logical_agent_id=content.logical_agent_id,
                plan_hash=plan_hash,
            )
            return plan.model_copy(deep=True)

    async def approve_plan(
        self,
        *,
        plan_id: str,
        expected_plan_hash: str,
        expected_plan_version: int,
        approver_subject: str,
        request_id: str,
        lifetime_seconds: int = 900,
    ) -> ApprovalReceipt:
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                await self._deny(
                    code="plan_not_found",
                    message="plan not found",
                    event_type="plan_approve",
                    request_id=request_id,
                    resource=f"plan:{plan_id}",
                    actor_subject=approver_subject,
                )
            if plan.version != expected_plan_version:
                await self._deny(
                    code="stale_plan_version",
                    message="plan version changed",
                    event_type="plan_approve",
                    request_id=request_id,
                    resource=f"plan:{plan_id}",
                    actor_subject=approver_subject,
                    task_id=plan.content.task_id,
                    plan_hash=plan.plan_hash,
                )
            if plan.state != "awaiting_approval" or plan.plan_hash != expected_plan_hash:
                await self._deny(
                    code="plan_changed",
                    message="plan state or hash changed",
                    event_type="plan_approve",
                    request_id=request_id,
                    resource=f"plan:{plan_id}",
                    actor_subject=approver_subject,
                    task_id=plan.content.task_id,
                    plan_hash=plan.plan_hash,
                )
            if not 1 <= lifetime_seconds <= 900:
                await self._deny(
                    code="approval_lifetime_invalid",
                    message="approval lifetime exceeds policy",
                    event_type="plan_approve",
                    request_id=request_id,
                    resource=f"plan:{plan_id}",
                    actor_subject=approver_subject,
                    task_id=plan.content.task_id,
                    plan_hash=plan.plan_hash,
                )
            approved_at = self._clock()
            receipt_data = {
                "approval_id": self._id_factory("approval"),
                "plan_id": plan.plan_id,
                "task_id": plan.content.task_id,
                "engagement_id": plan.content.engagement_id,
                "workspace_id": plan.content.workspace_id,
                "logical_agent_id": plan.content.logical_agent_id,
                "plan_hash": plan.plan_hash,
                "operation_id": plan.content.operation_id,
                "target_resource": plan.content.target_resource,
                "safe_query_version": plan.content.safe_query_version,
                "approved_by_hash": subject_hash(approver_subject),
                "approved_at": approved_at,
                "expires_at": approved_at + timedelta(seconds=lifetime_seconds),
                "one_time_id": self._id_factory("grant"),
                "consumed_at": None,
                "version": 1,
            }
            approval = ApprovalReceipt(**receipt_data, receipt_hash=content_hash(receipt_data))
            self._approvals[approval.approval_id] = approval
            self._plans[plan_id] = plan.model_copy(
                update={"state": "approved", "version": plan.version + 1}
            )
            await self._event(
                event_type="plan_approve",
                request_id=request_id,
                resource=f"approval:{approval.approval_id}",
                decision="allow",
                reason_code="approval_issued",
                actor_subject=approver_subject,
                task_id=plan.content.task_id,
                engagement_id=plan.content.engagement_id,
                workspace_id=plan.content.workspace_id,
                logical_agent_id=plan.content.logical_agent_id,
                plan_hash=plan.plan_hash,
                approval_id=approval.approval_id,
            )
            return approval.model_copy(deep=True)

    async def execute_approved_plan(
        self,
        *,
        plan_id: str,
        approval_id: str,
        expected_plan_hash: str,
        expected_task_version: int,
        expected_approval_version: int,
        one_time_id: str,
        operator_subject: str,
        request_id: str,
    ) -> ExecutionRecord:
        async with self._lock:
            plan = self._plans.get(plan_id)
            approval = self._approvals.get(approval_id)
            task = self._tasks.get(plan.content.task_id) if plan else None
            if plan is None or approval is None or task is None:
                await self._deny(
                    code="execution_context_missing",
                    message="plan, approval, or task is missing",
                    event_type="execution",
                    request_id=request_id,
                    resource=f"plan:{plan_id}",
                    actor_subject=operator_subject,
                )
            correlation = {
                "task_id": task.task_id,
                "engagement_id": plan.content.engagement_id,
                "workspace_id": plan.content.workspace_id,
                "logical_agent_id": plan.content.logical_agent_id,
                "plan_hash": plan.plan_hash,
                "approval_id": approval.approval_id,
            }
            if task.version != expected_task_version or approval.version != expected_approval_version:
                await self._deny(
                    code="stale_execution_context",
                    message="task or approval version changed",
                    event_type="execution",
                    request_id=request_id,
                    resource=f"approval:{approval_id}",
                    actor_subject=operator_subject,
                    **correlation,
                )
            if approval.consumed_at is not None:
                await self._deny(
                    code="approval_replayed",
                    message="approval was already consumed",
                    event_type="execution",
                    request_id=request_id,
                    resource=f"approval:{approval_id}",
                    actor_subject=operator_subject,
                    **correlation,
                )
            if task.state != "contained" or plan.state != "approved":
                await self._deny(
                    code="execution_state_invalid",
                    message="task or plan is not executable",
                    event_type="execution",
                    request_id=request_id,
                    resource=f"approval:{approval_id}",
                    actor_subject=operator_subject,
                    **correlation,
                )
            binding_matches = (
                approval.plan_id == plan.plan_id
                and approval.plan_hash == plan.plan_hash == expected_plan_hash
                and approval.task_id == task.task_id
                and approval.engagement_id == plan.content.engagement_id
                and approval.workspace_id == plan.content.workspace_id
                and approval.logical_agent_id == plan.content.logical_agent_id
                and approval.operation_id == plan.content.operation_id
                and approval.target_resource == plan.content.target_resource
                and approval.safe_query_version == plan.content.safe_query_version
                and approval.one_time_id == one_time_id
            )
            if not binding_matches:
                await self._deny(
                    code="approval_binding_mismatch",
                    message="approval does not bind the requested execution",
                    event_type="execution",
                    request_id=request_id,
                    resource=f"approval:{approval_id}",
                    actor_subject=operator_subject,
                    **correlation,
                )
            if approval.expires_at <= self._clock():
                self._plans[plan_id] = plan.model_copy(
                    update={"state": "expired", "version": plan.version + 1}
                )
                await self._deny(
                    code="approval_expired",
                    message="approval expired",
                    event_type="execution",
                    request_id=request_id,
                    resource=f"approval:{approval_id}",
                    actor_subject=operator_subject,
                    **correlation,
                )
            if approval.approved_by_hash == subject_hash(operator_subject):
                await self._deny(
                    code="self_approval_denied",
                    message="approver cannot trigger the same plan",
                    event_type="execution",
                    request_id=request_id,
                    resource=f"approval:{approval_id}",
                    actor_subject=operator_subject,
                    **correlation,
                )
            try:
                operation = validate_operation(plan.content.operation_id, plan.content.parameters)
                started_at = self._clock()
                operation_receipt = await self.remediation_broker.execute(
                    operation,
                    plan.content.parameters,
                )
                completed_at = self._clock()
            except OperationDeniedError as error:
                await self._deny(
                    code="broker_denied",
                    message=str(error),
                    event_type="execution",
                    request_id=request_id,
                    resource=f"approval:{approval_id}",
                    actor_subject=operator_subject,
                    **correlation,
                )
            execution_data = {
                "execution_id": self._id_factory("execution"),
                "task_id": task.task_id,
                "plan_id": plan.plan_id,
                "approval_id": approval.approval_id,
                "plan_hash": plan.plan_hash,
                "safe_query_version": plan.content.safe_query_version,
                "workspace_id": plan.content.workspace_id,
                "triggered_by_hash": subject_hash(operator_subject),
                "state": "succeeded",
                "operation_receipt": operation_receipt,
                "started_at": started_at,
                "completed_at": completed_at,
            }
            execution = ExecutionRecord(
                **execution_data,
                receipt_hash=content_hash(execution_data),
            )
            consumed_data = approval.model_dump(mode="python", exclude={"receipt_hash"})
            consumed_data.update(
                consumed_at=completed_at,
                version=approval.version + 1,
            )
            self._approvals[approval_id] = ApprovalReceipt(
                **consumed_data,
                receipt_hash=content_hash(consumed_data),
            )
            self._plans[plan_id] = plan.model_copy(
                update={"state": "consumed", "version": plan.version + 1}
            )
            self._tasks[task.task_id] = task.model_copy(
                update={"state": "remediated", "version": task.version + 1}
            )
            self._executions[execution.execution_id] = execution
            await self._event(
                event_type="execution",
                request_id=request_id,
                resource=f"execution:{execution.execution_id}",
                decision="allow",
                reason_code=operation_receipt.result_code,
                actor_subject=operator_subject,
                execution_id=execution.execution_id,
                **correlation,
            )
            return execution.model_copy(deep=True)

    async def verify_execution(
        self,
        *,
        execution_id: str,
        verification_profile: str,
        request_id: str,
    ) -> VerificationRecord:
        async with self._lock:
            execution = self._executions.get(execution_id)
            if execution is None or execution.state != "succeeded":
                await self._deny(
                    code="execution_not_verifiable",
                    message="execution is missing or unsuccessful",
                    event_type="verification",
                    request_id=request_id,
                    resource=f"execution:{execution_id}",
                    execution_id=execution_id,
                )
            task = self._tasks[execution.task_id]
            checks = await self.verifier.verify(execution.safe_query_version)
            state = "passed" if checks and all(checks.values()) else "failed"
            verified_at = self._clock()
            verification_data = {
                "verification_id": self._id_factory("verification"),
                "task_id": execution.task_id,
                "execution_id": execution.execution_id,
                "plan_hash": execution.plan_hash,
                "safe_query_version": execution.safe_query_version,
                "verification_profile": verification_profile,
                "workspace_id": execution.workspace_id,
                "state": state,
                "checks": checks,
                "verified_at": verified_at,
            }
            verification = VerificationRecord(
                **verification_data,
                receipt_hash=content_hash(verification_data),
            )
            self._verifications[verification.verification_id] = verification
            if state == "passed":
                self._tasks[task.task_id] = task.model_copy(
                    update={"state": "verified", "version": task.version + 1}
                )
            await self._event(
                event_type="verification",
                request_id=request_id,
                resource=f"verification:{verification.verification_id}",
                decision="allow" if state == "passed" else "deny",
                reason_code=f"verification_{state}",
                task_id=execution.task_id,
                workspace_id=execution.workspace_id,
                plan_hash=execution.plan_hash,
                approval_id=execution.approval_id,
                execution_id=execution.execution_id,
                verification_id=verification.verification_id,
                details={"check_count": len(checks)},
            )
            return verification.model_copy(deep=True)

    async def complete_task(
        self,
        *,
        task_id: str,
        execution_id: str,
        verification_id: str,
        plan_hash: str,
        safe_query_version: str,
        verification_profile: str,
        workspace_id: str,
        expected_task_version: int,
        operator_subject: str,
        request_id: str,
    ) -> TaskRecord:
        async with self._lock:
            task = self._tasks.get(task_id)
            execution = self._executions.get(execution_id)
            verification = self._verifications.get(verification_id)
            if task is None or execution is None or verification is None:
                await self._deny(
                    code="completion_context_missing",
                    message="completion receipts are missing",
                    event_type="task_complete",
                    request_id=request_id,
                    resource=f"task:{task_id}",
                    actor_subject=operator_subject,
                    task_id=task_id,
                )
            matches = (
                task.version == expected_task_version
                and task.state == "verified"
                and execution.task_id == task_id
                and verification.task_id == task_id
                and verification.execution_id == execution_id
                and verification.state == "passed"
                and execution.plan_hash == verification.plan_hash == plan_hash
                and execution.safe_query_version == verification.safe_query_version == safe_query_version
                and verification.verification_profile == verification_profile
                and execution.workspace_id == verification.workspace_id == workspace_id
            )
            if not matches:
                await self._deny(
                    code="completion_receipt_mismatch",
                    message="successful matching verification is required",
                    event_type="task_complete",
                    request_id=request_id,
                    resource=f"task:{task_id}",
                    actor_subject=operator_subject,
                    task_id=task_id,
                    execution_id=execution_id,
                    verification_id=verification_id,
                    plan_hash=plan_hash,
                    workspace_id=workspace_id,
                )
            completed = task.model_copy(
                update={"state": "completed", "version": task.version + 1}
            )
            self._tasks[task_id] = completed
            await self._event(
                event_type="task_complete",
                request_id=request_id,
                resource=f"task:{task_id}",
                decision="allow",
                reason_code="verified_task_completed",
                actor_subject=operator_subject,
                task_id=task_id,
                execution_id=execution_id,
                verification_id=verification_id,
                plan_hash=plan_hash,
                workspace_id=workspace_id,
            )
            return completed.model_copy(deep=True)

    async def get_plan(self, plan_id: str) -> ResolutionPlan:
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise WorkflowError("plan_not_found", "plan not found")
            return plan.model_copy(deep=True)

    async def get_approval(self, approval_id: str) -> ApprovalReceipt:
        async with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None:
                raise WorkflowError("approval_not_found", "approval not found")
            return approval.model_copy(deep=True)