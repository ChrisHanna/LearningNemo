from datetime import UTC, datetime
import json

import pytest

from task_agent.control.invoice_contract import InvoiceEvidence, InvoiceStep, PlanningDecision, missing_operations
from task_agent.control.invoice_repository import InvoiceRepository, capability_context, new_capability
from task_agent.control.operations import OperationDeniedError


class Client:
    def __init__(self):
        self.calls = []
        self.admitted = True

    async def call(self, name, parameters):
        self.calls.append((name, parameters))
        if name == 'control.usp_admit_invoice_tool':
            return [{'scenario_id': 'a' * 32, 'kind': 'planning', 'sandbox_id': 'b' * 32}] if self.admitted else []
        if name == 'ops.usp_diagnose_invoice_summary':
            return [evidence().model_dump(mode='json')]
        if name == 'control.usp_record_invoice_evidence':
            return [{'evidence_hash': parameters['evidence_hash']}]
        if name == 'control.usp_record_invoice_plan':
            return [{'plan_id': json.loads(parameters['plan_json'])['plan_id'], 'plan_hash': parameters['plan_hash']}]
        raise AssertionError(name)


def evidence():
    return InvoiceEvidence(scenario_id='a' * 32, revision=1, observed_at=datetime.now(UTC), orders=12, active_invoices=24,
                           duplicate_invoices=12, duplicate_set_hash='c' * 64, expected_cents=100, actual_cents=200,
                           reported_cents=200, import_version='retry-unsafe-v1')


@pytest.mark.asyncio
async def test_diagnostics_admit_before_read_and_persist_without_writes():
    client = Client()
    result = await InvoiceRepository(client).summary(new_capability('d' * 32))
    assert result['duplicate_invoices'] == 12
    assert result['proposal_parameter_reference']['target'] == result['scenario_id']
    assert len(result['proposal_parameter_reference']['target']) == 32
    assert result['proposal_parameter_reference']['first_expected_revision'] == result['revision']
    assert result['proposal_parameter_reference']['quarantine_duplicate_set_hash'] == result['duplicate_set_hash']
    assert [name for name, _ in client.calls] == ['control.usp_admit_invoice_tool', 'ops.usp_diagnose_invoice_summary', 'control.usp_record_invoice_evidence']


@pytest.mark.asyncio
async def test_revoked_or_invalid_capability_cannot_read():
    client = Client()
    client.admitted = False
    repository = InvoiceRepository(client)
    for token in (new_capability('d' * 32), 'human-token', 'a' * 500):
        with pytest.raises(OperationDeniedError):
            await repository.summary(token)
    assert all(name == 'control.usp_admit_invoice_tool' for name, _ in client.calls)


@pytest.mark.asyncio
async def test_model_cannot_choose_another_scenario_or_duplicate_set():
    client = Client()
    observed = evidence()
    for target, digest in [('e' * 32, observed.duplicate_set_hash), (observed.scenario_id, 'f' * 64)]:
        decision = PlanningDecision(outcome='proposal', diagnosis='Duplicates', rationale='Repeated order keys',
            evidence_hash=observed.evidence_hash, risks=['Preserve genuine orders'], steps=[InvoiceStep(
                step_id=1, operation='invoice.quarantine-duplicates.v1', target=target, expected_revision=1, duplicate_set_hash=digest)])
        with pytest.raises(OperationDeniedError):
            await InvoiceRepository(client).save_proposal(decision, sponsor_hash='0' * 64, run_id='1' * 32,
                                                         sandbox_id='2' * 32, evidence=observed)
    assert client.calls == []

QUARANTINE, REBUILD, ACTIVATE = ('invoice.quarantine-duplicates.v1', 'invoice.rebuild-total.v1', 'invoice.activate-idempotent-import.v1')


def evidence_with(**changes):
    return InvoiceEvidence(**{**evidence().model_dump(), **changes})


def proposal(observed, operations):
    steps = [InvoiceStep(step_id=index, operation=operation, target=observed.scenario_id, expected_revision=observed.revision + index - 1,
                         duplicate_set_hash=observed.duplicate_set_hash if operation == QUARANTINE else None)
             for index, operation in enumerate(operations, 1)]
    return PlanningDecision(outcome='proposal', diagnosis='Duplicates', rationale='Repeated order keys',
                            evidence_hash=observed.evidence_hash, risks=['Preserve genuine orders'], steps=steps)


