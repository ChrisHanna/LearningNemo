from datetime import UTC, datetime, timedelta
import json

import pytest

from task_agent.control.canonical import content_hash, subject_hash
from task_agent.control.models import PlanContent, ResolutionPlan
from task_agent.control.operations import OperationDeniedError
from task_agent.control.review import ReviewDecision, SqlReviewRepository
from task_agent.control.sql_backend import SqlProcedureUnavailableError


NOW = datetime(2026, 9, 15, 3, tzinfo=UTC)


class ReviewClient:
    def __init__(self):
        content = PlanContent(task_id="task-review", engagement_id="engagement-review",
                              workspace_id="workspace-review", logical_agent_id="agent-review",
                              operation_id="remediate.activate-cycle-safe-query-v1",
                              target_resource="lab.QueryVersions/cycle-safe-v1", safe_query_version="cycle-safe-v1",
                              rollback_version="cycle-unsafe-v1", parameters={"query_version": "cycle-safe-v1"})
        self.plan = ResolutionPlan(plan_id="plan-review", content=content, plan_hash=content_hash(content),
                                   state="awaiting_approval", created_by_hash=subject_hash("author"), created_at=NOW)
        self.expires = NOW + timedelta(minutes=20)
        self.writes = []

    async def call(self, procedure, parameters):
        if procedure == "control.usp_list_review_plans":
            return ({"plan_json": self.plan.model_dump_json(), "expires_at": self.expires, "author_identity_scheme": "entra-tenant-oid-v1"},)
        assert procedure == "control.usp_decide_review_plan"
        self.writes.append(parameters)
        return ({"plan_id": self.plan.plan_id, "state": "approved" if parameters["decision"] == "approve" else "rejected"},)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_independent_reviewer_writes_exact_plan_binding(decision):
    client = ReviewClient()
    result = await SqlReviewRepository(client).decide(client.plan.plan_id,
        ReviewDecision(plan_hash=client.plan.plan_hash, plan_version=1, decision=decision),
        reviewer_hash=subject_hash("reviewer"), now=NOW)
    assert result["state"] == ("approved" if decision == "approve" else "rejected")
    assert client.writes[0]["expected_plan_version"] == 1
    if decision == "approve":
        receipt = json.loads(client.writes[0]["receipt_json"])
        assert receipt["approved_by_hash"] == subject_hash("reviewer")
        assert receipt["plan_hash"] == client.plan.plan_hash
        assert receipt["consumed_at"] is None
        assert datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00")) == NOW + timedelta(minutes=15)
    else:
        assert client.writes[0]["receipt_json"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["author", "hash", "version", "expired", "consumed"])
async def test_unsafe_decisions_never_reach_sql_write(case):
    client = ReviewClient()
    if case == "expired":
        client.expires = NOW
    if case == "consumed":
        client.plan = client.plan.model_copy(update={"state": "consumed"})
    decision = ReviewDecision(plan_hash="0" * 64 if case == "hash" else client.plan.plan_hash,
                              plan_version=2 if case == "version" else 1, decision="approve")
    with pytest.raises(OperationDeniedError):
        await SqlReviewRepository(client).decide(client.plan.plan_id, decision,
            reviewer_hash=subject_hash("author" if case == "author" else "reviewer"), now=NOW)
    assert not client.writes


@pytest.mark.asyncio
async def test_legacy_actor_hash_scheme_is_not_reviewable():
    client = ReviewClient()
    async def legacy_call(procedure, parameters):
        return ({"plan_json": client.plan.model_dump_json(), "expires_at": client.expires, "author_identity_scheme": None},)
    client.call = legacy_call
    with pytest.raises(SqlProcedureUnavailableError, match="invalid plan"):
        await SqlReviewRepository(client).list_plans()