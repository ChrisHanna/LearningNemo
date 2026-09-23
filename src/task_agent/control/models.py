"""Strict records for diagnosis, approval, execution, verification, and evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


TaskState = Literal["open", "contained", "remediated", "verified", "completed"]
PlanState = Literal["awaiting_approval", "approved", "rejected", "expired", "consumed"]
ExecutionState = Literal["queued", "running", "succeeded", "failed"]
VerificationState = Literal["pending", "passed", "failed"]
EvidenceLevel = Literal["configured", "observed", "verified"]
Scalar = str | int | bool


class FrozenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskRecord(FrozenRecord):
    task_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    state: TaskState = "open"
    version: int = Field(default=1, ge=1)


class DiagnosisRecord(FrozenRecord):
    diagnosis_id: str
    task_id: str
    engagement_id: str
    workspace_id: str
    logical_agent_id: str
    summary: str = Field(min_length=1, max_length=2000)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=20)
    created_at: datetime


class PlanContent(FrozenRecord):
    task_id: str
    engagement_id: str
    workspace_id: str
    logical_agent_id: str
    operation_id: str
    target_resource: str
    safe_query_version: str
    rollback_version: str
    parameters: dict[str, Scalar] = Field(default_factory=dict)


class ResolutionPlan(FrozenRecord):
    plan_id: str
    content: PlanContent
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: PlanState
    version: int = Field(default=1, ge=1)
    created_by_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class ApprovalReceipt(FrozenRecord):
    approval_id: str
    plan_id: str
    task_id: str
    engagement_id: str
    workspace_id: str
    logical_agent_id: str
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: str
    target_resource: str
    safe_query_version: str
    approved_by_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_at: datetime
    expires_at: datetime
    one_time_id: str
    consumed_at: datetime | None = None
    version: int = Field(default=1, ge=1)
    receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class OperationReceipt(FrozenRecord):
    operation_id: str
    result_code: str
    result: dict[str, Scalar]


class ExecutionRecord(FrozenRecord):
    execution_id: str
    task_id: str
    plan_id: str
    approval_id: str
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    safe_query_version: str
    workspace_id: str
    triggered_by_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: ExecutionState
    operation_receipt: OperationReceipt
    started_at: datetime
    completed_at: datetime
    receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class VerificationRecord(FrozenRecord):
    verification_id: str
    task_id: str
    execution_id: str
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    safe_query_version: str
    verification_profile: str
    workspace_id: str
    state: VerificationState
    checks: dict[str, bool]
    verified_at: datetime
    receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvidenceEvent(FrozenRecord):
    sequence: int = Field(ge=1)
    event_id: str
    timestamp: datetime
    event_type: str
    level: EvidenceLevel
    request_id: str
    task_id: str | None = None
    engagement_id: str | None = None
    workspace_id: str | None = None
    logical_agent_id: str | None = None
    plan_hash: str | None = None
    approval_id: str | None = None
    execution_id: str | None = None
    verification_id: str | None = None
    actor_hash: str | None = None
    resource: str
    decision: str
    reason_code: str
    details: dict[str, Scalar] = Field(default_factory=dict)
    previous_hash: str = Field(pattern=r"^(|[0-9a-f]{64})$")
    event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")