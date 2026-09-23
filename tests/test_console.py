from __future__ import annotations

import base64
import json
import threading
import time
from typing import Any

from fastapi.testclient import TestClient

from task_agent.console.agent_client import AgentReply
from task_agent.console.agent_client import AgentClientError
from task_agent.console.app import create_app
from task_agent.console.browser_sessions import BrowserSessionRegistry
from task_agent.console.browser_sessions import CSRF_HEADER
from task_agent.console.identity import EntraTestSettings
from task_agent.console.hosting import ConsoleHosting
from task_agent.console.sessions import AuthenticatedUser
from task_agent.console.sessions import DeviceAuthManager


def _access_token(
    roles: tuple[str, ...],
    account_id: str = "test-account",
) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "scp": "agent.invoke tasks.read tasks.execute",
                "roles": list(roles),
                "exp": time.time() + 3600,
                "oid": account_id,
            }
        ).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.signature"


class FakeMsalApp:
    def __init__(self, token: str, completed: threading.Event) -> None:
        self._token = token
        self._completed = completed

    def initiate_device_flow(self, scopes: list[str]) -> dict[str, Any]:
        assert scopes
        return {
            "user_code": "TEST-CODE",
            "verification_uri": "https://example.test/device",
            "expires_at": time.time() + 900,
        }

    def acquire_token_by_device_flow(self, _flow: dict[str, Any]) -> dict[str, str]:
        self._completed.set()
        return {"access_token": self._token}


class FakeAuthManager:
    def __init__(self, token: str | None = None, persona: str = "reader") -> None:
        self._token = token
        self._persona = persona

    def _session(self) -> dict[str, Any]:
        authenticated = self._token is not None
        roles = {"reader": ["Task.Reader"], "operator": ["Task.Reader", "Task.Operator"], "approver": ["Task.Approver"]}[self._persona]
        return {
            "status": "authenticated" if authenticated else "signed_out",
            "persona": self._persona if authenticated else None,
            "expectedScopes": ["agent.invoke", "tasks.read", "tasks.execute"],
            "acceptedRoles": ["Task.Reader", "Task.Operator"],
            "grantedScopes": ["agent.invoke", "tasks.read", "tasks.execute"] if authenticated else [],
            "grantedRoles": roles if authenticated else [],
            "accountFingerprint": "TEST1234" if authenticated else None,
            "userCode": None,
            "verificationUri": None,
            "expiresAt": time.time() + 3600 if authenticated else None,
            "error": None,
        }

    def snapshot(self) -> dict[str, Any]:
        return self._session()

    def start(self) -> dict[str, Any]:
        return self._session()

    def clear(self) -> dict[str, Any]:
        self._token = None
        return self._session()

    def current_user(self) -> AuthenticatedUser:
        if self._token is None:
            raise RuntimeError("Sign in before calling the agent")
        roles = frozenset({"Task.Reader"})
        if self._persona == "operator":
            roles = roles | {"Task.Operator"}
        if self._persona == "approver":
            roles = frozenset({"Task.Approver"})
        return AuthenticatedUser(
            persona=self._persona,
            access_token=self._token,
            scopes=frozenset({"agent.invoke", "tasks.read", "tasks.execute"}),
            roles=roles,
            account_fingerprint="TEST1234",
        )


class FakeAgentClient:
    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.prompts: list[str] = []

    async def health(self) -> dict[str, Any]:
        return {"status": "online", "httpStatus": 200, "durationMs": 4}

    async def chat(self, access_token: str, prompt: str) -> AgentReply:
        self.tokens.append(access_token)
        self.prompts.append(prompt)
        return AgentReply(content=f"Response to: {prompt}", status_code=200, duration_ms=12)


class FailingAgentClient(FakeAgentClient):
    async def chat(self, access_token: str, prompt: str) -> AgentReply:
        raise AgentClientError(502, "sensitive upstream stack and endpoint details")


class DeniedAgentClient(FakeAgentClient):
    async def chat(self, access_token: str, prompt: str) -> AgentReply:
        raise AgentClientError(403, "raw NeMo workflow exception", duration_ms=17)


def _settings() -> EntraTestSettings:
    return EntraTestSettings(
        tenant_id="tenant",
        api_client_id="api-client",
        public_client_id="learningnemo-client",
    )


def _secured_client(app) -> tuple[TestClient, dict[str, str]]:
    client = TestClient(app, base_url="http://testserver")
    bootstrap = client.get("/api/bootstrap")
    assert bootstrap.status_code == 200
    return client, {
        "Origin": "http://testserver",
        CSRF_HEADER: bootstrap.json()["csrfToken"],
    }


