"""Submit a trusted, persisted investigation for independent human review."""

from datetime import datetime
import json
import re

from task_agent.console.incident_contract import IncidentProposal, InvestigationPlan, PlanSubmission
from task_agent.control.canonical import canonical_json, content_hash
from task_agent.control.models import DiagnosisRecord, OperationReceipt
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureClient, SqlProcedureUnavailableError


class TrustedInvestigationRecorder:
    def __init__(self, client: SqlProcedureClient) -> None:
        self.client = client

    async def record(self, *, plan: InvestigationPlan, diagnosis: DiagnosisRecord,
                     containment: OperationReceipt, run_id: str, sponsor_hash: str,
                     expected_task_version: int, now: datetime) -> dict:
        plan = InvestigationPlan.model_validate(plan.model_dump(mode="json"))
        if (re.fullmatch(r"[0-9a-f]{64}", sponsor_hash) is None
                or plan.created_by_hash != sponsor_hash or plan.state != "draft" or plan.version != 1
                or type(expected_task_version) is not int or expected_task_version < 1):
            raise OperationDeniedError("verified sponsor and new draft required")
        if now.tzinfo is None or diagnosis.created_at.tzinfo is None or not 0 <= (now - diagnosis.created_at).total_seconds() <= 900:
            raise OperationDeniedError("fresh trusted diagnosis required")
        for field in ("task_id", "engagement_id", "workspace_id", "logical_agent_id"):
            if getattr(diagnosis, field) != getattr(plan.content, field):
                raise OperationDeniedError("diagnosis belongs to a different incident context")
        if (re.fullmatch(r"run-[A-Za-z0-9-]{1,120}", run_id) is None
                or f"query-run:{run_id}" not in diagnosis.evidence_refs
                or containment.operation_id != "contain.cancel-owned-query-v1"
                or containment.result_code != "owned_query_cancelled"
                or containment.result != {"run_id": run_id, "state": "cancelled"}):
            raise OperationDeniedError("matching trusted containment evidence required")
        rows = await self.client.call("control.usp_record_human_investigation", {
            "plan_json": plan.model_dump_json(), "run_id": run_id,
            "expected_task_version": expected_task_version,
            "diagnosis_receipt_hash": content_hash(diagnosis),
            "containment_receipt_hash": content_hash(containment),
            "diagnosis_json": canonical_json(diagnosis), "containment_json": canonical_json(containment),
        })
        expected = {"plan_id": plan.plan_id, "plan_hash": plan.plan_hash, "state": "draft"}
        if len(rows) != 1 or dict(rows[0]) != expected:
            raise SqlProcedureUnavailableError("investigation recording unconfirmed; inspect before retrying")
        return expected


class SqlIncidentRepository:
    def __init__(self, client: SqlProcedureClient) -> None:
        self.client = client

    @staticmethod
    def _author(author_hash: str) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", author_hash) is None:
            raise OperationDeniedError("verified sponsor identity required")

    async def list_proposals(self, *, author_hash: str, now: datetime) -> tuple[IncidentProposal, ...]:
        self._author(author_hash)
        if now.tzinfo is None:
            raise ValueError("timezone-aware clock required")
        rows = await self.client.call("control.usp_list_human_incidents", {"sponsor_hash": author_hash})
        proposals = []
        try:
            for row in rows:
                if set(row) != {"proposal_json"}:
                    raise ValueError("unexpected incident row")
                proposal = IncidentProposal.model_validate(json.loads(row["proposal_json"]))
                if proposal.plan.created_by_hash != author_hash:
                    raise ValueError("incident belongs to a different sponsor")
                eligible = proposal.plan.state == "draft" and proposal.expires_at > now
                proposals.append(proposal.model_copy(update={"canSubmit": proposal.canSubmit and eligible}))
        except (ValueError, TypeError) as error:
            raise SqlProcedureUnavailableError("incident repository returned an invalid proposal") from error
        if len(proposals) > 100:
            raise SqlProcedureUnavailableError("incident repository exceeded the queue limit")
        return tuple(proposals)

    async def submit(self, plan_id: str, submission: PlanSubmission, *, author_hash: str, now: datetime) -> dict:
        proposal = next((item for item in await self.list_proposals(author_hash=author_hash, now=now)
                         if item.plan.plan_id == plan_id), None)
        if proposal is None:
            raise OperationDeniedError("investigation unavailable for this sponsor")
        if submission.plan_hash != proposal.plan.plan_hash or submission.investigation_version != proposal.investigation_version:
            raise OperationDeniedError("investigation changed; refresh before submitting")
        if not proposal.canSubmit:
            raise OperationDeniedError("investigation is expired or already submitted")
        rows = await self.client.call("control.usp_submit_human_plan", {
            "plan_id": plan_id, "sponsor_hash": author_hash,
            "expected_plan_hash": submission.plan_hash,
            "expected_investigation_version": submission.investigation_version,
        })
        expected = {"plan_id": plan_id, "plan_hash": submission.plan_hash, "state": "awaiting_approval"}
        if len(rows) != 1 or dict(rows[0]) != expected:
            raise SqlProcedureUnavailableError("submission outcome unconfirmed; refresh before retrying")
        return {"planId": plan_id, "planHash": submission.plan_hash, "state": "awaiting_approval"}