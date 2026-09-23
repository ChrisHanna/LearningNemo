"""NeMo agent tools and sandbox entry point for bounded invoice investigations."""

import asyncio
from contextvars import ContextVar
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from task_agent.control.invoice_contract import Identifier, InvoicePlan, InvoiceStep, PlanningDecision
from task_agent.control.invoice_repository import capability_context


class RunManifest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    kind: Literal['planning', 'execution']
    run_id: Identifier
    sandbox_id: Identifier
    gateway_origin: str
    capability: SecretStr
    expires_at: str
    plan: InvoicePlan | None = None


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class AgentSession:
    def __init__(self, manifest, client, emit):
        self.manifest, self.client, self.emit = manifest, client, emit
        self.calls = 0
        self.decision = None
        self.receipts = []
        self.stopped = False

    async def call(self, tool, body=None):
        allowed = {'invoice_summary', 'invoice_batches'} if self.manifest.kind == 'planning' else {'execute_step'}
        if self.stopped or tool not in allowed or self.calls >= 12:
            raise ValueError('agent tool or budget denied')
        self.calls += 1
        self.emit('tool-requested', tool=tool, source='agent-runtime')
        try:
            response = await self.client.post(self.manifest.gateway_origin + '/v2/invoice/tools/' + tool,
                headers={'Authorization': 'Bearer ' + self.manifest.capability.get_secret_value()}, json=body or {}, timeout=30)
        except httpx.HTTPError:
            self.stopped = True
            self.emit('tool-unconfirmed', tool=tool, source='agent-runtime', reason='transport-error')
            raise ValueError('tool outcome unconfirmed; run stopped') from None
        if response.status_code != 200 or len(response.content) > 65536:
            self.stopped = True
            self.emit('tool-unconfirmed', tool=tool, source='agent-runtime', http_status=response.status_code)
            raise ValueError('tool call denied or unconfirmed; do not retry a mutation')
        try:
            result = response.json()
        except ValueError:
            self.stopped = True
            raise ValueError('tool returned invalid JSON; run stopped') from None
        self.emit('tool-returned', tool=tool, source='agent-runtime', http_status=200)
        return result

    async def execute(self, step):
        plan = self.manifest.plan
        if self.manifest.kind != 'execution' or plan is None:
            raise ValueError('execution agent required')
        position = len(self.receipts)
        if position >= len(plan.steps) or step != plan.steps[position]:
            self.emit('tool-denied', tool='execute_step', source='agent-runtime', reason='off-plan-request')
            raise ValueError('step is not the next exact approved operation')
        receipt = await self.call('execute_step', step.model_dump(mode='json'))
        self.receipts.append(receipt)
        return receipt


SESSION: ContextVar[AgentSession] = ContextVar('invoice_agent_session')
_TOOLS_REGISTERED = False


def register_tools():
    global _TOOLS_REGISTERED
    if _TOOLS_REGISTERED:
        return
    from nat.builder.function_info import FunctionInfo
    from nat.cli.register_workflow import register_function
    from nat.data_models.function import FunctionBaseConfig

    class InvoiceToolConfig(FunctionBaseConfig, name='invoice_agent_tool'):
        operation: Literal['invoice_summary', 'invoice_batches', 'publish_decision', 'execute_step']

    @register_function(config_type=InvoiceToolConfig)
    async def invoice_tool(config, _builder):
        if config.operation == 'publish_decision':
            async def publish(request: PlanningDecision) -> str:
                """Publish an evidence-backed decision; does not submit or approve a plan."""
                session = SESSION.get()
                if session.manifest.kind != 'planning' or session.decision is not None:
                    raise ValueError('one planning decision permitted')
                session.decision = request
                session.emit('decision-produced', source='agent-runtime', outcome=request.outcome)
                return json.dumps({'outcome': request.outcome, 'submitted': False})
            yield FunctionInfo.from_fn(publish, input_schema=PlanningDecision, description=publish.__doc__)
        elif config.operation == 'execute_step':
            async def execute(request: InvoiceStep) -> str:
                """Request the next exact approved step. On an error, stop without retrying."""
                return json.dumps(await SESSION.get().execute(request))
            yield FunctionInfo.from_fn(execute, input_schema=InvoiceStep, description=execute.__doc__)
        else:
            async def diagnose(request: EmptyInput) -> str:
                """Read bounded invoice evidence for this run. No target or SQL can be supplied."""
                return json.dumps(await SESSION.get().call(config.operation))
            descriptions = {'invoice_summary': 'Inspect invoice counts, totals, duplicate business keys, revision, and import version.',
                            'invoice_batches': 'Inspect bounded invoice/order keys and import attempts; repeated purchases can be legitimate.'}
            yield FunctionInfo.from_fn(diagnose, input_schema=EmptyInput, description=descriptions[config.operation])
    _TOOLS_REGISTERED = True


