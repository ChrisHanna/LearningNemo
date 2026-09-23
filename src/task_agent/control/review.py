"""Human review of authoritative SQL plans; never an in-memory approval queue."""

from datetime import UTC, datetime, timedelta
import json
import re
from uuid import uuid4
from typing import Literal

from task_agent.console.review_contract import ReviewDecision
from task_agent.control.canonical import content_hash
from task_agent.control.models import ApprovalReceipt, FrozenRecord, ResolutionPlan
from task_agent.control.operations import OperationDeniedError, validate_operation
from task_agent.control.sql_backend import SqlProcedureClient, SqlProcedureUnavailableError


class ReviewPlan(FrozenRecord):
    plan: ResolutionPlan
    expires_at: datetime
    author_identity_scheme: Literal["entra-tenant-oid-v1"]


class SqlReviewRepository:
    def __init__(self, client: SqlProcedureClient) -> None:
        self.client = client

    async def list_plans(self) -> tuple[ReviewPlan, ...]:
        rows = await self.client.call("control.usp_list_review_plans", {})
        records = []
        try:
            for row in rows:
                if set(row) != {"plan_json", "expires_at", "author_identity_scheme"}:
                    raise ValueError("invalid review row")
                record = ReviewPlan(plan=json.loads(row["plan_json"]), expires_at=row["expires_at"], author_identity_scheme=row["author_identity_scheme"])
                self._validate(record)
                records.append(record)
        except (ValueError, TypeError) as error:
            raise SqlProcedureUnavailableError("review repository returned an invalid plan") from error
        return tuple(records)

    @staticmethod
    def _validate(record: ReviewPlan) -> None:
        plan = record.plan
        if content_hash(plan.content) != plan.plan_hash:
            raise ValueError("stored plan hash differs")
        operation = validate_operation(plan.content.operation_id, plan.content.parameters)
        if (operation.worker != "remediation" or plan.content.parameters != {"query_version": plan.content.safe_query_version}
            or plan.content.target_resource != "lab.QueryVersions/cycle-safe-v1"):
            raise ValueError("stored plan is not a registered remediation")
        if record.expires_at.tzinfo is None:
            raise ValueError("review expiry requires a timezone")

    async def decide(
        self, plan_id: str, decision: ReviewDecision, *, reviewer_hash: str, now: datetime,
    ) -> dict[str, object]:
        if re.fullmatch(r"[0-9a-f]{64}", reviewer_hash) is None:
            raise OperationDeniedError("reviewer identity binding is invalid")
        record = next((item for item in await self.list_plans() if item.plan.plan_id == plan_id), None)
        if record is None:
            raise OperationDeniedError("review plan is unavailable")
        plan = record.plan
        if plan.created_by_hash == reviewer_hash:
            raise OperationDeniedError("the plan author cannot review their own plan")
        if now.tzinfo is None or record.expires_at <= now:
            raise OperationDeniedError("review plan expired")
        if plan.state != "awaiting_approval" or decision.plan_hash != plan.plan_hash or decision.plan_version != plan.version:
            raise OperationDeniedError("review plan changed; refresh before deciding")
        receipt = None
        if decision.decision == "approve":
            approved_at = now.astimezone(UTC).replace(microsecond=0)
            payload = {
                "approval_id": f"approval-{uuid4().hex}", "plan_id": plan.plan_id,
                "task_id": plan.content.task_id, "engagement_id": plan.content.engagement_id,
                "workspace_id": plan.content.workspace_id, "logical_agent_id": plan.content.logical_agent_id,
                "plan_hash": plan.plan_hash, "operation_id": plan.content.operation_id,
                "target_resource": plan.content.target_resource, "safe_query_version": plan.content.safe_query_version,
                "approved_by_hash": reviewer_hash, "approved_at": approved_at,
                "expires_at": min(approved_at + timedelta(minutes=15), record.expires_at),
                "one_time_id": f"grant-{uuid4().hex}", "consumed_at": None, "version": 1,
            }
            receipt = ApprovalReceipt(**payload, receipt_hash=content_hash(payload))
        rows = await self.client.call("control.usp_decide_review_plan", {
            "plan_id": plan_id, "expected_plan_hash": decision.plan_hash,
            "expected_plan_version": decision.plan_version, "reviewer_hash": reviewer_hash,
            "decision": decision.decision,
            "receipt_json": receipt.model_dump_json() if receipt else None,
        })
        expected = "approved" if decision.decision == "approve" else "rejected"
        if len(rows) != 1 or dict(rows[0]) != {"plan_id": plan_id, "state": expected}:
            raise SqlProcedureUnavailableError("review decision result is unverified; refresh before retrying")
        return {"planId": plan_id, "state": expected, "planHash": plan.plan_hash}