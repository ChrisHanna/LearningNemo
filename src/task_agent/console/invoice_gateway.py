"""Capability-authenticated invoice tools; each deployment has one agent role."""

from datetime import UTC, datetime
from copy import deepcopy
import json
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from task_agent.control.invoice_contract import InvoiceStep, PlanningDecision
from task_agent.control.invoice_model import OutputRailBlocked
from task_agent.control.invoice_repository import capability_context
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureUnavailableError
from task_agent.console.invoice_availability import validate_availability


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class InferenceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: Literal['gpt-4o-mini']
    messages: list[dict] = Field(min_length=1, max_length=32)
    tools: list[dict] = Field(default_factory=list, max_length=3)
    tool_choice: Literal['auto', 'none', 'required'] = 'auto'
    temperature: Literal[0, 0.0] = 0
    stream: bool = True
    stream_options: dict | None = None
    parallel_tool_calls: Literal[False] = False
    max_tokens: int = Field(default=2048, ge=1, le=2048)


def proposal_schema(scenario_id, revision, duplicate_set_hash):
    schema = PlanningDecision.model_json_schema()
    choices = []
    operations = InvoiceStep.model_json_schema()['properties']['operation']['enum']
    for position in range(1, 4):
        for operation in operations:
            item = deepcopy(schema['$defs']['InvoiceStep'])
            properties = item['properties']
            for key, value in {'step_id':position, 'target':scenario_id, 'expected_revision':revision+position-1, 'operation':operation}.items():
                properties[key]['enum'] = [value]
            properties['duplicate_set_hash'] = {'type':'string','enum':[duplicate_set_hash]} if operation == operations[0] else {'type':'null'}
            item['required'] = list(properties)
            choices.append(item)
    schema['properties']['steps']['items'] = {'anyOf':choices}
    schema.pop('$defs', None)
    return schema


def create_invoice_gateway(*, kind, repository, inference_admission, model_gateway, expires_at, audit, availability_mode='leased'):
    validate_availability(availability_mode, expires_at)
    if kind not in ('planning', 'execution'):
        raise ValueError('one role and an aware service deadline required')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    bearer = HTTPBearer(auto_error=False)

    @app.middleware('http')
    async def admission(request: Request, call_next):
        if request.url.path != '/healthz' and expires_at is not None and datetime.now(UTC) >= expires_at:
            return JSONResponse({'detail': 'Invoice gateway lease expired'}, status_code=503)
        if request.method == 'POST':
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > 65536:
                    return JSONResponse({'detail': 'Bounded request exceeded'}, status_code=413)
            request._body = bytes(content)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    async def credential(value: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if value is None or value.scheme.lower() != 'bearer':
            raise HTTPException(401, 'Run capability required')
        try:
            capability_context(value.credentials)
        except OperationDeniedError:
            raise HTTPException(401, 'Invalid run capability') from None
        return value.credentials

    async def event(token, tool, outcome):
        run_id, _ = capability_context(token)
        await audit(run_id=run_id, source='invoice-' + kind + '-gateway', tool=tool, outcome=outcome)

    @app.exception_handler(OperationDeniedError)
    async def denied(_request, _error):
        return JSONResponse({'detail': 'Run authority or exact approved operation denied'}, status_code=403)

    @app.exception_handler(SqlProcedureUnavailableError)
    async def unavailable(_request, _error):
        return JSONResponse({'detail': 'Outcome unconfirmed; inspect persisted state, do not replay a write'}, status_code=503)

    @app.get('/healthz')
    async def health():
        return {'status': 'ok', 'role': kind}

    @app.post('/v2/invoice/tools/invoice_summary')
    async def summary(_body: EmptyRequest, token: str = Depends(credential)):
        if kind != 'planning':
            await event(token, 'invoice_summary', 'role-denied')
            raise OperationDeniedError('planning only')
        result = await repository.summary(token)
        await event(token, 'invoice_summary', 'returned')
        return result

    @app.post('/v2/invoice/tools/invoice_batches')
    async def batches(_body: EmptyRequest, token: str = Depends(credential)):
        if kind != 'planning':
            await event(token, 'invoice_batches', 'role-denied')
            raise OperationDeniedError('planning only')
        result = await repository.batches(token)
        await event(token, 'invoice_batches', 'returned')
        return result

    @app.post('/v2/invoice/tools/execute_step')
    async def execute(step: InvoiceStep, token: str = Depends(credential)):
        if kind != 'execution':
            await event(token, 'execute_step', 'role-denied')
            raise OperationDeniedError('execution only')
        try:
            result = await repository.execute_step(token, step)
        except (OperationDeniedError, SqlProcedureUnavailableError):
            await event(token, 'execute_step', 'denied-or-unconfirmed')
            raise
        await event(token, 'execute_step', 'receipt-recorded')
        return result

    @app.post('/v2/invoice/inference/v1/chat/completions')
    async def inference(body: InferenceRequest, token: str = Depends(credential)):
        authority = await inference_admission.admit(token, 'inference')
        if authority['kind'] != kind:
            raise OperationDeniedError('run role differs')
        allowed = {'invoice_summary', 'invoice_batches', 'publish_decision'} if kind == 'planning' else {'execute_step'}
        for tool in body.tools:
            if tool.get('type') != 'function' or tool.get('function', {}).get('name') not in allowed:
                raise OperationDeniedError('model tool catalog differs')
        await event(token, 'inference', 'admitted')
        payload = body.model_dump(exclude_none=True)
        if kind == 'planning':
            for tool in payload['tools']:
                if tool['function']['name'] == 'publish_decision':
                    rows = await repository.client.call('ops.usp_diagnose_invoice_summary', {'scenario_id':authority['scenario_id']})
                    if len(rows) != 1 or rows[0]['scenario_id'] != authority['scenario_id']:
                        raise OperationDeniedError('proposal parameter evidence unavailable')
                    observed = rows[0]
                    tool['function']['parameters'] = proposal_schema(authority['scenario_id'],observed['revision'],observed['duplicate_set_hash'])
                    tool['function']['strict'] = True
                    tool['function']['description'] = ('Publish a complete evidence-supported resolution or a no-change decision. '
                        'The target is the run-bound scenario ID, never a hash. Success requires no duplicates, reconciled '
                        'reported totals, and the idempotent importer active. Quarantine alone does not rebuild the reported '
                        'total or activate the importer. Select each needed registered operation in order; do not execute it.')
        if not await model_gateway.check(payload['messages'], kind=kind):
            await event(token, 'inference', 'guardrail-denied')
            raise OperationDeniedError('untrusted instructions blocked')
        if not await model_gateway.check_tool_results(payload['messages'], kind=kind):
            await event(token, 'inference', 'tool-result-guardrail-denied')
            raise OperationDeniedError('tool result blocked')
        await event(token, 'inference', 'guardrail-passed')
        try:
            if body.stream:
                chunks = await model_gateway.stream(payload, kind=kind, scenario_id=authority['scenario_id'])
            else:
                result = await model_gateway.complete(payload, kind=kind, scenario_id=authority['scenario_id'])
        except OutputRailBlocked:
            await event(token, 'inference', 'output-guardrail-denied')
            raise OperationDeniedError('model output blocked') from None
        await event(token, 'inference', 'output-guardrail-passed')
        if body.stream:
            return StreamingResponse(iter(chunks), media_type='text/event-stream')
        return result

    return app