"""Execution wire contracts usable without the trusted control package."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    plan_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    plan_version: int = Field(ge=1)


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    plan_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class BrokerEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    operation_id: Literal['remediate.activate-cycle-safe-query-v1']
    result_code: Literal['safe_query_activated']
    result: dict[str, str]


class VerificationEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    execution_id: str
    plan_hash: str
    safe_query_version: Literal['cycle-safe-v1']
    checks: dict[str, bool]


def evidence_hash(document):
    canonical = json.dumps(document.model_dump(mode='json'), ensure_ascii=True, separators=(',', ':'), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ExecutionStatus(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    execution_id: str = Field(pattern=r'^execution-[a-f0-9]{32}$')
    plan_id: str = Field(pattern=r'^plan-[A-Za-z0-9-]{1,120}$')
    plan_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    state: Literal['claimed', 'broker', 'verification', 'completed']
    broker: BrokerEvidence | None = None
    broker_hash: str | None = None
    verification: VerificationEvidence | None = None
    verification_hash: str | None = None

    @model_validator(mode='after')
    def validate_evidence(self):
        if self.state != 'claimed':
            if (self.broker is None or evidence_hash(self.broker) != self.broker_hash
                    or self.broker.result != {'safe_query_version': 'cycle-safe-v1'}):
                raise ValueError('persisted broker evidence invalid')
        if self.state in ('verification', 'completed'):
            receipt = self.verification
            if (receipt is None or evidence_hash(receipt) != self.verification_hash
                    or receipt.execution_id != self.execution_id or receipt.plan_hash != self.plan_hash
                    or set(receipt.checks) != {'safe_query_version_active', 'no_owned_query_running', 'deterministic_result'}
                    or not all(receipt.checks.values())):
                raise ValueError('persisted verification evidence invalid')
        return self