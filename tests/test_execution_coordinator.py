from datetime import UTC, datetime, timedelta

import pytest
from pathlib import Path
from sqlfluff.core import Linter

from task_agent.control.canonical import content_hash
from task_agent.control.execution import ApprovedExecutionCoordinator, ExecutionClaim, ExecutionIntent, VerifiedRecovery
from task_agent.control.models import ApprovalReceipt, OperationReceipt
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureUnavailableError
from task_agent.control.execution import ExecutionStatus, SqlExecutionRepository


NOW = datetime(2026, 9, 15, 6, tzinfo=UTC)


def approval():
    values = dict(approval_id='approval-test', plan_id='plan-test', task_id='task-test', engagement_id='engagement-test',
        workspace_id='workspace-test', logical_agent_id='agent-test', plan_hash='a'*64,
        operation_id='remediate.activate-cycle-safe-query-v1', target_resource='lab.QueryVersions/cycle-safe-v1',
        safe_query_version='cycle-safe-v1', approved_by_hash='b'*64, approved_at=NOW,
        expires_at=NOW+timedelta(minutes=10), one_time_id='grant-test', consumed_at=None, version=1)
    return ApprovalReceipt(**values, receipt_hash=content_hash(values))


class Repository:
    def __init__(self):
        self.claimed = False
        self.stages = []
        self.fail_stage = None

    async def claim(self, intent, sponsor_hash, execution_id):
        if self.claimed: raise OperationDeniedError('execution already claimed')
        self.claimed = True
        return ExecutionClaim(execution_id=execution_id, sponsor_hash=sponsor_hash, approval=approval())

    async def record(self, claim, stage, document):
        if self.fail_stage == stage: raise SqlProcedureUnavailableError('persistence unknown')
        self.stages.append(stage)


class Transport:
    def __init__(self):
        self.calls = []
        self.failure = None

    async def execute(self, claim):
        self.calls.append('execute')
        if self.failure == 'timeout': raise TimeoutError('broker response lost')
        return OperationReceipt(operation_id=claim.approval.operation_id, result_code='safe_query_activated',
            result={'safe_query_version': 'other' if self.failure == 'broker' else 'cycle-safe-v1'})

    async def verify(self, claim):
        self.calls.append('verify')
        checks = {'safe_query_version_active': True, 'no_owned_query_running': True, 'deterministic_result': self.failure != 'checks'}
        if self.failure == 'empty': checks = {}
        return VerifiedRecovery(execution_id='other' if self.failure == 'binding' else claim.execution_id,
            plan_hash=claim.approval.plan_hash, safe_query_version='cycle-safe-v1', checks=checks)


@pytest.mark.asyncio
async def test_verified_execution_requires_persisted_broker_and_independent_receipt():
    repository, transport = Repository(), Transport()
    coordinator = ApprovedExecutionCoordinator(repository, transport, lambda: NOW)
    result = await coordinator.execute(ExecutionIntent(plan_id='plan-test', plan_hash='a'*64, plan_version=2), sponsor_hash='c'*64)
    assert transport.calls == ['execute', 'verify']
    assert repository.stages == ['broker', 'verification']
    assert result['state'] == 'verified' and result['completionRequired'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['timeout', 'broker', 'checks', 'empty', 'binding', 'persistence'])
async def test_uncertain_or_mismatched_execution_never_reports_success_or_replays(failure):
    repository, transport = Repository(), Transport()
    transport.failure = failure
    if failure == 'persistence': repository.fail_stage = 'broker'
    coordinator = ApprovedExecutionCoordinator(repository, transport, lambda: NOW)
    intent = ExecutionIntent(plan_id='plan-test', plan_hash='a'*64, plan_version=2)
    with pytest.raises((OperationDeniedError, TimeoutError, SqlProcedureUnavailableError)):
        await coordinator.execute(intent, sponsor_hash='c'*64)
    with pytest.raises(OperationDeniedError, match='already claimed'):
        await coordinator.execute(intent, sponsor_hash='c'*64)
    assert transport.calls.count('execute') == 1
    assert 'verification' not in repository.stages
    if failure in ('timeout','broker','persistence'): assert 'verify' not in transport.calls


def test_executor_cannot_be_the_approver():
    with pytest.raises(ValueError):
        ExecutionClaim(execution_id='execution-'+'d'*32, sponsor_hash='b'*64, approval=approval())


def test_execution_sql_claim_and_completion_preserve_separate_authority():
    source = (Path(__file__).parents[1] / 'infra/next-phase/review-service/003_execution_coordination.sql').read_text()
    linter = Linter(dialect='tsql')
    assert not [str(error) for batch in source.split('\nGO\n') if batch.strip() for error in linter.parse_string(batch).violations]
    assert 'UQ_HumanExecutions_Plan UNIQUE' in source
    assert 'approval.ApprovedByHash <> @sponsor_hash' in source
    assert "execution_record.State = N'verification' AND task.State = N'verified'" in source
    assert 'WITH (UPDLOCK, HOLDLOCK)' in source
    assert 'EXEC ops.usp_activate_cycle_safe_query' not in source
    assert 'GRANT ' not in source


@pytest.mark.parametrize('state', ['broker', 'verification', 'completed'])
def test_status_never_accepts_missing_persisted_evidence(state):
    with pytest.raises(ValueError):
        ExecutionStatus(execution_id='execution-'+'a'*32, plan_id='plan-test', plan_hash='a'*64, state=state)


@pytest.mark.asyncio
async def test_execution_status_is_owner_scoped_and_read_only():
    class Client:
        calls = []
        async def call(self, name, parameters):
            self.calls.append((name, parameters))
            return ()
    client = Client()
    assert await SqlExecutionRepository(client).status('plan-test', sponsor_hash='c'*64) is None
    assert client.calls == [('control.usp_get_human_execution', {'plan_id': 'plan-test', 'sponsor_hash': 'c'*64})]


def test_reconciliation_sql_never_reexecutes_remediation():
    source = (Path(__file__).parents[1] / 'infra/next-phase/review-service/006_execution_reconciliation.sql').read_text()
    assert not [str(error) for batch in source.split('\nGO\n') if batch.strip() for error in Linter(dialect='tsql').parse_string(batch).violations]
    assert "event_record.ReasonCode = N'safe_query_activated'" in source
    assert "approval.ConsumedAt IS NOT NULL" in source
    assert 'ops.usp_activate' not in source and 'UPDATE lab.' not in source