from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest

from task_agent.control.canonical import content_hash
from task_agent.control.models import PlanContent
from task_agent.control.operations import InMemoryIncidentBackend
from task_agent.control.operations import OperationDeniedError
from task_agent.control.operations import validate_operation
from task_agent.control.service import TrustedWorkflowService
from task_agent.control.service import WorkflowError


class ManualClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 12, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **values: int) -> None:
        self.current += timedelta(**values)


class SequentialIds:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self, prefix: str) -> str:
        self._value += 1
        return f"{prefix}-{self._value:08d}"


@pytest.fixture
def workflow() -> tuple[TrustedWorkflowService, ManualClock]:
    clock = ManualClock()
    service = TrustedWorkflowService(clock=clock, id_factory=SequentialIds())
    return service, clock


async def prepare_plan(
    service: TrustedWorkflowService,
    *,
    parameters: dict[str, str] | None = None,
) -> tuple[object, object]:
    service.backend.start_query("run-abcdefgh")
    await service.create_task(
        task_id="task-incident",
        title="Resolve recursive query incident",
        request_id="request-create",
    )
    diagnosis = await service.record_diagnosis(
        task_id="task-incident",
        engagement_id="engagement-alice",
        workspace_id="workspace-alice",
        logical_agent_id="agent-runner-alice",
        summary="Cycle detected in the controlled hierarchy fixture.",
        evidence_refs=("evidence-diagnostic-1",),
        request_id="request-diagnose",
    )
    await service.contain_owned_query(
        task_id="task-incident",
        run_id="run-abcdefgh",
        operator_subject="operator-alice",
        expected_task_version=1,
        request_id="request-contain",
    )
    content = PlanContent(
        task_id="task-incident",
        engagement_id="engagement-alice",
        workspace_id="workspace-alice",
        logical_agent_id="agent-runner-alice",
        operation_id="remediate.activate-cycle-safe-query-v1",
        target_resource="azure-sql:controlled-lab",
        safe_query_version="cycle-safe-v1",
        rollback_version="cycle-unsafe-v1",
        parameters=parameters or {"query_version": "cycle-safe-v1"},
    )
    plan = await service.propose_plan(
        content=content,
        created_by_subject="agent-runner-alice",
        diagnosis_id=diagnosis.diagnosis_id,
        request_id="request-plan",
    )
    return diagnosis, plan


async def approve_and_execute(service: TrustedWorkflowService):
    _diagnosis, plan = await prepare_plan(service)
    approval = await service.approve_plan(
        plan_id=plan.plan_id,
        expected_plan_hash=plan.plan_hash,
        expected_plan_version=plan.version,
        approver_subject="approver-carol",
        request_id="request-approve",
    )
    task = await service.get_task("task-incident")
    execution = await service.execute_approved_plan(
        plan_id=plan.plan_id,
        approval_id=approval.approval_id,
        expected_plan_hash=plan.plan_hash,
        expected_task_version=task.version,
        expected_approval_version=approval.version,
        one_time_id=approval.one_time_id,
        operator_subject="operator-alice",
        request_id="request-execute",
    )
    return plan, approval, execution


async def test_verified_receipts_are_required_for_completion(
    workflow: tuple[TrustedWorkflowService, ManualClock],
) -> None:
    service, _clock = workflow
    plan, approval, execution = await approve_and_execute(service)
    verification = await service.verify_execution(
        execution_id=execution.execution_id,
        verification_profile="cycle-recovery-v1",
        request_id="request-verify",
    )
    task = await service.get_task("task-incident")
    completed = await service.complete_task(
        task_id=task.task_id,
        execution_id=execution.execution_id,
        verification_id=verification.verification_id,
        plan_hash=plan.plan_hash,
        safe_query_version=plan.content.safe_query_version,
        verification_profile=verification.verification_profile,
        workspace_id=plan.content.workspace_id,
        expected_task_version=task.version,
        operator_subject="operator-alice",
        request_id="request-complete",
    )

    consumed_approval = await service.get_approval(approval.approval_id)
    assert completed.state == "completed"
    assert consumed_approval.consumed_at is not None
    assert consumed_approval.version == 2
    assert content_hash(
        consumed_approval.model_dump(mode="python", exclude={"receipt_hash"})
    ) == consumed_approval.receipt_hash
    assert content_hash(execution.model_dump(mode="python", exclude={"receipt_hash"})) == execution.receipt_hash
    assert content_hash(
        verification.model_dump(mode="python", exclude={"receipt_hash"})
    ) == verification.receipt_hash
    assert await service.evidence.validate()
    event_types = [event.event_type for event in await service.evidence.list()]
    assert event_types == [
        "task_create",
        "diagnosis_record",
        "containment",
        "plan_propose",
        "plan_approve",
        "execution",
        "verification",
        "task_complete",
    ]


