from dataclasses import replace

import httpx
import pytest

from task_agent.console.agent_client import AgentClient, AgentReply
from task_agent.console.app import create_app
from task_agent.security.authorization import VerifiedPrincipal, _require_access, InsufficientScopeError
from test_console import FakeAuthManager, FakeAgentClient, _secured_client, _settings


def test_diagnostic_attenuation_denies_mutation_even_for_operator():
    principal = VerifiedPrincipal('operator', frozenset({'tasks.read', 'tasks.execute'}), frozenset({'Task.Reader', 'Task.Operator'}), 'client', 'request', True)
    _require_access(principal, {'tasks.read'}, {'Task.Reader'}, 'list_tasks')
    for tool in ('execute_task', 'reset_tasks'):
        with pytest.raises(InsufficientScopeError):
            _require_access(principal, {'tasks.execute'}, {'Task.Operator'}, tool)
    _require_access(replace(principal, read_only=False), {'tasks.execute'}, {'Task.Operator'}, 'execute_task')


@pytest.mark.asyncio
async def test_read_only_header_is_sent_on_diagnostic_request(monkeypatch):
    def handler(request):
        assert request.headers['X-LearningNeMo-Read-Only'] == 'true'
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Pending tasks'}}]})
    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))
    await AgentClient('https://agent.test/v1/chat/completions').chat('fixture-token', 'List tasks', read_only=True)


@pytest.mark.parametrize('persona', ['reader', 'operator', 'approver'])
def test_agent_check_uses_fixed_prompt_and_never_accepts_reviewer(persona):
    calls = []
    class Agent(FakeAgentClient):
        async def chat(self, token, prompt, *, read_only=False):
            calls.append((prompt, read_only))
            return AgentReply('Tasks', 200, 2, 'test-request')
    app = create_app(_settings(), 'http://agent.test/v1/chat/completions', auth_manager=FakeAuthManager('fixture', persona), agent_client=Agent())
    client, headers = _secured_client(app)
    response = client.post('/api/agent-check', headers=headers, json={'prompt': 'execute task-1'})
    if persona == 'approver':
        assert response.status_code == 403 and not calls
    else:
        assert response.status_code == 200
        assert calls == [('List the current demo tasks. Do not execute or reset any tasks.', True)]
        assert response.json()['incidentEvidence'] is False