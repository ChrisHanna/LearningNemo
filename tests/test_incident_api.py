from datetime import UTC, datetime, timedelta
import json

from fastapi.testclient import TestClient
import pytest

from task_agent.console.app import create_app
from task_agent.console.browser_sessions import CSRF_HEADER
from task_agent.console.incident_service import create_incident_app
from task_agent.console.remote_incident import RemoteIncidentService, IncidentUnavailable
from task_agent.console.review_service import create_review_app, ReviewIdentity
from task_agent.control.incident import SqlIncidentRepository
from task_agent.control.review import SqlReviewRepository
from test_console import FakeAgentClient, FakeAuthManager, _settings
from test_incident_handoff import IncidentClient


class HumanVerifier:
    async def verify(self, token):
        if token == "invalid":
            raise ValueError("invalid token")
        return ReviewIdentity(persona=token, subject_hash=IncidentClient().proposal["plan"]["created_by_hash"] if token == "operator" else "f" * 64)


class HandoffStore(IncidentClient):
    def __init__(self):
        super().__init__()
        self.proposal["expires_at"] = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()

    async def call(self, procedure, parameters):
        if procedure == "control.usp_list_review_plans":
            if self.proposal["plan"]["state"] == "draft":
                return ()
            return ({"plan_json": json.dumps(self.proposal["plan"]), "expires_at": self.proposal["expires_at"], "author_identity_scheme": "entra-tenant-oid-v1"},)
        if procedure == "control.usp_decide_review_plan":
            assert parameters["expected_plan_version"] == self.proposal["plan"]["version"]
            self.writes.append(parameters)
            self.proposal["plan"]["state"] = "approved" if parameters["decision"] == "approve" else "rejected"
            self.proposal["plan"]["version"] += 1
            return ({"plan_id": self.proposal["plan"]["plan_id"], "state": self.proposal["plan"]["state"]},)
        result = await super().call(procedure, parameters)
        if procedure == "control.usp_submit_human_plan":
            self.proposal["plan"]["state"] = "awaiting_approval"
            self.proposal["plan"]["version"] += 1
            self.proposal["investigation_version"] += 1
        return result


def test_separate_human_apis_handoff_the_same_persisted_plan():
    store = HandoffStore()
    operator = TestClient(create_incident_app(SqlIncidentRepository(store), HumanVerifier()))
    reviewer = TestClient(create_review_app(SqlReviewRepository(store), HumanVerifier()))
    operator_headers = {"Authorization": "Bearer operator"}
    reviewer_headers = {"Authorization": "Bearer approver"}
    assert reviewer.get("/reviews", headers=reviewer_headers).json()["plans"] == []
    queue = operator.get("/incidents", headers=operator_headers).json()
    proposal = queue["incidents"][0]
    plan = proposal["plan"]
    body = {"plan_hash": plan["plan_hash"], "investigation_version": proposal["investigation_version"]}
    assert operator.post(f"/incidents/{plan['plan_id']}/submit", headers=reviewer_headers, json=body).status_code == 403
    assert not store.writes
    assert operator.post(f"/incidents/{plan['plan_id']}/submit", headers=operator_headers, json=body).json()["state"] == "awaiting_approval"
    assert operator.post(f"/incidents/{plan['plan_id']}/submit", headers=operator_headers, json=body).status_code == 409
    record = reviewer.get("/reviews", headers=reviewer_headers).json()["plans"][0]
    assert record["plan"]["plan_id"] == plan["plan_id"]
    assert record["plan"]["plan_hash"] == plan["plan_hash"]
    decision = {"plan_hash": plan["plan_hash"], "plan_version": record["plan"]["version"], "decision": "approve"}
    assert reviewer.post(f"/reviews/{plan['plan_id']}/decision", headers=operator_headers, json=decision).status_code == 403
    assert reviewer.post(f"/reviews/{plan['plan_id']}/decision", headers=reviewer_headers, json=decision).json()["state"] == "approved"
    assert operator.get("/incidents", headers=operator_headers).json()["incidents"][0]["plan"]["state"] == "approved"
    assert len(store.writes) == 2
    assert operator.post(f"/incidents/{plan['plan_id']}/execute", headers=operator_headers).status_code == 404
    assert operator.post("/incidents", headers=operator_headers, json={}).status_code == 405
    assert operator.post("/investigations/record", headers=operator_headers, json={}).status_code == 404
    assert operator.post(f"/reviews/{plan['plan_id']}/decision", headers=operator_headers, json=decision).status_code == 404


