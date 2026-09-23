"""Trusted, sponsor-bound incident initiation; never approval or remediation."""

from datetime import datetime, timedelta
import hashlib
import re
from typing import Protocol
from uuid import UUID

from task_agent.console.incident_contract import InvestigationPlan
from task_agent.control.canonical import content_hash
from task_agent.control.incident import TrustedInvestigationRecorder
from task_agent.control.models import DiagnosisRecord, OperationReceipt, PlanContent
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureClient, SqlProcedureUnavailableError


class InvestigationWorkers(Protocol):
    async def investigate(self, *, run_id: str) -> tuple[dict, OperationReceipt]: ...


class TrustedIncidentInitiator:
    def __init__(self, client: SqlProcedureClient, workers: InvestigationWorkers, *, clock, expires_at: datetime):
        self.client, self.workers, self.clock, self.expires_at = client, workers, clock, expires_at

    async def start(self, *, sponsor_hash: str, request_id: str) -> dict:
        if re.fullmatch(r'[0-9a-f]{64}', sponsor_hash) is None or str(UUID(request_id)) != request_id:
            raise OperationDeniedError('verified sponsor and canonical request ID required')
        now = self.clock()
        expiry = min(now + timedelta(minutes=30), self.expires_at)
        if now.tzinfo is None or (expiry - now).total_seconds() < 300:
            raise OperationDeniedError('investigation lease is too short')
        suffix = hashlib.sha256((sponsor_hash + ':' + request_id).encode()).hexdigest()[:32]
        context = {name: prefix + suffix for name, prefix in (('task_id', 'task-'), ('engagement_id', 'engagement-'), ('workspace_id', 'workspace-'), ('logical_agent_id', 'investigator-'))}
        run_id, plan_id = 'run-' + suffix, 'plan-' + suffix
        rows = await self.client.call('control.usp_begin_human_investigation', {
            **context, 'request_id': request_id, 'sponsor_hash': sponsor_hash, 'run_id': run_id,
            'plan_id': plan_id, 'expires_at': expiry, 'now_utc': now,
        })
        if len(rows) != 1 or set(rows[0]) != {'state', 'plan_id'} or rows[0]['plan_id'] != plan_id:
            raise SqlProcedureUnavailableError('initiation admission unconfirmed; retain request ID')
        state = rows[0]['state']
        if state == 'recorded':
            return {'state': 'draft', 'planId': plan_id, 'requestId': request_id, 'replayed': True}
        if state != 'admitted':
            raise OperationDeniedError('investigation already running or needs reconciliation; retain request ID')
        snapshot, containment = await self.workers.investigate(run_id=run_id)
        if snapshot.get('active_query_version') != 'cycle-unsafe-v1' or snapshot.get('query_run_states', {}).get(run_id) != 'running':
            raise OperationDeniedError('trusted diagnostic evidence does not match the owned incident')
        content = PlanContent(**context, operation_id='remediate.activate-cycle-safe-query-v1',
            target_resource='lab.QueryVersions/cycle-safe-v1', safe_query_version='cycle-safe-v1',
            rollback_version='cycle-unsafe-v1', parameters={'query_version': 'cycle-safe-v1'})
        diagnosis = DiagnosisRecord(**context, diagnosis_id='diagnosis-' + suffix,
            summary='The controlled query uses cycle-unsafe-v1 and the owned run was observed running. Propose the registered cycle-safe query after independent review.',
            evidence_refs=('query-run:' + run_id, 'diagnostic-sha256:' + content_hash(snapshot)), created_at=self.clock())
        plan = InvestigationPlan(plan_id=plan_id, content=content.model_dump(mode='json'), plan_hash=content_hash(content), state='draft',
            version=1, created_by_hash=sponsor_hash, created_at=self.clock())
        await TrustedInvestigationRecorder(self.client).record(plan=plan, diagnosis=diagnosis, containment=containment,
            run_id=run_id, sponsor_hash=sponsor_hash, expected_task_version=1, now=self.clock())
        return {'state': 'draft', 'planId': plan_id, 'requestId': request_id, 'replayed': False}