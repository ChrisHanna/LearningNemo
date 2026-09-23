from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from task_agent.control.invoice_contract import InvoicePlan
from task_agent.control.invoice_controller import InvoiceController
from task_agent.control.operations import OperationDeniedError
from test_invoice_contract import plan_values


class Runtime:
    def __init__(self, sandbox_id):
        self.sandbox_id = sandbox_id
        self.prepared = []
        self.stopped = []
    def prepare(self, kind, run_id):
        self.prepared.append((kind, run_id))
        return {'run_id': run_id, 'sandbox_id': self.sandbox_id, 'policy_hash': 'a' * 64}
    async def execute(self, manifest, on_event):
        await on_event({'source': 'agent-runtime', 'event_type': 'agent-finished', 'independently_verified': False})
        return {}
    def stop(self, kind, run_id):
        self.stopped.append((kind, run_id))


@pytest.mark.asyncio
@pytest.mark.parametrize('requested', [None, False, True])
async def test_execution_uses_new_sandbox_and_independent_verifier(requested):
    plan = InvoicePlan(**plan_values())
    calls, events = [], []
    async def call(name, values):
        calls.append(name)
        if name == 'control.usp_claim_invoice_sandbox_test': return ({'test_requested':requested},)
        if name == 'control.usp_record_invoice_sandbox_test': return ({'job_id':values['job_id']},)
        return [{'plan_json': plan.model_dump_json()}] if 'claim' in name else [{'run_id': values['run_id']}]
    async def plans(sponsor):
        assert sponsor == plan.sponsor_hash
        return [{'plan_json': plan.model_dump(mode='json'), 'plan_hash': plan.plan_hash, 'state': 'approved',
                 'execution_before': (datetime.now(UTC) + timedelta(minutes=8)).isoformat(),
                 'approval_expires_at': datetime.now(UTC) + timedelta(minutes=10)}]
    async def verify(run_id):
        calls.append('independent-verification')
        return {'fixture_checks': True}
    async def audit(**event): events.append(event)
    runtime = Runtime('b' * 32)
    async def probe(selected_runtime, kind, run_id, prepared):
        calls.append('probe')
        assert selected_runtime is runtime and kind == 'execution'
        assert runtime.prepared == [(kind, run_id)] and not runtime.stopped
        assert prepared['sandbox_id'] == runtime.sandbox_id
        return {'run_id':run_id,'sandbox_id':prepared['sandbox_id'],'outcome':'denied'}
    controller = InvoiceController(admission_client=SimpleNamespace(call=call), incident_repository=SimpleNamespace(plans=plans),
        verifier_repository=SimpleNamespace(verify=verify), runtime=runtime, audit=audit, sandbox_probe=probe if requested is not None else None)
    result = await controller.execute(plan.plan_id, plan.plan_hash, sponsor_hash=plan.sponsor_hash)
    assert result['completion_required'] is True
    assert calls == ['control.usp_claim_invoice_execution', 'independent-verification', 'control.usp_revoke_invoice_run'] + (
        ['control.usp_claim_invoice_sandbox_test'] if requested is not None else []) + (
        ['probe', 'control.usp_record_invoice_sandbox_test'] if requested else [])
    assert runtime.prepared == runtime.stopped
    assert [entry['event']['event_type'] for entry in events] == ['preparing-sandbox', 'sandbox-bound', 'authority-issued', 'agent-finished',
        'verification-started', 'verification-passed', 'authority-revoked'] + (
        ['sandbox-test-started'] if requested else []) + ['sandbox-stopped'] + (['sandbox-test-recorded'] if requested else [])
    assert ('sandbox_test' in result) is bool(requested)
    if requested: assert result['sandbox_test']['sandbox_stopped'] is True


