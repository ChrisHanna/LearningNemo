import httpx
import pytest

from task_agent.security.semantic_guardrail import SemanticGuardrailConfig, semantic_guardrail_middleware


@pytest.mark.asyncio
async def test_worker_json_handoff_keeps_secret_out_of_config_and_resolves_at_runtime(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-worker-key')
    config = SemanticGuardrailConfig(base_url='https://gateway.test/guardrails', api_key_env='OPENAI_API_KEY')
    serialized = config.model_dump_json(round_trip=True)
    assert 'fixture-worker-key' not in serialized
    restored = SemanticGuardrailConfig.model_validate_json(serialized)
    def handler(request):
        assert request.headers['Authorization'] == 'Bearer fixture-worker-key'
        return httpx.Response(200, json={'choices': [{'message': {'content': 'No'}}]})
    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))
    async with semantic_guardrail_middleware(restored, None) as guardrail:
        assert await guardrail._is_allowed({'input_message': 'List tasks'})


@pytest.mark.parametrize('value', ['', '   ', '**********', '${OPENAI_API_KEY}'])
def test_missing_or_serialized_secret_fails_before_http(monkeypatch, value):
    monkeypatch.setenv('OPENAI_API_KEY', value)
    config = SemanticGuardrailConfig(base_url='https://gateway.test/guardrails', api_key_env='OPENAI_API_KEY')
    with pytest.raises(ValueError, match='missing or redacted'):
        config.resolved_api_key()


def test_credential_source_must_be_unambiguous():
    with pytest.raises(ValueError):
        SemanticGuardrailConfig(base_url='https://gateway.test/guardrails', api_key='fixture', api_key_env='OPENAI_API_KEY')