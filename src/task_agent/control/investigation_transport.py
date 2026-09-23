"""Fixed diagnostic and owned-query calls under the Incident service identity."""

import asyncio
from uuid import UUID

import httpx

from task_agent.control.models import OperationReceipt
from task_agent.control.sql_backend import SqlProcedureUnavailableError


class ManagedInvestigationWorkers:
    def __init__(self, *, diagnostic_audience, query_audience, credential):
        self.audiences = {'diagnostic': str(UUID(diagnostic_audience)), 'query-runner': str(UUID(query_audience))}
        self.credential = credential

    async def request(self, service, method, path, body=None):
        origin = f'https://ca-learningnemo-{service}-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io'
        try:
            token = await asyncio.to_thread(self.credential.get_token, f'api://{self.audiences[service]}/.default')
            async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
                response = await client.request(method, origin + path, headers={'Authorization': f'Bearer {token.token}'}, json=body)
            if response.status_code != 200 or len(response.content) > 65536:
                raise SqlProcedureUnavailableError('trusted investigation worker did not confirm the operation')
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError('unexpected worker response')
            return result
        except (httpx.HTTPError, ValueError) as error:
            raise SqlProcedureUnavailableError('investigation outcome unconfirmed; keep the request ID') from error

    async def investigate(self, *, run_id):
        for service in self.audiences:
            if (await self.request(service, 'GET', '/readyz')).get('status') != 'ready':
                raise SqlProcedureUnavailableError('investigation worker not ready')
        try:
            started = await self.request('query-runner', 'POST', '/v1/query-runs/start', {'run_id': run_id})
            if started.get('result_code') != 'owned_query_started' or started.get('result') != {'run_id': run_id, 'state': 'running'}:
                raise SqlProcedureUnavailableError('owned query start unconfirmed')
            snapshot = await self.request('diagnostic', 'GET', '/v1/diagnostics/current')
        finally:
            containment = OperationReceipt.model_validate(await self.request('query-runner', 'POST', '/v1/query-runs/cancel', {'run_id': run_id}))
        return snapshot, containment