def test_device_auth_exposes_scopes_but_not_token() -> None:
    completed = threading.Event()
    access_token = _access_token(("Task.Reader",))
    app = FakeMsalApp(access_token, completed)
    manager = DeviceAuthManager(
        _settings(),
        app=app,
    )

    manager.start()
    assert completed.wait(timeout=1)
    session = manager.snapshot()

    assert session["status"] == "authenticated"
    assert session["grantedScopes"] == ["agent.invoke", "tasks.execute", "tasks.read"]
    assert session["grantedRoles"] == ["Task.Reader"]
    assert len(session["accountFingerprint"]) == 8
    assert "test-account" not in json.dumps(session)
    assert access_token not in json.dumps(session)
    assert session["persona"] == "reader"
    assert manager.current_user().access_token == access_token


def test_device_auth_derives_operator_from_roles() -> None:
    completed = threading.Event()
    manager = DeviceAuthManager(
        _settings(),
        app=FakeMsalApp(_access_token(("Task.Reader", "Task.Operator")), completed),
    )

    manager.start()
    assert completed.wait(timeout=1)

    assert manager.snapshot()["persona"] == "operator"
    assert manager.current_user().roles == frozenset({"Task.Reader", "Task.Operator"})


def test_browser_storage_key_survives_manager_recreation_but_is_not_a_raw_identity():
    keys = []
    for _ in range(2):
        manager = DeviceAuthManager(_settings(), app=FakeMsalApp(_access_token(("Task.Reader", "Task.Operator")), threading.Event()))
        manager._session.flow_id = 'storage-test'
        manager._poll('storage-test', {})
        session = manager.snapshot()
        assert session['status'] == 'authenticated'
        keys.append(session['storageKey'])
        assert len(keys[-1]) == 64 and 'test-account' not in keys[-1]
        assert manager.clear()['storageKey'] is None
    assert keys[0] == keys[1]


def test_device_auth_sanitizes_profile_rejection() -> None:
    completed = threading.Event()
    access_token = _access_token(("Unassigned.Role",))
    manager = DeviceAuthManager(
        _settings(),
        app=FakeMsalApp(access_token, completed),
    )

    manager.start()
    assert completed.wait(timeout=1)
    deadline = time.monotonic() + 1
    while (session := manager.snapshot())["status"] == "pending" and time.monotonic() < deadline:
        threading.Event().wait(0.01)

    assert session["status"] == "error"
    assert session["error"] == "This account is not authorized for LearningNeMo. Use an assigned Reader, Operator, or separate Approver account."
    assert access_token not in json.dumps(session)


def test_bootstrap_returns_health_and_safe_role_metadata() -> None:
    app = create_app(
        _settings(),
        "http://agent.test/v1/chat/completions",
        auth_manager=FakeAuthManager("server-side-token"),
        agent_client=FakeAgentClient(),
    )

    response = TestClient(app).get("/api/bootstrap")

    assert response.status_code == 200
    assert response.json()["agent"]["status"] == "online"
    assert "server-side-token" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]


def test_console_shell_and_assets_are_served() -> None:
    app = create_app(
        _settings(),
        "http://agent.test/v1/chat/completions",
        auth_manager=FakeAuthManager(),
        agent_client=FakeAgentClient(),
    )
    client = TestClient(app)

    index_response = client.get("/")
    script_response = client.get("/assets/learningnemo.js")
    showcase_script = client.get("/assets/showcase.js")
    showcase_style = client.get("/assets/showcase.css")
    pattern_script = client.get("/assets/pattern.js")
    pattern_style = client.get("/assets/pattern.css")
    lucide_response = client.get("/assets/vendor/lucide.min.js")
    license_response = client.get("/assets/vendor/lucide.LICENSE")
    style_response = client.get("/assets/styles.css")

    assert index_response.status_code == 200
    assert "LearningNeMo" in index_response.text
    assert script_response.status_code == 200
    assert "runScenario" in script_response.text
    assert showcase_script.status_code == 200
    assert showcase_style.status_code == 200
    assert pattern_script.status_code == 200
    assert pattern_style.status_code == 200
    assert 'id="patternWorkspace"' in index_response.text
    assert "Target permission design / not your signed-in role" in index_response.text
    assert 'fetch(' not in pattern_script.text
    assert 'api(' not in pattern_script.text
    assert 'innerHTML' not in pattern_script.text
    assert "showcaseWorkspace" in index_response.text
    assert "identityWorkspace" in index_response.text
    assert "buildWorkspace" in index_response.text
    assert "innerHTML" not in showcase_script.text
    assert "recorded-summary" in showcase_script.text
    assert lucide_response.status_code == 200
    assert license_response.status_code == 200
    assert "unpkg.com" not in index_response.text
    assert "fonts.googleapis.com" not in index_response.text
    assert style_response.status_code == 200
    assert ".workspace" in style_response.text


