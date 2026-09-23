from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from task_agent.console.invoice_gateway import create_invoice_gateway
from task_agent.control.invoice_repository import new_capability


class Repository:
    def __init__(self, kind):
        self.kind = kind
        self.calls = []
        self.client = self
    async def call(self, name, parameters):
        assert name == 'ops.usp_diagnose_invoice_summary' and parameters == {'scenario_id':'b'*32}
        return [{'scenario_id':'b'*32,'revision':1,'duplicate_set_hash':'c'*64}]
    async def admit(self, token, tool):
        self.calls.append(tool)
        return {'kind': self.kind, 'scenario_id': 'b' * 32}
    async def summary(self, token):
        self.calls.append('summary')
        return {'fixture': True}
    async def execute_step(self, token, step):
        self.calls.append('execute')
        return {'fixture': True}


class Model:
    def __init__(self):
        self.allowed = True
        self.calls = 0
    async def check(self, messages, *, kind):
        return self.allowed
    async def complete(self, payload):
        self.calls += 1
        self.payload = payload
        return {'choices': []}
    async def stream(self, payload):
        self.calls += 1
        yield b'data: {"choices":[]}\n\ndata: [DONE]\n\n'


def setup(kind='planning'):
    repository, model, events = Repository(kind), Model(), []
    async def audit(**event):
        events.append(event)
    app = create_invoice_gateway(kind=kind, repository=repository, inference_admission=repository,
        model_gateway=model, expires_at=datetime.now(UTC) + timedelta(hours=1), audit=audit)
    return TestClient(app), repository, model, events


def test_planning_write_denied_without_invoking_broker():
    client, repository, _, events = setup()
    token = new_capability('a' * 32)
    body = dict(step_id=1, operation='invoice.rebuild-total.v1', target='b' * 32, expected_revision=1)
    response = client.post('/v2/invoice/tools/execute_step', headers={'Authorization': 'Bearer ' + token}, json=body)
    assert response.status_code == 403 and repository.calls == []
    assert events[0]['source'] == 'invoice-planning-gateway' and events[0]['outcome'] == 'role-denied'
    assert token not in str(events)


def test_diagnostic_input_cannot_supply_sql_or_target():
    client, repository, _, _ = setup()
    path = '/v2/invoice/tools/invoice_summary'
    assert client.post(path, json={}).status_code == 401
    headers = {'Authorization': 'Bearer ' + new_capability('a' * 32)}
    assert client.post(path, headers=headers, json={'sql': 'select secret'}).status_code == 422
    assert client.post(path, headers=headers, json={}).status_code == 200
    assert repository.calls == ['summary']


def test_model_stream_is_capability_and_guardrail_gated():
    client, repository, model, events = setup()
    path = '/v2/invoice/inference/v1/chat/completions'
    headers = {'Authorization': 'Bearer ' + new_capability('a' * 32)}
    body = {'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': 'Investigate'}], 'stream': True}
    assert client.post(path, headers=headers, json=body).status_code == 200
    assert model.calls == 1 and repository.calls == ['inference']
    model.allowed = False
    assert client.post(path, headers=headers, json=body).status_code == 403
    assert model.calls == 1 and events[-1]['outcome'] == 'guardrail-denied'
    assert client.post(path, headers=headers, json={**body, 'messages': [{'content': 'x' * 70000}]}).status_code == 413


def test_execution_model_cannot_request_planning_tool():
    client, _, model, _ = setup('execution')
    response = client.post('/v2/invoice/inference/v1/chat/completions',
        headers={'Authorization': 'Bearer ' + new_capability('a' * 32)}, json={
            'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': 'Investigate'}],
            'tools': [{'type': 'function', 'function': {'name': 'invoice_summary'}}]})
    assert response.status_code == 403 and model.calls == 0


def test_proposal_schema_target_is_bound_to_sql_admission():
    client, _, model, _ = setup()
    response = client.post('/v2/invoice/inference/v1/chat/completions',
        headers={'Authorization':'Bearer '+new_capability('a'*32)},json={
            'model':'gpt-4o-mini','stream':False,'messages':[{'role':'user','content':'Investigate'}],
            'tools':[{'type':'function','function':{'name':'publish_decision','parameters':{'target':'untrusted'}}}]})
    assert response.status_code == 200
    schema = model.payload['tools'][0]['function']['parameters']
    choices = schema['properties']['steps']['items']['anyOf']
    assert len(choices) == 9
    for choice in choices:
        properties = choice['properties']
        assert properties['target']['enum'] == ['b'*32]
        assert properties['expected_revision']['enum'] == properties['step_id']['enum']
        assert set(choice['required']) == set(properties)
    assert model.payload['tools'][0]['function']['strict'] is True
    assert 'untrusted' not in str(schema)