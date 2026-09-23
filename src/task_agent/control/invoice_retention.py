"""Fail-closed evidence checks for disposable, successfully completed sandboxes."""

from datetime import UTC, datetime, timedelta
import json
import re

from task_agent.control.canonical import content_hash
from task_agent.control.invoice_contract import InvoiceEvidence, InvoicePlan
from task_agent.control.invoice_sandbox import sandbox_name


CHECKS = ('no_duplicates', 'legitimate_invoices_preserved', 'total_reconciles',
          'idempotent_version_active', 'replay_created_no_invoices')


def retention_candidate(record, now=None, *, minimum_age_hours=24):
    if minimum_age_hours not in (0, 24):
        raise ValueError('fixed retention age required')
    now = now or datetime.now(UTC)
    run_id, sandbox_id = record['run_id'], record['sandbox_id']
    if any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value) for value in (run_id, sandbox_id)):
        raise ValueError('invalid retention binding')
    kind = record['kind']
    name = sandbox_name(kind, run_id)
    if record['state'] != 'finished':
        raise ValueError('only confirmed successful jobs can expire')
    stopped = datetime.fromisoformat(record['stopped_at'].replace('Z', '+00:00'))
    revoked = datetime.fromisoformat(record['revoked_at'].replace('Z', '+00:00'))
    if stopped.tzinfo is None or revoked.tzinfo is None or not revoked <= stopped <= now - timedelta(hours=minimum_age_hours):
        raise ValueError('revocation or retention age not established')
    events = record['events']
    if not any(item['event'].get('source') == 'workspace-controller' and item['event'].get('event_type') == 'sandbox-stopped'
               and item['event'].get('sandbox_id') == sandbox_id for item in events):
        raise ValueError('bound stop receipt missing')
    if not any(item['event'].get('source') == 'workspace-controller' and item['event'].get('event_type') == 'authority-revoked' for item in events):
        raise ValueError('revocation receipt missing')
    result = record['result']
    proof = result.get('sandbox_test')
    if proof is not None and (proof.get('outcome') != 'denied' or proof.get('run_id') != run_id or proof.get('sandbox_id') != sandbox_id or proof.get('sandbox_stopped') is not True):
        raise ValueError('sandbox test unresolved; preserve diagnostics')
    if kind == 'planning':
        if result.get('outcome') not in ('proposal', 'no-change'):
            raise ValueError('Planning outcome is not successful')
        evidence_values = dict(result['diagnostics'])
        digest = evidence_values.pop('evidence_hash')
        evidence = InvoiceEvidence.model_validate(evidence_values)
        if digest != evidence.evidence_hash or evidence.scenario_id != record['scenario_id']:
            raise ValueError('diagnostic evidence differs')
        if result['outcome'] == 'proposal':
            plan = InvoicePlan.model_validate(record['plan'])
            if plan.plan_hash != record['plan_hash'] or result['plan_hash'] != plan.plan_hash or result['plan'] != plan.model_dump(mode='json'):
                raise ValueError('Planning artifact differs')
            if plan.planning_run_id != run_id or plan.planning_sandbox_id != sandbox_id or plan.evidence_hash != digest:
                raise ValueError('Planning artifact binding differs')
        elif result.get('plan') is not None:
            raise ValueError('unexpected no-change plan')
    else:
        plan = InvoicePlan.model_validate(record['plan'])
        if plan.plan_hash != record['plan_hash'] or result.get('plan_hash') != plan.plan_hash or result.get('run_id') != run_id or result.get('plan_id') != plan.plan_id or plan.scenario_id != record['scenario_id'] or plan.planning_sandbox_id == sandbox_id:
            raise ValueError('Execution artifact differs')
        if record.get('plan_state') not in ('verified', 'completed') or any((record.get('verification') or {}).get(key) is not True or (result.get('checks') or {}).get(key) is not True for key in CHECKS):
            raise ValueError('independent verification missing')
        expected = [{'run_id': run_id, 'step_id': step.step_id, 'step_hash': content_hash(step),
                     'scenario_id': step.target, 'revision': step.expected_revision + 1, 'operation': step.operation} for step in plan.steps]
        if record['receipts'] != expected:
            raise ValueError('execution receipts differ')
    return {'schema_version': 1, 'run_id': run_id, 'sandbox_id': sandbox_id, 'name': name,
            'kind': kind, 'stopped_at': stopped.isoformat(), 'evidence': record}


async def retention_candidates(client, now=None, *, pressure=False, sponsor_hash=None):
    rows = await client.call('control.usp_read_invoice_retention', {})
    candidates, retained = [], []
    for row in rows:
        record = json.loads(row['record_json'])
        if sponsor_hash is not None and record.get('sponsor_hash') != sponsor_hash:
            continue
        try:
            candidates.append(retention_candidate(record, now, minimum_age_hours=0 if pressure else 24))
        except (ValueError, KeyError, TypeError):
            retained.append(record.get('run_id'))
    return candidates, retained