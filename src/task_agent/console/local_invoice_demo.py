"""Deterministic, process-local backend for the invoice demonstration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import re
import threading

from task_agent.control.invoice_contract import InvoicePlan, InvoiceStep
from task_agent.console.remote_invoice import InvoiceRemoteError


_IDENTIFIER = re.compile(r"^[a-f0-9]{32}$")
_CHECKS = {
    "no_duplicates": True,
    "legitimate_invoices_preserved": True,
    "total_reconciles": True,
    "idempotent_version_active": True,
    "replay_created_no_invoices": True,
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _iso(minutes: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


class LocalInvoiceDemoService:
    """Implement the private invoice API contract without external side effects."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scenarios: dict[str, dict] = {}
        self._jobs: dict[str, dict] = {}
        self._events: dict[str, list[dict]] = {}
        self._plans: dict[str, dict] = {}
        self._session = {
            "source": "invoice-demo-session",
            "session_id": "d" * 32,
            "state": "active",
            "expires_at": _iso(240),
            "can_end": True,
            "inflight": 0,
        }

    @staticmethod
    def _persona(token: str) -> str:
        if token == "local-demo-operator":
            return "operator"
        if token == "local-demo-approver":
            return "approver"
        raise InvoiceRemoteError(401, "Local demo session expired; sign in again")

    @staticmethod
    def _require_identifier(value: str) -> None:
        if not _IDENTIFIER.fullmatch(value):
            raise InvoiceRemoteError(422, "Invalid local demo identifier")

    def _plan_row(self, plan_id: str) -> dict:
        try:
            return self._plans[plan_id]
        except KeyError:
            raise InvoiceRemoteError(404, "Invoice plan not found") from None

    def _evidence(self, scenario_id: str, *, repaired: bool = False) -> dict:
        duplicate_hash = _digest("duplicates:" + scenario_id)
        return {
            "source": "invoice-diagnostic-api",
            "scenario_id": scenario_id,
            "evidence_hash": _digest("evidence:" + scenario_id),
            "revision": 4 if repaired else 1,
            "observed_at": _iso(),
            "orders": 12,
            "active_invoices": 12 if repaired else 24,
            "duplicate_invoices": 0 if repaired else 12,
            "duplicate_set_hash": duplicate_hash,
            "expected_cents": 147800,
            "actual_cents": 147800 if repaired else 295600,
            "reported_cents": 147800 if repaired else 295600,
            "import_version": "idempotent-v2" if repaired else "retry-unsafe-v1",
        }

    def _events_for(self, job_id: str, kind: str, sandbox_id: str, plan: InvoicePlan | None = None) -> list[dict]:
        names = ["preparing-sandbox", "sandbox-bound", "authority-issued", "agent-started"]
        events = [
            {
                "sequence": index,
                "observed_at": _iso(),
                "source": "workspace-controller" if name != "agent-started" else "agent-runtime",
                "event_type": name,
                "kind": kind,
                "sandbox_id": sandbox_id,
                "policy_hash": _digest("local-policy:" + kind),
            }
            for index, name in enumerate(names, 1)
        ]
        if kind == "planning":
            for name, tool in (("tool-requested", "invoice_summary"), ("tool-returned", "invoice_summary"),
                               ("tool-requested", "invoice_batches"), ("tool-returned", "invoice_batches")):
                events.append({"sequence": len(events) + 1, "observed_at": _iso(), "source": "agent-runtime", "event_type": name, "kind": kind, "tool": tool})
            events.append({"sequence": len(events) + 1, "observed_at": _iso(), "source": "agent-runtime", "event_type": "decision-produced", "kind": kind})
        elif plan is not None:
            for step in plan.steps:
                events.append({
                    "sequence": len(events) + 1, "observed_at": _iso(), "source": "workspace-controller",
                    "event_type": "step-receipt-recorded", "kind": kind, "step_id": step.step_id,
                    "operation": step.operation, "target": step.target,
                    "revision": step.expected_revision + 1, "receipt_hash": _digest(f"{job_id}:{step.step_id}"),
                })
            events.extend([
                {"sequence": len(events) + 1, "observed_at": _iso(), "source": "workspace-controller", "event_type": "verification-started", "kind": kind},
                {"sequence": len(events) + 2, "observed_at": _iso(), "source": "workspace-controller", "event_type": "verification-passed", "kind": kind},
            ])
        events.extend([
            {"sequence": len(events) + 1, "observed_at": _iso(), "source": "agent-runtime", "event_type": "agent-finished", "kind": kind},
            {"sequence": len(events) + 2, "observed_at": _iso(), "source": "workspace-controller", "event_type": "authority-revoked", "kind": kind},
            {"sequence": len(events) + 3, "observed_at": _iso(), "source": "workspace-controller", "event_type": "sandbox-stopped", "kind": kind, "sandbox_id": sandbox_id},
        ])
        return events

    def _create_planning_job(self, body: dict) -> dict:
        scenario_id = body["target_id"]
        if scenario_id not in self._scenarios:
            raise InvoiceRemoteError(404, "Local scenario not found")
        job_id = body["job_id"]
        evidence = self._evidence(scenario_id)
        plan_id = _digest("plan:" + job_id)[:32]
        sandbox_id = _digest("sandbox:" + job_id)[:32]
        plan = InvoicePlan(
            plan_id=plan_id,
            scenario_id=scenario_id,
            sponsor_hash=_digest("local-operator"),
            planning_run_id=job_id,
            planning_sandbox_id=sandbox_id,
            evidence_hash=_digest("evidence:" + scenario_id),
            created_at=datetime.now(UTC),
            diagnosis="A retried import created duplicate invoices",
            rationale="Twelve orders have two active invoices after an acknowledgement was lost.",
            risks=("Quarantine only the exact duplicate set and preserve legitimate invoices.",),
            steps=(
                InvoiceStep(step_id=1, operation="invoice.quarantine-duplicates.v1", target=scenario_id, expected_revision=1, duplicate_set_hash=evidence["duplicate_set_hash"]),
                InvoiceStep(step_id=2, operation="invoice.rebuild-total.v1", target=scenario_id, expected_revision=2),
                InvoiceStep(step_id=3, operation="invoice.activate-idempotent-import.v1", target=scenario_id, expected_revision=3),
            ),
        )
        row = {
            "plan_json": plan.model_dump(mode="json"),
            "plan_hash": plan.plan_hash,
            "state": "draft",
            "review_expires_at": _iso(180),
            "approval_expires_at": None,
            "execution_before": _iso(210),
            "scenario_expires_at": self._scenarios[scenario_id]["expires_at"],
            "execution_run_id": None,
            "diagnostics": evidence,
            "verification_json": None,
        }
        self._plans[plan_id] = row
        job = {
            "job_id": job_id, "kind": "planning", "target_id": scenario_id,
            "state": "finished", "created_at": _iso(),
            "result": {"plan": row["plan_json"], "diagnostics": evidence},
        }
        self._jobs[job_id] = job
        self._events[job_id] = self._events_for(job_id, "planning", sandbox_id)
        return job

    def _create_execution_job(self, body: dict) -> dict:
        row = self._plan_row(body["target_id"])
        if row["state"] != "approved" or body.get("plan_hash") != row["plan_hash"]:
            raise InvoiceRemoteError(409, "Approved local plan changed")
        job_id = body["job_id"]
        plan = InvoicePlan.model_validate(row["plan_json"])
        sandbox_id = _digest("sandbox:" + job_id)[:32]
        row["state"] = "executing"
        row["execution_run_id"] = job_id
        repaired = self._evidence(plan.scenario_id, repaired=True)
        job = {
            "job_id": job_id, "kind": "execution", "target_id": plan.plan_id,
            "plan_hash": row["plan_hash"], "state": "finished", "created_at": _iso(),
            "result": {"diagnostics": repaired, "executed_steps": 3, "independently_verified": True},
        }
        self._jobs[job_id] = job
        self._events[job_id] = self._events_for(job_id, "execution", sandbox_id, plan)
        return job

    async def request(self, method: str, path: str, token: str, body: dict | None = None, *, review: bool = False, after: int = 0) -> dict:
        persona = self._persona(token)
        if review != (persona == "approver") and path != "/invoices/demo-session":
            raise InvoiceRemoteError(403, "Use the independently assigned local demo account")
        body = body or {}
        with self._lock:
            if method == "GET" and path == "/invoices/demo-session":
                return dict(self._session)
            if method == "POST" and path in {"/invoices/demo-session/start", "/invoices/demo-session/end"}:
                self._require_identifier(body.get("session_id", ""))
                self._session.update(session_id=body["session_id"], state="active" if path.endswith("start") else "idle", expires_at=_iso(240), can_end=path.endswith("start"))
                return dict(self._session)
            if method == "GET" and path == "/invoices/plans":
                rows = list(self._plans.values())
                if persona == "approver":
                    rows = [row for row in rows if row["state"] in {"submitted", "approved", "rejected"}]
                return {"source": "azure-sql", "plans": rows}
            if persona != "operator":
                if method == "POST" and path.endswith("/decision"):
                    plan_id = path.split("/")[3]
                    row = self._plan_row(plan_id)
                    if row["state"] != "submitted" or body.get("plan_hash") != row["plan_hash"]:
                        raise InvoiceRemoteError(409, "Submitted local plan changed")
                    row["state"] = "approved" if body.get("decision") == "approve" else "rejected"
                    row["approval_expires_at"] = _iso(30) if row["state"] == "approved" else None
                    return {"plan_id": plan_id, "plan_hash": row["plan_hash"], "decision": body["decision"], "state": row["state"]}
                raise InvoiceRemoteError(403, "Approver demo account is read-only outside review")
            if method == "POST" and path == "/invoices/scenarios":
                scenario_id = body.get("scenario_id", "")
                self._require_identifier(scenario_id)
                scenario = {"source": "owned-scenario-status", "scenario_id": scenario_id, "state": "created", "variant": body["variant"], "expires_at": _iso(120), "planning_before": _iso(110)}
                self._scenarios[scenario_id] = scenario
                return dict(scenario)
            if method == "GET" and path.startswith("/invoices/scenarios/"):
                scenario_id = path.rsplit("/", 1)[1]
                return dict(self._scenarios.get(scenario_id) or (_ for _ in ()).throw(InvoiceRemoteError(404, "Local scenario not found")))
            if method == "POST" and path == "/invoices/jobs":
                self._require_identifier(body.get("job_id", ""))
                return self._create_planning_job(body) if body.get("kind") == "planning" else self._create_execution_job(body)
            if method == "GET" and re.fullmatch(r"/invoices/jobs/[a-f0-9]{32}", path):
                job_id = path.rsplit("/", 1)[1]
                return dict(self._jobs.get(job_id) or (_ for _ in ()).throw(InvoiceRemoteError(404, "Local job not found")))
            if method == "GET" and path.endswith("/events"):
                job_id = path.split("/")[3]
                return {"job_id": job_id, "events": [event for event in self._events.get(job_id, []) if event["sequence"] > after]}
            if method == "GET" and path.endswith("/evidence"):
                plan_id = path.split("/")[3]
                row = self._plan_row(plan_id)
                repaired = row["state"] in {"verified", "completed"}
                summary = self._evidence(row["plan_json"]["scenario_id"], repaired=repaired)
                return {"source": "diagnostic-sql-observation", "plan_id": plan_id, "plan_hash": row["plan_hash"], "observed_at": _iso(), "summary": summary,
                        "rows": [{"order_id": f"order-{index:02d}", "attempt": 1 if repaired else (index % 2) + 1, "amount_cents": 12000 + index * 50, "quarantined": False} for index in range(1, 13)],
                        "receipts": [] if not repaired else [{"step_id": index, "operation": step["operation"], "revision": step["expected_revision"] + 1} for index, step in enumerate(row["plan_json"]["steps"], 1)]}
            if method == "POST" and path.endswith("/submit"):
                plan_id = path.split("/")[3]
                row = self._plan_row(plan_id)
                if row["state"] != "draft" or body.get("plan_hash") != row["plan_hash"]:
                    raise InvoiceRemoteError(409, "Draft local plan changed")
                row["state"] = "submitted"
                return {"plan_id": plan_id, "plan_hash": row["plan_hash"], "state": "submitted", "review_expires_at": row["review_expires_at"]}
            if method == "POST" and path.endswith("/reconcile"):
                plan_id = path.split("/")[3]
                row = self._plan_row(plan_id)
                if row["state"] != "executing" or body.get("plan_hash") != row["plan_hash"]:
                    raise InvoiceRemoteError(409, "Execution receipt changed")
                row["state"] = "verified"
                row["verification_json"] = dict(_CHECKS)
                receipts = [{"step_id": step["step_id"], "operation": step["operation"], "revision": step["expected_revision"] + 1} for step in row["plan_json"]["steps"]]
                return {"plan_id": plan_id, "plan_hash": row["plan_hash"], "state": "verified", "receipts": receipts, "checks": dict(_CHECKS), "replayed_steps": 0}
            if method == "POST" and path.endswith("/complete"):
                plan_id = path.split("/")[3]
                row = self._plan_row(plan_id)
                if row["state"] != "verified" or body.get("plan_hash") != row["plan_hash"]:
                    raise InvoiceRemoteError(409, "Verified local plan changed")
                row["state"] = "completed"
                return {"plan_id": plan_id, "plan_hash": row["plan_hash"], "state": "completed"}
            if method == "GET" and path == "/invoices/sandboxes":
                return {"source": "openshell-sandbox-inventory", "limit": 24, "target_count": 13, "cleanup_at_count": 14, "free_slots": 24, "free_gib": 64, "checked_at": _iso(), "remaining_count": 0, "sandboxes": []}
        raise InvoiceRemoteError(404, "Local invoice demo route not implemented")