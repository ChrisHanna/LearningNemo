from datetime import UTC, datetime, timedelta

import pytest

from task_agent.console.invoice_demo_session import InvoiceDemoSession
from task_agent.control.operations import OperationDeniedError


def test_idle_status_never_starts_or_touches_sql():
    session = InvoiceDemoSession()
    assert session.status()['state'] == 'idle'
    with pytest.raises(OperationDeniedError): session.acquire()


def test_end_drains_existing_work_and_claims_cleanup_once():
    session = InvoiceDemoSession()
    session.start('a'*32, 'owner')
    session.acquire()
    assert session.end('a'*32, 'owner')['state'] == 'ending'
    with pytest.raises(OperationDeniedError): session.acquire()
    assert session.claim_final_cleanup() is False
    session.release()
    assert session.claim_final_cleanup() is True
    assert session.claim_final_cleanup() is False
    session.finish_cleanup(False)
    assert session.status()['state'] == 'idle'
    assert session.status()['cleanup'] == 'unconfirmed'
    assert session.start('a'*32, 'owner')['state'] == 'idle'


def test_expiry_does_not_extend_on_reads_or_repeated_start():
    now = datetime.now(UTC)
    session = InvoiceDemoSession(clock=lambda: now)
    first = session.start('a'*32, 'owner')
    now += timedelta(hours=3)
    assert session.start('a'*32, 'owner')['expires_at'] == first['expires_at']
    now += timedelta(hours=1)
    assert session.status()['state'] == 'ending'
    with pytest.raises(OperationDeniedError): session.acquire()
    assert InvoiceDemoSession().status()['state'] == 'idle'


def test_only_owner_can_end_exact_session():
    session = InvoiceDemoSession()
    session.start('a'*32, 'owner')
    for identifier, owner in [('b'*32, 'owner'), ('a'*32, 'other')]:
        with pytest.raises(OperationDeniedError): session.end(identifier, owner)


async def test_admitted_background_failure_releases_without_replay():
    session = InvoiceDemoSession()
    session.start('a'*32, 'owner')
    session.acquire()
    calls = []
    async def work():
        calls.append(True)
        raise RuntimeError('uncertain')
    session.end('a'*32, 'owner')
    with pytest.raises(RuntimeError): await session.run_admitted(work)
    assert calls == [True] and session.inflight == 0


