from types import SimpleNamespace

import httpx
import pytest

from task_agent.control.execution import ExecutionClaim
from task_agent.control.execution_transport import ManagedExecutionTransport
from task_agent.control.sql_backend import SqlProcedureUnavailableError
from test_execution_coordinator import approval


BROKER = 'https://ca-learningnemo-remediation-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io'
VERIFIER = 'https://ca-learningnemo-verifier-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io'
AUDIENCE = '11111111-1111-4111-8111-111111111111'


class Credential:
    def get_token(self, scope):
        assert scope == f'api://{AUDIENCE}/.default'
        return SimpleNamespace(token='fixture-service-token')


@pytest.mark.asyncio
async def test_fixed_broker_transport_keeps_credential_out_of_request_body():
    import json
    requests = []
    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.headers['Authorization'] == 'Bearer fixture-service-token'
        assert b'fixture-service-token' not in request.content
        if request.url.path == '/v1/remediations/execute':
            assert body['expected_plan_hash'] == approval().plan_hash
            assert body['one_time_id'] == approval().one_time_id
            return httpx.Response(200, json={'operation_id': approval().operation_id, 'result_code': 'safe_query_activated', 'result': {'safe_query_version': 'cycle-safe-v1'}})
        assert request.url.path == '/v1/verifications/cycle-recovery'
        assert body == {'safe_query_version': 'cycle-safe-v1'}
        return httpx.Response(200, json={'passed': True, 'checks': {'safe_query_version_active': True, 'no_owned_query_running': True, 'deterministic_result': True}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = ManagedExecutionTransport(broker_origin=BROKER, verifier_origin=VERIFIER,
            broker_audience=AUDIENCE, verifier_audience=AUDIENCE, credential=Credential(), client=client)
        claim = ExecutionClaim(execution_id='execution-'+'d'*32, sponsor_hash='c'*64, approval=approval())
        assert (await transport.execute(claim)).result_code == 'safe_query_activated'
        assert (await transport.verify(claim)).execution_id == claim.execution_id
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['redirect', 'timeout'])
async def test_write_is_not_retried_or_redirected(failure):
    requests = []
    def handler(request):
        requests.append(request)
        if failure == 'timeout': raise httpx.ReadTimeout('unconfirmed')
        return httpx.Response(307, headers={'Location': 'https://unapproved.example/execute'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = ManagedExecutionTransport(broker_origin=BROKER, verifier_origin=VERIFIER,
            broker_audience=AUDIENCE, verifier_audience=AUDIENCE, credential=Credential(), client=client)
        with pytest.raises(SqlProcedureUnavailableError):
            await transport.execute(ExecutionClaim(execution_id='execution-'+'d'*32, sponsor_hash='c'*64, approval=approval()))
    assert len(requests) == 1