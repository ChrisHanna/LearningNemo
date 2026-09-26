"""Task-agent output and execution rails, exercised through the real NAT and NeMo runtime."""

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).parents[1]


def agent_middleware():
    return yaml.safe_load((ROOT / 'configs' / 'agent.yml').read_text(encoding='utf-8'))['middleware']


def workflow_config(tmp_path):
    middleware = agent_middleware()
    config = {
        'functions': {
            'list_tasks': {'_type': 'list_tasks', 'middleware': ['tool_execution_rails']},
            'execute_task': {'_type': 'execute_task', 'middleware': ['tool_execution_rails']},
        },
        'llms': {'openai_llm': {'_type': 'openai', 'base_url': 'https://example.invalid/v1', 'model_name': 'gpt-4o-mini'}},
        'middleware': {name: middleware[name] for name in ('tool_execution_rails', 'response_output_rails')},
        'workflow': {'_type': 'tool_calling_agent', 'llm_name': 'openai_llm', 'tool_names': ['list_tasks', 'execute_task'],
                     'middleware': ['response_output_rails'], 'max_iterations': 2, 'handle_tool_errors': False},
    }
    path = tmp_path / 'agent.yml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    return path


@pytest.mark.asyncio
async def test_execution_rail_blocks_tool_arguments_and_output_rail_masks_response(monkeypatch, tmp_path):
    from langchain_core.messages import AIMessage, AIMessageChunk
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from langchain_openai import ChatOpenAI
    from nat.builder.workflow_builder import WorkflowBuilder
    from nat.runtime.loader import load_config
    import task_agent.register  # noqa: F401
    from task_agent.tasks.store import task_store

    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-only')
    await task_store.reset()
    model_calls = []

    async def generate(_self, messages, **_kwargs):
        model_calls.append(messages)
        if len(model_calls) == 1:
            message = AIMessage(content='', tool_calls=[{'id': 'call-1', 'name': 'execute_task', 'args': {'task_id': 'task-1; rm -rf /'}}])
        else:
            message = AIMessage(content='Nothing changed. Contact alice@example.com for help.')
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def stream(model, messages, **kwargs):
        message = (await generate(model, messages, **kwargs)).generations[0].message
        yield ChatGenerationChunk(message=AIMessageChunk(content=message.content, tool_calls=message.tool_calls))

    monkeypatch.setattr(ChatOpenAI, '_agenerate', generate)
    monkeypatch.setattr(ChatOpenAI, '_astream', stream)
    async with WorkflowBuilder.from_config(load_config(workflow_config(tmp_path))) as builder:
        workflow = await builder.build()
        async with workflow.run('Execute task-1') as runner:
            result = await runner.result()

    tool_results = [message.content for message in model_calls[1] if message.type == 'tool']
    assert tool_results == ["I'm sorry, I can't respond to that."]
    assert all(task.status == 'pending' for task in await task_store.list())
    assert 'alice@example.com' not in str(result) and '<EMAIL_ADDRESS>' in str(result)


@pytest.mark.asyncio
async def test_tool_result_and_response_rails_block_secrets():
    from nemoguardrails import LLMRails, RailsConfig
    from nemoguardrails.rails.llm.options import RailType

    for name in ('tool_execution_rails', 'response_output_rails'):
        rails = LLMRails(RailsConfig.parse_object(agent_middleware()[name]['guardrails']))
        for leaked in ('Password=hunter2', 'Bearer ' + 'a' * 24, '-----BEGIN RSA PRIVATE KEY-----'):
            result = await rails.check_async([{'role': 'user', 'content': 'tool'}, {'role': 'assistant', 'content': leaked}],
                                             rail_types=[RailType.OUTPUT])
            assert result.status == 'blocked', (name, leaked)
        result = await rails.check_async([{'role': 'user', 'content': 'tool'}, {'role': 'assistant', 'content': '{"id": "task-1"}'}],
                                         rail_types=[RailType.OUTPUT])
        assert result.status == 'passed'