def test_showcase_is_dated_read_only_and_does_not_claim_live_health() -> None:
    agent = FakeAgentClient()
    client = TestClient(create_app(
        _settings(), "http://agent.test", agent_client=agent, auth_manager=FakeAuthManager(),
    ))
    response = client.get("/api/showcase")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "recorded-summary"
    assert payload["currentHealth"] == "unknown"
    assert payload["recordedOn"] == "2026-09-14"
    assert {item["id"] for item in payload["sandboxes"]} == {"planning", "execution", "probe"}
    assert payload["milestones"][-1]["state"] == "failed"
    assert payload["journey"][-1]["status"] == "Pending"
    assert agent.tokens == [] and agent.prompts == []
    assert response.headers["cache-control"] == "no-store"
    for forbidden in ("/subscriptions/", "access_token", "tls.key", "client_secret"):
        assert forbidden not in response.text
    for document in ("build", "demo", "diagnostics"):
        result = client.get(f"/api/showcase/documents/{document}")
        assert result.status_code == 200
        assert result.headers["content-type"].startswith("text/plain")
    assert client.get("/api/showcase/documents/private-state").status_code == 404
    assert client.get("/api/showcase/documents/%2e%2e%2fREADME.md").status_code == 404
    for sandbox in payload["sandboxes"]:
        assert len(sandbox["checks"]) == 3
        assert all(check["expected"] == check["observed"] for check in sandbox["checks"])
    assert len({item["id"] for item in payload["decisions"]}) == len(payload["decisions"])


class FakeWorkspaceTransport:
    enabled = True

    def __init__(self):
        self.runs = []
        self.after_run = None

    def check(self):
        return {"source": "live-azure-query", "readyForProbe": False, "gateway": "not-checked"}

    def run(self, run_id):
        self.runs.append(run_id)
        if self.after_run:
            self.after_run()
        return {"status": "blocked", "detail": "Lease expired", "cloud": self.check()}


class FakeWorkspaceVerifier:
    def __init__(self, *, allowed=True, invalid=False):
        self.allowed = allowed
        self.invalid = invalid
        self.tokens = []

    async def verify(self, token):
        self.tokens.append(token)
        if self.invalid:
            raise ValueError("sensitive verifier detail")
        return {"allowed": self.allowed, "requiredRoles": ["Task.Operator"]}


def test_live_workspace_denies_signed_out_csrf_and_verified_reader():
    transport = FakeWorkspaceTransport()
    verifier = FakeWorkspaceVerifier(allowed=False)
    auth = FakeAuthManager()
    client, headers = _secured_client(create_app(
        _settings(), "http://agent.test", auth_manager=auth, agent_client=FakeAgentClient(),
        live_workspace=transport, workspace_verifier=verifier,
    ))
    assert client.post("/api/live-workspace/run").status_code == 403
    assert client.post("/api/live-workspace/run", headers=headers).status_code == 401
    assert transport.runs == [] and verifier.tokens == []
    auth._token = "reader-token"
    response = client.post("/api/live-workspace/run", headers=headers)
    assert response.status_code == 403
    assert response.json()["workspaceExecuted"] is False
    assert verifier.tokens == ["reader-token"] and transport.runs == []


def test_live_workspace_requires_verified_claims_even_for_operator_session():
    transport = FakeWorkspaceTransport()
    client, headers = _secured_client(create_app(
        _settings(), "http://agent.test", auth_manager=FakeAuthManager("token", "operator"),
        agent_client=FakeAgentClient(), live_workspace=transport,
        workspace_verifier=FakeWorkspaceVerifier(invalid=True),
    ))
    response = client.post("/api/live-workspace/run", headers=headers)
    assert response.status_code == 401
    assert "sensitive verifier detail" not in response.text
    assert transport.runs == []