async def test_idle_monitor_has_zero_sql_and_end_cleanup_runs_once(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from task_agent.console import invoice_retention_worker as worker
    calls = asyncio.Queue()
    async def cleanup(*args):
        await calls.put(True)
        return {'status':'applied'}
    monkeypatch.setattr(worker,'cleanup_pressure',cleanup)
    controller=SimpleNamespace(lock=asyncio.Lock())
    manager=worker.InvoiceSandboxManager(None,None,controller)
    async with worker.retention_lifespan(None,None,controller,manager=manager)(None):
        yielded=asyncio.Event();asyncio.get_running_loop().call_soon(yielded.set);await yielded.wait()
        assert calls.empty()
        manager.demo.start('a'*32,'owner')
        await asyncio.wait_for(calls.get(),1)
        manager.demo.acquire()
        manager.demo.end('a'*32,'owner')
        assert manager.demo.status()['state']=='ending'
        manager.demo.release()
        await asyncio.wait_for(calls.get(),1)
    assert calls.empty() and manager.demo.status()['state']=='idle'


def test_operator_idle_routes_never_reach_sql_and_approver_can_read_status():
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from task_agent.console.invoice_service import create_invoice_service
    from test_invoice_service import Verifier
    calls=[]
    async def plans(owner):calls.append(owner);return []
    session=InvoiceDemoSession()
    client=TestClient(create_invoice_service(mode='operator',repository=SimpleNamespace(plans=plans),identity_verifier=Verifier(scopes=('agent.invoke','tasks.read','tasks.execute')),expires_at=None,availability_mode='operator-managed',demo_session=session))
    headers={'Authorization':'Bearer fixture'}
    assert client.get('/invoices/plans',headers=headers).status_code==409
    assert calls==[]
    assert client.get('/invoices/demo-session',headers=headers).json()['state']=='idle'
    assert client.post('/invoices/demo-session/start',headers=headers,json={'session_id':'a'*32,'sql':'anything'}).status_code==422
    assert client.post('/invoices/demo-session/start',headers=headers,json={'session_id':'a'*32}).status_code==200
    assert client.get('/invoices/plans',headers=headers).status_code==200 and len(calls)==1
    assert client.post('/invoices/demo-session/end',headers=headers,json={'session_id':'a'*32}).status_code==200
    assert client.get('/invoices/plans',headers=headers).status_code==409 and len(calls)==1
    reviewer=TestClient(create_invoice_service(mode='operator',repository=None,identity_verifier=Verifier('approver',('agent.invoke','plans.review')),expires_at=None,availability_mode='operator-managed',demo_session=session))
    assert reviewer.get('/invoices/demo-session',headers=headers).status_code==200
    assert reviewer.post('/invoices/demo-session/start',headers=headers,json={'session_id':'b'*32}).status_code==403


@pytest.mark.parametrize('state',['idle','ending','active'])
async def test_review_requires_shared_active_demo_before_sql(state):
    from types import SimpleNamespace
    import httpx
    from task_agent.console.invoice_demo_session import acquire_remote_demo
    calls=[]
    async def post(path,**kwargs):
        calls.append(path)
        return httpx.Response(200 if state=='active' else 409,json={'lease_id':kwargs['json']['session_id']})
    if state=='active':await acquire_remote_demo(SimpleNamespace(post=post),'fixture')
    else:
        with pytest.raises(OperationDeniedError):await acquire_remote_demo(SimpleNamespace(post=post),'fixture')
    assert calls==['https://ca-nemo-invoice-operator-dev.internal.jollybeach-503c7ed1.eastus.azurecontainerapps.io/invoices/demo-session/lease']


def test_end_waits_for_review_and_release_is_owner_bound_and_idempotent():
    session=InvoiceDemoSession()
    session.start('a'*32,'operator')
    session.acquire_review('b'*32,'reviewer')
    session.end('a'*32,'operator')
    assert session.claim_final_cleanup() is False
    with pytest.raises(OperationDeniedError):session.release_review('b'*32,'other')
    session.release_review('b'*32,'reviewer');session.release_review('b'*32,'reviewer')
    assert session.inflight==0 and session.claim_final_cleanup() is True
    session.finish_cleanup(True)
    session.start('c'*32,'operator');session.end('c'*32,'operator');session.finish_cleanup(True)
    with pytest.raises(OperationDeniedError):session.start('a'*32,'operator')


async def test_review_lease_is_not_retried_on_lost_response():
    import httpx
    from types import SimpleNamespace
    from task_agent.console.invoice_demo_session import acquire_remote_demo
    calls=[]
    async def post(path,**kwargs):calls.append(path);raise httpx.ReadTimeout('lost response')
    with pytest.raises(httpx.ReadTimeout):await acquire_remote_demo(SimpleNamespace(post=post),'fixture')
    assert len(calls)==1


async def test_failed_final_cleanup_leaves_idle_without_retry(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from task_agent.console import invoice_retention_worker as worker
    calls=[];attempted=asyncio.Event()
    async def cleanup(*args):
        calls.append(True);attempted.set();raise RuntimeError('SQL unavailable')
    monkeypatch.setattr(worker,'cleanup_pressure',cleanup)
    controller=SimpleNamespace(lock=asyncio.Lock())
    manager=worker.InvoiceSandboxManager(None,None,controller)
    manager.demo.start('a'*32,'owner');manager.demo.end('a'*32,'owner')
    async with worker.retention_lifespan(None,None,controller,manager=manager)(None):
        await asyncio.wait_for(attempted.wait(),1)
    assert calls==[True] and manager.demo.status()['state']=='idle'
    assert manager.demo.status()['cleanup']=='unconfirmed'


def test_background_job_admitted_before_end_is_not_cancelled():
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from task_agent.console.invoice_service import create_invoice_service
    from test_invoice_service import Verifier
    session=InvoiceDemoSession();session.start('a'*32,'a'*64)
    calls=[]
    async def enqueue(**values):return {'job_id':values['job_id']}
    async def work(*args):
        session.end('a'*32,'a'*64)
        assert session.inflight>0 and not session.claim_final_cleanup()
        calls.append(args)
    client=TestClient(create_invoice_service(mode='operator',repository=None,identity_verifier=Verifier(scopes=('agent.invoke','tasks.read','tasks.execute')),expires_at=None,availability_mode='operator-managed',demo_session=session,jobs=SimpleNamespace(enqueue=enqueue,work=work)))
    response=client.post('/invoices/jobs',headers={'Authorization':'Bearer fixture'},json={'job_id':'b'*32,'kind':'planning','target_id':'c'*32})
    assert response.status_code==202 and len(calls)==1
    assert session.inflight==0 and session.claim_final_cleanup()


def test_health_reports_demo_state_without_identity_or_sql():
    from fastapi.testclient import TestClient
    from task_agent.console.invoice_service import create_invoice_service
    session=InvoiceDemoSession()
    client=TestClient(create_invoice_service(mode='operator',repository=None,identity_verifier=None,
        expires_at=None,availability_mode='operator-managed',demo_session=session))
    assert client.get('/healthz').json()=={'status':'ok','mode':'operator','demo_state':'idle'}
    session.start('a'*32,'operator')
    assert client.get('/healthz').json()['demo_state']=='active'
    session.end('a'*32,'operator')
    assert client.get('/healthz').json()['demo_state']=='ending'