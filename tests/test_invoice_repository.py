from datetime import UTC, datetime

import pytest

from task_agent.control.invoice_contract import InvoiceEvidence, InvoiceStep, PlanningDecision
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


def test_capabilities_are_distinct_and_only_digests_are_persistable():
    first, second = (new_capability('d' * 32) for _ in range(2))
    assert first != second
    run_id, digest = capability_context(first)
    assert run_id == 'd' * 32 and len(digest) == 64 and first not in digest