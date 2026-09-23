from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from task_agent.console.invoice_service import create_invoice_service
from task_agent.control.invoice_jobs import InvoiceJobs
from task_agent.control.sql_backend import SqlProcedureUnavailableError
from test_invoice_service import Verifier


def service(identity, jobs):
    return TestClient(create_invoice_service(mode='operator', repository=None, jobs=jobs,
        identity_verifier=identity, expires_at=None, availability_mode='operator-managed'))


def test_bound_request_requires_operator_scope_and_passes_only_verified_owner():
    calls = []
    async def call(name, values):
        calls.append((name, values))
        return ({'job_id': values['job_id'], 'sandbox_id': values['sandbox_id']},)
    jobs = InvoiceJobs(SimpleNamespace(call=call), None)
    path = '/invoices/jobs/'+'b'*32+'/sandbox-test'
    headers = {'Authorization': 'Bearer fixture'}
    body = {'sandbox_id': 'c'*32}
    assert service(Verifier(), jobs).post(path, headers=headers, json=body).status_code == 403
    assert service(Verifier('approver', ('agent.invoke','plans.review','tasks.execute')), jobs).post(path, headers=headers, json=body).status_code == 403
    client = service(Verifier(scopes=('agent.invoke','tasks.read','tasks.execute')), jobs)
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, headers=headers, json={**body, 'command':'arbitrary'}).status_code == 422
    result = client.post(path, headers=headers, json=body)
    assert result.status_code == 202 and result.json()['state'] == 'requested'
    assert calls == [('control.usp_request_invoice_sandbox_test', {'job_id':'b'*32, 'sponsor_hash':'a'*64, 'sandbox_id':'c'*32})]


async def test_request_lost_response_never_replays():
    calls = []
    async def call(name, values):
        calls.append(name)
        raise SqlProcedureUnavailableError('lost response')
    with pytest.raises(SqlProcedureUnavailableError):
        await InvoiceJobs(SimpleNamespace(call=call), None).request_sandbox_test('b'*32,'a'*64,'c'*32)
    assert len(calls) == 1


def test_bound_test_sql_has_exact_owner_lifecycle_and_once_only_gates():
    from pathlib import Path
    from sqlfluff.core import Linter
    source = (Path(__file__).parents[1]/'infra/next-phase/review-service/014_invoice_bound_sandbox_test.sql').read_text()
    assert not Linter(dialect='tsql').parse_string(source).violations
    for required in ('run.SandboxId = @sandbox_id', 'job.SponsorHash = @sponsor_hash', "job.State = 'running'", 'job.SandboxTestClosed = 0',
                     'run.RevokedAt IS NOT NULL', 'job.SandboxTestReceiptJson IS NULL', 'SandboxTestClosed = 1'):
        assert required in source
    assert 'ops.usp_execute_invoice_step' not in source
    from task_agent.control.invoice_catalog import GRANTS
    for kind in ('diagnostic','inference','broker','review','simulator','verifier'):
        assert all('sandbox_test' not in procedure for procedure in GRANTS[kind])


async def test_status_returns_bound_receipt_without_dispatching_another_probe():
    import json
    calls=[]
    receipt={'run_id':'b'*32,'sandbox_id':'c'*32,'outcome':'denied'}
    async def call(name, values):
        calls.append(name)
        return ({'job_id':'b'*32,'state':'finished','result_json':'{}','sandbox_test_requested':True,'sandbox_test_closed':True,'sandbox_test_json':json.dumps(receipt)},)
    result=await InvoiceJobs(SimpleNamespace(call=call), None).status('b'*32,'a'*64)
    assert result['sandbox_test']==receipt and 'sandbox_test_json' not in result
    assert calls==['control.usp_get_invoice_job']


