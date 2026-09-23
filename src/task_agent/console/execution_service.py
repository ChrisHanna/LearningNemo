"""Operator-only execution API; no approval issuing or browser-supplied receipts."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
import os

from fastapi import Depends, FastAPI, HTTPException, Path
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import httpx
import uvicorn

from task_agent.console.execution_contract import ExecuteRequest, CompleteRequest
from task_agent.console.identity import EntraTestSettings
from task_agent.console.review_service import EntraReviewVerifier, ReviewIdentity, ReviewVerifier
from task_agent.control.execution import ApprovedExecutionCoordinator, ExecutionIntent, SqlExecutionRepository
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureUnavailableError


def create_execution_app(coordinator: ApprovedExecutionCoordinator, repository: SqlExecutionRepository,
                         verifier: ReviewVerifier, *, expires_at: datetime, lifespan=None) -> FastAPI:
    if expires_at.tzinfo is None:
        raise ValueError('explicit timezone-aware service lease required')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    bearer = HTTPBearer(auto_error=False)

    async def operator(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> ReviewIdentity:
        if credentials is None or credentials.scheme.lower() != 'bearer':
            raise HTTPException(401, 'Operator authentication required')
        try:
            identity = await verifier.verify(credentials.credentials)
        except Exception as error:
            raise HTTPException(401, 'Execution identity verification failed') from error
        if identity.persona != 'operator' or 'tasks.execute' not in identity.scopes:
            raise HTTPException(403, 'Independent reviewers and Readers cannot execute remediation')
        return identity

    @app.middleware('http')
    async def admission(request, call_next):
        if request.url.path != '/healthz' and datetime.now(UTC) >= expires_at:
            return JSONResponse({'detail': 'Execution service lease expired'}, status_code=503, headers={'Cache-Control': 'no-store'})
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/healthz')
    async def health():
        return {'status': 'ok'}

    @app.get('/readyz')
    async def ready():
        try:
            await repository.status('plan-readiness', sponsor_hash='0' * 64)
        except SqlProcedureUnavailableError as error:
            raise HTTPException(503, 'Execution repository unavailable') from error
        return {'status': 'ready'}

    @app.get('/executions/{plan_id}')
    async def status(plan_id: str = Path(pattern=r'^plan-[A-Za-z0-9-]{1,120}$'), identity: ReviewIdentity = Depends(operator)):
        try:
            record = await repository.status(plan_id, sponsor_hash=identity.subject_hash)
            return {'source': 'azure-sql', 'execution': record.model_dump(mode='json') if record else None}
        except SqlProcedureUnavailableError as error:
            raise HTTPException(503, 'Execution status unavailable; do not replay the write') from error

    @app.post('/executions/{plan_id}')
    async def execute(body: ExecuteRequest, plan_id: str = Path(pattern=r'^plan-[A-Za-z0-9-]{1,120}$'), identity: ReviewIdentity = Depends(operator)):
        try:
            return await coordinator.execute(ExecutionIntent(plan_id=plan_id, plan_hash=body.plan_hash, plan_version=body.plan_version), sponsor_hash=identity.subject_hash)
        except OperationDeniedError as error:
            raise HTTPException(409, str(error)) from error
        except (SqlProcedureUnavailableError, TimeoutError) as error:
            raise HTTPException(503, 'Execution outcome unconfirmed. Reconcile the claimed operation; do not automatically retry.') from error

    @app.post('/executions/{execution_id}/complete')
    async def complete(body: CompleteRequest, execution_id: str = Path(pattern=r'^execution-[a-f0-9]{32}$'), identity: ReviewIdentity = Depends(operator)):
        try:
            return await repository.complete(execution_id, sponsor_hash=identity.subject_hash, plan_hash=body.plan_hash)
        except OperationDeniedError as error:
            raise HTTPException(409, str(error)) from error
        except SqlProcedureUnavailableError as error:
            raise HTTPException(503, 'Completion outcome unconfirmed; no completion is assumed') from error

    @app.post('/executions/{plan_id}/reconcile')
    async def reconcile(plan_id: str = Path(pattern=r'^plan-[A-Za-z0-9-]{1,120}$'), identity: ReviewIdentity = Depends(operator)):
        try:
            return await coordinator.reconcile(plan_id, sponsor_hash=identity.subject_hash)
        except (OperationDeniedError, SqlProcedureUnavailableError) as error:
            raise HTTPException(409, 'Committed execution could not be reconciled; no remediation was replayed') from error

    return app


def deployed_execution_app() -> FastAPI:
    from azure.identity import ManagedIdentityCredential
    from task_agent.control.execution_transport import ManagedExecutionTransport
    from task_agent.control.mssql_client import MssqlProcedureClient
    expires_at = datetime.fromisoformat(os.environ['LEARNINGNEMO_EXECUTION_EXPIRES_AT'].replace('Z', '+00:00'))
    if expires_at.tzinfo is None or not 0 < (expires_at - datetime.now(UTC)).total_seconds() <= 7200:
        raise ValueError('execution lease must be bounded to two hours')
    client_id = os.environ['AZURE_CLIENT_ID']
    sql = MssqlProcedureClient(server=os.environ['LEARNINGNEMO_SQL_SERVER'], database=os.environ['LEARNINGNEMO_SQL_DATABASE'],
                              client_id=client_id, application_name='LearningNeMoExecution')
    credential = ManagedIdentityCredential(client_id=client_id)
    client = httpx.AsyncClient()
    transport = ManagedExecutionTransport(broker_origin=os.environ['LEARNINGNEMO_BROKER_ORIGIN'], verifier_origin=os.environ['LEARNINGNEMO_VERIFIER_ORIGIN'],
        broker_audience=os.environ['LEARNINGNEMO_BROKER_AUDIENCE'], verifier_audience=os.environ['LEARNINGNEMO_VERIFIER_AUDIENCE'], credential=credential, client=client)
    repository = SqlExecutionRepository(sql)

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await client.aclose()
            credential.close()

    return create_execution_app(ApprovedExecutionCoordinator(repository, transport), repository,
        EntraReviewVerifier(EntraTestSettings.from_sources()), expires_at=expires_at, lifespan=lifespan)


def main():
    uvicorn.run(deployed_execution_app(), host='0.0.0.0', port=8080, workers=1, proxy_headers=False)


if __name__ == '__main__':
    main()