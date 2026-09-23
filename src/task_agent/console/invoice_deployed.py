"""Deployment composition for separate invoice workflow identities and services."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
import os
from pathlib import Path

import httpx
import uvicorn
from azure.identity import ManagedIdentityCredential
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from task_agent.console.identity import EntraTestSettings
from task_agent.console.invoice_gateway import create_invoice_gateway
from task_agent.console.invoice_service import create_invoice_service
from task_agent.console.invoice_remote_runtime import AzureInvoiceRuntime
from task_agent.console.invoice_observations import add_observation_route, RemoteInvoiceObserver
from task_agent.console.invoice_probes import run_probe, run_prepared_probe
from task_agent.console.invoice_availability import configured_expiry
from task_agent.console.review_service import EntraReviewVerifier
from task_agent.control.invoice_controller import InvoiceController
from task_agent.control.invoice_jobs import InvoiceJobs
from task_agent.control.invoice_challenges import InvoiceChallenges
from task_agent.control.invoice_model import InvoiceModelGateway, build_guardrails
from task_agent.control.invoice_repository import InvoiceRepository
from task_agent.control.mssql_client import MssqlProcedureClient


DOMAIN = 'jollybeach-503c7ed1.eastus.azurecontainerapps.io'


class VerificationRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    run_id: str=Field(pattern=r'^[a-f0-9]{32}$')


class RemoteInvoiceVerifier:
    def __init__(self, client, credential, audience):
        self.client,self.credential,self.audience=client,credential,audience
    async def verify(self,run_id):
        import asyncio
        token=await asyncio.to_thread(self.credential.get_token,self.audience+'/.default')
        response=await self.client.post('https://ca-nemo-invoice-verifier-dev.internal.'+DOMAIN+'/verify',
            headers={'Authorization':'Bearer '+token.token},json={'run_id':run_id},timeout=60)
        if response.status_code!=200:
            raise RuntimeError('independent invoice verification unconfirmed')
        return response.json()['checks']


def main():
    kind=os.environ['INVOICE_SERVICE_KIND']
    availability_mode=os.environ.get('INVOICE_AVAILABILITY_MODE','leased')
    expiry=configured_expiry(availability_mode,os.environ.get('INVOICE_EXPIRES_AT'))
    credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    sql=MssqlProcedureClient(server=os.environ['LEARNINGNEMO_SQL_SERVER'],database=os.environ['LEARNINGNEMO_SQL_DATABASE'],
        client_id=os.environ['AZURE_CLIENT_ID'],application_name='Invoice'+kind.title())
    read_sql = MssqlProcedureClient(server=os.environ['LEARNINGNEMO_SQL_SERVER'],database=os.environ['LEARNINGNEMO_SQL_DATABASE'],
        client_id=os.environ['AZURE_CLIENT_ID'],application_name='InvoiceRead'+kind.title(),
        connection_timeout_seconds=30,connection_attempts=3) if kind in ('operator','review') else sql
    repository=InvoiceRepository(sql,read_client=read_sql)
    http=httpx.AsyncClient(follow_redirects=False,timeout=60)
    if kind in ('planning','execution'):
        def setup_model():
            token=credential.get_token('https://vault.azure.net/.default')
            with httpx.Client(timeout=30,follow_redirects=False) as bootstrap:
                response=bootstrap.get('https://kvnemo8370187d.vault.azure.net/secrets/llm-gateway-client-key?api-version=7.4',headers={'Authorization':'Bearer '+token.token})
            if response.status_code!=200: raise RuntimeError('model gateway credential unavailable')
            return response.json()['value']
        api_key=setup_model()
        async def audit(**event):
            print(json.dumps({'source':'invoice-'+kind+'-gateway',**event}),flush=True)
        model=InvoiceModelGateway(origin=os.environ['OPENAI_BASE_URL'],client=http,api_key=api_key,
            rails=build_guardrails(base_url=os.environ['OPENAI_GUARDRAIL_BASE_URL'],api_key=api_key))
        app=create_invoice_gateway(kind=kind,repository=repository,inference_admission=repository,model_gateway=model,expires_at=expiry,audit=audit,availability_mode=availability_mode)
        if kind=='planning':
            from task_agent.console.cloud_auth import CloudJwtProvider
            add_observation_route(app, repository, CloudJwtProvider(EntraTestSettings.from_sources()), os.environ['INVOICE_OPERATOR_OBJECT_ID'], expiry,availability_mode=availability_mode)
    elif kind=='operator':
        from azure.mgmt.compute import ComputeManagementClient
        compute=ComputeManagementClient(credential,os.environ['AZURE_SUBSCRIPTION_ID'],polling_interval=3)
        runtime=AzureInvoiceRuntime(compute_client=compute,image=os.environ['INVOICE_AGENT_IMAGE'],policies=Path('/app/invoice-policies'),availability_mode=availability_mode)
        verifier=RemoteInvoiceVerifier(http,credential,os.environ['INVOICE_VERIFIER_AUDIENCE'])
        jobs=InvoiceJobs(sql,None)
        observer=RemoteInvoiceObserver(http,credential,os.environ['INVOICE_VERIFIER_AUDIENCE'])
        controller=InvoiceController(admission_client=sql,incident_repository=repository,verifier_repository=verifier,runtime=runtime,audit=jobs.audit,observer=observer,sandbox_probe=run_prepared_probe)
        from task_agent.console.invoice_retention_worker import InvoiceSandboxManager
        sandboxes = InvoiceSandboxManager(runtime, read_sql, controller) if availability_mode == 'operator-managed' else None
        if sandboxes is not None:
            controller.maintenance = sandboxes.before_run
            jobs.on_finished = sandboxes.notify
        jobs.controller=controller
        challenges=InvoiceChallenges(controller,run_probe,on_finished=sandboxes.notify if sandboxes else None)
        simulator=MssqlProcedureClient(server=os.environ['LEARNINGNEMO_SQL_SERVER'],database=os.environ['LEARNINGNEMO_SQL_DATABASE'],
            client_id=os.environ['INVOICE_SIMULATOR_CLIENT_ID'],application_name='InvoiceFixture',
            connection_timeout_seconds=30,connection_attempts=3)
        app=create_invoice_service(mode='operator',repository=repository,identity_verifier=EntraReviewVerifier(EntraTestSettings.from_sources()),
            expires_at=expiry,jobs=jobs,simulator=simulator,reconciler=controller,challenges=challenges,observer=observer,sandboxes=sandboxes,demo_session=sandboxes.demo if sandboxes else None,availability_mode=availability_mode)
        if availability_mode == 'operator-managed':
            from task_agent.console.invoice_retention_worker import retention_lifespan
            app.router.lifespan_context = retention_lifespan(runtime,read_sql,controller,manager=sandboxes)
    elif kind=='review':
        from task_agent.console.invoice_demo_session import acquire_remote_demo
        async def demo_check(token):
            return await acquire_remote_demo(http, token)
        app=create_invoice_service(mode='review',repository=repository,identity_verifier=EntraReviewVerifier(EntraTestSettings.from_sources()),expires_at=expiry,demo_check=demo_check if availability_mode=='operator-managed' else None,availability_mode=availability_mode)
    elif kind=='verifier':
        from task_agent.console.cloud_auth import CloudJwtProvider
        settings=EntraTestSettings.from_sources()
        provider=CloudJwtProvider(settings)
        bearer=HTTPBearer(auto_error=False)
        app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
        @app.get('/healthz')
        async def health(): return {'status':'ok'}
        @app.post('/verify')
        async def verify(body:VerificationRequest, value:HTTPAuthorizationCredentials|None=Depends(bearer)):
            if expiry is not None and datetime.now(UTC)>=expiry: raise HTTPException(503,'Verification lease expired')
            if value is None: raise HTTPException(401,'Workload identity required')
            verified=await provider.verify(value.credentials)
            if not verified.active or verified.object_id!=os.environ['INVOICE_OPERATOR_OBJECT_ID']:
                raise HTTPException(403,'Only the invoice coordinator may request verification')
            return {'checks':await repository.verify(body.run_id)}
    else:
        raise ValueError('unknown invoice service role')
    uvicorn.run(app,host='0.0.0.0',port=8080,workers=1,proxy_headers=False)


if __name__=='__main__': main()