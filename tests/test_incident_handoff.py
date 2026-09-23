from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest
from sqlfluff.core import Linter

from task_agent.console.incident_contract import InvestigationPlan, PlanSubmission
from task_agent.control.incident import SqlIncidentRepository, TrustedInvestigationRecorder
from task_agent.control.canonical import content_hash
from task_agent.control.models import DiagnosisRecord, OperationReceipt
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureUnavailableError
from test_review_repository import ReviewClient


NOW = datetime(2026, 9, 15, 5, tzinfo=UTC)


class IncidentClient:
    def __init__(self):
        record = ReviewClient().plan.model_dump(mode="json")
        record["state"] = "draft"
        self.proposal = {"plan": record, "investigation_version": 1,
            "diagnosis_receipt_hash": "d" * 64, "containment_receipt_hash": "c" * 64,
            "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
            "author_identity_scheme": "entra-tenant-oid-v1", "canSubmit": True}
        self.writes = []

    async def call(self, procedure, parameters):
        if procedure == "control.usp_list_human_incidents":
            return ({"proposal_json": json.dumps(self.proposal)},)
        assert procedure == "control.usp_submit_human_plan"
        self.writes.append(parameters)
        return ({"plan_id": self.proposal["plan"]["plan_id"], "plan_hash": self.proposal["plan"]["plan_hash"], "state": "awaiting_approval"},)


@pytest.mark.asyncio
async def test_persisted_investigation_is_submitted_without_execution():
    client = IncidentClient()
    plan = client.proposal["plan"]
    repository = SqlIncidentRepository(client)
    assert len(await repository.list_proposals(author_hash=plan["created_by_hash"], now=NOW)) == 1
    assert not client.writes
    result = await repository.submit(plan["plan_id"], PlanSubmission(plan_hash=plan["plan_hash"], investigation_version=1),
        author_hash=plan["created_by_hash"], now=NOW)
    assert result["state"] == "awaiting_approval"
    assert client.writes == [{"plan_id": plan["plan_id"], "sponsor_hash": plan["created_by_hash"],
        "expected_plan_hash": plan["plan_hash"], "expected_investigation_version": 1}]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["owner", "hash", "version", "expiry", "submitted", "missing_evidence", "legacy"])
async def test_unsafe_handoff_never_writes(case):
    client = IncidentClient()
    plan = client.proposal["plan"]
    if case == "expiry": client.proposal["expires_at"] = NOW.isoformat()
    if case == "submitted": plan["state"] = "awaiting_approval"
    if case == "missing_evidence": del client.proposal["diagnosis_receipt_hash"]
    if case == "legacy": client.proposal["author_identity_scheme"] = "scripted"
    with pytest.raises((OperationDeniedError, SqlProcedureUnavailableError)):
        await SqlIncidentRepository(client).submit(plan["plan_id"],
            PlanSubmission(plan_hash="0" * 64 if case == "hash" else plan["plan_hash"], investigation_version=2 if case == "version" else 1),
            author_hash="f" * 64 if case == "owner" else plan["created_by_hash"], now=NOW)
    assert not client.writes


def test_incident_sql_is_parsed_and_submit_cannot_execute_or_approve():
    source = (Path(__file__).parents[1] / "infra/next-phase/review-service/002_incident_handoff.sql").read_text()
    linter = Linter(dialect="tsql")
    assert not [str(error) for batch in source.split("\nGO\n") if batch.strip() for error in linter.parse_string(batch).violations]
    submission = source.split("CREATE OR ALTER PROCEDURE control.usp_submit_human_plan")[1]
    assert "WITH (UPDLOCK, HOLDLOCK)" in submission
    assert "engagement.SponsorSubjectHash = @sponsor_hash" in submission
    assert "investigation.Version = @expected_investigation_version" in submission
    assert "control.usp_issue_approval" not in source
    assert "ops.usp_activate_cycle_safe_query" not in source
    assert "GRANT" not in source
    assert "DELETE control.TaskEvents" not in source
    assert "EXEC control.usp_record_cycle_containment_plan" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["valid", "sponsor", "workspace", "agent", "run", "stale", "containment"])
async def test_only_matching_trusted_results_create_a_draft(case):
    plan = InvestigationPlan.model_validate(IncidentClient().proposal["plan"])
    diagnosis = DiagnosisRecord(diagnosis_id="diagnosis-test", task_id=plan.content.task_id,
        engagement_id=plan.content.engagement_id, workspace_id="other" if case == "workspace" else plan.content.workspace_id,
        logical_agent_id="other" if case == "agent" else plan.content.logical_agent_id,
        summary="Cycle detected in the controlled query", evidence_refs=("query-run:run-test",),
        created_at=NOW - timedelta(minutes=16 if case == "stale" else 1))
    receipt = OperationReceipt(operation_id="contain.cancel-owned-query-v1", result_code="owned_query_cancelled",
        result={"run_id": "run-test", "state": "running" if case == "containment" else "cancelled"})
    class RecorderClient:
        def __init__(self): self.writes = []
        async def call(self, procedure, parameters):
            assert procedure == "control.usp_record_human_investigation"
            self.writes.append(parameters)
            return ({"plan_id": plan.plan_id, "plan_hash": plan.plan_hash, "state": "draft"},)
    client = RecorderClient()
    async def record():
        return await TrustedInvestigationRecorder(client).record(plan=plan, diagnosis=diagnosis, containment=receipt,
            run_id="run-other" if case == "run" else "run-test",
            sponsor_hash="f" * 64 if case == "sponsor" else plan.created_by_hash,
            expected_task_version=1, now=NOW)
    if case == "valid":
        assert (await record())["state"] == "draft"
        assert client.writes[0]["diagnosis_receipt_hash"] == content_hash(diagnosis)
        assert client.writes[0]["containment_receipt_hash"] == content_hash(receipt)
        assert json.loads(client.writes[0]["diagnosis_json"])["task_id"] == plan.content.task_id
    else:
        with pytest.raises(OperationDeniedError): await record()
        assert not client.writes