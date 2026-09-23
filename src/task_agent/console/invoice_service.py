"""Private human-facing invoice workflow API with independent reviewer authority."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from task_agent.control.invoice_contract import Digest, Identifier
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureUnavailableError
from task_agent.console.invoice_availability import validate_availability


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scenario_id: Identifier


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_hash: Digest


class DecisionRequest(PlanRequest):
    decision: Literal['approve', 'reject']


class AgentJobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: Identifier
    kind: Literal['planning', 'execution']
    target_id: Identifier
    plan_hash: Digest | None = None


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scenario_id: Identifier
    variant: Literal['lost-acknowledgement', 'healthy']


class ChallengeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    challenge_id: Identifier
    kind: Literal['planning','execution']


class SandboxTestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sandbox_id: Identifier


class DemoSessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: Identifier


def create_invoice_service(*, mode, repository, identity_verifier, expires_at, controller=None, activity=None, jobs=None, simulator=None, reconciler=None, challenges=None, observer=None, sandboxes=None, demo_session=None, demo_check=None, availability_mode='leased'):
    validate_availability(availability_mode, expires_at)
    if mode not in ('operator', 'review'):
        raise ValueError('explicit service mode and lease required')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    bearer = HTTPBearer(auto_error=False)

    @app.middleware('http')
    async def lease(request, call_next):
        if request.url.path != '/healthz' and expires_at is not None and datetime.now(UTC) >= expires_at:
            return JSONResponse({'detail': 'Invoice service lease expired'}, status_code=503)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    async def participant(value: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if value is None:
            raise HTTPException(401, 'Entra identity required')
        try:
            user = await identity_verifier.verify(value.credentials)
        except Exception:
            raise HTTPException(401, 'Identity verification failed') from None
        required = {'agent.invoke', 'tasks.read'} if user.persona == 'operator' else {'agent.invoke', 'plans.review'}
        if user.persona not in ('operator', 'approver') or not required <= user.scopes:
            raise HTTPException(403, 'This identity has no authority for this service')
        return user

    async def identity(user=Depends(participant), value: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if user.persona != ('operator' if mode == 'operator' else 'approver'):
            raise HTTPException(403, 'This identity has no authority for this service')
        if demo_session is not None:
            with demo_session.admitted():
                yield user
        else:
            release = await demo_check(value.credentials) if demo_check is not None else None
            try:
                yield user
            finally:
                if release is not None:
                    await release()

    async def demo_operator(user=Depends(participant)):
        if user.persona != 'operator' or 'tasks.execute' not in user.scopes:
            raise HTTPException(403, 'Operator execution scope required for demo session control')
        if demo_session is None:
            raise HTTPException(503, 'Demo sessions unavailable')
        return user

    @app.exception_handler(OperationDeniedError)
    async def denied(_request, error):
        return JSONResponse({'detail': str(error)}, status_code=409)

    @app.exception_handler(SqlProcedureUnavailableError)
    async def unavailable(request, _error):
        return JSONResponse({'detail': 'Read unavailable; retry this status check' if request.method == 'GET' else 'Persisted outcome unconfirmed; do not repeat a mutation'}, status_code=503)

    @app.get('/healthz')
    async def health():
        return {'status': 'ok', 'mode': mode,
            **({'demo_state': demo_session.status()['state']} if demo_session is not None else {})}

    @app.get('/invoices/plans')
    async def plans(user=Depends(identity)):
        rows = await repository.plans(user.subject_hash) if mode == 'operator' else await repository.reviews()
        return {'source': 'azure-sql', 'plans': rows}

    if mode == 'operator':
        @app.post('/invoices/demo-session/lease')
        async def demo_review_lease(body: DemoSessionRequest, user=Depends(participant)):
            if user.persona != 'approver': raise HTTPException(403, 'Approver identity required')
            if demo_session is None: raise HTTPException(503, 'Demo sessions unavailable')
            return demo_session.acquire_review(body.session_id, user.subject_hash)

        @app.post('/invoices/demo-session/release')
        async def demo_review_release(body: DemoSessionRequest, user=Depends(participant)):
            if user.persona != 'approver': raise HTTPException(403, 'Approver identity required')
            if demo_session is None: raise HTTPException(503, 'Demo sessions unavailable')
            return demo_session.release_review(body.session_id, user.subject_hash)

        @app.get('/invoices/demo-session')
        async def demo_status(user=Depends(participant)):
            if demo_session is None: raise HTTPException(503, 'Demo sessions unavailable')
            return {**demo_session.status(), 'can_end': user.persona == 'operator' and user.subject_hash == demo_session.owner}

        @app.post('/invoices/demo-session/start')
        async def demo_start(body: DemoSessionRequest, user=Depends(demo_operator)):
            return {**demo_session.start(body.session_id, user.subject_hash), 'can_end': True}

        @app.post('/invoices/demo-session/end')
        async def demo_end(body: DemoSessionRequest, user=Depends(demo_operator)):
            return {**demo_session.end(body.session_id, user.subject_hash), 'can_end': True}

        @app.get('/invoices/sandboxes')
        async def sandbox_inventory(user=Depends(identity)):
            if sandboxes is None: raise HTTPException(503, 'Sandbox inventory unavailable')
            return await sandboxes.inventory(user.subject_hash)

        @app.post('/invoices/sandboxes/{sandbox_id}/delete')
        async def delete_sandbox(body: SandboxTestRequest, sandbox_id: str = Path(pattern=r'^[a-f0-9]{32}$'), user=Depends(identity)):
            if 'tasks.execute' not in user.scopes: raise HTTPException(403, 'Operator execution scope required for deletion')
            if body.sandbox_id != sandbox_id: raise HTTPException(422, 'Exact sandbox confirmation required')
            if sandboxes is None: raise HTTPException(503, 'Sandbox inventory unavailable')
            return await sandboxes.delete(sandbox_id, user.subject_hash)

        @app.post('/invoices/challenges', status_code=202)
        async def challenge(body: ChallengeRequest, background: BackgroundTasks, user=Depends(identity)):
            if 'tasks.execute' not in user.scopes: raise HTTPException(403, 'Operator execution scope required for sandbox probes')
            if challenges is None: raise HTTPException(503, 'Challenge service unavailable')
            if demo_session is not None: demo_session.acquire()
            try:
                result = await challenges.enqueue(body.challenge_id, user.subject_hash, body.kind)
            except BaseException:
                if demo_session is not None: demo_session.release()
                raise
            if demo_session is None: background.add_task(challenges.work, body.challenge_id, user.subject_hash)
            else: background.add_task(demo_session.run_admitted, challenges.work, body.challenge_id, user.subject_hash)
            return result

        @app.get('/invoices/challenges/{challenge_id}')
        async def challenge_status(challenge_id: str = Path(pattern=r'^[a-f0-9]{32}$'), user=Depends(identity)):
            if challenges is None: raise HTTPException(503, 'Challenge service unavailable')
            return await challenges.status(challenge_id, user.subject_hash)

        @app.get('/invoices/plans/{plan_id}/evidence')
        async def evidence(plan_id: str = Path(pattern=r'^[a-f0-9]{32}$'), user=Depends(identity)):
            if observer is None: raise HTTPException(503, 'Diagnostic observer unavailable')
            rows = await repository.plans(user.subject_hash)
            row = next((item for item in rows if item['plan_json']['plan_id']==plan_id), None)
            if row is None: raise HTTPException(403, 'Owned plan required')
            snapshot = await observer.snapshot(row['plan_json']['scenario_id'])
            receipts = []
            if row.get('execution_run_id'):
                import json
                raw = await repository.client.call('control.usp_get_invoice_execution', {'run_id':row['execution_run_id'],'sponsor_hash':user.subject_hash})
                receipts = [json.loads(item['receipt_json']) for item in raw]
            return {**snapshot, 'plan_id':plan_id,'plan_hash':row['plan_hash'],'receipts':receipts,
                'authorization':{'identity':'Microsoft Entra','persona':user.persona,'required_scopes':['agent.invoke','tasks.read'],'ownership':'verified by invoice API'}}

        @app.post('/invoices/plans/{plan_id}/reconcile')
        async def reconcile(body: PlanRequest, plan_id: str = Path(pattern=r'^[a-f0-9]{32}$'), user=Depends(identity)):
            if 'tasks.execute' not in user.scopes:
                raise HTTPException(403, 'Reconciliation scope required')
            if reconciler is None:
                raise HTTPException(503, 'Reconciliation is not configured')
            return await reconciler.reconcile(plan_id, body.plan_hash, sponsor_hash=user.subject_hash)

        @app.post('/invoices/scenarios', status_code=201)
        async def setup_scenario(body: ScenarioRequest, user=Depends(identity)):
            if 'tasks.execute' not in user.scopes:
                raise HTTPException(403, 'Explicit fixture setup requires Operator execution scope')
            if simulator is None:
                raise HTTPException(503, 'Dedicated scenario identity is not configured')
            from task_agent.control.invoice_repository import InvoiceRepository
            rows = await simulator.call('control.usp_create_owned_invoice_scenario', {**body.model_dump(), 'sponsor_hash': user.subject_hash})
            return InvoiceRepository.scenario_receipt(rows, body.scenario_id)

        @app.get('/invoices/scenarios/{scenario_id}')
        async def scenario_status(scenario_id: str = Path(pattern=r'^[a-f0-9]{32}$'), user=Depends(identity)):
            return await repository.scenario(scenario_id, user.subject_hash)

        @app.post('/invoices/jobs', status_code=202)
        async def start_job(body: AgentJobRequest, background: BackgroundTasks, user=Depends(identity)):
            if jobs is None:
                raise HTTPException(503, 'Durable agent runtime is not configured')
            from task_agent.control.invoice_challenges import TARGETS
            if body.target_id in TARGETS.values(): raise HTTPException(422, 'Probe targets cannot be used as agent jobs')
            if body.kind == 'execution' and 'tasks.execute' not in user.scopes:
                raise HTTPException(403, 'Execution scope required')
            if (body.kind == 'execution') != (body.plan_hash is not None):
                raise HTTPException(422, 'Execution requires the exact plan hash')
            if demo_session is not None: demo_session.acquire()
            try:
                result = await jobs.enqueue(job_id=body.job_id, sponsor_hash=user.subject_hash, kind=body.kind,
                    target_id=body.target_id, plan_hash=body.plan_hash)
            except BaseException:
                if demo_session is not None: demo_session.release()
                raise
            if demo_session is None: background.add_task(jobs.work, body.job_id, user.subject_hash)
            else: background.add_task(demo_session.run_admitted, jobs.work, body.job_id, user.subject_hash)
            return result

        @app.get('/invoices/jobs/{job_id}')
        async def job_status(job_id: str = Path(pattern=r'^[a-f0-9]{32}$'), user=Depends(identity)):
            if jobs is None:
                raise HTTPException(503, 'Durable agent runtime is not configured')
            return await jobs.status(job_id, user.subject_hash)

        @app.post('/invoices/jobs/{job_id}/sandbox-test', status_code=202)
        async def sandbox_test(body: SandboxTestRequest, job_id: str = Path(pattern=r'^[a-f0-9]{32}$'), user=Depends(identity)):
            if 'tasks.execute' not in user.scopes:
                raise HTTPException(403, 'Operator execution scope required for sandbox tests')
            if jobs is None:
                raise HTTPException(503, 'Durable agent runtime is not configured')
            return await jobs.request_sandbox_test(job_id, user.subject_hash, body.sandbox_id)

        @app.get('/invoices/jobs/{job_id}/events')
        async def job_events(job_id: str = Path(pattern=r'^[a-f0-9]{32}$'), after: int = Query(default=0, ge=0), user=Depends(identity)):
            if jobs is None:
                raise HTTPException(503, 'Durable activity store is not configured')
            return {'source': 'azure-sql-activity', 'job_id': job_id, 'events': await jobs.events(job_id, user.subject_hash, after)}

        @app.post('/invoices/analyze')
        async def analyze(body: AnalyzeRequest, user=Depends(identity)):
            if controller is None:
                raise HTTPException(503, 'Sandbox agent runtime is not configured')
            return await controller.analyze(body.scenario_id, sponsor_hash=user.subject_hash)

        @app.post('/invoices/plans/{plan_id}/submit')
        async def submit(body: PlanRequest, plan_id: str = Path(pattern=r'^[a-f0-9]{32}$'), user=Depends(identity)):
            await repository.submit(plan_id, body.plan_hash, user.subject_hash)
            return {'plan_id': plan_id, 'state': 'submitted'}

        @app.post('/invoices/plans/{plan_id}/execute')
        async def execute(body: PlanRequest, plan_id: str = Path(pattern=r'^[a-f0-9]{32}$'), user=Depends(identity)):
            if 'tasks.execute' not in user.scopes:
                raise HTTPException(403, 'Execution scope required')
            if controller is None:
                raise HTTPException(503, 'Fresh execution sandbox runtime is not configured')
            return await controller.execute(plan_id, body.plan_hash, sponsor_hash=user.subject_hash)

        @app.post('/invoices/plans/{plan_id}/complete')
        async def complete(body: PlanRequest, plan_id: str = Path(pattern=r'^[a-f0-9]{32}$'), user=Depends(identity)):
            if 'tasks.execute' not in user.scopes:
                raise HTTPException(403, 'Execution scope required')
            rows = await repository.client.call('control.usp_complete_invoice_plan',
                {'plan_id': plan_id, 'plan_hash': body.plan_hash, 'sponsor_hash': user.subject_hash})
            if len(rows) != 1 or rows[0] != {'plan_id': plan_id}:
                raise SqlProcedureUnavailableError('completion unconfirmed')
            return {'plan_id': plan_id, 'state': 'completed'}

        @app.get('/invoices/activity/{run_id}')
        async def events(run_id: str = Path(pattern=r'^[a-f0-9]{32}$'), after: int = Query(default=0, ge=0), user=Depends(identity)):
            if activity is None:
                raise HTTPException(503, 'Activity collector unavailable')
            return {'source': 'controller-activity-store', 'run_id': run_id,
                    'events': activity.read(sponsor_hash=user.subject_hash, run_id=run_id, after=after)}
    else:
        @app.post('/invoices/plans/{plan_id}/decision')
        async def decide(body: DecisionRequest, plan_id: str = Path(pattern=r'^[a-f0-9]{32}$'), user=Depends(identity)):
            await repository.decide(plan_id, body.plan_hash, user.subject_hash, body.decision)
            return {'plan_id': plan_id, 'decision': body.decision}

    return app