def test_live_workspace_receipt_is_request_bound_and_cleared_on_session_change():
    transport = FakeWorkspaceTransport()
    auth = FakeAuthManager("operator-secret", "operator")
    client, headers = _secured_client(create_app(
        _settings(), "http://agent.test", auth_manager=auth, agent_client=FakeAgentClient(),
        live_workspace=transport, workspace_verifier=FakeWorkspaceVerifier(),
    ))
    response = client.post("/api/live-workspace/run", headers=headers, json={"command": "ignored"})
    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert response.json()["runId"] == transport.runs[0]
    assert response.json()["source"] == "live-request"
    assert "operator-secret" not in response.text
    transport.after_run = auth.clear
    assert client.post("/api/live-workspace/run", headers=headers).status_code == 401


def test_live_workspace_disabled_by_default_and_status_is_explicit():
    client, headers = _secured_client(create_app(
        _settings(), "http://agent.test", auth_manager=FakeAuthManager("token"), agent_client=FakeAgentClient(),
    ))
    assert client.get("/api/live-workspace").json()["enabled"] is False
    assert client.post("/api/live-workspace/check", headers=headers).status_code == 503
    assert client.post("/api/live-workspace/run", headers=headers).status_code == 503
    assert client.get("/assets/live-workspace.js").status_code == 200
    assert 'id="liveViewTab"' in client.get("/").text


def test_cloud_console_pins_https_origin_and_secure_cookie():
    app = create_app(
        _settings(), "http://agent.internal", auth_manager=FakeAuthManager(),
        agent_client=FakeAgentClient(), hosting=ConsoleHosting("https://demo.example.test"),
    )
    client = TestClient(app, base_url="https://demo.example.test")
    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    assert response.json()["hosting"] == "azure"
    assert "Secure" in response.headers["set-cookie"]
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    csrf = response.json()["csrfToken"]
    for origin in ("http://demo.example.test", "https://other.example.test"):
        assert client.post("/api/auth/start", headers={"Origin": origin, CSRF_HEADER: csrf}).status_code == 403
    assert client.post("/api/auth/start", headers={"Origin": "https://demo.example.test", CSRF_HEADER: csrf}).status_code == 200
    assert client.get("/", headers={"Host": "other.example.test"}).status_code == 400


def test_cloud_console_refuses_operator_transport():
    import pytest
    with pytest.raises(ValueError, match="operator workspace transport"):
        create_app(
            _settings(), "http://agent.internal", live_workspace=FakeWorkspaceTransport(),
            hosting=ConsoleHosting("https://demo.example.test"),
        )
    for origin in ("http://demo.example.test", "https://localhost", "https://demo.example.test/path", "https://*.example.test"):
        with pytest.raises(ValueError):
            ConsoleHosting(origin)


def test_cloud_console_requires_verified_identity_for_records_and_operations():
    class Verifier:
        async def verify(self, token):
            assert token == "reader-token"
            return {"canRead": True, "allowed": False}
    auth = FakeAuthManager()
    client = TestClient(create_app(
        _settings(), "https://agent.internal", auth_manager=auth, agent_client=FakeAgentClient(),
        hosting=ConsoleHosting("https://demo.example.test"), workspace_verifier=Verifier(),
    ), base_url="https://demo.example.test")
    assert client.get("/healthz", headers={"Host": "probe.internal"}).status_code == 200
    bootstrap = client.get("/api/bootstrap")
    assert bootstrap.json()["agent"]["status"] == "not_authenticated"
    assert client.get("/api/showcase").status_code == 401
    auth._token = "reader-token"
    assert client.get("/api/showcase").status_code == 200


def test_capability_inventory_describes_security_without_secrets() -> None:
    app = create_app(
        _settings(),
        "http://agent.test/v1/chat/completions",
        auth_manager=FakeAuthManager(),
        agent_client=FakeAgentClient(),
    )

    response = TestClient(app).get("/api/capabilities")

    assert response.status_code == 200
    payload = response.json()
    capability_ids = {item["id"] for item in payload["capabilities"]}
    assert {"entra-authentication", "input-guardrails", "tool-authorization", "llm-gateway", "key-vault"} <= capability_ids
    assert {tool["name"] for tool in payload["tools"]} == {
        "current_datetime",
        "list_tasks",
        "execute_task",
        "reset_tasks",
    }
    assert [rail["name"] for rail in payload["guardrails"]] == [
        "PII masking",
        "Semantic prompt-injection check",
    ]
    assert [rail["remoteCall"] for rail in payload["guardrails"]] == [False, True]
    assert payload["identityModel"]["client"]["name"] == "LearningNeMo Local Client"
    assert payload["identityModel"]["profiles"][0]["roles"] == ["Task.Reader"]
    serialized = response.text.lower()
    assert "openai_api_key" not in serialized
    assert "access_token" not in serialized
    assert "bearer ey" not in serialized


