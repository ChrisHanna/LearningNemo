from fastapi.testclient import TestClient

from task_agent.console.app import create_app
from task_agent.console.browser_sessions import BrowserSessionRegistry, CSRF_HEADER
from task_agent.console.identity import EntraTestSettings
from task_agent.console.local_demo_auth import LocalDemoAuthManager
from task_agent.console.local_invoice import LocalDemoAgentClient
from task_agent.console.local_invoice_demo import LocalInvoiceDemoService


def local_client():
    settings = EntraTestSettings("local-demo", "local-demo-api", "local-demo-client")
    registry = BrowserSessionRegistry(settings, auth_factory=LocalDemoAuthManager)
    app = create_app(
        settings,
        "local-demo://agent",
        session_registry=registry,
        agent_client=LocalDemoAgentClient(),
        invoice_service=LocalInvoiceDemoService(),
    )
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    bootstrap = client.get("/api/bootstrap")
    return client, {
        "Origin": "http://127.0.0.1:8765",
        CSRF_HEADER: bootstrap.json()["csrfToken"],
    }


def sign_in(client, headers, persona):
    response = client.post("/api/auth/start", headers=headers, json={"persona": persona})
    assert response.status_code == 200
    assert response.json()["persona"] == persona
    assert "local-demo-" not in response.text


def test_local_invoice_demo_completes_with_separate_accounts():
    client, headers = local_client()
    assert client.get("/api/bootstrap").json()["session"]["authMode"] == "local-demo"
    assert client.post("/api/auth/start", headers=headers, json={"persona": "reader"}).status_code == 422

    sign_in(client, headers, "operator")
    scenario_id = "1" * 32
    planning_id = "2" * 32
    execution_id = "3" * 32
    scenario = client.post(
        "/api/invoices/scenarios",
        headers=headers,
        json={"scenario_id": scenario_id, "variant": "lost-acknowledgement"},
    )
    assert scenario.status_code == 201
    planning = client.post(
        "/api/invoices/jobs",
        headers=headers,
        json={"job_id": planning_id, "kind": "planning", "target_id": scenario_id},
    )
    assert planning.status_code == 202
    plan = planning.json()["result"]["plan"]
    row = client.get("/api/invoices/plans").json()["plans"][0]
    submitted = client.post(
        f"/api/invoices/plans/{plan['plan_id']}/submit",
        headers=headers,
        json={"plan_hash": row["plan_hash"]},
    )
    assert submitted.json()["state"] == "submitted"

    assert client.delete("/api/auth", headers=headers).status_code == 200
    sign_in(client, headers, "approver")
    queue = client.get("/api/invoices/plans").json()["plans"]
    assert len(queue) == 1 and queue[0]["state"] == "submitted"
    assert queue[0]["diagnostics"]["evidence_hash"] == queue[0]["plan_json"]["evidence_hash"]
    approved = client.post(
        f"/api/invoices/review/{plan['plan_id']}",
        headers=headers,
        json={"plan_hash": row["plan_hash"], "decision": "approve"},
    )
    assert approved.json()["state"] == "approved"

    assert client.delete("/api/auth", headers=headers).status_code == 200
    sign_in(client, headers, "operator")
    execution = client.post(
        "/api/invoices/jobs",
        headers=headers,
        json={"job_id": execution_id, "kind": "execution", "target_id": plan["plan_id"], "plan_hash": row["plan_hash"]},
    )
    assert execution.status_code == 202
    verified = client.post(
        f"/api/invoices/plans/{plan['plan_id']}/reconcile",
        headers=headers,
        json={"plan_hash": row["plan_hash"]},
    )
    assert verified.json()["state"] == "verified"
    assert all(verified.json()["checks"].values())
    completed = client.post(
        f"/api/invoices/plans/{plan['plan_id']}/complete",
        headers=headers,
        json={"plan_hash": row["plan_hash"]},
    )
    assert completed.json()["state"] == "completed"


def test_local_invoice_demo_session_can_end_through_console_api():
    client, headers = local_client()
    sign_in(client, headers, "operator")
    current = client.get("/api/invoices/demo-session").json()

    ended = client.post(
        "/api/invoices/demo-session/end",
        headers=headers,
        json={"session_id": current["session_id"]},
    )

    assert ended.status_code == 200, ended.json()
    assert ended.json()["state"] == "idle"
