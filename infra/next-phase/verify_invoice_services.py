"""Independent invoice release verification; no human approval or remediation."""

import base64
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import pty
import subprocess
import zlib

from deploy_human_services import az, save
from availability_policy import managed


STATE = Path.home() / '.local/state/learningnemo'
GROUP = 'rg-learningnemo-invoice-dev'


def retryable_network_failure(output):
    text = output.lower()
    return ('network is unreachable' in text or 'failed to establish a new connection' in text) and not any(
        marker in text for marker in ('429', 'retry-after', 'too many requests', 'pass invoice_', 'successfully connected'))


def read_az(*args):
    for attempt in range(3):
        try:
            result = subprocess.run(['az', *args, '--output', 'json', '--only-show-errors'], capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired as error:
            output = error.stderr or b''
            text = output.decode(errors='replace') if isinstance(output, bytes) else output
            if attempt == 2 or any(marker in text.lower() for marker in ('429', 'retry-after', 'too many requests')):
                raise
            continue
        if result.returncode == 0:
            return json.loads(result.stdout.lstrip('\ufeff'))
        if attempt == 2 or not retryable_network_failure(result.stderr):
            raise RuntimeError('read-only Azure verification failed: ' + result.stderr[-1200:])


def remote(kind, code, marker, *, revision=None, replica=None, read_only=False):
    compile(code, '<invoice-verification>', 'exec')
    encoded = base64.b64encode(zlib.compress(code.encode())).decode()
    if len(encoded) > 1700:
        raise ValueError('bounded remote verification command exceeded')
    output = bytearray()
    def capture(descriptor):
        chunk = os.read(descriptor, 4096)
        output.extend(chunk)
        return chunk
    command = ['az', 'containerapp', 'exec', '-g', GROUP, '-n', f'ca-nemo-invoice-{kind}-dev']
    if revision and replica:
        command += ['--revision', revision, '--replica', replica, '--container', kind]
    command += ['--command', f"python -c exec(__import__('zlib').decompress(__import__('base64').b64decode('{encoded}')))"]
    for attempt in range(3 if read_only else 1):
        output.clear()
        status = pty.spawn(command, master_read=capture)
        text = output.decode(errors='replace')
        if status == 0 and marker.encode() in output and b'Traceback' not in output:
            return text
        if not read_only or attempt == 2 or not retryable_network_failure(text):
            raise RuntimeError('invoice runtime verification failed; no success assumed')


def sql_code(kind):
    return f'''
import os,httpx
from task_agent.control.mssql_client import MssqlProcedureClient,managed_identity_connect
from task_agent.control.invoice_catalog import GRANTS
kind={kind!r}
expected={{'operator':set(GRANTS['controller']+GRANTS['incident']+GRANTS['jobs']),'review':set(GRANTS['review']),'planning':set(GRANTS['diagnostic']+GRANTS['inference']),'execution':set(GRANTS['broker']+GRANTS['inference']),'verifier':set(GRANTS['verifier']),'simulator':set(GRANTS['simulator'])}}[kind]
health=httpx.get('http://127.0.0.1:8080/healthz')
assert health.status_code==200
if kind=='operator':assert health.json().get('demo_state')=='idle', 'release verification requires an idle demo'
client=MssqlProcedureClient(server=os.environ['LEARNINGNEMO_SQL_SERVER'],database='learningnemo',client_id=os.environ['INVOICE_SIMULATOR_CLIENT_ID' if kind=='simulator' else 'AZURE_CLIENT_ID'],application_name='Invoice')
with managed_identity_connect(client._connection_string,client._client_id,timeout_seconds=120,server=client._server) as connection:
    with connection.cursor() as cursor:
        cursor.execute('SELECT CURRENT_USER');assert cursor.fetchone()[0]=='id-learningnemo-invoice-'+kind+'-dev'
        cursor.execute("SELECT OBJECT_SCHEMA_NAME(major_id)+'.'+OBJECT_NAME(major_id) FROM sys.database_permissions WHERE grantee_principal_id=DATABASE_PRINCIPAL_ID() AND permission_name='EXECUTE' AND state_desc='GRANT'")
        assert {{row[0] for row in cursor.fetchall()}}==expected
        for target,permission in [('lab.InvoiceRows','SELECT'),('lab.usp_apply_invoice_operation','EXECUTE'),('lab.usp_import_invoice_batch','EXECUTE')]:
            cursor.execute("SELECT HAS_PERMS_BY_NAME(?,'OBJECT',?)",target,permission);assert cursor.fetchone()[0] in (None,0)
print('PASS invoice_sql_'+kind)
'''


def workload_code():
    return '''
import asyncio,os,httpx
from azure.identity import ManagedIdentityCredential
from task_agent.console.cloud_auth import CloudJwtProvider
from task_agent.console.identity import EntraTestSettings
credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
token=credential.get_token(os.environ['INVOICE_VERIFIER_AUDIENCE']+'/.default')
verified=asyncio.run(CloudJwtProvider(EntraTestSettings.from_sources()).verify(token.token))
assert verified.active and verified.object_id==os.environ['INVOICE_OPERATOR_OBJECT_ID']
assert verified.client_id==os.environ['AZURE_CLIENT_ID']
origin='https://ca-nemo-invoice-verifier-dev.internal.jollybeach-503c7ed1.eastus.azurecontainerapps.io'
response=httpx.post(origin+'/verify',json={'run_id':'0'*32},timeout=30)
assert response.status_code==401
print('PASS invoice_workload_identity')
'''


def operator_code():
    return sql_code('operator') + sql_code('simulator')


def connectivity_code():
    return workload_code() + '''
domain='jollybeach-503c7ed1.eastus.azurecontainerapps.io'
for kind,path in [('planning','/v2/invoice/tools/invoice_summary'),('execution','/v2/invoice/tools/execute_step')]:
    origin='https://ca-nemo-invoice-'+kind+'-dev.'+domain
    response=httpx.post(origin+path,json={},timeout=30);assert response.status_code in (401,422)
print('PASS invoice_private_connectivity')
'''


def main():
    live=json.loads((STATE/'invoice-services.live.json').read_text())
    if read_az('account','show')['id']!=os.environ['AZURE_SUBSCRIPTION_ID'] or live['subscription']!=os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    if (STATE/'human-migration.cleanup.json').exists():raise ValueError('administrator cleanup still pending')
    results={}
    for kind in ('operator','review','planning','execution','verifier'):
        app=read_az('containerapp','show','-g',GROUP,'-n',f'ca-nemo-invoice-{kind}-dev')
        properties=app['properties']
        if properties['latestRevisionName']!=properties['latestReadyRevisionName'] or properties['runningStatus']!='Running':
            raise ValueError('latest invoice revision not ready: '+kind)
        if properties['template']['containers'][0]['image']!=live['values']['image']:raise ValueError('invoice image differs')
        if properties['configuration'].get('activeRevisionsMode')!='Single':raise ValueError('single active invoice revision required')
        if properties['template']['scale'].get('maxReplicas')!=1:raise ValueError('single invoice replica required')
        revisions=read_az('containerapp','revision','list','-g',GROUP,'-n',f'ca-nemo-invoice-{kind}-dev')
        if [row['name'] for row in revisions if row['properties']['active']]!=[properties['latestReadyRevisionName']]:raise ValueError('older invoice revision remains active')
        env={entry['name']:entry.get('value') for entry in properties['template']['containers'][0]['env']}
        if live['values'].get('availabilityMode')=='operator-managed':
            if not managed(app) or env.get('INVOICE_AVAILABILITY_MODE')!='operator-managed' or env.get('INVOICE_EXPIRES_AT'):raise ValueError('managed invoice policy differs')
        if properties['configuration']['ingress']['external']!=(kind in ('planning','execution')):raise ValueError('invoice ingress differs')
        if properties['configuration']['ingress']['allowInsecure']:raise ValueError('HTTPS required')
        identity=read_az('identity','show','-g',GROUP,'-n',f'id-learningnemo-invoice-{kind}-dev')
        if identity['clientId']!=live['principals'][kind]['clientId'] or identity['principalId']!=live['principals'][kind]['objectId']:
            raise ValueError('invoice identity differs')
        attached={resource_id.rsplit('/',1)[-1] for resource_id in app['identity']['userAssignedIdentities']}
        expected={f'id-learningnemo-invoice-{kind}-dev'}|({'id-learningnemo-invoice-simulator-dev'} if kind=='operator' else set())
        if app['identity']['type']!='UserAssigned' or attached!=expected:raise ValueError('invoice attached identity inventory differs')
        replicas=read_az('containerapp','replica','list','-g',GROUP,'-n',f'ca-nemo-invoice-{kind}-dev','--revision',properties['latestReadyRevisionName'])
        if len(replicas)!=1:raise ValueError('exactly one invoice replica required')
        target={'revision':properties['latestReadyRevisionName'],'replica':replicas[0]['name'],'read_only':True}
        if kind=='operator':
            remote(kind,operator_code(),'PASS invoice_sql_simulator',**target)
            remote(kind,connectivity_code(),'PASS invoice_private_connectivity',**target)
        else:
            remote(kind,sql_code(kind),'PASS invoice_sql_'+kind,**target)
        results[kind]=properties['latestReadyRevisionName']
    expiry=datetime.fromisoformat(live['values']['expiresAt']) if live['values']['expiresAt'] else None
    if expiry is not None and expiry<=datetime.now(UTC):raise ValueError('invoice lease expired')
    if expiry is None and live['values'].get('availabilityMode')!='operator-managed':raise ValueError('explicit availability policy required')
    save('invoice-services.verified.json',{'image':live['values']['image'],'expiresAt':expiry.isoformat() if expiry else None,'availabilityMode':live['values'].get('availabilityMode','leased'),
        'checkedAt':datetime.now(UTC).isoformat(),'revisions':results,'demoState':'idle','singleActiveRevision':True,'humanRehearsal':False,'agentRehearsal':False})
    print('PASS dedicated invoice identities, SQL grants, runtime readiness and network reachability')


if __name__=='__main__':main()