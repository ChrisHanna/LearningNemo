"""Run one labelled OpenShell investigation; never invoke a model or mutate invoice data."""

import asyncio
from datetime import UTC, datetime
from functools import partial
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from deploy_human_services import az, save
from verify_invoice_services import remote


STATE = Path.home() / '.local/state/learningnemo'
SCENARIO = 'query-draft'


def investigation_code(run_id, sponsor_hash):
    return f'''
import asyncio,json,os
from functools import partial
from types import SimpleNamespace
from azure.identity import ManagedIdentityCredential
from azure.mgmt.compute import ComputeManagementClient
from task_agent.console.invoice_probes import run_probe
from task_agent.console.invoice_remote_runtime import AzureInvoiceRuntime
from task_agent.control.invoice_challenges import InvoiceChallenges
from task_agent.control.mssql_client import MssqlProcedureClient
run_id={run_id!r}
sponsor={sponsor_hash!r}
async def main():
    sql=MssqlProcedureClient(server=os.environ['LEARNINGNEMO_SQL_SERVER'],database='learningnemo',client_id=os.environ['AZURE_CLIENT_ID'],application_name='InvestigationRehearsal')
    credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    compute=ComputeManagementClient(credential,os.environ['AZURE_SUBSCRIPTION_ID'],polling_interval=3)
    runtime=AzureInvoiceRuntime(compute_client=compute,image=os.environ['INVOICE_AGENT_IMAGE'],policies='/app/invoice-policies',availability_mode=os.environ.get('INVOICE_AVAILABILITY_MODE','leased'))
    controller=SimpleNamespace(lock=asyncio.Lock(),runtime=runtime,admission=sql,maintenance=None,maintenance_active=False)
    challenges=InvoiceChallenges(controller,partial(run_probe,sql_host=os.environ['LEARNINGNEMO_SQL_SERVER']))
    await challenges.enqueue(run_id,sponsor,{SCENARIO!r})
    await challenges.work(run_id,sponsor)
    record=await challenges.status(run_id,sponsor)
    result=record['result']
    assert record['state']=='finished' and record['kind']=={SCENARIO!r}
    assert result['evidence_mode']=='live' and result['executed_in_sandbox'] is True
    assert result['sandbox_runtime']=='OpenShell MicroVM' and result['sandbox_executor']=='/opt/venv/bin/python'
    assert result['sandbox_stopped'] is True and result['sandbox_retained'] is True
    assert result['query_class']=='SELECT' and result['query_executed'] is False
    events=[event['event_type'] for event in record['events']]
    assert events==['preparing-sandbox','sandbox-bound','investigation-started','investigation-recorded','sandbox-stopped']
    print('PASS invoice_investigation_rehearsal '+json.dumps({{'run_id':run_id,'scenario':record['kind'],'sandbox_id':result['sandbox_id'],'policy_hash':result['policy_hash'],'uid':result['uid'],'executor':result['sandbox_executor'],'events':events,'sandbox_stopped':True,'sandbox_retained':True,'humanRehearsal':False,'modelInvolved':False,'queryExecuted':False}}),flush=True)
asyncio.run(main())
'''


def main():
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'invoice-investigation-rehearsal':
        raise ValueError('explicit automated investigation rehearsal acknowledgement required')
    live = json.loads((STATE / 'invoice-services.live.json').read_text())
    verified = json.loads((STATE / 'invoice-services.verified.json').read_text())
    if live['subscription'] != os.environ['AZURE_SUBSCRIPTION_ID'] or az('account','show')['id'] != live['subscription']:
        raise ValueError('subscription mismatch')
    if verified['image'] != live['values']['image'] or verified.get('availabilityMode') != 'operator-managed' or verified.get('expiresAt') is not None:
        raise ValueError('fresh verified operator-managed invoice release required')
    run_id = uuid4().hex
    sponsor_hash = hashlib.sha256(('automated-invoice-investigation-rehearsal:' + run_id).encode()).hexdigest()
    save('invoice-investigation-rehearsal.attempt.json', {'run_id': run_id, 'sponsor_hash': sponsor_hash,
        'scenario': SCENARIO, 'humanRehearsal': False, 'modelInvolved': False, 'image': verified['image']})
    output = remote('operator', investigation_code(run_id, sponsor_hash), 'PASS invoice_investigation_rehearsal ')
    records = [json.JSONDecoder().raw_decode(line.split('PASS invoice_investigation_rehearsal ', 1)[1])[0]
        for line in output.splitlines() if 'PASS invoice_investigation_rehearsal ' in line]
    if len(records) != 1 or records[0]['run_id'] != run_id or records[0]['scenario'] != SCENARIO:
        raise ValueError('investigation rehearsal receipt differs; do not replay')
    save('invoice-investigation-rehearsal.verified.json', {**records[0], 'checkedAt': datetime.now(UTC).isoformat(),
        'image': verified['image']})


if __name__ == '__main__':
    main()
