from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from task_agent.console.invoice_service import create_invoice_service
from task_agent.control.invoice_contract import InvoicePlan
from task_agent.control.invoice_repository import InvoiceRepository
from task_agent.control.sql_backend import SqlProcedureUnavailableError
from test_invoice_service import Verifier
from test_invoice_contract import plan_values
from test_invoice_repository import evidence


def receipt():
    now = datetime.now(UTC)
    return {'scenario_id': 'b' * 32, 'variant': 'healthy', 'created_at': now.isoformat(),
            'expires_at': (now + timedelta(hours=2)).isoformat(), 'planning_before': (now + timedelta(minutes=110)).isoformat()}


def test_lost_creation_response_is_recovered_by_owner_scoped_read_without_another_create():
    calls, persisted = [], []
    async def call(name, values):
        calls.append((name, values))
        assert values['sponsor_hash'] == 'a' * 64
        if name == 'control.usp_create_owned_invoice_scenario':
            persisted.append(receipt())
            raise SqlProcedureUnavailableError('response lost after commit')
        assert name == 'control.usp_read_invoice_scenario'
        return persisted
    sql = SimpleNamespace(call=call)
    client = TestClient(create_invoice_service(mode='operator', repository=InvoiceRepository(sql), simulator=sql,
        identity_verifier=Verifier(scopes=('agent.invoke', 'tasks.read', 'tasks.execute')), expires_at=None, availability_mode='operator-managed'))
    headers = {'Authorization': 'Bearer fixture'}
    assert client.post('/invoices/scenarios', headers=headers, json={'scenario_id': 'b' * 32, 'variant': 'healthy'}).status_code == 503
    response = client.get('/invoices/scenarios/' + 'b' * 32, headers=headers)
    assert response.status_code == 200 and response.json()['state'] == 'created'
    assert len(calls) == 2
    assert client.get('/invoices/scenarios/' + 'b' * 32).status_code == 401


async def test_missing_owner_record_is_unconfirmed_not_safe_to_replay():
    async def call(name, values): return []
    result = await InvoiceRepository(SimpleNamespace(call=call)).scenario('b' * 32, 'c' * 64)
    assert result['state'] == 'unconfirmed'
    assert 'expires_at' not in result


def test_review_snapshot_must_match_the_immutable_plan_evidence_hash():
    values = plan_values()
    observed = evidence().model_copy(update={'scenario_id': values['scenario_id']})
    plan = InvoicePlan(**{**values, 'evidence_hash': observed.evidence_hash})
    row = {'plan_json': plan.model_dump_json(), 'plan_hash': plan.plan_hash, 'evidence_json': observed.model_dump_json()}
    result = InvoiceRepository._plans([row])[0]
    assert result['diagnostics']['evidence_hash'] == plan.evidence_hash
    assert result['diagnostics']['duplicate_invoices'] == 12
    changed = observed.model_copy(update={'duplicate_invoices': 0})
    with pytest.raises(SqlProcedureUnavailableError, match='binding'):
        InvoiceRepository._plans([{**row, 'evidence_json': changed.model_dump_json()}])


@pytest.mark.parametrize('action', ['submit', 'decide'])
async def test_missing_action_receipt_cannot_be_acknowledged(action):
    async def call(name, values): return []
    repository = InvoiceRepository(SimpleNamespace(call=call))
    with pytest.raises(SqlProcedureUnavailableError, match='receipt unconfirmed'):
        if action == 'submit': await repository.submit('b' * 32, 'c' * 64, 'a' * 64)
        else: await repository.decide('b' * 32, 'c' * 64, 'a' * 64, 'approve')


@pytest.mark.parametrize('action', ['submit', 'decide'])
@pytest.mark.parametrize('container', [tuple, list])
async def test_committed_action_accepts_exact_receipt_from_sql_sequence(action, container):
    calls = []
    receipt = container([{'plan_id': 'b' * 32}])
    async def call(name, values):
        calls.append((name, values))
        return receipt
    repository = InvoiceRepository(SimpleNamespace(call=call))
    result = await repository.submit('b' * 32, 'c' * 64, 'a' * 64) if action == 'submit' else await repository.decide('b' * 32, 'c' * 64, 'a' * 64, 'approve')
    assert result == receipt
    assert len(calls) == 1


@pytest.mark.parametrize('action', ['submit', 'decide'])
@pytest.mark.parametrize('receipt', [({'plan_id': 'c' * 32},), ({'plan_id': 'b' * 32}, {'plan_id': 'b' * 32}), ({'plan_id': 'b' * 32, 'unexpected': True},)])
async def test_action_rejects_mismatched_or_ambiguous_sql_receipts(action, receipt):
    async def call(name, values): return receipt
    repository = InvoiceRepository(SimpleNamespace(call=call))
    with pytest.raises(SqlProcedureUnavailableError, match='receipt unconfirmed'):
        if action == 'submit': await repository.submit('b' * 32, 'c' * 64, 'a' * 64)
        else: await repository.decide('b' * 32, 'c' * 64, 'a' * 64, 'approve')


@pytest.mark.parametrize('mode,action,state', [('operator', 'submit', 'submitted'), ('review', 'decision', 'approve')])
def test_human_action_api_acknowledges_tuple_receipt_without_false_503(mode, action, state):
    calls = []
    async def call(name, values):
        calls.append((name, values))
        return ({'plan_id': values['plan_id']},)
    identity = Verifier() if mode == 'operator' else Verifier('approver', ('agent.invoke', 'plans.review'))
    client = TestClient(create_invoice_service(mode=mode, repository=InvoiceRepository(SimpleNamespace(call=call)),
        identity_verifier=identity, expires_at=None, availability_mode='operator-managed'))
    body = {'plan_hash': 'c' * 64}
    if mode == 'review': body['decision'] = 'approve'
    response = client.post('/invoices/plans/' + 'b' * 32 + '/' + action, headers={'Authorization': 'Bearer fixture'}, json=body)
    assert response.status_code == 200
    assert response.json() == {'plan_id': 'b' * 32, 'state' if mode == 'operator' else 'decision': state}
    assert len(calls) == 1