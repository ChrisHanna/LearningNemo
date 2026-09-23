"""Synthetic SQL contract rehearsal, always rolled back; never human evidence."""

from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

from task_agent.console.incident_contract import InvestigationPlan
from task_agent.console.analysis_contract import AnalysisReceipt, DiagnosticSnapshot, snapshot_hash
from task_agent.control.canonical import canonical_json, content_hash
from task_agent.control.execution import ExecutionClaim, ExecutionStatus, VerifiedRecovery
from task_agent.control.models import ApprovalReceipt, PlanContent
from task_agent.control.mssql_client import PROCEDURES, _normalize


def verify_rolled_back_workflow(connection):
    suffix = uuid4().hex
    now = datetime.now(UTC).replace(microsecond=0)
    sponsor, reviewer = '0' * 64, '1' * 64
    context = {'task_id': 'task-' + suffix, 'engagement_id': 'engagement-' + suffix,
               'workspace_id': 'workspace-' + suffix, 'logical_agent_id': 'analyst-' + suffix}
    analysis_id, plan_id, execution_id = 'analysis-' + suffix, 'plan-' + suffix, 'execution-' + suffix
    connection.autocommit = True
    with connection.cursor() as cursor:
        def call(name, parameters):
            statement, order = PROCEDURES[name]
            cursor.execute(statement, *[_normalize(parameters[key]) for key in order])
            if cursor.description is None:
                return []
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

        cursor.execute('BEGIN TRANSACTION')
        try:
            diagnostic = call('ops.usp_get_diagnostic_snapshot', {})[0]
            snapshot = DiagnosticSnapshot(active_query_version=diagnostic['active_query_version'],
                query_run_states={row['run_id']: row['state'] for row in json.loads(diagnostic['query_run_states_json'])})
            evidence = AnalysisReceipt(analysis_id=analysis_id, observed_at=now, snapshot=snapshot, evidence_hash=snapshot_hash(snapshot))
            assert call('control.usp_record_readonly_analysis', {'sponsor_hash': sponsor, 'analysis_json': canonical_json(evidence)}) == [{'analysis_id': analysis_id}]
            content = PlanContent(**context, operation_id='remediate.activate-cycle-safe-query-v1', target_resource='lab.QueryVersions/cycle-safe-v1',
                safe_query_version='cycle-safe-v1', rollback_version='cycle-unsafe-v1', parameters={'query_version': 'cycle-safe-v1'})
            plan = InvestigationPlan(plan_id=plan_id, content=content.model_dump(), plan_hash=content_hash(content), state='draft',
                created_by_hash=sponsor, created_at=now, version=1)
            proposal_parameters = {'analysis_id': analysis_id, 'sponsor_hash': sponsor, 'evidence_hash': evidence.evidence_hash, 'plan_json': plan.model_dump_json()}
            assert call('control.usp_propose_readonly_analysis', proposal_parameters) == [{'plan_id': plan_id, 'plan_hash': plan.plan_hash}]
            assert call('control.usp_propose_readonly_analysis', proposal_parameters) == [{'plan_id': plan_id, 'plan_hash': plan.plan_hash}]
            call('control.usp_submit_human_plan', {'plan_id': plan_id, 'sponsor_hash': sponsor, 'expected_plan_hash': plan.plan_hash, 'expected_investigation_version': 1})
            values = dict(approval_id='approval-' + suffix, plan_id=plan_id, **context, plan_hash=plan.plan_hash,
                operation_id=content.operation_id, target_resource=content.target_resource, safe_query_version='cycle-safe-v1',
                approved_by_hash=reviewer, approved_at=now, expires_at=now + timedelta(minutes=10), one_time_id='grant-' + suffix, consumed_at=None, version=1)
            approval = ApprovalReceipt(**values, receipt_hash=content_hash(values))
            call('control.usp_decide_review_plan', {'plan_id': plan_id, 'expected_plan_hash': plan.plan_hash, 'expected_plan_version': 2,
                'reviewer_hash': reviewer, 'decision': 'approve', 'receipt_json': approval.model_dump_json()})
            claim_rows = call('control.usp_claim_human_execution', {'plan_id': plan_id, 'expected_plan_hash': plan.plan_hash, 'expected_plan_version': 3,
                'sponsor_hash': sponsor, 'execution_id': execution_id})
            claim = ExecutionClaim(execution_id=execution_id, sponsor_hash=sponsor, approval=json.loads(claim_rows[0]['approval_json']))
            broker_parameters = claim.approval.model_dump(exclude={'consumed_at', 'version'})
            broker_parameters.update(expected_approval_version=1, now_utc=now)
            broker = call('ops.usp_activate_cycle_safe_query', broker_parameters)
            assert broker[0]['result_code'] == 'safe_query_activated'
            assert call('control.usp_reconcile_human_broker', {'plan_id': plan_id, 'sponsor_hash': sponsor}) == [{'execution_id': execution_id}]
            checks = call('ops.usp_verify_cycle_recovery', {'safe_query_version': 'cycle-safe-v1'})[0]
            receipt = VerifiedRecovery(execution_id=execution_id, plan_hash=plan.plan_hash, safe_query_version='cycle-safe-v1', checks={key: bool(value) for key, value in checks.items()})
            assert all(receipt.checks.values())
            call('control.usp_record_human_execution_stage', {'execution_id': execution_id, 'sponsor_hash': sponsor, 'plan_hash': plan.plan_hash,
                'stage': 'verification', 'receipt_json': canonical_json(receipt), 'receipt_hash': content_hash(receipt)})
            call('control.usp_complete_human_execution', {'execution_id': execution_id, 'sponsor_hash': sponsor, 'plan_hash': plan.plan_hash})
            result = call('control.usp_get_human_execution', {'plan_id': plan_id, 'sponsor_hash': sponsor})
            assert ExecutionStatus.model_validate_json(result[0]['execution_json']).state == 'completed'
            assert call('control.usp_get_human_execution', {'plan_id': plan_id, 'sponsor_hash': reviewer}) == []
            cursor.execute('SELECT @@TRANCOUNT')
            assert cursor.fetchone()[0] == 1
        finally:
            cursor.execute('IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION')
        cursor.execute('SELECT COUNT(*) FROM control.Tasks WHERE TaskId=?', context['task_id'])
        assert cursor.fetchone()[0] == 0
    return {'sqlWorkflow': 'completed-and-rolled-back', 'humanRehearsal': False, 'queryProcessStarted': False}