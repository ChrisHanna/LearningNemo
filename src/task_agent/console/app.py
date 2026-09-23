"""Compose and launch the local browser console."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import uuid
import webbrowser
from pathlib import Path
from typing import Any
from typing import Literal

import uvicorn
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pydantic import Field

from task_agent.console.agent_client import AgentClient
from task_agent.console.agent_client import AgentClientError
from task_agent.console.incident_contract import IncidentStart, PlanSubmission
from task_agent.console.analysis_contract import ProposalRequest
from task_agent.console.remote_incident import IncidentUnavailable, RemoteIncidentService
from task_agent.console.remote_execution import ExecutionUnavailable, RemoteExecutionService
from task_agent.console.remote_invoice import RemoteInvoiceService, InvoiceRemoteError, InvoiceJobRequest, InvoicePlanRequest, InvoiceDecisionRequest, InvoiceScenarioRequest, InvoiceChallengeRequest, InvoiceSandboxTestRequest, InvoiceDemoSessionRequest
from task_agent.console.execution_contract import ExecuteRequest, CompleteRequest
from task_agent.console.browser_sessions import BrowserSession
from task_agent.console.browser_sessions import BrowserSessionRegistry
from task_agent.console.browser_sessions import CSRF_HEADER
from task_agent.console.browser_sessions import SESSION_COOKIE
from task_agent.console.capabilities import SYSTEM_CAPABILITIES
from task_agent.console.capabilities import request_evidence
from task_agent.console.identity import DEFAULT_SETTINGS_PATH
from task_agent.console.identity import EntraTestSettings
from task_agent.console.hosting import ConsoleHosting
from task_agent.console.remote_workspace import RemoteWorkspace
from task_agent.console.remote_review import RemoteReviewService, ReviewUnavailable
from task_agent.console.review_contract import ReviewDecision
from task_agent.console.live_workspace import LiveWorkspace, WorkspaceIdentityVerifier, WorkspaceLiveError
from task_agent.console.sessions import DeviceAuthManager
from task_agent.console.showcase import SHOWCASE
from task_agent.console.showcase import SHOWCASE_DOCUMENTS
from task_agent.console.walkthrough import WALKTHROUGH_BY_ID
from task_agent.console.walkthrough import walkthrough_manifest
from task_agent.security.policies import evaluate_tool_access


STATIC_DIR = Path(__file__).with_name("static")
logger = logging.getLogger(__name__)
APPROVER_READ_PATHS = {
    "/api/approvals", "/api/capabilities", "/api/showcase",
    *(f"/api/showcase/documents/{name}" for name in SHOWCASE_DOCUMENTS),
}


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class DemoSignInRequest(BaseModel):
    persona: Literal["operator", "approver"] | None = None


def create_app(
    settings: EntraTestSettings,
    agent_url: str,
    *,
    auth_manager: DeviceAuthManager | None = None,
    agent_client: AgentClient | None = None,
    session_registry: BrowserSessionRegistry | None = None,
    live_workspace: LiveWorkspace | None = None,
    workspace_verifier: Any = None,
    hosting: ConsoleHosting | None = None,
    review_service: RemoteReviewService | None = None,
    incident_service: RemoteIncidentService | None = None,
    execution_service: RemoteExecutionService | None = None,
    invoice_service: RemoteInvoiceService | None = None,
) -> FastAPI:
    host_policy = hosting or ConsoleHosting()
    registry = session_registry or BrowserSessionRegistry(
        settings,
        auth_factory=(lambda: auth_manager) if auth_manager is not None else (lambda: DeviceAuthManager(settings, review_enabled=review_service is not None or invoice_service is not None)),
    )
    agent = agent_client or AgentClient(agent_url)
    workspace = live_workspace or LiveWorkspace(None)
    if host_policy.cloud and workspace.enabled and not isinstance(workspace, RemoteWorkspace):
        raise ValueError("Cloud console cannot use the local operator workspace transport")
    identity_verifier = workspace_verifier or (WorkspaceIdentityVerifier(settings) if workspace.enabled else None)
    app = FastAPI(title="LearningNeMo", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def secure_local_console(request: Request, call_next):
        if request.url.path == "/healthz" and request.method in {"GET", "HEAD"}:
            response = JSONResponse({"status": "ok"})
            _apply_security_headers(response)
            return response
        if not host_policy.accepts_host(request.url.hostname):
            return _secured_error(400, "Invalid Host header")

        browser_session = registry.get(request.cookies.get(SESSION_COOKIE))
        created = False
        if browser_session is None and request.method in {"GET", "HEAD"}:
            browser_session = registry.create()
            created = True
        request.state.browser_session = browser_session

        if host_policy.cloud and request.url.path.startswith("/api/") and request.url.path not in {
            "/api/bootstrap", "/api/auth", "/api/auth/start", "/api/live-workspace",
        }:
            try:
                user = browser_session.auth.current_user() if browser_session else None
                decision = await identity_verifier.verify(user.access_token) if user else None
                permitted = decision and (
                    decision.get("canRead")
                    or (decision.get("canReview") and request.method == "GET" and (request.url.path in APPROVER_READ_PATHS or invoice_service is not None and request.url.path == '/api/invoices/plans'))
                    or (invoice_service is not None and decision.get('canReview') and 'plans.review' in user.scopes
                        and request.method == 'POST' and re.fullmatch(r'/api/invoices/review/[a-f0-9]{32}', request.url.path))
                    or (review_service is not None and decision.get("canReview") and request.method == "POST" and request.url.path == "/api/auth/review")
                    or (review_service is not None and decision.get("canReview") and "plans.review" in user.scopes
                        and request.method == "POST" and re.fullmatch(r"/api/approvals/plan-[A-Za-z0-9-]{1,120}/decision", request.url.path))
                )
                if not permitted:
                    return _secured_error(403, "This operation is not permitted for your assigned demo role")
            except Exception:
                return _secured_error(401, "Sign in with an assigned demo account")

        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            if browser_session is None:
                return _secured_error(403, "Browser session required")
            expected_origin = host_policy.expected_origin(f"{request.url.scheme}://{request.url.netloc}")
            if request.headers.get("origin") != expected_origin:
                return _secured_error(403, "Invalid request origin")
            supplied_csrf = request.headers.get(CSRF_HEADER, "")
            if not secrets.compare_digest(supplied_csrf, browser_session.csrf_token):
                return _secured_error(403, "Invalid CSRF token")

        response = await call_next(request)
        _apply_security_headers(response)
        if host_policy.cloud:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if created and browser_session is not None:
            response.set_cookie(
                SESSION_COOKIE,
                browser_session.id,
                httponly=True,
                samesite="strict",
                secure=host_policy.cloud,
                path="/",
            )
        return response

    def current_auth(request: Request) -> DeviceAuthManager:
        browser_session: BrowserSession | None = request.state.browser_session
        if browser_session is None:
            raise HTTPException(status_code=401, detail="Browser session required")
        return browser_session.auth

    def require_task_user(user) -> None:
        if "Task.Approver" in user.roles or "Task.Reader" not in user.roles:
            raise HTTPException(status_code=403, detail="Approver access does not permit agent tasks or workspace operations")

    @app.get("/api/bootstrap")
    async def bootstrap(request: Request) -> dict[str, Any]:
        browser_session: BrowserSession = request.state.browser_session
        auth = browser_session.auth
        session = auth.snapshot()
        return {
            "agent": {"status": "not_permitted"} if session.get("persona") == "approver" else await agent.health() if not host_policy.cloud or session["status"] == "authenticated" else {"status": "not_authenticated"},
            "agentUrl": agent_url if not host_policy.cloud else "Cloud agent API",
            "session": session,
            "csrfToken": browser_session.csrf_token,
            "hosting": "azure" if host_policy.cloud else "local",
            "reviewEnabled": review_service is not None or invoice_service is not None,
            "invoiceEnabled": invoice_service is not None,
        }

    @app.get("/api/capabilities")
    async def capabilities() -> dict[str, Any]:
        return SYSTEM_CAPABILITIES

    @app.get("/api/showcase")
    async def showcase() -> dict[str, Any]:
        return SHOWCASE

    def require_incident_operator(request: Request, *, read_only=False):
        try:
            user = current_auth(request).current_user()
        except RuntimeError as error:
            raise HTTPException(status_code=401, detail="Operator sign-in required") from error
        required_scopes = {'agent.invoke', 'tasks.read'} if read_only else {'agent.invoke', 'tasks.read', 'tasks.execute'}
        if "Task.Approver" in user.roles or not {"Task.Reader", "Task.Operator"} <= user.roles or not required_scopes <= user.scopes:
            raise HTTPException(status_code=403, detail="Operator scope and role required for incident handoff")
        return user

    async def invoice_forward(request, method, path, body=None, *, reviewer=False, after=0):
        if invoice_service is None:
            raise HTTPException(503, 'Invoice agent workflow is not configured')
        if reviewer:
            try:
                user = current_auth(request).current_user()
            except RuntimeError:
                raise HTTPException(401, 'Approver sign-in required') from None
            if user.persona != 'approver' or 'plans.review' not in user.scopes:
                raise HTTPException(403, 'Independent Approver scope and role required')
        else:
            user = require_incident_operator(request, read_only=True)
            if method == 'POST' and (path.endswith(('/complete','/reconcile','/sandbox-test','/delete')) or path in ('/invoices/scenarios','/invoices/challenges','/invoices/demo-session/start','/invoices/demo-session/end') or path == '/invoices/jobs' and body.get('kind') == 'execution') and 'tasks.execute' not in user.scopes:
                raise HTTPException(403, 'Execution scope required')
        try:
            return await invoice_service.request(method, path, user.access_token, body, review=reviewer and path != '/invoices/demo-session', after=after)
        except InvoiceRemoteError as error:
            raise HTTPException(error.status_code, str(error)) from error

    @app.get('/api/invoices/plans')
    async def invoice_plans(request: Request):
        try:
            reviewer = current_auth(request).current_user().persona == 'approver'
        except RuntimeError:
            raise HTTPException(401, 'Sign in to read invoice plans') from None
        return await invoice_forward(request, 'GET', '/invoices/plans', reviewer=reviewer)

    @app.get('/api/invoices/demo-session')
    async def invoice_demo_status(request: Request):
        try:
            reviewer = current_auth(request).current_user().persona == 'approver'
        except RuntimeError:
            raise HTTPException(401, 'Sign in to read demo status') from None
        return await invoice_forward(request, 'GET', '/invoices/demo-session', reviewer=reviewer)

    @app.post('/api/invoices/demo-session/{action}')
    async def invoice_demo_action(request: Request, action: str, body: InvoiceDemoSessionRequest):
        if action not in ('start','end'): raise HTTPException(422, 'Invalid demo action')
        return await invoice_forward(request, 'POST', '/invoices/demo-session/' + action, body.model_dump())

    @app.get('/api/invoices/sandboxes')
    async def invoice_sandbox_inventory(request: Request):
        return await invoice_forward(request, 'GET', '/invoices/sandboxes')

    @app.post('/api/invoices/sandboxes/{sandbox_id}/delete')
    async def invoice_sandbox_delete(request: Request, sandbox_id: str, body: InvoiceSandboxTestRequest):
        if not re.fullmatch(r'[a-f0-9]{32}', sandbox_id) or sandbox_id != body.sandbox_id: raise HTTPException(422, 'Exact sandbox confirmation required')
        return await invoice_forward(request, 'POST', '/invoices/sandboxes/' + sandbox_id + '/delete', body.model_dump())

    @app.post('/api/invoices/jobs/{job_id}/sandbox-test', status_code=202)
    async def invoice_sandbox_test(request: Request, job_id: str, body: InvoiceSandboxTestRequest):
        if not re.fullmatch('[a-f0-9]{32}', job_id): raise HTTPException(422, 'Invalid invoice job identifier')
        return await invoice_forward(request, 'POST', '/invoices/jobs/' + job_id + '/sandbox-test', body.model_dump())

    @app.post('/api/invoices/challenges', status_code=202)
    async def invoice_challenge(request: Request, body: InvoiceChallengeRequest):
        return await invoice_forward(request, 'POST', '/invoices/challenges', body.model_dump())

    @app.get('/api/invoices/challenges/{challenge_id}')
    async def invoice_challenge_status(request: Request, challenge_id: str):
        if not re.fullmatch('[a-f0-9]{32}', challenge_id): raise HTTPException(422, 'Invalid challenge identifier')
        return await invoice_forward(request, 'GET', '/invoices/challenges/'+challenge_id)

    @app.get('/api/invoices/plans/{plan_id}/evidence')
    async def invoice_evidence(request: Request, plan_id: str):
        if not re.fullmatch('[a-f0-9]{32}', plan_id): raise HTTPException(422, 'Invalid plan identifier')
        return await invoice_forward(request, 'GET', '/invoices/plans/'+plan_id+'/evidence')

    @app.post('/api/invoices/jobs', status_code=202)
    async def invoice_job(request: Request, body: InvoiceJobRequest):
        return await invoice_forward(request, 'POST', '/invoices/jobs', body.model_dump())

    @app.post('/api/invoices/scenarios', status_code=201)
    async def invoice_scenario(request: Request, body: InvoiceScenarioRequest):
        return await invoice_forward(request, 'POST', '/invoices/scenarios', body.model_dump())

    @app.get('/api/invoices/scenarios/{scenario_id}')
    async def invoice_scenario_status(request: Request, scenario_id: str):
        if not re.fullmatch('[a-f0-9]{32}', scenario_id): raise HTTPException(422, 'Invalid scenario identifier')
        return await invoice_forward(request, 'GET', '/invoices/scenarios/' + scenario_id)

    @app.get('/api/invoices/jobs/{job_id}')
    async def invoice_job_status(request: Request, job_id: str):
        if not re.fullmatch('[a-f0-9]{32}', job_id):
            raise HTTPException(422, 'Invalid invoice job identifier')
        return await invoice_forward(request, 'GET', '/invoices/jobs/' + job_id)

    @app.get('/api/invoices/jobs/{job_id}/events')
    async def invoice_job_events(request: Request, job_id: str, after: int = 0):
        if not re.fullmatch('[a-f0-9]{32}', job_id) or after < 0:
            raise HTTPException(422, 'Invalid invoice activity cursor')
        return await invoice_forward(request, 'GET', '/invoices/jobs/' + job_id + '/events', after=after)

    @app.post('/api/invoices/plans/{plan_id}/{action}')
    async def invoice_plan_action(request: Request, plan_id: str, action: str, body: InvoicePlanRequest):
        if not re.fullmatch('[a-f0-9]{32}', plan_id) or action not in ('submit', 'complete', 'reconcile'):
            raise HTTPException(422, 'Invalid invoice plan action')
        return await invoice_forward(request, 'POST', '/invoices/plans/' + plan_id + '/' + action, body.model_dump())

    @app.post('/api/invoices/review/{plan_id}')
    async def invoice_review_action(request: Request, plan_id: str, body: InvoiceDecisionRequest):
        if not re.fullmatch('[a-f0-9]{32}', plan_id):
            raise HTTPException(422, 'Invalid invoice plan identifier')
        return await invoice_forward(request, 'POST', '/invoices/plans/' + plan_id + '/decision', body.model_dump(), reviewer=True)

    @app.get("/api/incidents")
    async def incident_queue(request: Request):
        user = require_incident_operator(request, read_only=True)
        if incident_service is None:
            return {"source": "service-configuration", "availability": "not-configured",
                    "detail": "The incident producer and handoff service are not connected. No investigation has been created by this request."}
        try:
            queue = await incident_service.list_incidents(user.access_token)
            return {**queue, "availability": "connected"}
        except IncidentUnavailable as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error

    @app.get('/api/analysis')
    async def latest_analysis(request: Request):
        user = require_incident_operator(request, read_only=True)
        if incident_service is None:
            raise HTTPException(503, 'Read-only diagnostic service is not configured')
        try:
            return await incident_service.analysis(user.access_token)
        except IncidentUnavailable as error:
            raise HTTPException(error.status_code, str(error)) from error

    @app.post('/api/analysis')
    async def analyze_database(request: Request):
        user = require_incident_operator(request, read_only=True)
        if incident_service is None:
            raise HTTPException(503, 'Read-only diagnostic service is not configured')
        try:
            return await incident_service.analysis(user.access_token, refresh=True)
        except IncidentUnavailable as error:
            raise HTTPException(error.status_code, str(error)) from error

    @app.post('/api/analysis/propose')
    async def propose_analysis(body: ProposalRequest, request: Request):
        user = require_incident_operator(request, read_only=True)
        if incident_service is None:
            raise HTTPException(503, 'Proposal service is not configured')
        try:
            return await incident_service.propose(body, user.access_token)
        except IncidentUnavailable as error:
            raise HTTPException(error.status_code, str(error)) from error

    @app.post("/api/incidents/start")
    async def start_incident(body: IncidentStart, request: Request):
        require_incident_operator(request)
        raise HTTPException(status_code=403, detail="Operator analysis cannot start or cancel database workloads. Use read-only analysis.")

    @app.post("/api/incidents/{plan_id}/submit")
    async def submit_incident(plan_id: str, submission: PlanSubmission, request: Request):
        user = require_incident_operator(request)
        if incident_service is None:
            raise HTTPException(status_code=503, detail="Incident service not configured; no plan was submitted")
        if re.fullmatch(r"plan-[A-Za-z0-9-]{1,120}", plan_id) is None:
            raise HTTPException(status_code=422, detail="Invalid plan identifier")
        try:
            return await incident_service.submit(plan_id, submission, user.access_token)
        except IncidentUnavailable as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error

    @app.get('/api/executions/{plan_id}')
    async def execution_status(plan_id: str, request: Request):
        user = require_incident_operator(request)
        if execution_service is None:
            return {'availability': 'not-configured'}
        try:
            return {**await execution_service.status(plan_id, user.access_token), 'availability': 'connected'}
        except (ExecutionUnavailable, ValueError) as error:
            raise HTTPException(503, str(error)) from error

    @app.post('/api/executions/{plan_id}')
    async def execute_plan(plan_id: str, body: ExecuteRequest, request: Request):
        user = require_incident_operator(request)
        if execution_service is None:
            raise HTTPException(503, 'Execution service not connected')
        try:
            return await execution_service.execute(plan_id, body, user.access_token)
        except (ExecutionUnavailable, ValueError) as error:
            raise HTTPException(503, str(error)) from error

    @app.post('/api/executions/{execution_id}/complete')
    async def complete_execution(execution_id: str, body: CompleteRequest, request: Request):
        user = require_incident_operator(request)
        if execution_service is None:
            raise HTTPException(503, 'Execution service not connected')
        try:
            return await execution_service.complete(execution_id, body, user.access_token)
        except (ExecutionUnavailable, ValueError) as error:
            raise HTTPException(503, str(error)) from error

    @app.post('/api/executions/{plan_id}/reconcile')
    async def reconcile_execution(plan_id: str, request: Request):
        user = require_incident_operator(request)
        if execution_service is None:
            raise HTTPException(503, 'Execution service not connected')
        try:
            return await execution_service.reconcile(plan_id, user.access_token)
        except (ExecutionUnavailable, ValueError) as error:
            raise HTTPException(503, str(error)) from error

    @app.get("/api/approvals")
    async def approval_status(request: Request) -> dict[str, Any]:
        try:
            user = current_auth(request).current_user()
        except RuntimeError as error:
            raise HTTPException(status_code=401, detail="Sign in with the dedicated Approver account") from error
        if "Task.Approver" not in user.roles or "Task.Operator" in user.roles or "agent.invoke" not in user.scopes:
            raise HTTPException(status_code=403, detail="A separate Approver account is required")
        if review_service is not None:
            if "plans.review" not in user.scopes:
                raise HTTPException(status_code=403, detail="Review service requires plans.review consent; sign-in alone does not grant decision authority")
            try:
                queue = await review_service.list_plans(user.access_token)
                return {"persona": "approver", "accountFingerprint": user.account_fingerprint,
                        "source": "azure-sql", "availability": "connected", "canSubmitDecision": True,
                        "plans": queue["plans"], "detail": "Review the exact plan before approving or rejecting. A decision does not execute it."}
            except ReviewUnavailable as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
        return {
            "persona": "approver", "accountFingerprint": user.account_fingerprint,
            "source": "service-configuration", "availability": "not-configured",
            "canSubmitDecision": False,
            "detail": "Approver sign-in is enabled. The plan review queue and approval submission service are not connected yet.",
        }

    @app.post("/api/approvals/{plan_id}/decision")
    async def approval_decision(plan_id: str, decision: ReviewDecision, request: Request) -> dict[str, Any]:
        try:
            user = current_auth(request).current_user()
        except RuntimeError as error:
            raise HTTPException(status_code=401, detail="Approver sign-in required") from error
        if ("Task.Approver" not in user.roles or "Task.Operator" in user.roles
                or not {"agent.invoke", "plans.review"} <= user.scopes):
            raise HTTPException(status_code=403, detail="Separate Approver identity and plans.review scope required")
        if review_service is None:
            raise HTTPException(status_code=503, detail="Review service is not configured; no decision was submitted")
        if re.fullmatch(r"plan-[A-Za-z0-9-]{1,120}", plan_id) is None:
            raise HTTPException(status_code=422, detail="Invalid plan identifier")
        try:
            return await review_service.decide(plan_id, decision, user.access_token)
        except ReviewUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/live-workspace")
    async def live_workspace_config() -> dict[str, Any]:
        return {"enabled": workspace.enabled, "source": "configuration", "cloudStatus": "not-checked"}

    @app.post("/api/live-workspace/check")
    async def check_live_workspace(request: Request) -> dict[str, Any]:
        try:
            session = current_auth(request).snapshot()
            if session.get("status") == "authenticated" and session.get("persona") == "approver":
                raise HTTPException(status_code=403, detail="Approver access does not permit workspace operations")
            if host_policy.cloud:
                return await asyncio.to_thread(workspace.check, current_auth(request).current_user().access_token)
            return await asyncio.to_thread(workspace.check)
        except WorkspaceLiveError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.post("/api/live-workspace/run")
    async def run_live_workspace(request: Request) -> dict[str, Any]:
        auth = current_auth(request)
        try:
            user = auth.current_user()
        except RuntimeError as error:
            raise HTTPException(status_code=401, detail="Sign in before running workspace proof") from error
        require_task_user(user)
        if identity_verifier is None:
            raise HTTPException(status_code=503, detail="Live workspace transport is disabled")
        try:
            decision = await identity_verifier.verify(user.access_token)
        except Exception as error:
            raise HTTPException(status_code=401, detail="Identity verification failed; sign in again") from error
        run_id = uuid.uuid4().hex
        identity = {"accountFingerprint": user.account_fingerprint, "decision": decision}
        if not decision["allowed"]:
            return JSONResponse(status_code=403, content={
                "status": "denied", "source": "live-request", "runId": run_id,
                "identity": identity, "workspaceExecuted": False,
            })
        try:
            result = await asyncio.to_thread(workspace.run, run_id, user.access_token) if host_policy.cloud else await asyncio.to_thread(workspace.run, run_id)
        except WorkspaceLiveError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        try:
            if auth.current_user().access_token != user.access_token:
                raise RuntimeError("Session changed")
        except RuntimeError as error:
            raise HTTPException(status_code=401, detail="Session changed during the run; sign in again") from error
        return {**result, "source": "live-request", "runId": run_id, "identity": identity}

    @app.get("/api/showcase/documents/{document_id}")
    async def showcase_document(document_id: str) -> FileResponse:
        filename = SHOWCASE_DOCUMENTS.get(document_id)
        if filename is None:
            raise HTTPException(status_code=404, detail="Document not found")
        path = Path(__file__).resolve().parents[3] / "docs" / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Document not installed")
        return FileResponse(path, media_type="text/plain; charset=utf-8")

    @app.get("/api/walkthrough")
    async def walkthrough(request: Request) -> dict[str, Any]:
        auth = current_auth(request)
        try:
            user = auth.current_user()
        except RuntimeError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        require_task_user(user)
        steps = []
        for step in walkthrough_manifest(user.persona):
            steps.append(
                {
                    **step,
                    "accessDecision": evaluate_tool_access(
                        step["tool"],
                        user.scopes,
                        user.roles,
                    ),
                }
            )
        return {
            "persona": user.persona,
            "grantedScopes": sorted(user.scopes),
            "grantedRoles": sorted(user.roles),
            "accountFingerprint": user.account_fingerprint,
            "steps": steps,
        }

    @app.get("/api/auth")
    async def auth_status(request: Request) -> dict[str, Any]:
        auth = current_auth(request)
        return auth.snapshot()

    @app.post("/api/auth/start")
    async def start_auth(request: Request, body: DemoSignInRequest | None = None) -> dict[str, Any]:
        auth = current_auth(request)
        try:
            if body is not None and body.persona is not None:
                return await asyncio.to_thread(auth.start, body.persona)
            return await asyncio.to_thread(auth.start)
        except RuntimeError as error:
            logger.warning("authentication_start_failed: %s", error)
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.delete("/api/auth")
    async def clear_auth(request: Request) -> dict[str, Any]:
        auth = current_auth(request)
        return auth.clear()

    @app.post("/api/auth/review")
    async def authorize_review(request: Request):
        if review_service is None:
            raise HTTPException(status_code=503, detail="Review service not configured")
        auth = current_auth(request)
        try:
            if auth.current_user().persona != "approver":
                raise HTTPException(status_code=403, detail="Approver account required")
            return auth.start_review()
        except RuntimeError as error:
            raise HTTPException(status_code=401, detail="Sign in as Approver before authorizing review access") from error

    @app.post("/api/agent-check")
    async def agent_check(request: Request) -> dict[str, Any]:
        try:
            user = current_auth(request).current_user()
        except RuntimeError as error:
            raise HTTPException(status_code=401, detail="Sign in before checking the agent") from error
        require_task_user(user)
        try:
            reply = await agent.chat(user.access_token, "List the current demo tasks. Do not execute or reset any tasks.", read_only=True)
        except AgentClientError as error:
            logger.warning("agent_check_failed status=%s category=%s request_id=%s validation_fields=%s", error.status_code, error.category, error.request_id, json.dumps(error.validation_fields))
            raise HTTPException(status_code=error.status_code, detail="The protected read-only agent check failed", headers={"X-Request-ID": error.request_id}) from error
        return {"content": reply.content, "httpStatus": reply.status_code, "requestId": reply.request_id, "scope": "read-only-task-diagnostic", "incidentEvidence": False}

    @app.post("/api/chat")
    async def chat(request: Request, payload: ChatRequest) -> dict[str, Any]:
        auth = current_auth(request)
        try:
            user = auth.current_user()
        except RuntimeError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        require_task_user(user)
        try:
            reply = await agent.chat(user.access_token, payload.prompt)
        except AgentClientError as error:
            logger.warning("agent_request_failed status=%s category=%s request_id=%s validation_fields=%s", error.status_code, error.category, error.request_id, json.dumps(error.validation_fields))
            raise HTTPException(status_code=error.status_code, detail="The protected agent request failed", headers={"X-Request-ID": error.request_id}) from error
        return {
            "persona": user.persona,
            "content": reply.content,
            "httpStatus": reply.status_code,
            "durationMs": reply.duration_ms,
            "requestId": reply.request_id,
            "grantedScopes": sorted(user.scopes),
            "grantedRoles": sorted(user.roles),
            "evidence": request_evidence(
                user.persona,
                sorted(user.scopes),
                sorted(user.roles),
                reply.status_code,
                reply.duration_ms,
            ),
        }

    @app.post("/api/walkthrough/{step_id}")
    async def run_walkthrough_step(step_id: str, request: Request) -> dict[str, Any]:
        auth = current_auth(request)
        step = WALKTHROUGH_BY_ID.get(step_id)
        if step is None:
            raise HTTPException(status_code=404, detail="Unknown walkthrough step")
        try:
            user = auth.current_user()
        except RuntimeError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        require_task_user(user)
        if step.persona != user.persona:
            raise HTTPException(
                status_code=403,
                detail="Access denied",
            )
        access_decision = evaluate_tool_access(step.tool, user.scopes, user.roles)
        try:
            reply = await agent.chat(user.access_token, step.prompt)
        except AgentClientError as error:
            logger.warning("walkthrough_agent_request_failed status=%s category=%s request_id=%s validation_fields=%s", error.status_code, error.category, error.request_id, json.dumps(error.validation_fields))
            if not access_decision["allowed"] and error.status_code == 403:
                return JSONResponse(status_code=403, content={
                    "stepId": step.id,
                    "persona": user.persona,
                    "prompt": step.prompt,
                    "tool": step.tool,
                    "content": "Denied before tool execution by the configured access policy.",
                    "httpStatus": 403,
                    "durationMs": error.duration_ms,
                    "requestId": error.request_id,
                    "grantedScopes": sorted(user.scopes),
                    "grantedRoles": sorted(user.roles),
                    "accessDecision": access_decision,
                    "evidence": request_evidence(
                        user.persona,
                        sorted(user.scopes),
                        sorted(user.roles),
                        403,
                        error.duration_ms,
                    ),
                })
            raise HTTPException(status_code=error.status_code, detail="The protected agent request failed", headers={"X-Request-ID": error.request_id}) from error
        return {
            "stepId": step.id,
            "persona": user.persona,
            "prompt": step.prompt,
            "tool": step.tool,
            "content": reply.content,
            "httpStatus": reply.status_code,
            "durationMs": reply.duration_ms,
            "requestId": reply.request_id,
            "grantedScopes": sorted(user.scopes),
            "grantedRoles": sorted(user.roles),
            "accessDecision": access_decision,
            "evidence": request_evidence(
                user.persona,
                sorted(user.scopes),
                sorted(user.roles),
                reply.status_code,
                reply.duration_ms,
            ),
        }

    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="console-assets")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


def _apply_security_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"


def _secured_error(status_code: int, detail: str) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content={"detail": detail})
    _apply_security_headers(response)
    return response


def _open_browser(url: str) -> None:
    if os.getenv("WSL_DISTRO_NAME"):
        explorer = shutil.which("explorer.exe")
        if explorer:
            subprocess.Popen([explorer, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--agent-url", default="http://127.0.0.1:8001/v1/chat/completions")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--workspace-subscription", help="Opt in to fixed demo probes using this operator Azure CLI subscription")
    args = parser.parse_args()

    settings = EntraTestSettings.from_sources(args.settings)
    app = create_app(settings, args.agent_url, live_workspace=LiveWorkspace(args.workspace_subscription))
    console_url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        threading.Timer(1, _open_browser, args=(console_url,)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()