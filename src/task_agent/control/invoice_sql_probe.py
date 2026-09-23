"""Admin-only SQL rehearsal; all fixture records and repairs are rolled back."""

from datetime import UTC, datetime, timedelta
import hashlib
import json
from uuid import uuid4

from task_agent.control.canonical import canonical_json
from task_agent.control.invoice_contract import InvoiceEvidence, InvoicePlan, InvoiceStep
from task_agent.control.mssql_client import PROCEDURES, _normalize


def verify_invoice_workflow(connection, *, healthy=False, temporary_schema=False):
    scenario_id, planning_run, planning_sandbox = (uuid4().hex for _ in range(3))
    execution_run, execution_sandbox, plan_id = (uuid4().hex for _ in range(3))
    sponsor, reviewer = '0' * 64, '1' * 64
    planning_cap = hashlib.sha256(uuid4().bytes).hexdigest()
    execution_cap = hashlib.sha256(uuid4().bytes).hexdigest()
    connection.autocommit = True
    with connection.cursor() as cursor:
        def call(name, **values):
            statement, parameters = PROCEDURES[name]
            cursor.execute(statement, *[_normalize(values[name]) for name in parameters])
            if cursor.description is None:
                return []
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

        cursor.execute('SELECT @@TRANCOUNT')
        initial_transactions = cursor.fetchone()[0]
        cursor.execute('BEGIN TRANSACTION')
        try:
            call('control.usp_create_invoice_scenario', scenario_id=scenario_id,
                 variant='healthy' if healthy else 'lost-acknowledgement')
            call('control.usp_register_invoice_planning', run_id=planning_run, scenario_id=scenario_id,
                 sponsor_hash=sponsor, sandbox_id=planning_sandbox, policy_hash='2' * 64, capability_hash=planning_cap,
                 expires_at=datetime.now(UTC) + timedelta(minutes=10), call_limit=20)
            for tool in ('invoice_summary', 'invoice_batches'):
                admitted = call('control.usp_admit_invoice_tool', run_id=planning_run, capability_hash=planning_cap, tool=tool)
                assert admitted[0]['scenario_id'] == scenario_id
            for tool in ('execute_step', 'arbitrary_sql'):
                assert call('control.usp_admit_invoice_tool', run_id=planning_run, capability_hash=planning_cap, tool=tool) == []
            evidence = InvoiceEvidence(**call('ops.usp_diagnose_invoice_summary', scenario_id=scenario_id)[0])
            assert evidence.orders == 12
            assert evidence.duplicate_invoices == (0 if healthy else 12)
            assert evidence.active_invoices == (12 if healthy else 24)
            batches = call('ops.usp_diagnose_invoice_batches', scenario_id=scenario_id)
            assert sum(row['order_id'] in (1, 2) for row in batches) == (2 if healthy else 4)
            call('control.usp_record_invoice_evidence', run_id=planning_run, evidence_hash=evidence.evidence_hash,
                 evidence_json=canonical_json(evidence))
            if not healthy:
                operations = ('invoice.quarantine-duplicates.v1', 'invoice.rebuild-total.v1', 'invoice.activate-idempotent-import.v1')
                steps = tuple(InvoiceStep(step_id=index, operation=operation, target=scenario_id,
                    expected_revision=index, duplicate_set_hash=evidence.duplicate_set_hash if index == 1 else None)
                    for index, operation in enumerate(operations, 1))
                plan = InvoicePlan(plan_id=plan_id, scenario_id=scenario_id, sponsor_hash=sponsor,
                    planning_run_id=planning_run, planning_sandbox_id=planning_sandbox, evidence_hash=evidence.evidence_hash,
                    created_at=datetime.now(UTC), diagnosis='Rollback-only SQL fixture; not an agent or human investigation',
                    rationale='Duplicate order keys in repeated batch attempts', risks=('Quarantine only exact duplicate rows',), steps=steps)
                call('control.usp_record_invoice_plan', plan_json=canonical_json(plan), plan_hash=plan.plan_hash, sponsor_hash=sponsor)
                call('control.usp_submit_invoice_plan', plan_id=plan_id, plan_hash=plan.plan_hash, sponsor_hash=sponsor)
                call('control.usp_decide_invoice_plan', plan_id=plan_id, plan_hash=plan.plan_hash, reviewer_hash=reviewer, decision='approve')
                call('control.usp_claim_invoice_execution', plan_id=plan_id, plan_hash=plan.plan_hash, sponsor_hash=sponsor,
                     run_id=execution_run, sandbox_id=execution_sandbox, policy_hash='3' * 64, capability_hash=execution_cap)
                for step in plan.steps:
                    result = call('ops.usp_execute_invoice_step', run_id=execution_run, capability_hash=execution_cap, step_json=canonical_json(step))
                    receipt = json.loads(result[0]['receipt_json'])
                    assert receipt['step_id'] == step.step_id and receipt['revision'] == step.expected_revision + 1
                verification = call('ops.usp_verify_invoice_run', run_id=execution_run)
                assert all(json.loads(verification[0]['verification_json']).values())
                call('control.usp_complete_invoice_plan', plan_id=plan_id, plan_hash=plan.plan_hash, sponsor_hash=sponsor)
                assert call('control.usp_read_invoice_plans', sponsor_hash=sponsor)[0]['state'] == 'completed'
                assert call('control.usp_read_invoice_plans', sponsor_hash=reviewer) == []
                assert len(call('control.usp_get_invoice_execution', run_id=execution_run, sponsor_hash=sponsor)) == 3
                assert call('control.usp_get_invoice_execution', run_id=execution_run, sponsor_hash=reviewer) == []
                final = InvoiceEvidence(**call('ops.usp_diagnose_invoice_summary', scenario_id=scenario_id)[0])
                assert final.active_invoices == 12 and final.actual_cents == final.reported_cents == final.expected_cents
            call('control.usp_revoke_invoice_run', run_id=planning_run, sponsor_hash=sponsor)
            assert call('control.usp_admit_invoice_tool', run_id=planning_run, capability_hash=planning_cap, tool='invoice_summary') == []
            cursor.execute('SELECT @@TRANCOUNT')
            assert cursor.fetchone()[0] == initial_transactions + 1
        finally:
            cursor.execute('IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION')
        if temporary_schema:
            cursor.execute("SELECT OBJECT_ID(N'lab.InvoiceScenarios', N'U')")
            assert cursor.fetchone()[0] is None
        else:
            cursor.execute('SELECT COUNT(*) FROM lab.InvoiceScenarios WHERE ScenarioId=?', scenario_id)
            assert cursor.fetchone()[0] == 0
    return {'scenario': 'healthy' if healthy else 'lost-acknowledgement', 'sqlWorkflow': 'verified-and-rolled-back',
            'humanRehearsal': False, 'agentRun': False}