async def test_containment_requires_current_task_version(
    workflow: tuple[TrustedWorkflowService, ManualClock],
) -> None:
    service, _clock = workflow
    service.backend.start_query("run-abcdefgh")
    await service.create_task(task_id="task-incident", title="Incident", request_id="create")

    with pytest.raises(WorkflowError, match="version changed") as error:
        await service.contain_owned_query(
            task_id="task-incident",
            run_id="run-abcdefgh",
            operator_subject="operator-alice",
            expected_task_version=2,
            request_id="stale-contain",
        )

    assert error.value.code == "stale_task_version"
    assert service.backend.query_runs["run-abcdefgh"] == "running"


async def test_raw_sql_and_unknown_parameters_never_reach_broker(
    workflow: tuple[TrustedWorkflowService, ManualClock],
) -> None:
    service, _clock = workflow

    with pytest.raises(WorkflowError) as error:
        await prepare_plan(
            service,
            parameters={
                "query_version": "cycle-safe-v1",
                "sql": "UPDATE control.Tasks SET state = 'completed'",
            },
        )

    assert error.value.code == "operation_denied"
    assert service.backend.active_query_version == "cycle-unsafe-v1"


async def test_changed_plan_hash_cannot_be_approved(
    workflow: tuple[TrustedWorkflowService, ManualClock],
) -> None:
    service, _clock = workflow
    _diagnosis, plan = await prepare_plan(service)

    with pytest.raises(WorkflowError) as error:
        await service.approve_plan(
            plan_id=plan.plan_id,
            expected_plan_hash="0" * 64,
            expected_plan_version=plan.version,
            approver_subject="approver-carol",
            request_id="changed-plan",
        )

    assert error.value.code == "plan_changed"


async def test_approver_cannot_trigger_own_approval(
    workflow: tuple[TrustedWorkflowService, ManualClock],
) -> None:
    service, _clock = workflow
    _diagnosis, plan = await prepare_plan(service)
    approval = await service.approve_plan(
        plan_id=plan.plan_id,
        expected_plan_hash=plan.plan_hash,
        expected_plan_version=plan.version,
        approver_subject="same-human",
        request_id="approve",
    )
    task = await service.get_task("task-incident")

    with pytest.raises(WorkflowError) as error:
        await service.execute_approved_plan(
            plan_id=plan.plan_id,
            approval_id=approval.approval_id,
            expected_plan_hash=plan.plan_hash,
            expected_task_version=task.version,
            expected_approval_version=approval.version,
            one_time_id=approval.one_time_id,
            operator_subject="same-human",
            request_id="self-trigger",
        )

    assert error.value.code == "self_approval_denied"
    assert (await service.get_approval(approval.approval_id)).consumed_at is None


async def test_one_time_grant_substitution_is_denied(
    workflow: tuple[TrustedWorkflowService, ManualClock],
) -> None:
    service, _clock = workflow
    _diagnosis, plan = await prepare_plan(service)
    approval = await service.approve_plan(
        plan_id=plan.plan_id,
        expected_plan_hash=plan.plan_hash,
        expected_plan_version=plan.version,
        approver_subject="approver-carol",
        request_id="approve",
    )
    task = await service.get_task("task-incident")

    with pytest.raises(WorkflowError) as error:
        await service.execute_approved_plan(
            plan_id=plan.plan_id,
            approval_id=approval.approval_id,
            expected_plan_hash=plan.plan_hash,
            expected_task_version=task.version,
            expected_approval_version=approval.version,
            one_time_id="grant-substituted",
            operator_subject="operator-alice",
            request_id="grant-substitution",
        )

    assert error.value.code == "approval_binding_mismatch"


async def test_expired_approval_is_denied(
    workflow: tuple[TrustedWorkflowService, ManualClock],
) -> None:
    service, clock = workflow
    _diagnosis, plan = await prepare_plan(service)
    approval = await service.approve_plan(
        plan_id=plan.plan_id,
        expected_plan_hash=plan.plan_hash,
        expected_plan_version=plan.version,
        approver_subject="approver-carol",
        request_id="approve",
        lifetime_seconds=60,
    )
    clock.advance(seconds=61)
    task = await service.get_task("task-incident")

    with pytest.raises(WorkflowError) as error:
        await service.execute_approved_plan(
            plan_id=plan.plan_id,
            approval_id=approval.approval_id,
            expected_plan_hash=plan.plan_hash,
            expected_task_version=task.version,
            expected_approval_version=approval.version,
            one_time_id=approval.one_time_id,
            operator_subject="operator-alice",
            request_id="expired",
        )

    assert error.value.code == "approval_expired"
    assert (await service.get_plan(plan.plan_id)).state == "expired"


