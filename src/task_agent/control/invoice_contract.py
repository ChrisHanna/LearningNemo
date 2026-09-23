"""Versioned, closed contracts for the invoice incident agent workflow."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from task_agent.control.canonical import content_hash


Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Identifier = Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class InvoiceEvidence(Record):
    scenario_id: Identifier
    revision: int = Field(ge=1, strict=True)
    observed_at: AwareDatetime
    source: Literal['invoice-diagnostic-api'] = 'invoice-diagnostic-api'
    orders: int = Field(ge=0, strict=True)
    active_invoices: int = Field(ge=0, strict=True)
    duplicate_invoices: int = Field(ge=0, strict=True)
    duplicate_set_hash: Digest
    expected_cents: int = Field(ge=0, strict=True)
    actual_cents: int = Field(ge=0, strict=True)
    reported_cents: int = Field(ge=0, strict=True)
    import_version: Literal['retry-unsafe-v1', 'idempotent-v2']

    @property
    def evidence_hash(self):
        return content_hash(self)


class InvoiceStep(Record):
    step_id: int = Field(ge=1, le=3, strict=True)
    operation: Literal['invoice.quarantine-duplicates.v1', 'invoice.rebuild-total.v1', 'invoice.activate-idempotent-import.v1']
    target: Identifier
    expected_revision: int = Field(ge=1, strict=True)
    duplicate_set_hash: Digest | None = None

    @model_validator(mode='after')
    def validate_parameters(self):
        if (self.operation == 'invoice.quarantine-duplicates.v1') != (self.duplicate_set_hash is not None):
            raise ValueError('only quarantine accepts the exact duplicate set hash')
        return self


class InvoicePlan(Record):
    schema_version: Literal[2] = 2
    plan_id: Identifier
    scenario_id: Identifier
    sponsor_hash: Digest
    planning_run_id: Identifier
    planning_sandbox_id: Identifier
    evidence_hash: Digest
    created_at: AwareDatetime
    diagnosis: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)
    risks: tuple[Annotated[str, Field(min_length=1, max_length=500)], ...] = Field(min_length=1, max_length=5)
    steps: tuple[InvoiceStep, ...] = Field(min_length=1, max_length=3)
    failure_policy: Literal['stop-and-reconcile'] = 'stop-and-reconcile'
    verification_profile: Literal['invoice-integrity-and-replay.v1'] = 'invoice-integrity-and-replay.v1'

    @model_validator(mode='after')
    def validate_steps(self):
        operations = [step.operation for step in self.steps]
        catalog_order = ['invoice.quarantine-duplicates.v1', 'invoice.rebuild-total.v1', 'invoice.activate-idempotent-import.v1']
        if len(set(operations)) != len(operations) or operations != sorted(operations, key=catalog_order.index):
            raise ValueError('duplicate or out-of-order operation')
        for position, step in enumerate(self.steps, 1):
            if step.step_id != position or step.target != self.scenario_id:
                raise ValueError('step scope or sequence differs')
            if step.expected_revision != self.steps[0].expected_revision + position - 1:
                raise ValueError('step revision sequence differs')
        return self

    @property
    def plan_hash(self):
        return content_hash(self)


class PlanningDecision(Record):
    outcome: Literal['proposal', 'no-change', 'insufficient-evidence', 'no-permitted-resolution']
    diagnosis: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_hash: Digest
    risks: tuple[Annotated[str, Field(min_length=1, max_length=500)], ...] = Field(max_length=5)
    steps: tuple[InvoiceStep, ...] = Field(max_length=3)

    @model_validator(mode='after')
    def validate_outcome(self):
        if (self.outcome == 'proposal') != bool(self.steps):
            raise ValueError('only a proposal contains operations')
        if self.outcome == 'proposal' and not self.risks:
            raise ValueError('proposal requires risk disclosure')
        return self


class InvoiceApproval(Record):
    approval_id: Identifier
    plan_hash: Digest
    sponsor_hash: Digest
    reviewer_hash: Digest
    approved_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode='after')
    def validate_independence(self):
        if self.sponsor_hash == self.reviewer_hash:
            raise ValueError('self approval denied')
        lifetime = (self.expires_at - self.approved_at).total_seconds()
        if not 0 < lifetime <= 1800:
            raise ValueError('approval must be bounded to thirty minutes')
        return self