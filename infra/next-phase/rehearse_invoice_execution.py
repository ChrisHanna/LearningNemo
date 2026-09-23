"""Execute only the labelled synthetic Planning rehearsal record; never a human plan."""

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from deploy_human_services import az, save
from verify_invoice_services import remote


STATE = Path.home()/'.local/state/learningnemo'


def execution_code(plan_id, plan_hash, sponsor, run_id):
    return f'''
import asyncio,json,os,httpx
from azure.identity import ManagedIdentityCredential
from azure.mgmt.compute import ComputeManagementClient
from task_agent.console.invoice_remote_runtime import AzureInvoiceRuntime
from task_agent.console.invoice_deployed import RemoteInvoiceVerifier
from task_agent.console.invoice_observations import RemoteInvoiceObserver
from task_agent.control.invoice_controller import InvoiceController
from task_agent.control.invoice_jobs import InvoiceJobs
from task_agent.control.invoice_repository import InvoiceRepository
from task_agent.control.mssql_client import MssqlProcedureClient
async def main():
    sql=MssqlProcedureClient(server=os.environ['LEARNINGNEMO_SQL_SERVER'],database='learningnemo',client_id=os.environ['AZURE_CLIENT_ID'],application_name='InvoiceRehearsal')
    credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    runtime=AzureInvoiceRuntime(compute_client=ComputeManagementClient(credential,os.environ['AZURE_SUBSCRIPTION_ID'],polling_interval=3),image=os.environ['INVOICE_AGENT_IMAGE'],policies='/app/invoice-policies')
    jobs=InvoiceJobs(sql,None)
    async def audit(**values):
        await jobs.audit(**values)
        print(values['event']['event_type'],flush=True)
    async with httpx.AsyncClient() as http:
        controller=InvoiceController(admission_client=sql,incident_repository=InvoiceRepository(sql),verifier_repository=RemoteInvoiceVerifier(http,credential,os.environ['INVOICE_VERIFIER_AUDIENCE']),runtime=runtime,audit=audit,observer=RemoteInvoiceObserver(http,credential,os.environ['INVOICE_VERIFIER_AUDIENCE']))
        await jobs.enqueue(job_id={run_id!r},sponsor_hash={sponsor!r},kind='execution',target_id={plan_id!r},plan_hash={plan_hash!r})
        assert len(await sql.call('control.usp_claim_invoice_job',{{'job_id':{run_id!r},'sponsor_hash':{sponsor!r}}}))==1
        try:
            result=await controller.execute({plan_id!r},{plan_hash!r},sponsor_hash={sponsor!r},run_id={run_id!r})
        except Exception as error:
            await sql.call('control.usp_finish_invoice_job',{{'job_id':{run_id!r},'sponsor_hash':{sponsor!r},'state':'uncertain','result_json':json.dumps({{'error_type':type(error).__name__}})}})
            raise
        await sql.call('control.usp_finish_invoice_job',{{'job_id':{run_id!r},'sponsor_hash':{sponsor!r},'state':'finished','result_json':json.dumps(result)}})
        assert len(result['checks'])==5 and all(value is True for value in result['checks'].values())
        assert result['database_observation']['summary']['duplicate_invoices']==0
        result['database_observation'].pop('rows')
        print('PASS invoice_execution_rehearsal '+json.dumps({{**result,'humanRehearsal':False}}),flush=True)
asyncio.run(main())
'''


def main():
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'invoice-execution-rehearsal':
        raise ValueError('explicit synthetic execution rehearsal acknowledgement required')
    live=json.loads((STATE/'invoice-services.live.json').read_text())
    if live['subscription']!=os.environ['AZURE_SUBSCRIPTION_ID'] or az('account','show')['id']!=live['subscription']:
        raise ValueError('subscription mismatch')
    planning=json.loads((STATE/'invoice-planning-rehearsal.verified.json').read_text())
    attempt=json.loads((STATE/'invoice-planning-rehearsal.attempt.json').read_text())
    sponsor=hashlib.sha256(('automated-invoice-planning-rehearsal:'+planning['run_id']).encode()).hexdigest()
    if planning['humanRehearsal'] is not False or planning['submitted'] is not False or attempt['run_id']!=planning['run_id'] or attempt['sponsor_hash']!=sponsor:
        raise ValueError('only the explicitly synthetic rehearsal plan is eligible')
    if planning['image']!=live['values']['image'] or (datetime.now(UTC)-datetime.fromisoformat(planning['checkedAt'])).total_seconds()>900:
        raise ValueError('fresh matching rehearsal plan required')
    run_id=uuid4().hex
    reviewer=hashlib.sha256(('automated-invoice-review-fixture:'+run_id).encode()).hexdigest()
    plan_id,plan_hash=planning['plan_id'],planning['plan_hash']
    save('invoice-execution-rehearsal.attempt.json',{'run_id':run_id,'plan_id':plan_id,'plan_hash':plan_hash,'sponsor_hash':sponsor,'synthetic_reviewer_hash':reviewer,'humanRehearsal':False})
    prefix="import asyncio,os\nfrom task_agent.control.mssql_client import MssqlProcedureClient\nclient=MssqlProcedureClient(server=os.environ['LEARNINGNEMO_SQL_SERVER'],database='learningnemo',client_id=os.environ['AZURE_CLIENT_ID'],application_name='InvoiceRehearsal')\n"
    remote('operator',prefix+f"asyncio.run(client.call('control.usp_submit_invoice_plan',{{'plan_id':{plan_id!r},'plan_hash':{plan_hash!r},'sponsor_hash':{sponsor!r}}}))\nprint('PASS synthetic_submission')",'PASS synthetic_submission')
    remote('review',prefix+f"asyncio.run(client.call('control.usp_decide_invoice_plan',{{'plan_id':{plan_id!r},'plan_hash':{plan_hash!r},'reviewer_hash':{reviewer!r},'decision':'approve'}}))\nprint('PASS synthetic_review_not_human')",'PASS synthetic_review_not_human')
    output=remote('operator',execution_code(plan_id,plan_hash,sponsor,run_id),'PASS invoice_execution_rehearsal ')
    rows=[json.JSONDecoder().raw_decode(line.split('PASS invoice_execution_rehearsal ',1)[1])[0] for line in output.splitlines() if 'PASS invoice_execution_rehearsal ' in line]
    if len(rows)!=1 or rows[0]['run_id']!=run_id:raise ValueError('execution receipt differs')
    save('invoice-execution-rehearsal.verified.json',{**rows[0],'checkedAt':datetime.now(UTC).isoformat(),'image':live['values']['image']})


if __name__=='__main__':main()