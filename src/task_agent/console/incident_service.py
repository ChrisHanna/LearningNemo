"""Private sponsor-facing incident API, separate from review and execution."""

from datetime import UTC, datetime
import os

from fastapi import Depends, FastAPI, HTTPException, Path
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import uvicorn

from task_agent.console.identity import EntraTestSettings
from task_agent.console.incident_contract import IncidentStart, PlanSubmission
from task_agent.console.analysis_contract import ProposalRequest
from task_agent.console.review_service import EntraReviewVerifier, ReviewIdentity, ReviewVerifier
from task_agent.control.incident import SqlIncidentRepository
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureUnavailableError


def create_incident_app(repository: SqlIncidentRepository, verifier: ReviewVerifier, *, expires_at: datetime | None = None, initiator=None, analysis=None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    bearer = HTTPBearer(auto_error=False)

    async def operator(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> ReviewIdentity:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Operator sign-in required")
        try:
            identity = await verifier.verify(credentials.credentials)
        except Exception as error:
            raise HTTPException(status_code=401, detail="Incident identity verification failed") from error
        if identity.persona != "operator":
            raise HTTPException(status_code=403, detail="Operator role required; reviewers cannot submit plans")
        return identity

    @app.middleware("http")
    async def admission(request, call_next):
        if expires_at is not None and datetime.now(UTC) >= expires_at and request.url.path != "/healthz":
            return JSONResponse({"detail": "Incident service lease expired"}, status_code=503, headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready():
        try:
            await repository.list_proposals(author_hash="0" * 64, now=datetime.now(UTC))
        except (SqlProcedureUnavailableError, OperationDeniedError) as error:
            raise HTTPException(status_code=503, detail="Incident repository unavailable") from error
        return {"status": "ready"}

    @app.get("/incidents")
    async def incidents(identity: ReviewIdentity = Depends(operator)):
        try:
            records = await repository.list_proposals(author_hash=identity.subject_hash, now=datetime.now(UTC))
        except (SqlProcedureUnavailableError, OperationDeniedError) as error:
            raise HTTPException(status_code=503, detail="Incident repository unavailable; no empty queue is assumed") from error
        return {"source": "azure-sql", "incidents": [record.model_dump(mode="json") for record in records],
            "initiation_available": False, "producer": None}

    @app.post("/incidents/start")
    async def start(body: IncidentStart, identity: ReviewIdentity = Depends(operator)):
        raise HTTPException(status_code=403, detail="Operator analysis cannot start or cancel database workloads. Use read-only analysis.")

    @app.post("/incidents/{plan_id}/submit")
    async def submit(submission: PlanSubmission, plan_id: str = Path(pattern=r"^plan-[A-Za-z0-9-]{1,120}$"),
                     identity: ReviewIdentity = Depends(operator)):
        try:
            return await repository.submit(plan_id, submission, author_hash=identity.subject_hash, now=datetime.now(UTC))
        except OperationDeniedError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except SqlProcedureUnavailableError as error:
            raise HTTPException(status_code=503, detail="Submission unconfirmed; refresh before retrying") from error

    async def analyst(identity: ReviewIdentity = Depends(operator)):
        if not {'agent.invoke', 'tasks.read'} <= identity.scopes:
            raise HTTPException(403, 'Read-only analysis requires agent.invoke and tasks.read')
        if analysis is None:
            raise HTTPException(503, 'Read-only analysis is not configured')
        return identity

    @app.get('/analysis')
    async def latest(identity: ReviewIdentity = Depends(analyst)):
        try:
            receipt = await analysis.latest(identity.subject_hash)
            return {'available': True, 'analysis': receipt.model_dump(mode='json') if receipt else None}
        except SqlProcedureUnavailableError as error:
            raise HTTPException(503, 'Analysis evidence unavailable') from error

    @app.post('/analysis')
    async def analyze(identity: ReviewIdentity = Depends(analyst)):
        try:
            receipt = await analysis.analyze(identity.subject_hash)
            return {'available': True, 'analysis': receipt.model_dump(mode='json')}
        except SqlProcedureUnavailableError as error:
            raise HTTPException(503, 'Read-only analysis unavailable; no workload changed') from error

    @app.post('/analysis/propose')
    async def propose(body: ProposalRequest, identity: ReviewIdentity = Depends(analyst)):
        try:
            return await analysis.propose(body, identity.subject_hash)
        except OperationDeniedError as error:
            raise HTTPException(409, str(error)) from error
        except SqlProcedureUnavailableError as error:
            raise HTTPException(503, 'Proposal persistence unconfirmed; refresh before retrying') from error

    return app


def deployed_incident_app() -> FastAPI:
    from task_agent.control.mssql_client import MssqlProcedureClient
    expiry = datetime.fromisoformat(os.environ["LEARNINGNEMO_INCIDENT_EXPIRES_AT"].replace("Z", "+00:00"))
    if expiry.tzinfo is None or not 0 < (expiry - datetime.now(UTC)).total_seconds() <= 7200:
        raise ValueError("Incident service needs a future lease of at most two hours")
    client = MssqlProcedureClient(server=os.environ["LEARNINGNEMO_SQL_SERVER"], database=os.environ["LEARNINGNEMO_SQL_DATABASE"],
        client_id=os.environ["AZURE_CLIENT_ID"], application_name="LearningNeMoHumanIncident", query_timeout_seconds=15)
    analysis = None
    if os.environ.get("LEARNINGNEMO_DIAGNOSTIC_AUDIENCE"):
        from azure.identity import ManagedIdentityCredential
        from task_agent.control.database_analysis import DatabaseAnalysisService, DiagnosticReader
        reader = DiagnosticReader(os.environ['LEARNINGNEMO_DIAGNOSTIC_AUDIENCE'], lambda: ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID']))
        analysis = DatabaseAnalysisService(client, reader)
    return create_incident_app(SqlIncidentRepository(client), EntraReviewVerifier(EntraTestSettings.from_sources()), expires_at=expiry, analysis=analysis)


def main() -> None:
    uvicorn.run(deployed_incident_app(), host="0.0.0.0", port=8080, workers=1, proxy_headers=False)


if __name__ == "__main__":
    main()