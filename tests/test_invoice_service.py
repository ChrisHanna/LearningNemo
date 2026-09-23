from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from task_agent.console.invoice_service import create_invoice_service
from task_agent.console.review_service import ReviewIdentity
from task_agent.control.invoice_activity import InvoiceActivityStore


class Verifier:
    def __init__(self, persona='operator', scopes=('agent.invoke', 'tasks.read')):
        self.persona, self.scopes = persona, scopes
    async def verify(self, token):
        return ReviewIdentity(subject_hash='a' * 64, persona=self.persona, scopes=frozenset(self.scopes))


def service(**kwargs):
    return TestClient(create_invoice_service(mode='operator', repository=None, identity_verifier=Verifier(),
        expires_at=datetime.now(UTC) + timedelta(hours=1), **kwargs))


def test_operator_read_scope_can_analyze_but_cannot_execute():
    calls = []
    async def analyze(scenario_id, **identity):
        calls.append((scenario_id, identity))
        return {'outcome': 'no-change'}
    client = service(controller=SimpleNamespace(analyze=analyze))
    headers = {'Authorization': 'Bearer fixture'}
    assert client.post('/invoices/analyze', json={'scenario_id': 'b' * 32}).status_code == 401
    assert client.post('/invoices/analyze', headers=headers, json={'scenario_id': 'b' * 32}).status_code == 200
    assert client.post('/invoices/plans/' + 'c' * 32 + '/execute', headers=headers, json={'plan_hash': 'd' * 64}).status_code == 403
    assert calls == [('b' * 32, {'sponsor_hash': 'a' * 64})]


def test_operator_cannot_supply_identity_or_decide_approval():
    client = service()
    headers = {'Authorization': 'Bearer fixture'}
    assert client.post('/invoices/analyze', headers=headers, json={'scenario_id': 'b' * 32, 'sponsor_hash': 'c' * 64}).status_code == 422
    assert client.post('/invoices/plans/' + 'b' * 32 + '/decision', headers=headers, json={}).status_code == 404


def test_diagnostic_scope_cannot_start_scenario():
    client=service(simulator=SimpleNamespace())
    assert client.post('/invoices/scenarios',headers={'Authorization':'Bearer fixture'},
        json={'scenario_id':'a'*32,'variant':'lost-acknowledgement'}).status_code==403


def test_expired_service_rejects_scenario_before_identity_or_sql():
    async def forbidden(*args, **kwargs):
        raise AssertionError('expired request must not reach identity or SQL')
    client = TestClient(create_invoice_service(mode='operator', repository=None,
        identity_verifier=SimpleNamespace(verify=forbidden), simulator=SimpleNamespace(call=forbidden),
        expires_at=datetime.now(UTC)-timedelta(seconds=1)))
    response = client.post('/invoices/scenarios', headers={'Authorization':'Bearer fixture'},
        json={'scenario_id':'a'*32,'variant':'healthy'})
    assert response.status_code == 503
    assert response.json() == {'detail': 'Invoice service lease expired'}


async def test_proxy_distinguishes_expired_lease_from_unconfirmed_sql(monkeypatch):
    import httpx
    from task_agent.console import remote_invoice
    responses = [httpx.Response(503, json={'detail': 'Invoice service lease expired'}),
        httpx.Response(503, json={'detail': 'Persisted outcome unconfirmed; do not repeat a mutation'}),
        httpx.Response(503, text='upstream unavailable')]
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, *args, **kwargs): return responses.pop(0)
    monkeypatch.setattr(remote_invoice.httpx, 'AsyncClient', Client)
    domain = '.internal.jollybeach-503c7ed1.eastus.azurecontainerapps.io'
    proxy = remote_invoice.RemoteInvoiceService('https://ca-nemo-invoice-operator-dev'+domain,
        'https://ca-nemo-invoice-review-dev'+domain)
    import pytest
    with pytest.raises(remote_invoice.InvoiceRemoteError, match='lease expired.*rejected before execution'):
        await proxy.request('POST', '/invoices/scenarios', 'fixture', {'scenario_id':'a'*32,'variant':'healthy'})
    for _ in range(2):
        with pytest.raises(remote_invoice.InvoiceRemoteError, match='no successful outcome assumed'):
            await proxy.request('POST', '/invoices/scenarios', 'fixture', {'scenario_id':'a'*32,'variant':'healthy'})


