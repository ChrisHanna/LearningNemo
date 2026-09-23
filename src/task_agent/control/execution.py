"""Durable coordination of approved execution; ambiguous writes are never replayed."""

from datetime import UTC, datetime
import json
from typing import Callable, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from task_agent.control.canonical import canonical_json, content_hash
from task_agent.control.models import ApprovalReceipt, OperationReceipt
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureClient, SqlProcedureUnavailableError


class ExecutionIntent(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    plan_id: str = Field(pattern=r'^plan-[A-Za-z0-9-]{1,120}$')
    plan_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    plan_version: int = Field(ge=1)


class ExecutionClaim(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    execution_id: str = Field(pattern=r'^execution-[a-f0-9]{32}$')
    sponsor_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    approval: ApprovalReceipt

    @model_validator(mode='after')
    def validate_receipt(self):
        if content_hash(self.approval.model_dump(exclude={'receipt_hash'})) != self.approval.receipt_hash:
            raise ValueError('stored approval hash differs')
        if self.approval.approved_by_hash == self.sponsor_hash:
            raise ValueError('executor cannot approve their own operation')
        if self.approval.consumed_at is not None:
            raise ValueError('approval is already consumed')
        return self


class VerifiedRecovery(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    execution_id: str
    plan_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    safe_query_version: str
    checks: dict[str, bool]


class ExecutionTransport(Protocol):
    async def execute(self, claim: ExecutionClaim) -> OperationReceipt: ...
    async def verify(self, claim: ExecutionClaim) -> VerifiedRecovery: ...


class ExecutionStatus(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    execution_id: str = Field(pattern=r'^execution-[a-f0-9]{32}$')
    plan_id: str
    plan_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    state: Literal['claimed', 'broker', 'verification', 'completed']
    broker: OperationReceipt | None = None
    broker_hash: str | None = None
    verification: VerifiedRecovery | None = None
    verification_hash: str | None = None

    @model_validator(mode='after')
    def validate_evidence(self):
        if self.state != 'claimed':
            if (self.broker is None or content_hash(self.broker) != self.broker_hash
                    or self.broker.operation_id != 'remediate.activate-cycle-safe-query-v1'
                    or self.broker.result_code != 'safe_query_activated'
                    or self.broker.result != {'safe_query_version': 'cycle-safe-v1'}):
                raise ValueError('persisted broker evidence invalid')
        if self.state in ('verification', 'completed'):
            receipt = self.verification
            if (receipt is None or content_hash(receipt) != self.verification_hash
                    or receipt.execution_id != self.execution_id or receipt.plan_hash != self.plan_hash
                    or receipt.safe_query_version != 'cycle-safe-v1'
                    or set(receipt.checks) != {'safe_query_version_active', 'no_owned_query_running', 'deterministic_result'}
                    or not all(receipt.checks.values())):
                raise ValueError('persisted verification evidence invalid')
        return self


class SqlExecutionRepository:
    def __init__(self, client: SqlProcedureClient):
        self.client = client

    async def status(self, plan_id: str, *, sponsor_hash: str) -> ExecutionStatus | None:
        rows = await self.client.call('control.usp_get_human_execution', {'plan_id': plan_id, 'sponsor_hash': sponsor_hash})
        if not rows:
            return None
        try:
            if len(rows) != 1 or set(rows[0]) != {'execution_json'}:
                raise ValueError('unexpected execution status')
            result = ExecutionStatus.model_validate_json(rows[0]['execution_json'])
            if result.plan_id != plan_id:
                raise ValueError('execution belongs to another plan')
            return result
        except (ValueError, TypeError) as error:
            raise SqlProcedureUnavailableError('persisted execution evidence is invalid') from error

    async def claim(self, intent: ExecutionIntent, sponsor_hash: str, execution_id: str) -> ExecutionClaim:
        rows = await self.client.call('control.usp_claim_human_execution', {
            'plan_id': intent.plan_id, 'expected_plan_hash': intent.plan_hash, 'expected_plan_version': intent.plan_version,
            'sponsor_hash': sponsor_hash, 'execution_id': execution_id,
        })
        try:
            if len(rows) != 1 or set(rows[0]) != {'approval_json'}:
                raise ValueError('unrecognized claim response')
            claim = ExecutionClaim(execution_id=execution_id, sponsor_hash=sponsor_hash,
                                   approval=json.loads(rows[0]['approval_json']))
            if claim.approval.plan_id != intent.plan_id or claim.approval.plan_hash != intent.plan_hash:
                raise ValueError('claimed plan differs')
            return claim
        except (ValueError, TypeError) as error:
            raise SqlProcedureUnavailableError('execution claim unconfirmed; do not retry automatically') from error

    async def record(self, claim: ExecutionClaim, stage: str, document: BaseModel) -> None:
        if stage not in {'broker', 'verification'}:
            raise ValueError('unknown execution stage')
        rows = await self.client.call('control.usp_record_human_execution_stage', {
            'execution_id': claim.execution_id, 'sponsor_hash': claim.sponsor_hash,
            'plan_hash': claim.approval.plan_hash, 'stage': stage,
            'receipt_json': canonical_json(document), 'receipt_hash': content_hash(document),
        })
        if len(rows) != 1 or dict(rows[0]) != {'execution_id': claim.execution_id, 'stage': stage}:
            raise SqlProcedureUnavailableError('execution stage persistence unconfirmed; reconcile before continuing')

    async def complete(self, execution_id: str, *, sponsor_hash: str, plan_hash: str) -> dict:
        rows = await self.client.call('control.usp_complete_human_execution', {
            'execution_id': execution_id, 'sponsor_hash': sponsor_hash, 'plan_hash': plan_hash,
        })
        if len(rows) != 1 or dict(rows[0]) != {'execution_id': execution_id, 'state': 'completed'}:
            raise SqlProcedureUnavailableError('completion outcome unconfirmed')
        return {'executionId': execution_id, 'planHash': plan_hash, 'state': 'completed'}


class ApprovedExecutionCoordinator:
    def __init__(self, repository: SqlExecutionRepository, transport: ExecutionTransport,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.repository = repository
        self.transport = transport
        self.clock = clock

    async def reconcile(self, plan_id: str, *, sponsor_hash: str) -> dict:
        current = await self.repository.status(plan_id, sponsor_hash=sponsor_hash)
        if current is None:
            raise OperationDeniedError('no claimed execution to reconcile')
        if current.state in ('verification', 'completed'):
            return current.model_dump(mode='json')
        rows = await self.repository.client.call('control.usp_reconcile_human_broker', {'plan_id': plan_id, 'sponsor_hash': sponsor_hash})
        if len(rows) != 1 or dict(rows[0]) != {'execution_id': current.execution_id}:
            raise SqlProcedureUnavailableError('broker commitment unconfirmed; no write replayed')
        receipt = await self.transport.verify_bound(current.execution_id, current.plan_hash, 'cycle-safe-v1')
        expected_checks = {'safe_query_version_active', 'no_owned_query_running', 'deterministic_result'}
        if (receipt.execution_id != current.execution_id or receipt.plan_hash != current.plan_hash
                or receipt.safe_query_version != 'cycle-safe-v1' or set(receipt.checks) != expected_checks or not all(receipt.checks.values())):
            raise OperationDeniedError('reconciliation verification failed')
        rows = await self.repository.client.call('control.usp_record_human_execution_stage', {
            'execution_id': current.execution_id, 'sponsor_hash': sponsor_hash, 'plan_hash': current.plan_hash,
            'stage': 'verification', 'receipt_json': canonical_json(receipt), 'receipt_hash': content_hash(receipt),
        })
        if len(rows) != 1 or dict(rows[0]) != {'execution_id': current.execution_id, 'stage': 'verification'}:
            raise SqlProcedureUnavailableError('reconciled verification persistence unconfirmed')
        return (await self.repository.status(plan_id, sponsor_hash=sponsor_hash)).model_dump(mode='json')

    async def execute(self, intent: ExecutionIntent, *, sponsor_hash: str) -> dict:
        claim = await self.repository.claim(intent, sponsor_hash, 'execution-' + uuid4().hex)
        now = self.clock()
        if now.tzinfo is None or claim.approval.expires_at.tzinfo is None or claim.approval.expires_at <= now:
            raise OperationDeniedError('approval expired before dispatch; claimed operation requires reconciliation')
        receipt = await self.transport.execute(claim)
        if (receipt.operation_id != claim.approval.operation_id or receipt.result_code != 'safe_query_activated'
                or receipt.result != {'safe_query_version': claim.approval.safe_query_version}):
            raise OperationDeniedError('broker response does not establish the approved change')
        await self.repository.record(claim, 'broker', receipt)
        verification = await self.transport.verify(claim)
        expected_checks = {'safe_query_version_active', 'no_owned_query_running', 'deterministic_result'}
        if (verification.execution_id != claim.execution_id or verification.plan_hash != intent.plan_hash
                or verification.safe_query_version != claim.approval.safe_query_version
                or set(verification.checks) != expected_checks or not all(verification.checks.values())):
            raise OperationDeniedError('independent verification failed or belongs to a different execution')
        await self.repository.record(claim, 'verification', verification)
        return {'executionId': claim.execution_id, 'planId': intent.plan_id, 'planHash': intent.plan_hash,
                'state': 'verified', 'completionRequired': True}