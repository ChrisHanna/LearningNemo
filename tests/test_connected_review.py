from datetime import UTC, datetime, timedelta
import threading

from fastapi.testclient import TestClient
import pytest

from task_agent.console.app import create_app
from task_agent.console.browser_sessions import CSRF_HEADER
from task_agent.console.remote_review import RemoteReviewService, ReviewUnavailable
from task_agent.console.sessions import AuthenticatedUser, DeviceAuthManager
from test_console import FakeAgentClient, FakeAuthManager, FakeMsalApp, _access_token, _settings
from test_review_repository import ReviewClient


def queue_document():
    store = ReviewClient()
    return {"source": "azure-sql", "plans": [{"plan": store.plan.model_dump(mode="json"),
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(), "canDecide": True, "author_identity_scheme": "entra-tenant-oid-v1"}]}


class ReviewAuth(FakeAuthManager):
    def current_user(self):
        user = super().current_user()
        return AuthenticatedUser(user.persona, user.access_token, user.scopes | {"plans.review"}, user.roles, user.account_fingerprint)


class ReviewService:
    def __init__(self):
        self.decisions = []

    async def list_plans(self, token):
        assert token == "review-token"
        return queue_document()

    async def decide(self, plan_id, decision, token):
        assert token == "review-token"
        self.decisions.append((plan_id, decision))
        return {"planId": plan_id, "planHash": decision.plan_hash,
                "state": "approved" if decision.decision == "approve" else "rejected"}


@pytest.mark.parametrize("enabled", [False, True])
def test_review_scope_is_requested_in_first_signin_only_when_configured(enabled):
    msal = FakeMsalApp(_access_token(("Task.Approver",)), threading.Event())
    manager = DeviceAuthManager(_settings(), app=msal, review_enabled=enabled)
    assert ("plans.review" in manager.snapshot()["expectedScopes"]) is enabled
    captured = []
    def initiate(scopes):
        captured.extend(scopes)
        raise RuntimeError("stop before device flow")
    msal.initiate_device_flow = initiate
    with pytest.raises(RuntimeError, match="stop before"):
        manager.start()
    assert any(scope.endswith("/plans.review") for scope in captured) is enabled


def test_review_scope_is_requested_only_after_approver_signin():
    msal = FakeMsalApp(_access_token(("Task.Approver",)), threading.Event())
    manager = DeviceAuthManager(_settings(), app=msal, review_enabled=True)
    manager._session.flow_id = "fixture"
    manager._poll("fixture", {})
    captured = []
    def initiate(scopes):
        captured.extend(scopes)
        raise RuntimeError("stop before device flow")
    msal.initiate_device_flow = initiate
    with pytest.raises(RuntimeError, match="stop before"):
        manager.start_review()
    assert {scope.rsplit("/", 1)[1] for scope in captured} == {"agent.invoke", "plans.review"}
    assert "plans.review" in manager.clear()["expectedScopes"]


def test_pending_device_flow_expires_and_late_result_cannot_authenticate():
    import time
    msal = FakeMsalApp(_access_token(("Task.Approver",)), threading.Event())
    manager = DeviceAuthManager(_settings(), app=msal, review_enabled=True)
    manager._session.status = 'pending'
    manager._session.flow_id = 'old-flow'
    manager._session.user_code = 'old-code'
    manager._session.expires_at = time.time() - 1
    assert manager.snapshot()['status'] == 'expired'
    manager._poll('old-flow', {})
    assert manager.snapshot()['status'] == 'expired'
    assert manager.snapshot()['userCode'] is None


def test_authorized_approver_does_not_start_second_device_flow():
    from task_agent.console.sessions import AuthSession
    import time
    msal = FakeMsalApp('unused', threading.Event())
    manager = DeviceAuthManager(_settings(), app=msal, review_enabled=True)
    manager._session = AuthSession(status='authenticated', persona='approver', access_token='fixture',
        granted_scopes=('agent.invoke', 'plans.review'), expires_at=time.time() + 600)
    def forbidden(**kwargs): raise AssertionError('second device flow must not start')
    msal.initiate_device_flow = forbidden
    assert manager.start_review()['status'] == 'authenticated'
    assert manager.current_user().access_token == 'fixture'


