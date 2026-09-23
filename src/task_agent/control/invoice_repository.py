"""Trusted invoice adapters; agent identities and procedure names are server-owned."""

from datetime import UTC, datetime
import hashlib
import json
import re
import secrets
from uuid import uuid4

from task_agent.control.canonical import canonical_json, content_hash
from task_agent.control.invoice_contract import InvoiceEvidence, InvoicePlan, InvoiceStep, PlanningDecision
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureUnavailableError


CAPABILITY = re.compile(r'^([a-f0-9]{32})\.([A-Za-z0-9_-]{43})$')


def new_capability(run_id):
    if not re.fullmatch(r'[a-f0-9]{32}', run_id):
        raise ValueError('invalid run identifier')
    return run_id + '.' + secrets.token_urlsafe(32)


def capability_context(token):
    matched = CAPABILITY.fullmatch(token)
    if not matched:
        raise OperationDeniedError('invalid run capability')
    return matched.group(1), hashlib.sha256(token.encode('ascii')).hexdigest()


class InvoiceRepository:
    def __init__(self, client, clock=lambda: datetime.now(UTC), read_client=None):
        self.client, self.clock = client, clock
        self.read_client = read_client or client

    async def admit(self, token, tool):
        run_id, digest = capability_context(token)
        rows = await self.client.call('control.usp_admit_invoice_tool', {'run_id': run_id, 'capability_hash': digest, 'tool': tool})
        if len(rows) != 1 or set(rows[0]) != {'scenario_id', 'kind', 'sandbox_id'}:
            raise OperationDeniedError('run authority expired, revoked, exhausted, or denied')
        return dict(rows[0])

    async def summary(self, token):
        authority = await self.admit(token, 'invoice_summary')
        if authority['kind'] != 'planning':
            raise OperationDeniedError('planning capability required')
        rows = await self.client.call('ops.usp_diagnose_invoice_summary', {'scenario_id': authority['scenario_id']})
        if len(rows) != 1:
            raise SqlProcedureUnavailableError('diagnostic scenario unavailable')
        evidence = InvoiceEvidence(**rows[0])
        if evidence.scenario_id != authority['scenario_id']:
            raise SqlProcedureUnavailableError('diagnostic evidence scope differs')
        run_id, _ = capability_context(token)
        recorded = await self.client.call('control.usp_record_invoice_evidence', {
            'run_id': run_id, 'evidence_hash': evidence.evidence_hash, 'evidence_json': canonical_json(evidence)})
        if len(recorded) != 1 or recorded[0] != {'evidence_hash': evidence.evidence_hash}:
            raise SqlProcedureUnavailableError('diagnostic persistence unconfirmed')
        return {**evidence.model_dump(mode='json'), 'evidence_hash': evidence.evidence_hash,
            'proposal_parameter_reference': {'target': evidence.scenario_id, 'first_expected_revision': evidence.revision,
                'quarantine_duplicate_set_hash': evidence.duplicate_set_hash,
                'registered_operations': ['invoice.quarantine-duplicates.v1', 'invoice.rebuild-total.v1', 'invoice.activate-idempotent-import.v1']}}

    async def batches(self, token):
        authority = await self.admit(token, 'invoice_batches')
        if authority['kind'] != 'planning':
            raise OperationDeniedError('planning capability required')
        rows = await self.client.call('ops.usp_diagnose_invoice_batches', {'scenario_id': authority['scenario_id']})
        expected = {'invoice_id', 'order_id', 'customer_id', 'batch_id', 'attempt', 'amount_cents', 'quarantined'}
        if len(rows) > 48 or any(set(row) != expected for row in rows):
            raise SqlProcedureUnavailableError('batch evidence shape differs')
        return {'scenario_id': authority['scenario_id'], 'rows': [dict(row) for row in rows]}

    async def save_proposal(self, decision: PlanningDecision, *, sponsor_hash, run_id, sandbox_id, evidence: InvoiceEvidence):
        if decision.evidence_hash != evidence.evidence_hash or not 0 <= (self.clock() - evidence.observed_at).total_seconds() <= 900:
            raise OperationDeniedError('proposal evidence missing or stale')
        if decision.outcome != 'proposal':
            return {'outcome': decision.outcome, 'diagnosis': decision.diagnosis, 'rationale': decision.rationale, 'plan': None}
        if any(step.target != evidence.scenario_id for step in decision.steps) or decision.steps[0].expected_revision != evidence.revision:
            raise OperationDeniedError('proposal target or revision differs')
        for step in decision.steps:
            if step.duplicate_set_hash is not None and step.duplicate_set_hash != evidence.duplicate_set_hash:
                raise OperationDeniedError('proposal changes the evidenced duplicate set')
        plan = InvoicePlan(plan_id=uuid4().hex, scenario_id=evidence.scenario_id, sponsor_hash=sponsor_hash,
                           planning_run_id=run_id, planning_sandbox_id=sandbox_id, evidence_hash=evidence.evidence_hash,
                           created_at=self.clock(), diagnosis=decision.diagnosis, rationale=decision.rationale,
                           risks=decision.risks, steps=decision.steps)
        rows = await self.client.call('control.usp_record_invoice_plan', {
            'plan_json': canonical_json(plan), 'plan_hash': plan.plan_hash, 'sponsor_hash': sponsor_hash})
        if len(rows) != 1 or rows[0] != {'plan_id': plan.plan_id, 'plan_hash': plan.plan_hash}:
            raise SqlProcedureUnavailableError('plan persistence unconfirmed')
        return {'outcome': 'proposal', 'plan': plan.model_dump(mode='json'), 'plan_hash': plan.plan_hash}

    async def execute_step(self, token, step: InvoiceStep):
        run_id, digest = capability_context(token)
        rows = await self.client.call('ops.usp_execute_invoice_step', {
            'run_id': run_id, 'capability_hash': digest, 'step_json': canonical_json(step)})
        if len(rows) != 1 or set(rows[0]) != {'receipt_json'}:
            raise SqlProcedureUnavailableError('execution unconfirmed; read persisted receipts before proceeding')
        receipt = json.loads(rows[0]['receipt_json'])
        expected = {'run_id': run_id, 'step_id': step.step_id, 'step_hash': content_hash(step),
                    'scenario_id': step.target, 'revision': step.expected_revision + 1, 'operation': step.operation}
        if receipt != expected:
            raise SqlProcedureUnavailableError('execution receipt does not match the approved request')
        return receipt

    async def plans(self, sponsor_hash):
        rows = await self.read_client.call('control.usp_read_invoice_plans', {'sponsor_hash': sponsor_hash})
        return self._plans(rows)

    async def reviews(self):
        return self._plans(await self.read_client.call('control.usp_review_invoice_plans', {}))

    async def scenario(self, scenario_id, sponsor_hash):
        rows = await self.read_client.call('control.usp_read_invoice_scenario', {'scenario_id': scenario_id, 'sponsor_hash': sponsor_hash})
        if not rows:
            return {'scenario_id': scenario_id, 'state': 'unconfirmed', 'source': 'owned-scenario-status'}
        return self.scenario_receipt(rows, scenario_id)

    @staticmethod
    def scenario_receipt(rows, scenario_id):
        expected = {'scenario_id', 'variant', 'created_at', 'expires_at', 'planning_before'}
        if len(rows) != 1 or set(rows[0]) != expected or rows[0]['scenario_id'] != scenario_id:
            raise SqlProcedureUnavailableError('scenario receipt differs; inspect status without replay')
        for name in ('created_at', 'expires_at', 'planning_before'):
            try:
                value = datetime.fromisoformat(rows[0][name].replace('Z', '+00:00'))
                if value.tzinfo is None: raise ValueError('timezone required')
            except (TypeError, ValueError, AttributeError):
                raise SqlProcedureUnavailableError('scenario deadline unconfirmed') from None
        return {**dict(rows[0]), 'state': 'created', 'source': 'owned-scenario-status'}

    @staticmethod
    def _plans(rows):
        result = []
        for row in rows:
            plan = InvoicePlan.model_validate_json(row['plan_json'])
            if plan.plan_hash != row['plan_hash']:
                raise SqlProcedureUnavailableError('stored invoice plan hash differs')
            record = {**dict(row), 'plan_json': plan.model_dump(mode='json')}
            if 'evidence_json' in record:
                evidence = InvoiceEvidence.model_validate_json(record.pop('evidence_json'))
                if evidence.evidence_hash != plan.evidence_hash or evidence.scenario_id != plan.scenario_id:
                    raise SqlProcedureUnavailableError('review evidence binding differs')
                record['diagnostics'] = {**evidence.model_dump(mode='json'), 'evidence_hash': evidence.evidence_hash}
            result.append(record)
        return result

    async def submit(self, plan_id, plan_hash, sponsor_hash):
        rows = await self.client.call('control.usp_submit_invoice_plan', {'plan_id': plan_id, 'plan_hash': plan_hash, 'sponsor_hash': sponsor_hash})
        if len(rows) != 1 or rows[0] != {'plan_id': plan_id}: raise SqlProcedureUnavailableError('submission receipt unconfirmed')
        return rows

    async def decide(self, plan_id, plan_hash, reviewer_hash, decision):
        if decision not in ('approve', 'reject'):
            raise OperationDeniedError('unrecognized review decision')
        rows = await self.client.call('control.usp_decide_invoice_plan', {
            'plan_id': plan_id, 'plan_hash': plan_hash, 'reviewer_hash': reviewer_hash, 'decision': decision})
        if len(rows) != 1 or rows[0] != {'plan_id': plan_id}: raise SqlProcedureUnavailableError('decision receipt unconfirmed')
        return rows

    async def verify(self, run_id):
        rows = await self.client.call('ops.usp_verify_invoice_run', {'run_id': run_id})
        if len(rows) != 1:
            raise SqlProcedureUnavailableError('independent verification unavailable')
        checks = json.loads(rows[0]['verification_json'])
        expected = {'no_duplicates', 'legitimate_invoices_preserved', 'total_reconciles', 'idempotent_version_active', 'replay_created_no_invoices'}
        if set(checks) != expected or any(value is not True for value in checks.values()):
            raise OperationDeniedError('independent verification did not pass')
        return checks