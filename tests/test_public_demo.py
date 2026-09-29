import base64
import json
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from task_agent.console.app import create_app
from task_agent.console.browser_sessions import BrowserSessionRegistry, CSRF_HEADER
from task_agent.console.hosting import ConsoleHosting
from task_agent.console.identity import EntraTestSettings
from task_agent.console.invoice_service import create_invoice_service
from task_agent.console.public_demo_auth import PublicDemoAuthManager
from task_agent.console.review_service import InvoiceIdentityVerifier, ReviewIdentity
from test_console import FakeAgentClient


def settings():
    return EntraTestSettings(
        tenant_id="11111111-1111-4111-8111-111111111111",
        api_client_id="api-client",
        public_client_id="public-client",
    )


class Remote:
    investigation_mode = "openshell"

    def __init__(self):
        self.calls = []

    async def request(self, method, path, token, body=None, **kwargs):
        self.calls.append((method, path, token, body, kwargs))
        if path == "/invoices/plans":
            return {"plans": []}
        return {"job_id": body["job_id"]} if body and "job_id" in body else {}


def public_client():
    remote = Remote()
    registry = BrowserSessionRegistry(settings(), auth_factory=PublicDemoAuthManager)
    app = create_app(
        settings(),
        "https://agent.internal/v1/chat/completions",
        agent_client=FakeAgentClient(),
        session_registry=registry,
        hosting=ConsoleHosting("https://learningnemo.ai"),
        invoice_service=remote,
    )
    client = TestClient(app, base_url="https://learningnemo.ai")
    bootstrap = client.get("/api/bootstrap").json()
    headers = {"Origin": "https://learningnemo.ai", CSRF_HEADER: bootstrap["csrfToken"]}
    return client, headers, remote, bootstrap


def test_public_demo_uses_guest_roles_without_entra_and_blocks_execution():
    client, headers, remote, bootstrap = public_client()
    assert bootstrap["session"]["authMode"] == "public-demo"
    assert bootstrap["session"]["status"] == "signed_out"
    signed_in = client.post("/api/auth/start", headers=headers, json={"persona": "operator"})
    assert signed_in.status_code == 200
    assert signed_in.json()["grantedRoles"] == ["Task.Operator", "Task.Reader"]

    assert client.get("/api/invoices/plans").status_code == 200
    planning = {"job_id": "a" * 32, "kind": "planning", "target_id": "b" * 32}
    assert client.post("/api/invoices/jobs", headers=headers, json=planning).status_code == 202
    challenge_id = "d" * 32
    assert client.post(
        "/api/invoices/challenges",
        headers=headers,
        json={"challenge_id": challenge_id, "kind": "denied-file"},
    ).status_code == 202
    assert client.get(f"/api/invoices/challenges/{challenge_id}").status_code == 200
    execution = {**planning, "job_id": "c" * 32, "kind": "execution", "plan_hash": "d" * 64}
    denied = client.post("/api/invoices/jobs", headers=headers, json=execution)
    assert denied.status_code == 403
    assert len(remote.calls) == 4
    assert all(call[4]["guest_subject"] and call[4]["guest_persona"] == "operator" for call in remote.calls)
    assert client.post(
        "/api/invoices/sandboxes/" + "e" * 32 + "/delete",
        headers=headers,
        json={"sandbox_id": "e" * 32},
    ).status_code == 403


class GuestVerifier:
    async def verify(self, _token, **context):
        assert context == {"guest_subject": "1" * 64, "guest_persona": "operator"}
        return ReviewIdentity(
            subject_hash="a" * 64,
            persona="operator",
            scopes=frozenset({"agent.invoke", "tasks.read", "tasks.execute"}),
            authentication="guest",
        )