@pytest.mark.asyncio
async def test_reusing_planning_sandbox_never_claims_execution():
    plan = InvoicePlan(**plan_values())
    async def plans(sponsor):
        return [{'plan_json': plan.model_dump(mode='json'), 'plan_hash': plan.plan_hash, 'state': 'approved',
                 'execution_before': (datetime.now(UTC) + timedelta(minutes=8)).isoformat(),
                 'approval_expires_at': datetime.now(UTC) + timedelta(minutes=10)}]
    async def forbidden(*args, **kwargs): pytest.fail('must not dispatch')
    async def audit(**event): pass
    runtime = Runtime(plan.planning_sandbox_id)
    controller = InvoiceController(admission_client=SimpleNamespace(call=forbidden), incident_repository=SimpleNamespace(plans=plans),
        verifier_repository=None, runtime=runtime, audit=audit)
    with pytest.raises(OperationDeniedError, match='reused'):
        await controller.execute(plan.plan_id, plan.plan_hash, sponsor_hash=plan.sponsor_hash)
    assert runtime.stopped == runtime.prepared


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['planning', 'execution'])
@pytest.mark.parametrize('failure', ['exception', 'missing-receipt'])
async def test_uncertain_admission_revokes_without_launching_agent(kind, failure):
    from task_agent.control.sql_backend import SqlProcedureUnavailableError
    plan = InvoicePlan(**plan_values())
    calls = []
    async def plans(sponsor):
        return [{'plan_json': plan.model_dump(mode='json'), 'plan_hash': plan.plan_hash, 'state': 'approved',
                 'execution_before': (datetime.now(UTC) + timedelta(minutes=8)).isoformat(),
                 'approval_expires_at': datetime.now(UTC) + timedelta(minutes=10)}]
    async def scenario(identifier, sponsor):
        return {'state': 'created', 'planning_before': (datetime.now(UTC) + timedelta(minutes=30)).isoformat()}
    async def call(name, values):
        calls.append((name, values))
        if name == 'control.usp_revoke_invoice_run':
            return []
        if failure == 'exception':
            raise SqlProcedureUnavailableError('response lost')
        return []
    async def forbidden(*args, **kwargs): pytest.fail('must not launch agent or emit success')
    events = []
    async def audit(**event): events.append(event['event']['event_type'])
    runtime = Runtime('b' * 32)
    runtime.execute = forbidden
    controller = InvoiceController(admission_client=SimpleNamespace(call=call), incident_repository=SimpleNamespace(plans=plans, scenario=scenario),
        verifier_repository=None, runtime=runtime, audit=audit)
    with pytest.raises(SqlProcedureUnavailableError):
        if kind == 'planning':
            await controller.analyze('c' * 32, sponsor_hash=plan.sponsor_hash)
        else:
            await controller.execute(plan.plan_id, plan.plan_hash, sponsor_hash=plan.sponsor_hash)
    assert len(calls) == 2
    assert 'authority-issued' not in events and 'verification-passed' not in events
    assert calls[-1] == ('control.usp_revoke_invoice_run', {'run_id': runtime.prepared[0][1], 'sponsor_hash': plan.sponsor_hash})
    assert runtime.stopped == runtime.prepared


@pytest.mark.parametrize('state', ['created', 'unconfirmed'])
async def test_expired_or_unconfirmed_scenario_never_allocates_a_sandbox(state):
    async def scenario(identifier, sponsor):
        assert sponsor == 'a' * 64
        return {'state': state, 'planning_before': (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
    runtime = Runtime('b' * 32)
    controller = InvoiceController(admission_client=None, incident_repository=SimpleNamespace(scenario=scenario),
        verifier_repository=None, runtime=runtime, audit=None)
    with pytest.raises(OperationDeniedError, match='scenario'):
        await controller.analyze('c' * 32, sponsor_hash='a' * 64)
    assert runtime.prepared == []


async def test_planning_waits_for_maintenance_then_checks_scenario_without_replay():
    import asyncio
    checked=asyncio.Event()
    async def scenario(*args):
        checked.set()
        return {'state':'unconfirmed'}
    runtime=Runtime('b'*32)
    controller=InvoiceController(admission_client=None,incident_repository=SimpleNamespace(scenario=scenario),verifier_repository=None,runtime=runtime,audit=None)
    async with controller.lock:
        controller.maintenance_active=True
        pending=asyncio.create_task(controller.analyze('c'*32,sponsor_hash='a'*64))
        yielded=asyncio.Event()
        asyncio.get_running_loop().call_soon(yielded.set)
        await yielded.wait()
        assert not pending.done() and not checked.is_set()
        controller.maintenance_active=False
    with pytest.raises(OperationDeniedError,match='scenario'):await pending
    assert checked.is_set() and runtime.prepared==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [1, 3])
async def test_reconciliation_reads_receipts_and_never_replays_agent(count):
    import json
    from task_agent.control.canonical import content_hash
    plan = InvoicePlan(**plan_values())
    run_id = 'c' * 32
    calls = []
    async def plans(sponsor):
        assert sponsor == plan.sponsor_hash
        return [{'plan_json': plan.model_dump(mode='json'), 'plan_hash': plan.plan_hash, 'state': 'executing', 'execution_run_id': run_id}]
    async def call(name, values):
        calls.append(name)
        if name == 'control.usp_get_invoice_job': return [{'state': 'uncertain'}]
        assert name == 'control.usp_get_invoice_execution'
        return [{'receipt_json': json.dumps({'run_id':run_id,'step_id':item.step_id,'step_hash':content_hash(item),
            'scenario_id':item.target,'revision':item.expected_revision+1,'operation':item.operation})} for item in plan.steps[:count]]
    async def verify(identifier):
        calls.append('verify')
        assert identifier == run_id
        return {'checked':True}
    controller = InvoiceController(admission_client=SimpleNamespace(call=call), incident_repository=SimpleNamespace(plans=plans,client=SimpleNamespace(call=call)),
        verifier_repository=SimpleNamespace(verify=verify), runtime=None, audit=None)
    result = await controller.reconcile(plan.plan_id, plan.plan_hash, sponsor_hash=plan.sponsor_hash)
    assert result['replayed_steps'] == 0
    assert result['state'] == ('partial' if count == 1 else 'verified')
    assert calls == ['control.usp_get_invoice_job','control.usp_get_invoice_execution'] + ([] if count == 1 else ['verify'])