def test_chat_requires_authentication() -> None:
    app = create_app(
        _settings(),
        "http://agent.test/v1/chat/completions",
        auth_manager=FakeAuthManager(),
        agent_client=FakeAgentClient(),
    )

    client, headers = _secured_client(app)
    response = client.post("/api/chat", json={"prompt": "List tasks"}, headers=headers)

    assert response.status_code == 401
    assert "Sign in" in response.json()["detail"]


def test_chat_proxies_response_without_exposing_token() -> None:
    agent = FakeAgentClient()
    app = create_app(
        _settings(),
        "http://agent.test/v1/chat/completions",
        auth_manager=FakeAuthManager("server-side-token"),
        agent_client=agent,
    )

    client, headers = _secured_client(app)
    response = client.post("/api/chat", json={"prompt": "List tasks"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["content"] == "Response to: List tasks"
    assert response.json()["durationMs"] == 12
    evidence = response.json()["evidence"]
    assert [item["level"] for item in evidence].count("observed") == 4
    assert [item["level"] for item in evidence].count("configured") == 3
    assert "APIM semantic check" in next(item["detail"] for item in evidence if item["name"] == "Input Guardrails")
    assert "server-side-token" not in response.text
    assert agent.tokens == ["server-side-token"]


def test_agent_failure_is_sanitized_for_browser() -> None:
    app = create_app(
        _settings(),
        "http://agent.test/v1/chat/completions",
        auth_manager=FakeAuthManager("server-side-token"),
        agent_client=FailingAgentClient(),
    )
    client, headers = _secured_client(app)

    response = client.post("/api/chat", json={"prompt": "List tasks"}, headers=headers)

    assert response.status_code == 502
    assert response.json()["detail"] == "The protected agent request failed"
    assert "sensitive upstream" not in response.text


def test_reader_walkthrough_plan_explains_missing_operator_role() -> None:
    client, _headers = _secured_client(
        create_app(
            _settings(),
            "http://agent.test/v1/chat/completions",
            auth_manager=FakeAuthManager("reader-token", persona="reader"),
            agent_client=FakeAgentClient(),
        )
    )

    response = client.get("/api/walkthrough")

    assert response.status_code == 200
    assert response.json()["persona"] == "reader"
    assert [step["id"] for step in response.json()["steps"]] == [
        "reader-baseline",
        "reader-denied-execute",
        "reader-verify-unchanged",
    ]
    decision = response.json()["steps"][1]["accessDecision"]
    assert decision["allowed"] is False
    assert decision["missing"]["scopes"] == []
    assert decision["missing"]["roles"] == ["Task.Operator"]


def test_walkthrough_endpoints_bind_steps_to_current_user() -> None:
    agent = FakeAgentClient()
    client, headers = _secured_client(
        create_app(
            _settings(),
            "http://agent.test/v1/chat/completions",
            auth_manager=FakeAuthManager("reader-server-token", persona="reader"),
            agent_client=agent,
        )
    )

    reader_response = client.post(
        "/api/walkthrough/reader-denied-execute",
        json={"role": "operator", "prompt": "Reset all tasks"},
        headers=headers,
    )
    operator_response = client.post("/api/walkthrough/operator-execute", headers=headers)

    assert reader_response.status_code == 200
    assert reader_response.json()["persona"] == "reader"
    assert reader_response.json()["prompt"] == "Execute task-1."
    assert reader_response.json()["accessDecision"]["allowed"] is False
    assert operator_response.status_code == 403
    assert operator_response.json()["detail"] == "Access denied"
    assert "operator" not in operator_response.text
    assert "reader" not in operator_response.text
    assert agent.tokens == ["reader-server-token"]
    assert agent.prompts == ["Execute task-1."]


def test_expected_tool_denial_maps_to_structured_403_without_model_narration() -> None:
    client, headers = _secured_client(
        create_app(
            _settings(),
            "http://agent.test/v1/chat/completions",
            auth_manager=FakeAuthManager("reader-token", persona="reader"),
            agent_client=DeniedAgentClient(),
        )
    )

    response = client.post("/api/walkthrough/reader-denied-execute", headers=headers)

    assert response.status_code == 403
    assert response.json()["httpStatus"] == 403
    assert response.json()["content"] == "Denied before tool execution by the configured access policy."
    assert response.json()["accessDecision"]["missing"]["roles"] == ["Task.Operator"]
    assert "raw NeMo" not in response.text


def test_422_is_not_masked_as_an_expected_reader_denial(caplog):
    class InvalidWorkflow(FakeAgentClient):
        async def chat(self, token, prompt):
            raise AgentClientError(422, "private upstream detail", 17, "a" * 32, "request_validation")
    client, headers = _secured_client(create_app(_settings(), "http://agent.test/v1/chat/completions",
        auth_manager=FakeAuthManager("reader-token", persona="reader"), agent_client=InvalidWorkflow()))
    response = client.post("/api/walkthrough/reader-denied-execute", headers=headers)
    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == "a" * 32
    assert "accessDecision" not in response.json()
    assert "private upstream detail" not in response.text and "private upstream detail" not in caplog.text
    assert "category=request_validation" in caplog.text and "request_id=" + "a" * 32 in caplog.text


def test_operator_walkthrough_uses_operator_session() -> None:
    agent = FakeAgentClient()
    client, headers = _secured_client(
        create_app(
            _settings(),
            "http://agent.test/v1/chat/completions",
            auth_manager=FakeAuthManager("operator-server-token", persona="operator"),
            agent_client=agent,
        )
    )

    response = client.post("/api/walkthrough/operator-execute", headers=headers)

    assert response.status_code == 200
    assert response.json()["persona"] == "operator"
    assert response.json()["accessDecision"]["allowed"] is True
    assert agent.tokens == ["operator-server-token"]


def test_operator_plan_verifies_execution_then_persisted_state() -> None:
    client, _headers = _secured_client(
        create_app(
            _settings(),
            "http://agent.test/v1/chat/completions",
            auth_manager=FakeAuthManager("operator-token", persona="operator"),
            agent_client=FakeAgentClient(),
        )
    )

    steps = client.get("/api/walkthrough").json()["steps"]

    assert [step["assertion"] for step in steps] == [
        "reset",
        "pending",
        "execution-succeeded",
        "completed",
    ]
    assert steps[2]["tool"] == "execute_task"
    assert steps[3]["tool"] == "list_tasks"


def test_walkthrough_step_requires_authenticated_user() -> None:
    client, headers = _secured_client(
        create_app(
            _settings(),
            "http://agent.test/v1/chat/completions",
            auth_manager=FakeAuthManager(),
            agent_client=FakeAgentClient(),
        )
    )

    response = client.post("/api/walkthrough/reader-baseline", headers=headers)

    assert response.status_code == 401
    assert "Sign in" in response.json()["detail"]


def test_mutation_requires_csrf_and_same_origin() -> None:
    agent = FakeAgentClient()
    app = create_app(
        _settings(),
        "http://agent.test/v1/chat/completions",
        auth_manager=FakeAuthManager("operator-token", persona="operator"),
        agent_client=agent,
    )
    client, headers = _secured_client(app)

    missing_csrf = client.post(
        "/api/walkthrough/operator-execute",
        headers={"Origin": "http://testserver"},
    )
    hostile_origin = client.post(
        "/api/walkthrough/operator-execute",
        headers={**headers, "Origin": "https://attacker.example"},
    )

    assert missing_csrf.status_code == 403
    assert hostile_origin.status_code == 403
    assert agent.tokens == []


def test_invalid_host_is_rejected() -> None:
    app = create_app(
        _settings(),
        "http://agent.test/v1/chat/completions",
        auth_manager=FakeAuthManager(),
        agent_client=FakeAgentClient(),
    )

    response = TestClient(app, base_url="http://attacker.example").get("/")

    assert response.status_code == 400


def test_browser_sessions_are_isolated() -> None:
    managers = [
        FakeAuthManager("first-browser-token", persona="operator"),
        FakeAuthManager(),
    ]
    registry = BrowserSessionRegistry(_settings(), auth_factory=lambda: managers.pop(0))
    app = create_app(
        _settings(),
        "http://agent.test/v1/chat/completions",
        session_registry=registry,
        agent_client=FakeAgentClient(),
    )
    first = TestClient(app)
    second = TestClient(app)

    first_bootstrap = first.get("/api/bootstrap")
    second_bootstrap = second.get("/api/bootstrap")

    assert first_bootstrap.json()["session"]["persona"] == "operator"
    assert second_bootstrap.json()["session"]["status"] == "signed_out"
    assert first.cookies.get("learningnemo_session") != second.cookies.get("learningnemo_session")