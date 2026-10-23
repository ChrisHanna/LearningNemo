"""Tests for NAT workflow telemetry middleware and correlated sandbox tool spans."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from task_agent.control.invoice_agent import AgentSession
from task_agent.control.invoice_governance import DelegationAuthority
from task_agent.control.invoice_repository import capability_context
from task_agent.control.telemetry import TelemetryCollector, percentile
from task_agent.security.telemetry import BLOCK_MESSAGE, WorkflowTelemetryConfig, WorkflowTelemetryMiddleware
from test_invoice_agent import manifest


def test_telemetry_collector_computes_percentiles_and_counts():
    collector = TelemetryCollector(max_records=10)
    for ms in (10.0, 20.0, 30.0, 40.0, 100.0):
        collector.record('invoice_summary', 'ok', ms, run_id='a' * 32, sandbox_id='b' * 32, actor='planning')
    collector.record('execute_step', 'refused', 0.5, run_id='a' * 32, sandbox_id='b' * 32, actor='planning')
    collector.record('invoice_batches', 'error', 15.0, run_id='a' * 32, sandbox_id='b' * 32, actor='planning')

    summary = collector.summary()
    assert summary['total_calls'] == 7
    assert summary['ok_calls'] == 5
    assert summary['refused_calls'] == 1
    assert summary['error_calls'] == 1
    assert summary['operations']['invoice_summary'] == {
        'count': 5,
        'ok': 5,
        'refused': 0,
        'errors': 0,
        'p50_ms': 30.0,
        'p95_ms': 100.0,
    }
    assert percentile([], 0.5) == 0.0


@pytest.mark.asyncio
async def test_agent_session_records_correlated_tool_spans_and_delegation_hash():
    authority = DelegationAuthority()
    base = manifest('planning')
    now = datetime.now(UTC)
    expiry = datetime.fromisoformat(base.expires_at.replace('Z', '+00:00'))
    cap_hash = capability_context(base.capability.get_secret_value())[1]
    delegation = authority.issue(
        kind='planning',
        run_id=base.run_id,
        sponsor_hash='1' * 64,
        sandbox_id=base.sandbox_id,
        target_id='c' * 32,
        policy_hash='2' * 64,
        capability_hash=cap_hash,
        issued_at=now,
        expires_at=expiry,
    )
    bound = base.model_copy(update={'delegation': delegation})

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _req: httpx.Response(200, json={'rows': []}))) as client:
        session = AgentSession(bound, client, lambda *_a, **_kw: None)
        await session.call('invoice_summary')
        with pytest.raises(ValueError, match='budget denied'):
            await session.call('execute_step')

    records = session.telemetry.records
    assert len(records) == 2
    assert records[0].operation == 'invoice_summary' and records[0].outcome == 'ok'
    assert records[0].run_id == bound.run_id and records[0].sandbox_id == bound.sandbox_id
    assert records[0].delegation_hash == delegation.delegation_hash
    assert records[1].operation == 'execute_step' and records[1].outcome == 'refused'


@pytest.mark.asyncio
async def test_nat_workflow_telemetry_middleware_tracks_invoke_and_stream():
    middleware = WorkflowTelemetryMiddleware(WorkflowTelemetryConfig(max_records=32))
    context = SimpleNamespace(name='list_tasks')

    result = await middleware.function_middleware_invoke(
        {'input': 'list'},
        call_next=AsyncMock(return_value='tasks-listed'),
        context=context,
    )
    assert result == 'tasks-listed'

    refused = await middleware.function_middleware_invoke(
        {'input': 'blocked'},
        call_next=AsyncMock(return_value=BLOCK_MESSAGE),
        context=context,
    )
    assert refused == BLOCK_MESSAGE

    async def stream_next(*_args, **_kwargs):
        yield 'chunk-1'
        yield 'chunk-2'

    chunks = [
        chunk
        async for chunk in middleware.function_middleware_stream(
            {'input': 'stream'},
            call_next=stream_next,
            context=SimpleNamespace(name='<workflow>'),
        )
    ]
    assert chunks == ['chunk-1', 'chunk-2']

    summary = middleware.collector.summary()
    assert summary['total_calls'] == 3
    assert summary['ok_calls'] == 2
    assert summary['refused_calls'] == 1
    assert summary['operations']['list_tasks']['count'] == 2
    assert summary['operations']['<workflow>']['count'] == 1
