"""Read diagnostics and persist audit evidence, without query-runner authority."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from task_agent.console.analysis_contract import AnalysisReceipt, DiagnosticSnapshot, snapshot_hash
from task_agent.console.incident_contract import InvestigationPlan
from task_agent.control.canonical import canonical_json, content_hash
from task_agent.control.models import PlanContent
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureUnavailableError


class DiagnosticReader:
    def __init__(self, audience, credential_factory):
        self.audience = str(UUID(audience))
        self.credential_factory = credential_factory

    async def read(self):
        try:
            with self.credential_factory() as credential:
                token = await asyncio.to_thread(credential.get_token, f'api://{self.audience}/.default')
                async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
                    response = await client.get('https://ca-learningnemo-diagnostic-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io/v1/diagnostics/current',
                        headers={'Authorization': 'Bearer ' + token.token})
            if response.status_code != 200 or len(response.content) > 65536:
                raise SqlProcedureUnavailableError('diagnostic read not confirmed')
            return DiagnosticSnapshot.model_validate(response.json())
        except (ValueError, httpx.HTTPError) as error:
            raise SqlProcedureUnavailableError('read-only diagnostics unavailable; no workload changed') from error


class DatabaseAnalysisService:
    def __init__(self, client, reader, clock=lambda: datetime.now(UTC)):
        self.client, self.reader, self.clock = client, reader, clock

    async def analyze(self, sponsor_hash):
        snapshot = await self.reader.read()
        receipt = AnalysisReceipt(analysis_id='analysis-' + uuid4().hex, observed_at=self.clock(), snapshot=snapshot, evidence_hash=snapshot_hash(snapshot))
        rows = await self.client.call('control.usp_record_readonly_analysis', {'sponsor_hash': sponsor_hash, 'analysis_json': canonical_json(receipt)})
        if len(rows) != 1 or dict(rows[0]) != {'analysis_id': receipt.analysis_id}:
            raise SqlProcedureUnavailableError('analysis evidence persistence unconfirmed')
        return receipt

    async def latest(self, sponsor_hash):
        rows = await self.client.call('control.usp_get_readonly_analysis', {'sponsor_hash': sponsor_hash})
        if not rows:
            return None
        if len(rows) != 1 or set(rows[0]) != {'analysis_json'}:
            raise SqlProcedureUnavailableError('invalid analysis evidence')
        try:
            return AnalysisReceipt.model_validate_json(rows[0]['analysis_json'])
        except ValueError as error:
            raise SqlProcedureUnavailableError('invalid analysis receipt') from error

    async def propose(self, request, sponsor_hash):
        receipt = await self.latest(sponsor_hash)
        if receipt is None or receipt.analysis_id != request.analysis_id or receipt.evidence_hash != request.evidence_hash:
            raise OperationDeniedError('analysis changed; refresh before proposing')
        if not 0 <= (self.clock() - receipt.observed_at).total_seconds() <= 900:
            raise OperationDeniedError('analysis expired; run read-only analysis again')
        if receipt.snapshot.active_query_version != 'cycle-unsafe-v1':
            raise OperationDeniedError('no registered change is proposed for this query version')
        suffix = receipt.analysis_id.removeprefix('analysis-')
        content = PlanContent(task_id='task-' + suffix, engagement_id='engagement-' + suffix, workspace_id='workspace-' + suffix,
            logical_agent_id='analyst-' + suffix, operation_id='remediate.activate-cycle-safe-query-v1', target_resource='lab.QueryVersions/cycle-safe-v1',
            safe_query_version='cycle-safe-v1', rollback_version='cycle-unsafe-v1', parameters={'query_version': 'cycle-safe-v1'})
        plan = InvestigationPlan(plan_id='plan-' + suffix, content=content.model_dump(), plan_hash=content_hash(content),
            state='draft', version=1, created_by_hash=sponsor_hash, created_at=self.clock())
        rows = await self.client.call('control.usp_propose_readonly_analysis', {'analysis_id': receipt.analysis_id, 'sponsor_hash': sponsor_hash,
            'evidence_hash': receipt.evidence_hash, 'plan_json': plan.model_dump_json()})
        if len(rows) != 1 or dict(rows[0]) != {'plan_id': plan.plan_id, 'plan_hash': plan.plan_hash}:
            raise SqlProcedureUnavailableError('proposal unconfirmed; refresh the saved plan')
        return {'planId': plan.plan_id, 'planHash': plan.plan_hash, 'state': 'draft'}