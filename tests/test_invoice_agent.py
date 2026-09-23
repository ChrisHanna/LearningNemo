from datetime import UTC, datetime, timedelta


def test_model_transport_uses_no_automatic_retries():
    from pathlib import Path
    import yaml
    for kind in ('planning', 'execution'):
        config = yaml.safe_load((Path(__file__).parents[1]/'configs'/f'invoice-{kind}.yml').read_text())
        assert config['llms']['invoice_model']['max_retries'] == 0
        assert config['llms']['invoice_model']['do_auto_retry'] is False

import httpx
import pytest

from task_agent.control.invoice_agent import AgentSession, RunManifest, validate_manifest
from task_agent.control.invoice_contract import InvoicePlan, InvoiceStep
from task_agent.control.invoice_repository import new_capability
from test_invoice_contract import plan_values


def manifest(kind='planning'):
    return RunManifest(kind=kind, run_id='a' * 32, sandbox_id='b' * 32,
        gateway_origin=f'https://ca-nemo-invoice-{kind}-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io',
        capability=new_capability('a' * 32), expires_at=(datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        plan=InvoicePlan(**plan_values()) if kind == 'execution' else None)


def test_manifest_requires_exact_origin_and_new_execution_sandbox():
    valid = manifest()
    validate_manifest(valid)
    assert valid.capability.get_secret_value() not in repr(valid)
    for changed in ({'gateway_origin': 'https://example.com'}, {'run_id': 'c' * 32}, {'expires_at': '2020-01-01T00:00:00Z'}):
        with pytest.raises(ValueError):
            validate_manifest(valid.model_copy(update=changed))
    execution = manifest('execution')
    with pytest.raises(ValueError):
        validate_manifest(execution.model_copy(update={'sandbox_id': execution.plan.planning_sandbox_id}))


@pytest.mark.asyncio
async def test_planning_cannot_execute_and_tool_events_contain_no_capability():
    calls, events = [], []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'rows': []})
    context = manifest()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        session = AgentSession(context, client, lambda name, **values: events.append((name, values)))
        await session.call('invoice_batches')
        with pytest.raises(ValueError):
            await session.call('execute_step')
    assert len(calls) == 1
    assert context.capability.get_secret_value() not in str(events)


@pytest.mark.asyncio
async def test_execution_rejects_tamper_and_does_not_retry_uncertain_write():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={'detail': 'unconfirmed'})
    context = manifest('execution')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        session = AgentSession(context, client, lambda *args, **kwargs: None)
        step = context.plan.steps[0]
        with pytest.raises(ValueError, match='next exact'):
            await session.execute(step.model_copy(update={'target': 'f' * 32}))
        assert calls == []
        with pytest.raises(ValueError, match='unconfirmed'):
            await session.execute(step)
        with pytest.raises(ValueError, match='budget denied'):
            await session.execute(step)
        assert len(calls) == 1 and session.receipts == []


def test_nemo_configs_load_registered_tools(monkeypatch):
    from pathlib import Path
    from nat.runtime.loader import load_config
    from task_agent.control.invoice_agent import register_tools
    register_tools()
    monkeypatch.setenv('INVOICE_MODEL_BASE_URL', manifest().gateway_origin + '/v2/invoice/inference/v1')
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-only')
    directory = Path(__file__).resolve().parents[1] / 'configs'
    for kind in ('planning', 'execution'):
        config = load_config(directory / f'invoice-{kind}.yml')
        assert config.workflow.type == 'tool_calling_agent'
        assert config.workflow.max_iterations <= 8


def test_runtime_honors_openshell_proxy_environment():
    import inspect
    from task_agent.control.invoice_agent import run_agent
    assert 'trust_env=True' in inspect.getsource(run_agent)


@pytest.mark.asyncio
async def test_real_nemo_workflow_dispatches_planning_tools(monkeypatch):
    from pathlib import Path
    from langchain_core.messages import AIMessage, AIMessageChunk
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from langchain_openai import ChatOpenAI
    from nat.builder.workflow_builder import WorkflowBuilder
    from nat.runtime.loader import load_config
    from task_agent.control.invoice_agent import SESSION, register_tools
    register_tools()
    context = manifest()
    monkeypatch.setenv('INVOICE_MODEL_BASE_URL', context.gateway_origin + '/v2/invoice/inference/v1')
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-only')
    model_calls = []
    async def generate(_self, messages, **_kwargs):
        model_calls.append(messages)
        if len(model_calls) == 1:
            message = AIMessage(content='', tool_calls=[{'id': 'call-summary', 'name': 'invoice_summary', 'args': {}}])
        elif len(model_calls) == 2:
            message = AIMessage(content='', tool_calls=[{'id': 'call-decision', 'name': 'publish_decision', 'args': {
                'outcome': 'insufficient-evidence', 'diagnosis': 'Unconfirmed', 'rationale': 'Fixture summary is incomplete',
                'evidence_hash': '0' * 64, 'risks': [], 'steps': []}}])
        else:
            message = AIMessage(content='Insufficient evidence; no change proposed.')
        return ChatResult(generations=[ChatGeneration(message=message)])
    monkeypatch.setattr(ChatOpenAI, '_agenerate', generate)
    async def stream(model, messages, **kwargs):
        result = await generate(model, messages, **kwargs)
        message = result.generations[0].message
        yield ChatGenerationChunk(message=AIMessageChunk(content=message.content, tool_calls=message.tool_calls))
    monkeypatch.setattr(ChatOpenAI, '_astream', stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'fixture': True}))) as client:
        session = AgentSession(context, client, lambda *args, **kwargs: None)
        token = SESSION.set(session)
        try:
            config = load_config(Path(__file__).resolve().parents[1] / 'configs/invoice-planning.yml')
            async with WorkflowBuilder.from_config(config) as builder:
                workflow = await builder.build()
                async with workflow.run('Investigate the fixture') as runner:
                    await runner.result()
            assert len(model_calls) == 3 and session.calls == 1
            assert session.decision.outcome == 'insufficient-evidence'
        finally:
            SESSION.reset(token)