def test_required_operations_follow_the_verification_profile():
    assert evidence().required_operations == (QUARANTINE, REBUILD, ACTIVATE)
    # Quarantined and reconciled already, importer still unsafe: only the importer is required.
    assert evidence_with(duplicate_invoices=0, reported_cents=100).required_operations == (ACTIVATE,)
    # Duplicates gone but the reported total is still overstated.
    assert evidence_with(duplicate_invoices=0, import_version='idempotent-v2').required_operations == (REBUILD,)
    assert evidence_with(duplicate_invoices=0, reported_cents=100, import_version='idempotent-v2').required_operations == ()
    assert missing_operations((QUARANTINE, REBUILD), proposal(evidence(), [QUARANTINE]).steps) == [REBUILD]


@pytest.mark.asyncio
async def test_summary_tells_the_model_which_operations_verification_needs():
    result = await InvoiceRepository(Client()).summary(new_capability('d' * 32))
    assert result['proposal_parameter_reference']['required_operations'] == [QUARANTINE, REBUILD, ACTIVATE]


@pytest.mark.asyncio
async def test_proposal_that_cannot_pass_verification_is_rejected_before_it_is_stored():
    observed = evidence()
    for operations in ([QUARANTINE], [QUARANTINE, REBUILD], [QUARANTINE, ACTIVATE]):
        client = Client()
        with pytest.raises(OperationDeniedError, match='requires for verification'):
            await InvoiceRepository(client).save_proposal(proposal(observed, operations), sponsor_hash='0' * 64,
                                                         run_id='1' * 32, sandbox_id='2' * 32, evidence=observed)
        assert client.calls == []


@pytest.mark.asyncio
async def test_complete_proposal_is_stored():
    observed = evidence()
    client = Client()
    saved = await InvoiceRepository(client).save_proposal(proposal(observed, [QUARANTINE, REBUILD, ACTIVATE]), sponsor_hash='0' * 64,
                                                         run_id='1' * 32, sandbox_id='2' * 32, evidence=observed)
    assert saved['outcome'] == 'proposal' and [name for name, _ in client.calls] == ['control.usp_record_invoice_plan']
    # Only the importer remains once duplicates are gone and the total reconciles.
    later = evidence_with(duplicate_invoices=0, reported_cents=100, revision=3)
    saved = await InvoiceRepository(Client()).save_proposal(proposal(later, [ACTIVATE]), sponsor_hash='0' * 64,
                                                           run_id='1' * 32, sandbox_id='2' * 32, evidence=later)
    assert [step['operation'] for step in saved['plan']['steps']] == [ACTIVATE]
@pytest.mark.asyncio
async def test_failed_verification_is_distinct_from_an_unavailable_database():
    from task_agent.control.invoice_repository import VERIFICATION_FAILED
    from task_agent.control.sql_backend import SqlProcedureUnavailableError

    class Failing:
        def __init__(self, cause):
            self.cause = cause
        async def call(self, name, parameters):
            assert name == 'ops.usp_verify_invoice_run'
            raise SqlProcedureUnavailableError('trusted SQL procedure call failed') from self.cause

    with pytest.raises(OperationDeniedError, match='Independent verification failed') as denied:
        await InvoiceRepository(Failing(RuntimeError("(51912) invoice_independent_verification_failed"))).verify('a' * 32)
    assert str(denied.value) == VERIFICATION_FAILED
    with pytest.raises(SqlProcedureUnavailableError):
        await InvoiceRepository(Failing(RuntimeError('connection timeout'))).verify('a' * 32)


def test_capabilities_are_distinct_and_only_digests_are_persistable():
    first, second = (new_capability('d' * 32) for _ in range(2))
    assert first != second
    run_id, digest = capability_context(first)
    assert run_id == 'd' * 32 and len(digest) == 64 and first not in digest


@pytest.mark.asyncio
async def test_demo_unlimited_hourly_metadata_and_live_daily_denial():
    class QuotaClient:
        admitted=True
        async def call(self,name,parameters):
            assert name=='control.usp_admit_invoice_guest_launch'
            return [{'admitted':self.admitted,'reason':None if self.admitted else 'daily',
                'visitor_launches_last_hour':40,'global_launches_today':99 if self.admitted else 100,
                'visitor_limit':None,'global_limit':100}]
    client=QuotaClient()
    repository=InvoiceRepository(client)
    result=await repository.admit_guest_launch('a'*32,'b'*64)
    assert result['visitor_limit'] is None and result['global_limit']==100
    client.admitted=False
    with pytest.raises(OperationDeniedError,match='100 agent or investigation'):
        await repository.admit_guest_launch('c'*32,'b'*64)