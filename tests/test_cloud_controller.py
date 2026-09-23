from fastapi.testclient import TestClient

from task_agent.console.cloud_controller import create_controller
from task_agent.console.cloud_controller import main
from task_agent.console.identity import EntraTestSettings


class Verifier:
    async def verify(self, token):
        if token == "invalid":
            raise ValueError("secret failure detail")
        return {"canRead": token in {"reader", "operator"}, "allowed": token == "operator"}


class Workspace:
    def __init__(self):
        self.runs = []
        self.checks = 0

    def check(self):
        self.checks += 1
        return {"readyForProbe": False}

    def run(self, run_id):
        self.runs.append(run_id)
        return {"status": "blocked"}


def test_private_controller_requires_independent_bearer_authorization():
    workspace = Workspace()
    client = TestClient(create_controller(EntraTestSettings("tenant", "api", "client"), workspace, Verifier()))
    assert client.post("/workspace/check").status_code == 401
    assert client.post("/workspace/check", headers={"Authorization": "Bearer reader"}).status_code == 200
    assert workspace.checks == 1
    payload = {"runId": "a" * 32}
    assert client.post("/workspace/run", json=payload, headers={"Authorization": "Bearer reader"}).status_code == 403
    assert workspace.runs == []
    assert client.post("/workspace/run", json=payload, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.post("/workspace/run", json={**payload, "script": "arbitrary"}, headers={"Authorization": "Bearer operator"}).status_code == 422
    assert client.post("/workspace/run", json=payload, headers={"Authorization": "Bearer operator"}).status_code == 200
    assert workspace.runs == ["a" * 32]


def test_controller_diagnostic_option_is_read_only(monkeypatch, capsys):
    from unittest.mock import patch
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "subscription")
    monkeypatch.setenv("AZURE_CLIENT_ID", "identity")
    with patch("sys.argv", ["controller", "--check"]), patch(
        "task_agent.console.cloud_controller.EntraTestSettings.from_sources"
    ), patch("task_agent.console.cloud_controller.ManagedWorkspace") as workspace, patch(
        "task_agent.console.cloud_controller.uvicorn.run"
    ) as server:
        workspace.return_value.check.return_value = {"readyForProbe": False}
        main()
        workspace.return_value.check.assert_called_once_with()
        workspace.return_value.run.assert_not_called()
        server.assert_not_called()
    assert '"readyForProbe": false' in capsys.readouterr().out