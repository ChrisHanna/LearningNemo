"""Fixed trusted-service transport; Azure tokens never enter browser payloads."""

import asyncio
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from task_agent.control.execution import ExecutionClaim, VerifiedRecovery
from task_agent.control.models import OperationReceipt
from task_agent.control.sql_backend import SqlProcedureUnavailableError


class ManagedExecutionTransport:
    def __init__(self, *, broker_origin: str, verifier_origin: str, broker_audience: str,
                 verifier_audience: str, credential, client: httpx.AsyncClient):
        for origin, expected in ((broker_origin, 'ca-learningnemo-remediation-dev'), (verifier_origin, 'ca-learningnemo-verifier-dev')):
            parsed = urlsplit(origin)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port
                    or parsed.path or parsed.query or parsed.fragment
                    or parsed.hostname != expected + '.jollybeach-503c7ed1.eastus.azurecontainerapps.io'):
                raise ValueError('fixed owned worker HTTPS origin required')
        self.broker_origin = broker_origin
        self.verifier_origin = verifier_origin
        self.broker_audience = str(UUID(broker_audience))
        self.verifier_audience = str(UUID(verifier_audience))
        self.credential = credential
        self.client = client

    async def _post(self, origin: str, path: str, audience: str, body: dict) -> dict:
        try:
            token = await asyncio.to_thread(self.credential.get_token, f'api://{audience}/.default')
            response = await self.client.post(origin + path, headers={'Authorization': f'Bearer {token.token}'},
                                              json=body, follow_redirects=False, timeout=60)
            if response.status_code != 200:
                raise SqlProcedureUnavailableError('trusted worker did not confirm operation; reconcile before retrying')
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError('unexpected worker response')
            return result
        except (httpx.HTTPError, ValueError) as error:
            raise SqlProcedureUnavailableError('trusted worker result unavailable; no successful outcome assumed') from error

    async def execute(self, claim: ExecutionClaim) -> OperationReceipt:
        approval = claim.approval
        result = await self._post(self.broker_origin, '/v1/remediations/execute', self.broker_audience, {
            'approval': approval.model_dump(mode='json'), 'expected_plan_hash': approval.plan_hash,
            'one_time_id': approval.one_time_id, 'operation_id': approval.operation_id,
            'parameters': {'query_version': approval.safe_query_version},
        })
        return OperationReceipt.model_validate(result)

    async def verify(self, claim: ExecutionClaim) -> VerifiedRecovery:
        return await self.verify_bound(claim.execution_id, claim.approval.plan_hash, claim.approval.safe_query_version)

    async def verify_bound(self, execution_id: str, plan_hash: str, safe_query_version: str) -> VerifiedRecovery:
        result = await self._post(self.verifier_origin, '/v1/verifications/cycle-recovery', self.verifier_audience,
                                  {'safe_query_version': safe_query_version})
        if result.get('passed') is not True or not isinstance(result.get('checks'), dict):
            raise SqlProcedureUnavailableError('independent verifier did not confirm recovery')
        return VerifiedRecovery(execution_id=execution_id, plan_hash=plan_hash,
                    safe_query_version=safe_query_version, checks=result['checks'])