@pytest.mark.parametrize('roles,status', [(('Task.Approver',), 200), (('Task.Reader',), 403), (('Task.Reader', 'Task.Operator'), 403)])
def test_one_signin_requests_review_but_roles_still_control_queue(monkeypatch, roles, status):
    import base64
    import json
    import task_agent.console.sessions as sessions
    token = _access_token(roles)
    claims = json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '=='))
    claims['scp'] += ' plans.review'
    token = 'header.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.signature'
    msal = FakeMsalApp(token, threading.Event())
    requests = []
    original = msal.initiate_device_flow
    def initiate(scopes):
        requests.append(scopes)
        return original(scopes)
    msal.initiate_device_flow = initiate
    class ImmediateThread:
        def __init__(self, *, target, args, daemon): self.target, self.args = target, args
        def start(self): self.target(*self.args)
    monkeypatch.setattr(sessions.threading, 'Thread', ImmediateThread)
    manager = DeviceAuthManager(_settings(), app=msal, review_enabled=True)
    result = manager.start()
    assert result['status'] == 'authenticated' and 'plans.review' in result['grantedScopes']
    if status == 200:
        assert manager.start_review()['status'] == 'authenticated'
    assert len(requests) == 1
    class Service:
        async def list_plans(self, token): return queue_document()
    client = TestClient(create_app(_settings(), 'https://agent.internal', agent_client=FakeAgentClient(), auth_manager=manager, review_service=Service()))
    assert client.get('/api/approvals').status_code == status


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_connected_console_requires_csrf_and_forwards_exact_decision(decision):
    service = ReviewService()
    agent = FakeAgentClient()
    client = TestClient(create_app(_settings(), "https://agent.internal", agent_client=agent,
        auth_manager=ReviewAuth("review-token", "approver"), review_service=service))
    bootstrap = client.get("/api/bootstrap").json()
    queue = client.get("/api/approvals").json()
    assert queue["availability"] == "connected"
    assert queue["plans"][0]["plan"]["plan_id"] == "plan-review"
    body = {"plan_hash": queue["plans"][0]["plan"]["plan_hash"], "plan_version": 1, "decision": decision}
    assert client.post("/api/approvals/plan-review/decision", json=body).status_code == 403
    assert not service.decisions
    headers = {"Origin": "http://testserver", CSRF_HEADER: bootstrap["csrfToken"]}
    response = client.post("/api/approvals/plan-review/decision", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["state"] == ("approved" if decision == "approve" else "rejected")
    assert len(service.decisions) == 1
    assert not agent.tokens
    assert "review-token" not in response.text


def test_configured_service_does_not_grant_missing_scope():
    service = ReviewService()
    client = TestClient(create_app(_settings(), "https://agent.internal", agent_client=FakeAgentClient(),
        auth_manager=FakeAuthManager("review-token", "approver"), review_service=service))
    bootstrap = client.get("/api/bootstrap").json()
    assert client.get("/api/approvals").status_code == 403
    response = client.post("/api/approvals/plan-review/decision",
        headers={"Origin": "http://testserver", CSRF_HEADER: bootstrap["csrfToken"]},
        json={"plan_hash": "a" * 64, "plan_version": 1, "decision": "approve"})
    assert response.status_code == 403
    assert not service.decisions


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["valid", "unregistered_operation", "missing_plan", "invalid_expiry", "unexpected_field", "wrong_hash", "legacy_identity"])
async def test_remote_queue_validates_reviewable_content(change):
    service = RemoteReviewService("https://review.internal.environment.azurecontainerapps.io")
    document = queue_document()
    entry = document["plans"][0]
    if change == "unregistered_operation": entry["plan"]["content"]["operation_id"] = "execute.arbitrary-sql"
    if change == "missing_plan": del entry["plan"]
    if change == "invalid_expiry": entry["expires_at"] = "not-a-time"
    if change == "unexpected_field": entry["access_token"] = "must-not-reach-browser"
    if change == "wrong_hash": entry["plan"]["plan_hash"] = "0" * 64
    if change == "legacy_identity": entry["author_identity_scheme"] = "azure-cli-approver"
    async def request(*args): return document
    service._request = request
    if change == "valid":
        assert (await service.list_plans("token"))["plans"][0]["plan"]["plan_id"] == "plan-review"
    else:
        with pytest.raises(ReviewUnavailable, match="unverified queue"):
            await service.list_plans("token")