def test_reconciliation_requires_operator_execution_scope_and_verified_owner():
    calls = []
    async def reconcile(plan_id, plan_hash, **identity):
        calls.append((plan_id, plan_hash, identity))
        return {'state':'partial','replayed_steps':0}
    endpoint = '/invoices/plans/' + 'b'*32 + '/reconcile'
    headers = {'Authorization':'Bearer fixture'}
    assert service(reconciler=SimpleNamespace(reconcile=reconcile)).post(endpoint,headers=headers,json={'plan_hash':'c'*64}).status_code==403
    client = TestClient(create_invoice_service(mode='operator',repository=None,identity_verifier=Verifier(scopes=('agent.invoke','tasks.read','tasks.execute')),
        expires_at=datetime.now(UTC)+timedelta(hours=1),reconciler=SimpleNamespace(reconcile=reconcile)))
    assert client.post(endpoint,headers=headers,json={'plan_hash':'c'*64}).json()=={'state':'partial','replayed_steps':0}
    assert calls==[('b'*32,'c'*64,{'sponsor_hash':'a'*64})]


def test_approver_has_no_execution_route():
    client = TestClient(create_invoice_service(mode='review', repository=None,
        identity_verifier=Verifier('approver', ('agent.invoke', 'plans.review')), expires_at=datetime.now(UTC) + timedelta(hours=1)))
    assert client.post('/invoices/plans/' + 'b' * 32 + '/execute', headers={'Authorization': 'Bearer fixture'}, json={}).status_code == 404
    assert client.post('/invoices/challenges', headers={'Authorization':'Bearer fixture'}, json={}).status_code == 404


def test_probe_creation_requires_execution_scope():
    client=service(challenges=SimpleNamespace())
    assert client.post('/invoices/challenges',headers={'Authorization':'Bearer fixture'},json={'challenge_id':'a'*32,'kind':'planning'}).status_code == 403


def test_ordinary_agent_job_cannot_claim_a_probe_target():
    client = service(jobs=SimpleNamespace())
    assert client.post('/invoices/jobs',headers={'Authorization':'Bearer fixture'},json={'job_id':'a'*32,'kind':'planning','target_id':'0'*31+'1'}).status_code == 422


def test_evidence_checks_owner_before_reading_database():
    async def plans(owner):
        assert owner=='a'*64
        return []
    async def forbidden(*args): raise AssertionError('must not observe another owner plan')
    client=TestClient(create_invoice_service(mode='operator',repository=SimpleNamespace(plans=plans),identity_verifier=Verifier(),
        expires_at=datetime.now(UTC)+timedelta(hours=1),observer=SimpleNamespace(snapshot=forbidden)))
    assert client.get('/invoices/plans/'+'b'*32+'/evidence',headers={'Authorization':'Bearer fixture'}).status_code==403


def test_durable_job_admission_binds_verified_owner():
    calls = []
    async def enqueue(**parameters):
        calls.append(parameters)
        return {'job_id': parameters['job_id']}
    async def work(job_id, owner):
        calls.append(('work', job_id, owner))
    client = service(jobs=SimpleNamespace(enqueue=enqueue, work=work))
    response = client.post('/invoices/jobs', headers={'Authorization': 'Bearer fixture'},
        json={'job_id': 'b' * 32, 'kind': 'planning', 'target_id': 'c' * 32})
    assert response.status_code == 202 and response.json() == {'job_id': 'b' * 32}
    assert calls[0]['sponsor_hash'] == 'a' * 64
    assert calls[1] == ('work', 'b' * 32, 'a' * 64)


async def test_activity_is_owner_scoped_and_strips_terminal_controls(tmp_path):
    activity = InvoiceActivityStore(tmp_path / 'activity.sqlite')
    await activity.append(sponsor_hash='a' * 64, run_id='b' * 32,
        event={'source': 'agent-runtime', 'event_type': 'tool-returned', 'tool': '\x1b[31mexample', 'capability': 'secret', 'headers': {'authorization': 'secret'}})
    assert activity.read(sponsor_hash='c' * 64, run_id='b' * 32) == []
    rows = activity.read(sponsor_hash='a' * 64, run_id='b' * 32)
    assert len(rows) == 1 and 'secret' not in str(rows) and '\x1b' not in str(rows)
    assert activity.read(sponsor_hash='a' * 64, run_id='b' * 32, after=rows[0]['sequence']) == []