async def test_consumed_approval_replay_is_denied(
    workflow: tuple[TrustedWorkflowService, ManualClock],
) -> None:
    service, _clock = workflow
    plan, approval, _execution = await approve_and_execute(service)
    current_task = await service.get_task("task-incident")
    current_approval = await service.get_approval(approval.approval_id)

    with pytest.raises(WorkflowError) as error:
        await service.execute_approved_plan(
            plan_id=plan.plan_id,
            approval_id=approval.approval_id,
            expected_plan_hash=plan.plan_hash,
            expected_task_version=current_task.version,
            expected_approval_version=current_approval.version,
            one_time_id=approval.one_time_id,
            operator_subject="operator-alice",
            request_id="replay",
        )

    assert error.value.code == "approval_replayed"


async def test_failed_verification_cannot_complete_task(
    workflow: tuple[TrustedWorkflowService, ManualClock],
) -> None:
    service, _clock = workflow
    plan, _approval, execution = await approve_and_execute(service)
    service.backend.active_query_version = "cycle-unsafe-v1"
    verification = await service.verify_execution(
        execution_id=execution.execution_id,
        verification_profile="cycle-recovery-v1",
        request_id="verify-failed",
    )
    task = await service.get_task("task-incident")

    assert verification.state == "failed"
    assert task.state == "remediated"
    with pytest.raises(WorkflowError) as error:
        await service.complete_task(
            task_id=task.task_id,
            execution_id=execution.execution_id,
            verification_id=verification.verification_id,
            plan_hash=plan.plan_hash,
            safe_query_version=plan.content.safe_query_version,
            verification_profile=verification.verification_profile,
            workspace_id=plan.content.workspace_id,
            expected_task_version=task.version,
            operator_subject="operator-alice",
            request_id="false-completion",
        )
    assert error.value.code == "completion_receipt_mismatch"


async def test_completion_rejects_workspace_substitution(
    workflow: tuple[TrustedWorkflowService, ManualClock],
) -> None:
    service, _clock = workflow
    plan, _approval, execution = await approve_and_execute(service)
    verification = await service.verify_execution(
        execution_id=execution.execution_id,
        verification_profile="cycle-recovery-v1",
        request_id="verify",
    )
    task = await service.get_task("task-incident")

    with pytest.raises(WorkflowError) as error:
        await service.complete_task(
            task_id=task.task_id,
            execution_id=execution.execution_id,
            verification_id=verification.verification_id,
            plan_hash=plan.plan_hash,
            safe_query_version=plan.content.safe_query_version,
            verification_profile=verification.verification_profile,
            workspace_id="workspace-bob",
            expected_task_version=task.version,
            operator_subject="operator-alice",
            request_id="workspace-substitution",
        )
    assert error.value.code == "completion_receipt_mismatch"


async def test_workers_cannot_cross_authority_boundaries(
    workflow: tuple[TrustedWorkflowService, ManualClock],
) -> None:
    service, _clock = workflow
    remediation = validate_operation(
        "remediate.activate-cycle-safe-query-v1",
        {"query_version": "cycle-safe-v1"},
    )
    containment = validate_operation(
        "contain.cancel-owned-query-v1",
        {"run_id": "run-abcdefgh"},
    )

    with pytest.raises(OperationDeniedError, match="containment operations only"):
        await service.query_runner.contain(remediation, {"query_version": "cycle-safe-v1"})
    with pytest.raises(OperationDeniedError, match="remediation operations only"):
        await service.remediation_broker.execute(containment, {"run_id": "run-abcdefgh"})


async def test_denials_are_hash_chained_without_raw_subjects(
    workflow: tuple[TrustedWorkflowService, ManualClock],
) -> None:
    service, _clock = workflow
    sensitive_subject = "operator-sensitive-object-id"
    service.backend.start_query("run-abcdefgh")
    await service.create_task(task_id="task-incident", title="Incident", request_id="create")
    with pytest.raises(WorkflowError):
        await service.contain_owned_query(
            task_id="task-incident",
            run_id="run-abcdefgh",
            operator_subject=sensitive_subject,
            expected_task_version=99,
            request_id="denied",
        )

    events = await service.evidence.list()
    serialized = "\n".join(event.model_dump_json() for event in events)
    assert await service.evidence.validate()
    assert sensitive_subject not in serialized
    assert events[-1].decision == "deny"
    assert events[-1].reason_code == "stale_task_version"