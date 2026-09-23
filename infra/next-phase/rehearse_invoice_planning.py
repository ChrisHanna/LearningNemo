"""Run real Planning against a labelled isolated fixture; never submit or approve."""

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from deploy_human_services import az, save
from verify_invoice_services import remote


STATE = Path.home()/'.local/state/learningnemo'


def planning_code(run_id, sponsor_hash):
    return f'''
import asyncio,json,os,httpx
from azure.identity import ManagedIdentityCredential
from azure.mgmt.compute import ComputeManagementClient
from task_agent.console.invoice_remote_runtime import AzureInvoiceRuntime
from task_agent.console.invoice_observations import RemoteInvoiceObserver
from task_agent.control.invoice_controller import InvoiceController
from task_agent.control.invoice_jobs import InvoiceJobs
from task_agent.control.invoice_repository import InvoiceRepository
from task_agent.control.mssql_client import MssqlProcedureClient
run_id={run_id!r}
sponsor={sponsor_hash!r}
def client(identity):
    return MssqlProcedureClient(server=os.environ['LEARNINGNEMO_SQL_SERVER'],database='learningnemo',client_id=identity,application_name='Rehearsal')
async def main():
    sql=client(os.environ['AZURE_CLIENT_ID'])
    simulator=client(os.environ['INVOICE_SIMULATOR_CLIENT_ID'])
    credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    compute=ComputeManagementClient(credential,os.environ['AZURE_SUBSCRIPTION_ID'],polling_interval=3)
    runtime=AzureInvoiceRuntime(compute_client=compute,image=os.environ['INVOICE_AGENT_IMAGE'],policies='/app/invoice-policies',availability_mode=os.environ.get('INVOICE_AVAILABILITY_MODE','leased'))
    jobs=InvoiceJobs(sql,None)
    async def audit(**values):
        await jobs.audit(**values)
        print(values['event']['event_type'],flush=True)
    http=httpx.AsyncClient()
    controller=InvoiceController(admission_client=sql,incident_repository=InvoiceRepository(sql),verifier_repository=None,runtime=runtime,audit=audit,observer=RemoteInvoiceObserver(http,credential,os.environ['INVOICE_VERIFIER_AUDIENCE']))
    await simulator.call('control.usp_create_owned_invoice_scenario',{{'scenario_id':run_id,'variant':'lost-acknowledgement','sponsor_hash':sponsor}})
    await jobs.enqueue(job_id=run_id,sponsor_hash=sponsor,kind='planning',target_id=run_id)
    claimed=await sql.call('control.usp_claim_invoice_job',{{'job_id':run_id,'sponsor_hash':sponsor}})
    assert len(claimed)==1
    try:
        result=await controller.analyze(run_id,sponsor_hash=sponsor,run_id=run_id)
    except Exception as error:
        await sql.call('control.usp_finish_invoice_job',{{'job_id':run_id,'sponsor_hash':sponsor,'state':'uncertain','result_json':json.dumps({{'error_type':type(error).__name__}})}})
        raise
    finally:
        await http.aclose()
    await sql.call('control.usp_finish_invoice_job',{{'job_id':run_id,'sponsor_hash':sponsor,'state':'finished','result_json':json.dumps(result)}})
    assert result['outcome']=='proposal'
    plan=result['plan']
    assert len(plan['steps'])==3
    assert result['database_observation']['summary']['duplicate_invoices']==12
    print('PASS invoice_planning_rehearsal '+json.dumps({{'run_id':run_id,'scenario_id':run_id,'sandbox_id':plan['planning_sandbox_id'],'plan_id':plan['plan_id'],'plan_hash':result['plan_hash'],'steps':len(plan['steps']),'humanRehearsal':False,'submitted':False}}),flush=True)
asyncio.run(main())
'''


def main():
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'invoice-planning-rehearsal':
        raise ValueError('explicit automated planning rehearsal acknowledgement required')
    live=json.loads((STATE/'invoice-services.live.json').read_text())
    verified=json.loads((STATE/'invoice-services.verified.json').read_text())
    if live['subscription'] != os.environ['AZURE_SUBSCRIPTION_ID'] or az('account','show')['id'] != live['subscription']:
        raise ValueError('subscription mismatch')
    if verified['image'] != live['values']['image'] or datetime.fromisoformat(verified['expiresAt']) <= datetime.now(UTC)+timedelta(minutes=20):
        raise ValueError('fresh verified invoice release required')
    run_id=uuid4().hex
    sponsor_hash=hashlib.sha256(('automated-invoice-planning-rehearsal:'+run_id).encode()).hexdigest()
    save('invoice-planning-rehearsal.attempt.json',{'run_id':run_id,'sponsor_hash':sponsor_hash,'humanRehearsal':False,'image':verified['image']})
    output=remote('operator',planning_code(run_id,sponsor_hash),'PASS invoice_planning_rehearsal ')
    records=[json.JSONDecoder().raw_decode(line.split('PASS invoice_planning_rehearsal ',1)[1])[0] for line in output.splitlines() if 'PASS invoice_planning_rehearsal ' in line]
    if len(records)!=1 or records[0]['run_id']!=run_id:
        raise ValueError('planning rehearsal receipt differs')
    save('invoice-planning-rehearsal.verified.json',{**records[0],'checkedAt':datetime.now(UTC).isoformat(),'image':verified['image']})


if __name__=='__main__':main()