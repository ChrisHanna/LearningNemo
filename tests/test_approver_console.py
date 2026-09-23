import base64
import json
import threading

import pytest
from fastapi.testclient import TestClient
from task_agent.console.app import create_app
from task_agent.console.hosting import ConsoleHosting
from task_agent.console.browser_sessions import CSRF_HEADER
from test_console import FakeAuthManager, FakeAgentClient, FakeWorkspaceTransport, FakeMsalApp, _settings, _access_token
from task_agent.console.sessions import DeviceAuthManager

from task_agent.console.identity import AccessProfileError, validate_signed_in_user


def token_for(roles, scopes="agent.invoke tasks.read tasks.execute"):
    payload = base64.urlsafe_b64encode(json.dumps({"roles": roles, "scp": scopes}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_approver_signin_does_not_require_or_invent_execution_authority():
    profile = validate_signed_in_user(token_for(["Task.Approver"], "agent.invoke"))
    assert profile.persona == "approver"
    assert profile.roles == {"Task.Approver"}
    assert profile.scopes == {"agent.invoke"}


@pytest.mark.parametrize("roles", [["Task.Approver", "Task.Operator"], ["Task.Approver", "Task.Operator", "Task.Reader"], ["Unknown"]])
def test_conflicting_or_unassigned_persona_is_rejected(roles):
    with pytest.raises(AccessProfileError):
        validate_signed_in_user(token_for(roles))


def test_reader_operator_personas_are_unchanged():
    assert validate_signed_in_user(token_for(["Task.Reader"])).persona == "reader"
    assert validate_signed_in_user(token_for(["Task.Reader", "Task.Operator"])).persona == "operator"


def test_device_flow_accepts_approver_and_exposes_no_token():
    token = _access_token(("Task.Approver",))
    manager = DeviceAuthManager(_settings(), app=FakeMsalApp(token, threading.Event()))
    manager._session.flow_id = "approver-flow"
    manager._poll("approver-flow", {})
    session = manager.snapshot()
    assert session["status"] == "authenticated"
    assert session["persona"] == "approver"
    assert session["grantedRoles"] == ["Task.Approver"]
    assert "Task.Approver" in session["acceptedRoles"]
    assert token not in json.dumps(session)


def test_approver_view_assets_and_login_copy():
    client = TestClient(create_app(_settings(), "https://agent.internal", agent_client=FakeAgentClient(), auth_manager=FakeAuthManager()))
    html = client.get("/").text
    assert 'id="approvalsWorkspace"' in html
    assert 'whether the Reader or Operator walkthrough runs' not in html
    assert '>Demo session</button>' in html
    assert 'id="approvalsViewTab"' not in html
    assert 'id="sessionSwitch"' in html
    assert 'No Operator handoff is waiting here.' in html
    script = client.get("/assets/approvals.js")
    assert script.status_code == 200
    assert 'api("/api/approvals")' in script.text
    assert 'innerHTML' not in script.text
    assert '/decision`' in script.text
    assert 'plan_hash: record.plan.plan_hash' in script.text
    assert 'No remediation was executed.' in script.text


@pytest.mark.parametrize("cloud", [False, True])
def test_approver_is_admitted_to_status_not_task_or_workspace_operations(cloud):
    agent = FakeAgentClient()
    workspace = FakeWorkspaceTransport()
    class Verifier:
        async def verify(self, token):
            assert token == "approver-token"
            return {"canReview": True, "canRead": False, "allowed": False}
    options = {"hosting": ConsoleHosting("https://demo.example.test")} if cloud else {"live_workspace": workspace}
    client = TestClient(create_app(
        _settings(), "https://agent.internal", auth_manager=FakeAuthManager("approver-token", "approver"),
        agent_client=agent, workspace_verifier=Verifier(), **options,
    ), base_url="https://demo.example.test" if cloud else "http://testserver")
    bootstrap = client.get("/api/bootstrap")
    assert bootstrap.json()["session"]["persona"] == "approver"
    assert bootstrap.json()["agent"]["status"] == "not_permitted"
    headers = {"Origin": str(client.base_url).rstrip("/"), CSRF_HEADER: bootstrap.json()["csrfToken"]}
    result = client.get("/api/approvals")
    assert result.status_code == 200
    assert result.json()["availability"] == "not-configured"
    assert result.json()["canSubmitDecision"] is False
    assert "approver-token" not in result.text
    assert client.get("/api/showcase").status_code == 200
    assert client.get("/api/walkthrough").status_code == 403
    for path in ("/api/chat", "/api/walkthrough/operator-reset", "/api/walkthrough/reader-baseline", "/api/live-workspace/check", "/api/live-workspace/run"):
        response = client.post(path, headers=headers, json={"prompt": "Execute task-1"})
        assert response.status_code == 403, (path, response.text)
    assert client.post("/api/approvals", headers=headers, json={"approve": True}).status_code in (403, 405)
    assert not agent.tokens and not workspace.runs


@pytest.mark.parametrize("persona,code", [(None, 401), ("reader", 403), ("operator", 403)])
def test_approval_status_rejects_unassigned_sessions(persona, code):
    client = TestClient(create_app(_settings(), "https://agent.internal", agent_client=FakeAgentClient(),
                                  auth_manager=FakeAuthManager("token" if persona else None, persona or "reader")))
    assert client.get("/api/approvals").status_code == code