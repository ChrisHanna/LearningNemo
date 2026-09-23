from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from task_agent.console.app import create_app
from test_console import FakeAgentClient, FakeAuthManager, _settings


STATIC = Path(__file__).parents[1] / "src/task_agent/console/static"


class SessionMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.parents = {}
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
            self.parents[attrs["id"]] = [item[1] for item in self.stack]
        if tag not in {"meta", "link", "input", "br", "hr", "img"}:
            self.stack.append((tag, attrs.get("id")))

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1][0] == tag
        self.stack.pop()


def test_all_roles_share_one_session_panel_and_valid_markup():
    parser = SessionMarkup()
    parser.feed((STATIC / "index.html").read_text())
    assert not parser.stack
    assert len(parser.ids) == len(set(parser.ids))
    assert "approvalsViewTab" not in parser.ids
    for panel in ("taskExperience", "approvalsWorkspace", "incidentWorkspace"):
        assert "identityWorkspace" in parser.parents[panel]
    for control in ("sessionSignIn", "sessionSwitch", "sessionSignOut"):
        assert "identityWorkspace" not in parser.parents[control]
    for region in ("liveReviewStatus", "liveObservedState", "liveRunResults"):
        assert "liveWorkspace" in parser.parents[region]
    assert "ACTUAL EXECUTION" not in (STATIC / "index.html").read_text()


@pytest.mark.parametrize("persona,count", [("reader", 3), ("operator", 4)])
def test_role_preview_is_read_only_and_has_evaluated_step_decisions(persona, count):
    agent = FakeAgentClient()
    client = TestClient(create_app(_settings(), "https://agent.internal",
                                  agent_client=agent,
                                  auth_manager=FakeAuthManager("fixture-token", persona)))
    response = client.get("/api/walkthrough")
    assert response.status_code == 200
    manifest = response.json()
    assert manifest["persona"] == persona
    assert len(manifest["steps"]) == count
    assert all("accessDecision" in step for step in manifest["steps"])
    assert not agent.tokens and not agent.prompts


def test_session_assets_do_not_change_role_authority():
    bootstrap = (STATIC / 'learningnemo.js').read_text()
    assert 'document.addEventListener("DOMContentLoaded", bootstrap, { once: true })' in bootstrap
    assert '\nbootstrap();' not in bootstrap
    script = (STATIC / "session.js").read_text()
    assert 'api("/api/walkthrough")' in script
    assert "requestGeneration !== generation" in script
    assert "state.activities = []" in script
    assert "await clearAuthentication()" in script
    assert "await authenticate()" in script
    assert 'method: "POST"' not in script
    assert "innerHTML" not in script
    approvals = (STATIC / "approvals.js").read_text()
    assert 'find("identityViewTab").hidden' not in approvals
    assert '.click()' not in approvals


def test_runtime_lock_failure_is_explicitly_a_historical_attempt():
    client = TestClient(create_app(_settings(), "https://agent.internal",
                                  agent_client=FakeAgentClient(),
                                  auth_manager=FakeAuthManager("fixture-token", "approver")))
    response = client.get("/api/showcase")
    assert response.status_code == 200
    record = response.json()
    assert record["recordedOn"] == "2026-09-14"
    assert record["currentHealth"] == "unknown"
    milestone = next(item for item in record["milestones"] if "Runtime lock" in item["label"])
    assert "14 Sep attempt" in milestone["label"]
    assert milestone["state"] == "failed"
    assert milestone["value"] == "Failed in this record"