from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from task_agent.control.invoice_contract import InvoicePlan
from task_agent.control.invoice_retention import retention_candidate
from test_invoice_contract import plan_values
from test_invoice_repository import evidence


def candidate_record():
    values = plan_values()
    observed = evidence().model_copy(update={'scenario_id': values['scenario_id']})
    plan = InvoicePlan(**{**values, 'evidence_hash': observed.evidence_hash})
    stopped = datetime.now(UTC) - timedelta(hours=25)
    return {'run_id': plan.planning_run_id, 'sandbox_id': plan.planning_sandbox_id,
            'scenario_id': plan.scenario_id, 'kind': 'planning', 'state': 'finished',
            'stopped_at': stopped.isoformat(), 'revoked_at': (stopped - timedelta(seconds=10)).isoformat(),
            'plan': plan.model_dump(mode='json'), 'plan_hash': plan.plan_hash,
            'result': {'outcome': 'proposal', 'plan': plan.model_dump(mode='json'), 'plan_hash': plan.plan_hash,
                       'diagnostics': {**observed.model_dump(mode='json'), 'evidence_hash': observed.evidence_hash}},
            'events': [{'event': {'source': 'workspace-controller', 'event_type': 'sandbox-stopped', 'sandbox_id': plan.planning_sandbox_id}},
                       {'event': {'source': 'workspace-controller', 'event_type': 'authority-revoked'}}]}


def test_old_successful_bound_planning_is_eligible():
    record = candidate_record()
    candidate = retention_candidate(record)
    assert candidate['name'] == 'ip-' + record['run_id'][:16]
    assert candidate['evidence'] == record


@pytest.mark.parametrize('state', ['queued', 'running', 'uncertain', 'failed'])
def test_non_successful_jobs_are_retained_even_when_old_and_stopped(state):
    record = candidate_record()
    record['state'] = state
    with pytest.raises(ValueError): retention_candidate(record)


@pytest.mark.parametrize('age', [0, 23.999, -1])
def test_young_or_future_stop_receipts_are_retained(age):
    record = candidate_record()
    record['stopped_at'] = (datetime.now(UTC) - timedelta(hours=age)).isoformat()
    with pytest.raises(ValueError): retention_candidate(record)


def test_exact_24_hour_boundary_is_eligible():
    record = candidate_record()
    now = datetime.now(UTC)
    record['stopped_at'] = (now - timedelta(hours=24)).isoformat()
    assert retention_candidate(record, now)


@pytest.mark.parametrize('field', ['plan_hash', 'sandbox_id', 'scenario_id'])
def test_mismatched_artifact_or_identity_is_retained(field):
    record = deepcopy(candidate_record())
    record[field] = 'f' * (64 if field == 'plan_hash' else 32)
    with pytest.raises(ValueError): retention_candidate(record)


def test_agent_claimed_cleanup_is_not_trusted():
    record = candidate_record()
    record['events'][0]['event']['source'] = 'agent-runtime'
    with pytest.raises(ValueError): retention_candidate(record)


def test_unconfirmed_attached_probe_prevents_automatic_retention_cleanup():
    record = candidate_record()
    record['result']['sandbox_test'] = {'outcome':'unconfirmed'}
    with pytest.raises(ValueError, match='sandbox test unresolved'): retention_candidate(record)


@pytest.mark.parametrize('fault', [None, 'missing-receipt', 'unverified', 'wrong-target', 'wrong-plan', 'same-sandbox'])
def test_execution_requires_independent_verification_and_exact_receipts(fault):
    from task_agent.control.canonical import content_hash
    from task_agent.control.invoice_retention import CHECKS
    record = candidate_record()
    plan = InvoicePlan.model_validate(record['plan'])
    record.update(kind='execution', run_id='f' * 32, sandbox_id='9' * 32, plan_state='verified', verification=dict.fromkeys(CHECKS, True))
    record['events'][0]['event']['sandbox_id'] = record['sandbox_id']
    record['result'] = {'run_id': record['run_id'], 'plan_id': plan.plan_id, 'plan_hash': plan.plan_hash, 'checks': dict.fromkeys(CHECKS, True)}
    record['receipts'] = [{'run_id': record['run_id'], 'step_id': step.step_id, 'step_hash': content_hash(step), 'scenario_id': step.target, 'revision': step.expected_revision + 1, 'operation': step.operation} for step in plan.steps]
    if fault == 'missing-receipt': record['receipts'].pop()
    if fault == 'unverified': record['verification']['no_duplicates'] = False
    if fault == 'wrong-target': record['scenario_id'] = '0' * 32
    if fault == 'wrong-plan': record['result']['plan_id'] = '0' * 32
    if fault == 'same-sandbox': record['sandbox_id'] = plan.planning_sandbox_id
    if fault:
        with pytest.raises(ValueError): retention_candidate(record)
    else:
        assert retention_candidate(record)['kind'] == 'execution'