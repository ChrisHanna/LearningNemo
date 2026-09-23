from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from task_agent.control.invoice_contract import InvoiceApproval, InvoicePlan, InvoiceStep, PlanningDecision


def plan_values():
    return dict(plan_id='a' * 32, scenario_id='b' * 32, sponsor_hash='c' * 64,
                planning_run_id='d' * 32, planning_sandbox_id='e' * 32, evidence_hash='f' * 64,
                created_at=datetime.now(UTC), diagnosis='Duplicate batch records', rationale='Business keys repeat after retry',
                risks=['Quarantine must preserve legitimate invoices'], steps=[
                    dict(step_id=1, operation='invoice.quarantine-duplicates.v1', target='b' * 32, expected_revision=1, duplicate_set_hash='0' * 64),
                    dict(step_id=2, operation='invoice.rebuild-total.v1', target='b' * 32, expected_revision=2),
                    dict(step_id=3, operation='invoice.activate-idempotent-import.v1', target='b' * 32, expected_revision=3)])


def test_plan_hash_binds_every_field_and_roundtrips():
    plan = InvoicePlan(**plan_values())
    assert InvoicePlan.model_validate_json(plan.model_dump_json()).plan_hash == plan.plan_hash
    for name, value in [('evidence_hash', '1' * 64), ('rationale', 'Changed reasoning'), ('planning_sandbox_id', '2' * 32)]:
        assert InvoicePlan(**{**plan_values(), 'created_at': plan.created_at, name: value}).plan_hash != plan.plan_hash


@pytest.mark.parametrize('change', [
    {'operation': 'EXEC arbitrary_sql'}, {'target': '0' * 32}, {'step_id': 2},
    {'expected_revision': 9}, {'sql': 'DELETE FROM anything'}, {'duplicate_set_hash': None}])
def test_arbitrary_scope_sequence_and_parameters_denied(change):
    values = plan_values()
    values['steps'][0].update(change)
    with pytest.raises(ValidationError):
        InvoicePlan(**values)


def test_model_can_decline_to_propose():
    decision = PlanningDecision(outcome='insufficient-evidence', diagnosis='Unknown', rationale='Need batch history',
                                evidence_hash='0' * 64, risks=[], steps=[])
    assert not decision.steps
    with pytest.raises(ValidationError):
        PlanningDecision(**{**decision.model_dump(), 'outcome': 'proposal'})


@pytest.mark.parametrize('minutes', [15, 30])
def test_approval_has_independent_identity_and_bounded_lifetime(minutes):
    now = datetime.now(UTC)
    values = dict(approval_id='a' * 32, plan_hash='b' * 64, sponsor_hash='c' * 64,
                  reviewer_hash='d' * 64, approved_at=now, expires_at=now + timedelta(minutes=minutes))
    assert InvoiceApproval(**values).reviewer_hash != values['sponsor_hash']
    for change in ({'reviewer_hash': values['sponsor_hash']}, {'expires_at': now + timedelta(seconds=1801)}, {'expires_at':now}):
        with pytest.raises(ValidationError):
            InvoiceApproval(**{**values, **change})


def test_plan_is_deeply_immutable():
    plan = InvoicePlan(**plan_values())
    assert isinstance(plan.steps, tuple)
    with pytest.raises(ValidationError):
        plan.steps[0].target = '0' * 32