@pytest.mark.parametrize("persona,status", [(None, 401), ("reader", 403), ("approver", 403), ("operator", 200), ("invalid", 401)])
def test_private_incident_queue_requires_operator(persona, status):
    store = HandoffStore()
    client = TestClient(create_incident_app(SqlIncidentRepository(store), HumanVerifier()))
    response = client.get("/incidents", headers={"Authorization": f"Bearer {persona}"} if persona else {})
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"
    assert not store.writes


def test_browser_submission_requires_csrf_and_fixed_payload():
    class Remote:
        def __init__(self): self.writes = []
        async def list_incidents(self, token):
            assert token == "operator-token"
            return {"source": "azure-sql", "incidents": [HandoffStore().proposal]}
        async def submit(self, plan_id, submission, token):
            assert token == "operator-token"
            self.writes.append(submission)
            return {"planId": plan_id, "planHash": submission.plan_hash, "state": "awaiting_approval"}
    remote = Remote()
    agent = FakeAgentClient()
    client = TestClient(create_app(_settings(), "https://agent.internal", agent_client=agent,
        auth_manager=FakeAuthManager("operator-token", "operator"), incident_service=remote))
    bootstrap = client.get("/api/bootstrap").json()
    assert client.get("/api/incidents").json()["availability"] == "connected"
    body = {"plan_hash": "a" * 64, "investigation_version": 1}
    assert client.post("/api/incidents/plan-review/submit", json=body).status_code == 403
    headers = {"Origin": "http://testserver", CSRF_HEADER: bootstrap["csrfToken"]}
    assert client.post("/api/incidents/plan-review/submit", headers=headers, json={**body, "sponsor_hash": "b" * 64}).status_code == 422
    assert not remote.writes
    assert client.post("/api/incidents/plan-review/submit", headers=headers, json=body).status_code == 200
    assert len(remote.writes) == 1
    assert not agent.tokens


@pytest.mark.parametrize("persona,status", [("reader", 403), ("approver", 403), ("operator", 200)])
def test_console_incident_configuration_does_not_expand_roles(persona, status):
    client = TestClient(create_app(_settings(), "https://agent.internal", agent_client=FakeAgentClient(),
        auth_manager=FakeAuthManager("token", persona)))
    response = client.get("/api/incidents")
    assert response.status_code == status
    if status == 200:
        assert response.json()["availability"] == "not-configured"
        assert "incidents" not in response.json()


@pytest.mark.asyncio
async def test_remote_incident_rejects_unverified_evidence_shape():
    service = RemoteIncidentService("https://incident.internal.environment.azurecontainerapps.io")
    proposal = HandoffStore().proposal
    del proposal["diagnosis_receipt_hash"]
    async def request(*args): return {"source": "azure-sql", "incidents": [proposal]}
    service._request = request
    with pytest.raises(IncidentUnavailable, match="unverified investigation"):
        await service.list_incidents("token")


def test_browser_initiation_requires_csrf_and_rejects_caller_identity_payload():
    from uuid import uuid4
    class Remote:
        calls = []
        async def start(self, body, token):
            self.calls.append((body.request_id, token))
            return {'state': 'draft', 'requestId': body.request_id, 'planId': 'plan-' + 'a' * 32, 'replayed': False}
    remote = Remote()
    client = TestClient(create_app(_settings(), 'https://agent.internal', agent_client=FakeAgentClient(),
        auth_manager=FakeAuthManager('operator-token', 'operator'), incident_service=remote))
    bootstrap = client.get('/api/bootstrap').json()
    body = {'request_id': str(uuid4())}
    assert client.post('/api/incidents/start', json=body).status_code == 403
    headers = {'Origin': 'http://testserver', CSRF_HEADER: bootstrap['csrfToken']}
    assert client.post('/api/incidents/start', headers=headers, json={**body, 'sponsor_hash': 'b' * 64}).status_code == 422
    assert not remote.calls
    assert client.post('/api/incidents/start', headers=headers, json=body).status_code == 403
    assert remote.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('status,detail,expected', [(401, 'invalid', 'fresh Operator'), (403, 'denied', 'current account'), (503, 'Incident service lease expired', 'must be renewed')])
async def test_remote_incident_reports_actionable_admission_failure(monkeypatch, status, detail, expected):
    import httpx
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, *args, **kwargs): return httpx.Response(status, json={'detail': detail})
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    service = RemoteIncidentService('https://incident.internal.environment.azurecontainerapps.io')
    with pytest.raises(IncidentUnavailable, match=expected):
        await service.list_incidents('fixture')