def validate_manifest(manifest, *, now=None):
    parsed = urlsplit(manifest.gateway_origin)
    expected = f'ca-nemo-invoice-{manifest.kind}-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io'
    if manifest.gateway_origin != 'https://' + expected or parsed.hostname != expected:
        raise ValueError('fixed invoice mediation origin required')
    run_id, _ = capability_context(manifest.capability.get_secret_value())
    if run_id != manifest.run_id:
        raise ValueError('capability run differs')
    expiry = datetime.fromisoformat(manifest.expires_at.replace('Z', '+00:00'))
    if expiry.tzinfo is None or not 0 < (expiry - (now or datetime.now(UTC))).total_seconds() <= 900:
        raise ValueError('bounded run lease required')
    if (manifest.kind == 'execution') != (manifest.plan is not None):
        raise ValueError('only execution accepts an approved artifact')
    if manifest.plan and manifest.sandbox_id == manifest.plan.planning_sandbox_id:
        raise ValueError('execution must use a different sandbox')
    return expiry


async def run_agent(manifest, config_directory):
    expiry = validate_manifest(manifest)
    if not hasattr(os, 'getuid') or os.getuid() == 0:
        raise ValueError('sandbox agent must run as non-root')
    os.environ['LANGCHAIN_OPENAI_TCP_KEEPALIVE'] = '0'
    from nat.builder.workflow_builder import WorkflowBuilder
    from nat.runtime.loader import load_config
    register_tools()
    os.environ['INVOICE_MODEL_BASE_URL'] = manifest.gateway_origin + '/v2/invoice/inference/v1'
    os.environ['OPENAI_API_KEY'] = manifest.capability.get_secret_value()
    sequence = 0

    def emit(event_type, **values):
        nonlocal sequence
        sequence += 1
        print(json.dumps({'sequence': sequence, 'timestamp': datetime.now(UTC).isoformat(), 'event_type': event_type,
                          'run_id': manifest.run_id, 'sandbox_id': manifest.sandbox_id, 'actor': manifest.kind, **values}), flush=True)

    config = load_config(config_directory / ('invoice-' + manifest.kind + '.yml'))
    async with httpx.AsyncClient(follow_redirects=False, trust_env=True) as client:
        session = AgentSession(manifest, client, emit)
        token = SESSION.set(session)
        try:
            emit('agent-started', source='agent-runtime', uid=os.getuid())
            async with WorkflowBuilder.from_config(config) as builder:
                workflow = await builder.build()
                prompt = 'Invoice totals do not reconcile with orders. Investigate without changing the database, then publish your supported decision.'
                if manifest.kind == 'execution':
                    prompt = 'Execute only the following approved artifact. Stop on any uncertainty.\n' + manifest.plan.model_dump_json()
                async with asyncio.timeout(min(600, (expiry - datetime.now(UTC)).total_seconds())):
                    async with workflow.run(prompt) as runner:
                        await runner.result()
            if manifest.kind == 'planning' and session.decision is None:
                raise ValueError('agent did not produce a structured decision')
            if manifest.kind == 'execution' and len(session.receipts) != len(manifest.plan.steps):
                raise ValueError('agent did not obtain every approved step receipt')
            emit('agent-finished', source='agent-runtime', decision=session.decision.model_dump(mode='json') if session.decision else None,
                 executed_steps=len(session.receipts), independently_verified=False)
        finally:
            SESSION.reset(token)
            os.environ.pop('OPENAI_API_KEY', None)


def main():
    raw = sys.stdin.buffer.readline(65537)
    if len(raw) > 65536:
        raise ValueError('run manifest too large')
    manifest = RunManifest.model_validate_json(raw)
    asyncio.run(run_agent(manifest, Path('/app/configs')))


if __name__ == '__main__':
    main()