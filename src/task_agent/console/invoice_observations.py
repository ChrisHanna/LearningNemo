"""Workload-authenticated diagnostic observations, separate from agent authority."""

from datetime import UTC, datetime

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from task_agent.control.invoice_contract import InvoiceEvidence
from task_agent.console.invoice_availability import validate_availability


class ObservationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scenario_id: str = Field(pattern=r'^[a-f0-9]{32}$')


def add_observation_route(app, repository, provider, operator_object_id, expires_at, availability_mode='leased'):
    validate_availability(availability_mode, expires_at)
    bearer = HTTPBearer(auto_error=False)
    @app.post('/internal/invoice-observation')
    async def observe(body: ObservationRequest, value=Depends(bearer)):
        if expires_at is not None and datetime.now(UTC) >= expires_at: raise HTTPException(503, 'Service lease expired')
        if value is None: raise HTTPException(401, 'Workload identity required')
        try:
            identity = await provider.verify(value.credentials)
        except Exception:
            raise HTTPException(401, 'Invalid workload identity') from None
        if not identity.active or identity.object_id != operator_object_id:
            raise HTTPException(403, 'Only the invoice coordinator may observe')
        rows = await repository.client.call('ops.usp_diagnose_invoice_summary', body.model_dump())
        if len(rows) != 1: raise HTTPException(409, 'Scenario observation unavailable')
        evidence = InvoiceEvidence(**rows[0])
        if evidence.scenario_id != body.scenario_id: raise HTTPException(409, 'Observation scope differs')
        batches = await repository.client.call('ops.usp_diagnose_invoice_batches', body.model_dump())
        fields = {'invoice_id','order_id','customer_id','batch_id','attempt','amount_cents','quarantined'}
        if len(batches) > 48 or any(set(row) != fields for row in batches):
            raise HTTPException(503, 'Unexpected diagnostic rows')
        after = await repository.client.call('ops.usp_diagnose_invoice_summary', body.model_dump())
        if len(after) != 1 or after[0]['revision'] != evidence.revision:
            raise HTTPException(409, 'Database changed during observation')
        return {'source':'diagnostic-sql-observation', 'summary':evidence.model_dump(mode='json'),
            'rows':[dict(row) for row in batches], 'observed_at':datetime.now(UTC).isoformat()}


class RemoteInvoiceObserver:
    def __init__(self, client, credential, audience):
        self.client, self.credential, self.audience = client, credential, audience

    async def snapshot(self, scenario_id):
        import asyncio
        token = await asyncio.to_thread(self.credential.get_token, self.audience+'/.default')
        response = await self.client.post('https://ca-nemo-invoice-planning-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io/internal/invoice-observation',
            headers={'Authorization':'Bearer '+token.token}, json={'scenario_id':scenario_id}, timeout=60)
        if response.status_code != 200 or len(response.content) > 32768:
            raise RuntimeError('diagnostic observation unavailable')
        result = response.json()
        if result.get('source') != 'diagnostic-sql-observation' or result.get('summary',{}).get('scenario_id') != scenario_id:
            raise RuntimeError('diagnostic observation scope differs')
        return result