"""Strict browser decision input; identity and plan content are never accepted."""

from typing import Literal
import hashlib
import json

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ReviewPlanContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(min_length=1, max_length=128)
    engagement_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    logical_agent_id: str = Field(min_length=1, max_length=128)
    operation_id: Literal["remediate.activate-cycle-safe-query-v1"]
    target_resource: Literal["lab.QueryVersions/cycle-safe-v1"]
    safe_query_version: Literal["cycle-safe-v1"]
    rollback_version: Literal["cycle-unsafe-v1"]
    parameters: dict[str, str | int | bool | None]


class ReviewPlanDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    plan_id: str = Field(pattern=r"^plan-[A-Za-z0-9-]{1,120}$")
    content: ReviewPlanContent
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["awaiting_approval", "approved", "rejected", "consumed"]
    version: int = Field(ge=1, strict=True)
    created_by_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_exact_plan(self):
        if self.content.parameters != {"query_version": "cycle-safe-v1"}:
            raise ValueError("unregistered plan parameters")
        canonical = json.dumps(self.content.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        if hashlib.sha256(canonical.encode()).hexdigest() != self.plan_hash:
            raise ValueError("plan content does not match its hash")
        return self


class ReviewQueueEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    plan: ReviewPlanDocument
    expires_at: AwareDatetime
    author_identity_scheme: Literal["entra-tenant-oid-v1"]
    canDecide: bool = Field(strict=True)


class ReviewQueue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["azure-sql"]
    plans: list[ReviewQueueEntry] = Field(max_length=100)


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_version: int = Field(ge=1)
    decision: Literal["approve", "reject"]