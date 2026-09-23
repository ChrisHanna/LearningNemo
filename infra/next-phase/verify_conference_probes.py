"""Rehearse fixed isolated probes without acting on a human plan."""

from datetime import UTC, datetime
import hashlib
import json
import os
from uuid import uuid4

from deploy_human_services import az, save
from verify_invoice_services import remote


def code(kind, identifier, owner):
    return f'''
import asyncio,json,os
from types import SimpleNamespace
from azure.identity import ManagedIdentityCredential
from azure.mgmt.compute import ComputeManagementClient
from task_agent.control.mssql_client import MssqlProcedureClient
from task_agent.control.invoice_challenges import InvoiceChallenges
from task_agent.console.invoice_probes import run_probe
from task_agent.console.invoice_remote_runtime import AzureInvoiceRuntime
async def main():
    credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    sql=MssqlProcedureClient(server=os.environ['LEARNINGNEMO_SQL_SERVER'],database='learningnemo',client_id=os.environ['AZURE_CLIENT_ID'],application_name='ConferenceProbe')
    runtime=AzureInvoiceRuntime(compute_client=ComputeManagementClient(credential,os.environ['AZURE_SUBSCRIPTION_ID'],polling_interval=3),image=os.environ['INVOICE_AGENT_IMAGE'],policies='/app/invoice-policies')
    jobs=InvoiceChallenges(SimpleNamespace(admission=sql,runtime=runtime,lock=asyncio.Lock()),run_probe)
    await jobs.enqueue({identifier!r},{owner!r},{kind!r})
    await jobs.work({identifier!r},{owner!r})
    result=await jobs.status({identifier!r},{owner!r})
    print('CONFERENCE_PROBE_RESULT '+json.dumps(result,default=str),flush=True)
asyncio.run(main())
'''


def main():
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'conference-probe-rehearsal':
        raise ValueError('explicit probe rehearsal acknowledgement required')
    if az('account','show')['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    results = []
    for kind in ('planning','execution'):
        identifier=uuid4().hex
        owner=hashlib.sha256(('automated-conference-probe:'+identifier).encode()).hexdigest()
        save('conference-probe-attempt-'+kind+'.json', {'id':identifier,'owner':owner,'kind':kind,'humanRehearsal':False})
        output=remote('operator',code(kind,identifier,owner),'CONFERENCE_PROBE_RESULT ')
        rows=[json.JSONDecoder().raw_decode(line.split('CONFERENCE_PROBE_RESULT ',1)[1])[0] for line in output.splitlines() if 'CONFERENCE_PROBE_RESULT ' in line]
        if len(rows)!=1: raise ValueError('probe receipt unreadable')
        receipt=rows[0]
        save('conference-probe-result-'+kind+'.json',receipt)
        if receipt['state']!='finished' or receipt['result']['outcome']!='denied' or receipt['result']['sandbox_stopped'] is not True:
            raise ValueError('probe denial or cleanup unconfirmed; result retained')
        results.append(receipt)
    save('conference-probes.verified.json',{'checkedAt':datetime.now(UTC).isoformat(),'results':results,'humanRehearsal':False})
    print('PASS both isolated role probes have actual OpenShell denials and confirmed stopped sandboxes')


if __name__=='__main__':main()