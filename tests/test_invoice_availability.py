from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from task_agent.console.invoice_availability import configured_expiry, validate_availability
from task_agent.console.invoice_service import create_invoice_service
from task_agent.console.invoice_gateway import create_invoice_gateway
from task_agent.console.invoice_observations import add_observation_route


def test_unbounded_availability_requires_explicit_mode():
    assert configured_expiry('operator-managed', None) is None
    with pytest.raises(ValueError): configured_expiry('leased', None)
    with pytest.raises(ValueError): configured_expiry('typo', None)
    with pytest.raises(ValueError): configured_expiry('operator-managed', datetime.now(UTC).isoformat())
    with pytest.raises(ValueError): configured_expiry('leased', (datetime.now(UTC)-timedelta(hours=1)).isoformat())
    with pytest.raises(ValueError): validate_availability('leased', datetime.now())


def test_managed_service_keeps_identity_and_scope_checks():
    async def forbidden(*args): raise AssertionError('SQL must not be reached')
    async def verify(token): return SimpleNamespace(persona='operator',scopes={'agent.invoke','tasks.read'})
    app=create_invoice_service(mode='operator',repository=None,identity_verifier=SimpleNamespace(verify=verify),
        expires_at=None,availability_mode='operator-managed',simulator=SimpleNamespace(call=forbidden))
    client=TestClient(app)
    body={'scenario_id':'a'*32,'variant':'healthy'}
    assert client.post('/invoices/scenarios',json=body).status_code==401
    assert client.post('/invoices/scenarios',headers={'Authorization':'Bearer fixture'},json=body).status_code==403


def test_managed_gateway_and_observer_still_require_credentials():
    app=create_invoice_gateway(kind='planning',repository=None,inference_admission=None,model_gateway=None,
        expires_at=None,audit=None,availability_mode='operator-managed')
    client=TestClient(app)
    assert client.post('/v2/invoice/tools/invoice_summary',json={}).status_code==401
    observer=FastAPI()
    add_observation_route(observer,None,None,'operator',None,availability_mode='operator-managed')
    assert TestClient(observer).post('/internal/invoice-observation',json={'scenario_id':'a'*32}).status_code==401