"""Browser-safe incident proposals; authorization and evidence remain server owned."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from task_agent.console.review_contract import ReviewPlanDocument


class IncidentStart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    request_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class PlanSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    investigation_version: int = Field(ge=1)


class InvestigationPlan(ReviewPlanDocument):
    state: Literal["draft", "awaiting_approval", "approved", "rejected", "consumed"]


class IncidentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    plan: InvestigationPlan
    investigation_version: int = Field(ge=1, strict=True)
    diagnosis_receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    containment_receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: AwareDatetime
    author_identity_scheme: Literal["entra-tenant-oid-v1"]
    canSubmit: bool = Field(strict=True)


class IncidentQueue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["azure-sql"]
    incidents: list[IncidentProposal] = Field(max_length=100)
    initiation_available: bool = False
    producer: Literal["trusted-fixed-investigation-v1"] | None = None