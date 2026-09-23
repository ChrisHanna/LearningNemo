from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from task_agent.console.invoice_observations import add_observation_route
from test_invoice_repository import evidence


def test_observation_requires_exact_workload_identity_and_consistent_revision():
    calls = []
    async def call(name, values):
        calls.append(name)
        return [] if 'batches' in name else [evidence().model_dump()]
    async def verify(token): return SimpleNamespace(active=True, object_id=token)
    app = FastAPI()
    add_observation_route(app,SimpleNamespace(client=SimpleNamespace(call=call)),SimpleNamespace(verify=verify),'operator',datetime.now(UTC)+timedelta(hours=1))
    client=TestClient(app); body={'scenario_id':'a'*32}
    assert client.post('/internal/invoice-observation',json=body).status_code==401
    assert client.post('/internal/invoice-observation',json=body,headers={'Authorization':'Bearer other'}).status_code==403
    assert not calls
    result=client.post('/internal/invoice-observation',json=body,headers={'Authorization':'Bearer operator'})
    assert result.status_code==200 and result.json()['source']=='diagnostic-sql-observation'
    assert len(calls)==3 and all('diagnose' in name for name in calls)