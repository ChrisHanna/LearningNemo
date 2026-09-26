"""Sponsor-bound orchestration; Planning and Execution never share capabilities."""

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from task_agent.control.canonical import content_hash
from task_agent.control.invoice_agent import RunManifest
from task_agent.control.invoice_contract import InvoiceEvidence, InvoicePlan, PlanningDecision
from task_agent.control.invoice_repository import capability_context, new_capability
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureUnavailableError



def run_authority(run_id, prepared):
    """Mint the run capability here, or accept the hash of one an OpenShell provider holds on the host."""
    if 'capability_hash' in prepared:
        if not re.fullmatch(r'[a-f0-9]{64}', str(prepared['capability_hash'])):
            raise OperationDeniedError('provider-held run capability hash is malformed')
        return None, prepared['capability_hash']
    capability = new_capability(run_id)
    return capability, capability_context(capability)[1]

class InvoiceController:
    def __init__(self, *, admission_client, incident_repository, verifier_repository, runtime, audit, clock=lambda: datetime.now(UTC), observer=None, sandbox_probe=None, maintenance=None):
        self.admission, self.incidents, self.verifier = admission_client, incident_repository, verifier_repository
        self.runtime, self.audit, self.clock = runtime, audit, clock
        self.lock = asyncio.Lock()
        self.maintenance_active = False
        self.observer = observer
        self.sandbox_probe = sandbox_probe
        self.maintenance = maintenance

    async def _snapshot(self, scenario_id):
        if self.observer is None: return None
        try:
            return await self.observer.snapshot(scenario_id)
        except Exception:
            return None

    async def _event(self, sponsor_hash, run_id, event):
        await self.audit(sponsor_hash=sponsor_hash, run_id=run_id, event=event)

    async def _cleanup(self, kind, run_id, sponsor_hash, prepared, admission_attempted, result):
        proof = None
        binding = {'job_id': run_id, 'sponsor_hash': sponsor_hash, 'sandbox_id': prepared['sandbox_id']}
        try:
            if admission_attempted:
                rows = await self.admission.call('control.usp_revoke_invoice_run', {'run_id': run_id, 'sponsor_hash': sponsor_hash})
                if len(rows) != 1 or rows[0] != {'run_id': run_id}:
                    raise SqlProcedureUnavailableError('revocation unconfirmed; sandbox test prohibited')
                await self._event(sponsor_hash, run_id, {'source':'workspace-controller', 'event_type':'authority-revoked', 'kind':kind})
                if result is not None and self.sandbox_probe is not None:
                    rows = await self.admission.call('control.usp_claim_invoice_sandbox_test', binding)
                    if len(rows) != 1 or set(rows[0]) != {'test_requested'} or rows[0]['test_requested'] not in (True, False):
                        raise SqlProcedureUnavailableError('sandbox test claim unconfirmed; no replay')
                    if rows[0]['test_requested']:
                        await self._event(sponsor_hash, run_id, {'source':'workspace-controller', 'event_type':'sandbox-test-started',
                            'kind':kind, 'sandbox_id':prepared['sandbox_id'], 'actor':'controlled-probe'})
                        try:
                            proof = await self.sandbox_probe(self.runtime, kind, run_id, prepared)
                        except Exception:
                            proof = {'run_id':run_id, 'sandbox_id':prepared['sandbox_id'], 'kind':kind, 'scope':'same-agent-sandbox',
                                'outcome':'unconfirmed', 'actor':'controlled-probe', 'agent_requested':False,
                                'agent_authority_revoked':True, 'probe_capability_issued':False, 'policy_hash':prepared['policy_hash'],
                                'detail':'Probe outcome unconfirmed. No sandbox was restarted or test replayed.'}
        finally:
            await asyncio.to_thread(self.runtime.stop, kind, run_id)
            await self._event(sponsor_hash, run_id, {'source':'workspace-controller', 'event_type':'sandbox-stopped',
                'kind':kind, 'sandbox_id':prepared['sandbox_id']})
        if proof is not None:
            proof['sandbox_stopped'] = True
            rows = await self.admission.call('control.usp_record_invoice_sandbox_test', {**binding, 'receipt_json':json.dumps(proof,separators=(',',':'))})
            if len(rows) != 1 or rows[0] != {'job_id':run_id}:
                raise SqlProcedureUnavailableError('sandbox test persistence unconfirmed; read existing receipts')
            result['sandbox_test'] = proof
            await self._event(sponsor_hash, run_id, {'source':'workspace-controller', 'event_type':'sandbox-test-recorded',
                'kind':kind, 'sandbox_id':prepared['sandbox_id'], 'outcome':proof['outcome'], 'actor':'controlled-probe'})

    def _manifest(self, kind, run_id, prepared, capability, expires_at, plan=None):
        if prepared['run_id'] != run_id:
            raise OperationDeniedError('sandbox preparation belongs to another run')
        return RunManifest(kind=kind, run_id=run_id, sandbox_id=prepared['sandbox_id'], capability=capability,
            gateway_origin=f'https://ca-nemo-invoice-{kind}-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io',
            expires_at=expires_at.isoformat(), plan=plan)

    async def reconcile(self, plan_id, plan_hash, *, sponsor_hash):
        if self.lock.locked():
            raise OperationDeniedError('another invoice agent run is active')
        async with self.lock:
            plans = await self.incidents.plans(sponsor_hash)
            row = next((item for item in plans if item['plan_json']['plan_id'] == plan_id and item['plan_hash'] == plan_hash), None)
            if row is None or row['state'] not in ('executing', 'verified', 'completed') or not row.get('execution_run_id'):
                raise OperationDeniedError('owned execution record required')
            run_id = row['execution_run_id']
            jobs = await self.admission.call('control.usp_get_invoice_job', {'job_id': run_id, 'sponsor_hash': sponsor_hash})
            if len(jobs) != 1 or jobs[0]['state'] not in ('finished', 'uncertain'):
                raise OperationDeniedError('agent job must finish before reconciliation')
            plan = InvoicePlan.model_validate(row['plan_json'])
            rows = await self.incidents.client.call('control.usp_get_invoice_execution', {'run_id': run_id, 'sponsor_hash': sponsor_hash})
            receipts = [json.loads(item['receipt_json']) for item in rows]
            expected = [{'run_id': run_id, 'step_id': item.step_id, 'step_hash': content_hash(item), 'scenario_id': item.target,
                'revision': item.expected_revision + 1, 'operation': item.operation} for item in plan.steps]
            if receipts != expected[:len(receipts)] or len(receipts) > len(expected):
                raise SqlProcedureUnavailableError('persisted step receipts differ from the exact plan')
            result = {'plan_id': plan_id, 'plan_hash': plan_hash, 'run_id': run_id, 'receipts': receipts, 'replayed_steps': 0}
            if len(receipts) != len(expected):
                return {**result, 'state': 'partial', 'checks': None, 'completion_required': False}
            checks = json.loads(row['verification_json']) if row['state'] == 'completed' else await self.verifier.verify(run_id)
            return {**result, 'state': 'completed' if row['state'] == 'completed' else 'verified', 'checks': checks,
                'completion_required': row['state'] != 'completed'}

    async def analyze(self, scenario_id, *, sponsor_hash, run_id=None):
        if self.lock.locked() and not self.maintenance_active:
            raise OperationDeniedError('another invoice agent run is active')
        async with self.lock:
            scenario = await self.incidents.scenario(scenario_id, sponsor_hash)
            deadline = scenario.get('planning_before')
            if scenario.get('state') != 'created' or not deadline or datetime.fromisoformat(deadline.replace('Z', '+00:00')) <= self.clock():
                raise OperationDeniedError('owned scenario is unconfirmed, expired, or too short for Planning')
            run_id = run_id or uuid4().hex
            if self.maintenance is not None:
                await self.maintenance()
            await self._event(sponsor_hash, run_id, {'source': 'workspace-controller', 'event_type': 'preparing-sandbox', 'kind': 'planning'})
            prepared = await asyncio.to_thread(self.runtime.prepare, 'planning', run_id)
            admission_attempted = False
            result = None
            try:
                capability, digest = run_authority(run_id, prepared)
                expiry = self.clock() + timedelta(minutes=8)
                admission_attempted = True
                rows = await self.admission.call('control.usp_register_invoice_planning', dict(run_id=run_id, scenario_id=scenario_id,
                    sponsor_hash=sponsor_hash, sandbox_id=prepared['sandbox_id'], policy_hash=prepared['policy_hash'],
                    capability_hash=digest, expires_at=expiry, call_limit=24))
                if len(rows) != 1 or rows[0] != {'run_id': run_id}:
                    raise SqlProcedureUnavailableError('planning admission unconfirmed')
                await self._event(sponsor_hash, run_id, {'source': 'workspace-controller', 'event_type': 'sandbox-bound',
                    'sandbox_id': prepared['sandbox_id'], 'policy_hash': prepared['policy_hash'], 'kind': 'planning'})
                await self._event(sponsor_hash, run_id, {'source': 'workspace-controller', 'event_type': 'authority-issued', 'kind': 'planning', 'sandbox_id': prepared['sandbox_id']})
                manifest = self._manifest('planning', run_id, prepared, capability, expiry)
                async def observed(event):
                    await self._event(sponsor_hash, run_id, event)
                    if event.get('event_type') == 'tool-returned' and event.get('tool') == 'invoice_summary':
                        observation = await self._snapshot(scenario_id)
                        if observation:
                            summary = observation['summary']
                            await self._event(sponsor_hash,run_id,{'source':'workspace-controller','event_type':'diagnostic-observed',
                                'orders':summary['orders'],'active_invoices':summary['active_invoices'],'duplicate_invoices':summary['duplicate_invoices'],
                                'expected_cents':summary['expected_cents'],'reported_cents':summary['reported_cents'],'revision':summary['revision'],
                                'target':scenario_id,'reason':'Independent diagnostic read after agent response'})
                final = await self.runtime.execute(manifest, observed)
                decision = PlanningDecision.model_validate(final['decision'])
                evidence_rows = await self.incidents.client.call('control.usp_get_invoice_evidence', {
                    'run_id': run_id, 'sponsor_hash': sponsor_hash, 'evidence_hash': decision.evidence_hash})
                if len(evidence_rows) != 1:
                    raise OperationDeniedError('agent decision has no matching persisted evidence')
                evidence = InvoiceEvidence.model_validate_json(evidence_rows[0]['evidence_json'])
                saved = await self.incidents.save_proposal(decision, sponsor_hash=sponsor_hash, run_id=run_id,
                    sandbox_id=prepared['sandbox_id'], evidence=evidence)
                observation = await self._snapshot(scenario_id)
                result = {**saved, 'diagnostics': {**evidence.model_dump(mode='json'), 'evidence_hash': evidence.evidence_hash}, 'database_observation': observation}
                return result
            finally:
                await self._cleanup('planning',run_id,sponsor_hash,prepared,admission_attempted,result)

    async def execute(self, plan_id, plan_hash, *, sponsor_hash, run_id=None):
        if self.lock.locked() and not self.maintenance_active:
            raise OperationDeniedError('another invoice agent run is active')
        async with self.lock:
            plans = await self.incidents.plans(sponsor_hash)
            row = next((row for row in plans if row['plan_json']['plan_id'] == plan_id and row['plan_hash'] == plan_hash), None)
            if row is None or row['state'] != 'approved':
                raise OperationDeniedError('owned approved plan required')
            plan = InvoicePlan.model_validate(row['plan_json'])
            approval_expiry = row['approval_expires_at']
            if isinstance(approval_expiry, str):
                approval_expiry = datetime.fromisoformat(approval_expiry.replace('Z', '+00:00'))
            if approval_expiry.tzinfo is None:
                approval_expiry = approval_expiry.replace(tzinfo=UTC)
            if approval_expiry <= self.clock() + timedelta(minutes=2):
                raise OperationDeniedError('approval lifetime too short to prepare execution')
            deadline = row.get('execution_before')
            if not deadline or datetime.fromisoformat(deadline.replace('Z', '+00:00')) <= self.clock():
                raise OperationDeniedError('scenario or approval preparation window expired')
            run_id = run_id or uuid4().hex
            if self.maintenance is not None:
                await self.maintenance()
            await self._event(sponsor_hash, run_id, {'source': 'workspace-controller', 'event_type': 'preparing-sandbox', 'kind': 'execution'})
            prepared = await asyncio.to_thread(self.runtime.prepare, 'execution', run_id)
            admission_attempted = False
            result = None
            try:
                if prepared['sandbox_id'] == plan.planning_sandbox_id:
                    raise OperationDeniedError('execution reused Planning sandbox')
                capability, digest = run_authority(run_id, prepared)
                admission_attempted = True
                rows = await self.admission.call('control.usp_claim_invoice_execution', dict(plan_id=plan_id, plan_hash=plan_hash,
                    sponsor_hash=sponsor_hash, run_id=run_id, sandbox_id=prepared['sandbox_id'],
                    policy_hash=prepared['policy_hash'], capability_hash=digest))
                if len(rows) != 1 or InvoicePlan.model_validate_json(rows[0]['plan_json']).plan_hash != plan_hash:
                    raise SqlProcedureUnavailableError('execution claim unconfirmed; reconcile before proceeding')
                await self._event(sponsor_hash, run_id, {'source': 'workspace-controller', 'event_type': 'sandbox-bound',
                    'sandbox_id': prepared['sandbox_id'], 'policy_hash': prepared['policy_hash'], 'kind': 'execution'})
                await self._event(sponsor_hash, run_id, {'source': 'workspace-controller', 'event_type': 'authority-issued', 'kind': 'execution', 'sandbox_id': prepared['sandbox_id']})
                manifest = self._manifest('execution', run_id, prepared, capability, min(approval_expiry, self.clock() + timedelta(minutes=8)), plan)
                seen = set()
                async def observed(event):
                    await self._event(sponsor_hash, run_id, event)
                    if event.get('event_type') == 'tool-returned' and event.get('tool') == 'execute_step':
                        receipts = await self.incidents.client.call('control.usp_get_invoice_execution', {'run_id':run_id,'sponsor_hash':sponsor_hash})
                        for receipt_row in receipts:
                            receipt = json.loads(receipt_row['receipt_json'])
                            position = receipt['step_id']
                            if position in seen: continue
                            if not 1 <= position <= len(plan.steps) or receipt['run_id'] != run_id or receipt['step_hash'] != content_hash(plan.steps[position-1]):
                                raise SqlProcedureUnavailableError('receipt observation differs')
                            seen.add(position)
                            await self._event(sponsor_hash, run_id, {'source':'workspace-controller','event_type':'step-receipt-recorded',
                                'step_id':position,'operation':receipt['operation'],'target':receipt['scenario_id'],'revision':receipt['revision'],'receipt_hash':receipt['step_hash']})
                await self.runtime.execute(manifest, observed)
                await self._event(sponsor_hash, run_id, {'source': 'workspace-controller', 'event_type': 'verification-started', 'kind': 'execution'})
                checks = await self.verifier.verify(run_id)
                await self._event(sponsor_hash, run_id, {'source': 'workspace-controller', 'event_type': 'verification-passed', 'kind': 'execution'})
                observation = await self._snapshot(plan.scenario_id)
                result = {'run_id': run_id, 'plan_id': plan_id, 'plan_hash': plan_hash, 'checks': checks, 'completion_required': True, 'database_observation': observation}
                return result
            finally:
                await self._cleanup('execution',run_id,sponsor_hash,prepared,admission_attempted,result)