async def test_proxy_accepts_only_fixed_bound_test_path_and_never_retries(monkeypatch):
    import httpx
    from task_agent.console import remote_invoice
    calls=[]
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self,*args,**kwargs):
            calls.append((args,kwargs))
            raise httpx.ReadTimeout('response lost')
    monkeypatch.setattr(remote_invoice.httpx,'AsyncClient',Client)
    domain='.internal.jollybeach-503c7ed1.eastus.azurecontainerapps.io'
    proxy=remote_invoice.RemoteInvoiceService('https://ca-nemo-invoice-operator-dev'+domain,'https://ca-nemo-invoice-review-dev'+domain)
    with pytest.raises(remote_invoice.InvoiceRemoteError):
        await proxy.request('POST','/invoices/jobs/'+'b'*32+'/sandbox-test','fixture',{'sandbox_id':'c'*32})
    assert len(calls)==1
    with pytest.raises(ValueError): await proxy.request('POST','/invoices/jobs/'+'b'*32+'/exec','fixture',{})


@pytest.mark.parametrize('requested,probe_fails', [(False,False),(True,False),(True,True)])
async def test_controller_revokes_tests_same_sandbox_stops_then_persists(requested, probe_fails):
    import json
    from task_agent.control.invoice_controller import InvoiceController
    order = []
    async def call(name, values):
        order.append(name)
        if name == 'control.usp_revoke_invoice_run': return ({'run_id':'b'*32},)
        if name == 'control.usp_claim_invoice_sandbox_test': return ({'test_requested':requested},)
        assert name == 'control.usp_record_invoice_sandbox_test'
        proof = json.loads(values['receipt_json'])
        assert proof['sandbox_stopped'] is True and proof['sandbox_id']=='c'*32
        return ({'job_id':'b'*32},)
    def stop(kind, run_id): order.append('stop')
    async def probe(runtime, kind, run_id, prepared):
        order.append('probe')
        assert kind=='planning' and run_id=='b'*32 and prepared['sandbox_id']=='c'*32
        if probe_fails: raise RuntimeError('missing proof')
        return {'run_id':run_id,'sandbox_id':prepared['sandbox_id'],'outcome':'denied'}
    async def audit(**values): order.append(values['event']['event_type'])
    controller=InvoiceController(admission_client=SimpleNamespace(call=call),incident_repository=None,verifier_repository=None,
        runtime=SimpleNamespace(stop=stop),audit=audit,sandbox_probe=probe)
    result={'outcome':'proposal'}
    await controller._cleanup('planning','b'*32,'a'*64,{'sandbox_id':'c'*32,'policy_hash':'d'*64},True,result)
    assert order[:3]==['control.usp_revoke_invoice_run','authority-revoked','control.usp_claim_invoice_sandbox_test']
    assert order.index('stop') < order.index('sandbox-stopped')
    if requested:
        assert order.index('probe') < order.index('stop') < order.index('control.usp_record_invoice_sandbox_test')
        assert result['sandbox_test']['outcome']==('unconfirmed' if probe_fails else 'denied')
    else:
        assert 'probe' not in order and 'sandbox_test' not in result


@pytest.mark.parametrize('failure', ['revocation','claim','stop'])
async def test_unconfirmed_control_steps_never_create_false_probe_success(failure):
    from task_agent.control.invoice_controller import InvoiceController
    calls=[]
    async def call(name, values):
        calls.append(name)
        if name == 'control.usp_revoke_invoice_run': return [] if failure=='revocation' else [{'run_id':'b'*32}]
        if name == 'control.usp_claim_invoice_sandbox_test': return [] if failure=='claim' else [{'test_requested':True}]
        pytest.fail('must not persist false cleanup success')
    def stop(*args):
        calls.append('stop')
        if failure=='stop': raise RuntimeError('stop unconfirmed')
    async def audit(**values): pass
    async def probe(*args): calls.append('probe'); return {'outcome':'denied'}
    controller=InvoiceController(admission_client=SimpleNamespace(call=call),incident_repository=None,verifier_repository=None,
        runtime=SimpleNamespace(stop=stop),audit=audit,sandbox_probe=probe)
    with pytest.raises((SqlProcedureUnavailableError,RuntimeError)):
        await controller._cleanup('planning','b'*32,'a'*64,{'sandbox_id':'c'*32,'policy_hash':'d'*64},True,{})
    assert 'stop' in calls
    if failure!='stop': assert 'probe' not in calls