def test_private_invoice_service_applies_quota_and_rejects_guest_execution():
    calls = []

    async def admit_guest_launch(job_id, guest_hash):
        calls.append(("quota", job_id, guest_hash))
        return {
            "admitted": True,
            "reason": None,
            "visitor_launches_last_hour": 1,
            "global_launches_today": 1,
            "visitor_limit": 3,
            "global_limit": 25,
        }

    async def enqueue(**values):
        calls.append(("enqueue", values))
        return {"job_id": values["job_id"]}

    async def work(job_id, owner):
        calls.append(("work", job_id, owner))

    async def challenge_enqueue(challenge_id, owner, kind):
        calls.append(("challenge", challenge_id, owner, kind))
        return {"challenge_id": challenge_id}

    async def challenge_work(challenge_id, owner):
        calls.append(("challenge-work", challenge_id, owner))

    async def challenge_status(challenge_id, owner):
        calls.append(("challenge-status", challenge_id, owner))
        return {"challenge_id": challenge_id, "kind": "denied-file", "state": "finished", "result": {}, "events": []}

    repository = SimpleNamespace(admit_guest_launch=admit_guest_launch)
    jobs = SimpleNamespace(enqueue=enqueue, work=work)
    challenges = SimpleNamespace(enqueue=challenge_enqueue, work=challenge_work, status=challenge_status)
    class HumanDemoGate:
        def admitted(self):
            raise AssertionError("guest request must bypass the human demo gate")

        def acquire(self):
            raise AssertionError("guest job must bypass the human demo gate")

    app = create_invoice_service(
        mode="operator",
        repository=repository,
        identity_verifier=GuestVerifier(),
        expires_at=None,
        jobs=jobs,
        challenges=challenges,
        demo_session=HumanDemoGate(),
        availability_mode="operator-managed",
    )
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer broker",
        "X-LearningNeMo-Guest-Subject": "1" * 64,
        "X-LearningNeMo-Guest-Persona": "operator",
    }
    response = client.post(
        "/invoices/jobs",
        headers=headers,
        json={"job_id": "b" * 32, "kind": "planning", "target_id": "c" * 32},
    )
    assert response.status_code == 202
    assert response.json()["quota"]["visitor_limit"] == 3
    assert calls[0] == ("quota", "b" * 32, "a" * 64)
    denied = client.post(
        "/invoices/jobs",
        headers=headers,
        json={"job_id": "d" * 32, "kind": "execution", "target_id": "e" * 32, "plan_hash": "f" * 64},
    )
    assert denied.status_code == 403
    challenge_id = "6" * 32
    challenge = client.post(
        "/invoices/challenges",
        headers=headers,
        json={"challenge_id": challenge_id, "kind": "denied-file"},
    )
    assert challenge.status_code == 202
    assert challenge.json()["quota"]["global_limit"] == 25
    assert ("quota", challenge_id, "a" * 64) in calls
    assert ("challenge", challenge_id, "a" * 64, "denied-file") in calls
    assert client.get(f"/invoices/challenges/{challenge_id}", headers=headers).status_code == 200
    quota_calls = [call for call in calls if call[0] == "quota"]
    assert client.post(
        "/invoices/challenges",
        headers=headers,
        json={"challenge_id": "7" * 32, "kind": "execution"},
    ).status_code == 403
    assert [call for call in calls if call[0] == "quota"] == quota_calls
    status = client.get("/invoices/demo-session", headers=headers)
    assert status.status_code == 200
    assert status.json()["source"] == "public-demo-quota"
    assert status.json()["state"] == "active"
    assert client.post(
        "/invoices/demo-session/start",
        headers=headers,
        json={"session_id": "1" * 32},
    ).status_code == 403


def test_guest_review_reads_only_guest_queue_and_cannot_use_execution_routes():
    calls = []

    class ApproverVerifier:
        async def verify(self, _token, **context):
            assert context["guest_persona"] == "approver"
            return ReviewIdentity(
                subject_hash="9" * 64,
                persona="approver",
                scopes=frozenset({"agent.invoke", "plans.review"}),
                authentication="guest",
            )

    async def reviews(*, guest_only=False):
        calls.append(("reviews", guest_only))
        return []

    async def decide(plan_id, plan_hash, reviewer_hash, decision, *, guest_only=False):
        calls.append(("decide", plan_id, plan_hash, reviewer_hash, decision, guest_only))

    async def forbidden_demo_lease(_token):
        raise AssertionError("guest review must not acquire a human demo lease")

    app = create_invoice_service(
        mode="review",
        repository=SimpleNamespace(reviews=reviews, decide=decide),
        identity_verifier=ApproverVerifier(),
        expires_at=None,
        demo_check=forbidden_demo_lease,
        availability_mode="operator-managed",
    )
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer broker",
        "X-LearningNeMo-Guest-Subject": "2" * 64,
        "X-LearningNeMo-Guest-Persona": "approver",
    }
    assert client.get("/invoices/plans", headers=headers).json() == {"source": "azure-sql", "plans": []}
    response = client.post(
        "/invoices/plans/" + "a" * 32 + "/decision",
        headers=headers,
        json={"plan_hash": "b" * 64, "decision": "approve"},
    )
    assert response.status_code == 200
    assert calls == [
        ("reviews", True),
        ("decide", "a" * 32, "b" * 64, "9" * 64, "approve", True),
    ]
    assert client.post("/invoices/challenges", headers=headers, json={}).status_code == 404


@pytest.mark.asyncio
async def test_guest_broker_requires_exact_workload_role_and_context():
    verifier = InvoiceIdentityVerifier(
        settings(),
        guest_broker_client_id="broker-client",
        guest_broker_object_id="22222222-2222-4222-8222-222222222222",
    )
    verifier.provider.verify = lambda _token: None

    async def verified(_token):
        return SimpleNamespace(
            active=True,
            client_id="broker-client",
            object_id="22222222-2222-4222-8222-222222222222",
            tenant_id=settings().tenant_id,
            iat=time.time() - 45 * 60,
        )

    verifier.provider.verify = verified
    payload = base64.urlsafe_b64encode(
        json.dumps({"roles": ["Invoice.GuestBroker"]}).encode()
    ).decode().rstrip("=")
    token = f"header.{payload}.signature"
    identity = await verifier.verify(
        token,
        guest_subject="3" * 64,
        guest_persona="approver",
    )
    assert identity.authentication == "guest"
    assert identity.persona == "approver"
    assert identity.scopes == frozenset({"agent.invoke", "plans.review"})
    with pytest.raises(ValueError):
        await verifier.verify(token, guest_subject="invalid", guest_persona="operator")
