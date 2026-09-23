import httpx
import pytest

from task_agent.console.agent_client import AgentClient, AgentClientError
from task_agent.console.agent_client import safe_validation_fields


@pytest.mark.asyncio
@pytest.mark.parametrize("detail,category", [
    ([{"loc": ["body", "messages"], "input": "sensitive prompt", "msg": "sensitive upstream detail"}], "request_validation"),
    ("private workflow exception with sensitive prompt", "workflow_rejected"),
])
async def test_agent_error_diagnostics_never_include_upstream_content(monkeypatch, detail, category):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(422, json={"detail": detail})
    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(AgentClientError) as raised:
        await AgentClient("https://agent.internal/v1/chat/completions").chat("private-token", "List tasks")
    error = raised.value
    assert error.category == category
    assert error.status_code == 422
    assert error.request_id == requests[0].headers["X-Request-ID"]
    assert "private" not in str(error) and "sensitive" not in error.detail


@pytest.mark.asyncio
async def test_openai_request_and_response_contract(monkeypatch):
    import json
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer fixture-token"
        assert json.loads(request.content) == {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "List tasks"}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": "task-1 pending"}}]})
    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))
    reply = await AgentClient("https://agent.internal/v1/chat/completions").chat("fixture-token", "List tasks")
    assert reply.content == "task-1 pending"
    assert reply.status_code == 200
    assert len(reply.request_id) == 32


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,code,category", [(httpx.ReadTimeout, 504, "transport_timeout"), (httpx.ConnectError, 502, "transport_error")])
async def test_connection_failures_keep_request_id_without_sensitive_error(monkeypatch, kind, code, category):
    requests = []
    def handler(request):
        requests.append(request)
        raise kind("secret token and private prompt")
    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(AgentClientError) as caught:
        await AgentClient("https://agent.internal/v1/chat/completions").chat("secret", "private prompt")
    assert caught.value.status_code == code and caught.value.category == category
    assert caught.value.request_id == requests[0].headers['X-Request-ID']
    assert 'secret' not in str(caught.value)
    assert len(requests) == 1


def test_validation_locations_are_bounded_and_do_not_include_inputs_or_custom_messages():
    details = [{'loc':['body','messages',0,'content'], 'type':'string_type', 'input':'private prompt', 'msg':'private detail'},
               {'loc':['body','private dynamic field'], 'type':'private custom exception', 'ctx':{'secret':'token'}}]
    assert safe_validation_fields(details) == (
        {'location':['body','messages',0,'content'],'type':'string_type'},
        {'location':['body','<field>'],'type':'validation_error'},
    )
    assert safe_validation_fields([{'loc':['body'],'type':[]}]) == ({'location':['body'],'type':'validation_error'},)