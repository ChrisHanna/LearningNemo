from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from task_agent.console.execution_service import create_execution_app
from task_agent.console.review_service import ReviewIdentity


class Verifier:
    async def verify(self, token):
        if token == 'invalid': raise ValueError('invalid fixture')
        return ReviewIdentity(persona=token, subject_hash='b'*64, scopes={'tasks.execute'} if token != 'no-scope' else set())


class Coordinator:
    def __init__(self): self.calls = []
    async def execute(self, intent, *, sponsor_hash):
        self.calls.append((intent.plan_id, sponsor_hash))
        return {'executionId': 'execution-'+'c'*32, 'state': 'verified', 'completionRequired': True}
    async def complete(self, execution_id, *, sponsor_hash, plan_hash):
        self.calls.append((execution_id, sponsor_hash))
        return {'state': 'completed'}
    async def status(self, plan_id, *, sponsor_hash):
        self.calls.append(('status', plan_id, sponsor_hash))
        return None


@pytest.mark.parametrize('actor,code', [('operator',200),('approver',403),('reader',403),('invalid',401),(None,401)])
def test_only_verified_operator_can_execute_and_complete(actor, code):
    coordinator = Coordinator()
    client = TestClient(create_execution_app(coordinator, coordinator, Verifier(), expires_at=datetime.now(UTC)+timedelta(minutes=10)))
    headers = {'Authorization': 'Bearer '+actor} if actor else {}
    response = client.post('/executions/plan-test', headers=headers, json={'plan_hash':'a'*64,'plan_version':2})
    assert response.status_code == code
    response = client.post('/executions/execution-'+'c'*32+'/complete', headers=headers, json={'plan_hash':'a'*64})
    assert response.status_code == code
    assert len(coordinator.calls) == (2 if code==200 else 0)
    assert response.headers['Cache-Control'] == 'no-store'


def test_browser_cannot_supply_sponsor_or_approval_receipt():
    coordinator = Coordinator()
    client = TestClient(create_execution_app(coordinator, coordinator, Verifier(), expires_at=datetime.now(UTC)+timedelta(minutes=10)))
    for extra in ({'sponsor_hash':'f'*64},{'approval':{}},{'sql':'SELECT 1'}):
        response = client.post('/executions/plan-test', headers={'Authorization':'Bearer operator'}, json={'plan_hash':'a'*64,'plan_version':2,**extra})
        assert response.status_code == 422
    assert not coordinator.calls


def test_expired_execution_service_accepts_no_business_operations():
    coordinator = Coordinator()
    client = TestClient(create_execution_app(coordinator, coordinator, Verifier(), expires_at=datetime(2020,1,1,tzinfo=UTC)))
    assert client.post('/executions/plan-test', headers={'Authorization':'Bearer operator'}, json={'plan_hash':'a'*64,'plan_version':2}).status_code == 503
    assert not coordinator.calls


def test_status_requires_operator_and_never_dispatches_execution():
    coordinator = Coordinator()
    client = TestClient(create_execution_app(coordinator, coordinator, Verifier(), expires_at=datetime.now(UTC)+timedelta(minutes=10)))
    assert client.get('/executions/plan-test', headers={'Authorization': 'Bearer operator'}).json() == {'source': 'azure-sql', 'execution': None}
    assert coordinator.calls == [('status', 'plan-test', 'b'*64)]
    assert client.get('/executions/plan-test', headers={'Authorization': 'Bearer no-scope'}).status_code == 403