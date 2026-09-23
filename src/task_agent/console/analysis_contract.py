"""Read-only database evidence and exact proposal inputs."""

import hashlib
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class DiagnosticSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    active_query_version: str = Field(max_length=64)
    query_run_states: dict[str, Literal['starting', 'running', 'cancel_requested', 'cancelled', 'failed', 'completed']] = Field(max_length=10000)


def snapshot_hash(snapshot):
    canonical = json.dumps(snapshot.model_dump(mode='json'), ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()


class AnalysisReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    analysis_id: str = Field(pattern=r'^analysis-[a-f0-9]{32}$')
    source: Literal['azure-sql-diagnostic-service'] = 'azure-sql-diagnostic-service'
    observed_at: AwareDatetime
    snapshot: DiagnosticSnapshot
    evidence_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    workload_changed: Literal[False] = False
    queries_cancelled: Literal[False] = False

    @model_validator(mode='after')
    def validate_hash(self):
        if snapshot_hash(self.snapshot) != self.evidence_hash:
            raise ValueError('diagnostic evidence hash differs')
        return self


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    analysis_id: str = Field(pattern=r'^analysis-[a-f0-9]{32}$')
    evidence_hash: str = Field(pattern=r'^[a-f0-9]{64}$')