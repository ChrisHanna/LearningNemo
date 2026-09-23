"""Private execution API client; no automatic mutation retries."""

import re
from urllib.parse import urlsplit

import httpx

from task_agent.console.execution_contract import ExecutionStatus


class ExecutionUnavailable(RuntimeError):
    pass


class RemoteExecutionService:
    def __init__(self, origin):
        parsed = urlsplit(origin)
        if origin != 'https://ca-learningnemo-execution-dev.internal.jollybeach-503c7ed1.eastus.azurecontainerapps.io' or parsed.scheme != 'https':
            raise ValueError('fixed private execution origin required')
        self.origin = origin

    async def request(self, method, path, token, body=None):
        try:
            async with httpx.AsyncClient(timeout=150, follow_redirects=False) as client:
                response = await client.request(method, self.origin + path, headers={'Authorization': 'Bearer ' + token}, json=body)
            if response.status_code != 200:
                raise ExecutionUnavailable('Execution outcome not confirmed. Check persisted status; do not retry the write.')
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ExecutionUnavailable('Execution service unavailable. Check persisted status before continuing.') from error

    async def status(self, plan_id, token):
        if not re.fullmatch(r'plan-[A-Za-z0-9-]{1,120}', plan_id):
            raise ValueError('invalid plan identifier')
        result = await self.request('GET', '/executions/' + plan_id, token)
        try:
            if set(result) != {'source', 'execution'} or result['source'] != 'azure-sql':
                raise ValueError('unexpected execution response')
            if result['execution'] is not None:
                record = ExecutionStatus.model_validate(result['execution'])
                if record.plan_id != plan_id:
                    raise ValueError('execution plan mismatch')
                result['execution'] = record.model_dump(mode='json')
            return result
        except (ValueError, TypeError) as error:
            raise ExecutionUnavailable('Execution evidence invalid') from error

    async def execute(self, plan_id, body, token):
        if not re.fullmatch(r'plan-[A-Za-z0-9-]{1,120}', plan_id):
            raise ValueError('invalid plan identifier')
        result = await self.request('POST', '/executions/' + plan_id, token, body.model_dump())
        if (result.get('state') != 'verified' or result.get('planId') != plan_id or result.get('planHash') != body.plan_hash
                or result.get('completionRequired') is not True or not re.fullmatch(r'execution-[a-f0-9]{32}', result.get('executionId', ''))):
            raise ExecutionUnavailable('Execution response unconfirmed; inspect persisted status')
        return result

    async def complete(self, execution_id, body, token):
        if not re.fullmatch(r'execution-[a-f0-9]{32}', execution_id):
            raise ValueError('invalid execution identifier')
        result = await self.request('POST', '/executions/' + execution_id + '/complete', token, body.model_dump())
        if result != {'executionId': execution_id, 'planHash': body.plan_hash, 'state': 'completed'}:
            raise ExecutionUnavailable('Completion unconfirmed; inspect persisted status')
        return result

    async def reconcile(self, plan_id, token):
        if not re.fullmatch(r'plan-[A-Za-z0-9-]{1,120}', plan_id):
            raise ValueError('invalid plan identifier')
        result = await self.request('POST', '/executions/' + plan_id + '/reconcile', token)
        record = ExecutionStatus.model_validate(result)
        if record.plan_id != plan_id:
            raise ExecutionUnavailable('reconciliation plan mismatch')
        return record.model_dump(mode='json')