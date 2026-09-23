"""Preview and explicitly enable successful stopped-sandbox expiry; preserve Azure resources."""

import argparse
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path

from deploy_human_services import az, save
from verify_invoice_services import remote
from task_agent.console.invoice_remote_runtime import HOST_PREFIX
from task_agent.console.invoice_retention_host import RETENTION_POLICY


STATE = Path.home() / '.local/state/learningnemo'
SUBSCRIPTION = '40dbf703-f68a-4ca1-b315-c37f3308c38b'


def sweep_code(apply):
    return f'''
import asyncio,json,os
from azure.identity import ManagedIdentityCredential
from azure.mgmt.compute import ComputeManagementClient
from task_agent.console.invoice_remote_runtime import AzureInvoiceRuntime
from task_agent.console.invoice_retention_worker import sweep
from task_agent.control.mssql_client import MssqlProcedureClient
assert os.environ['INVOICE_SERVICE_KIND']=='operator'
assert os.environ['INVOICE_AVAILABILITY_MODE']=='operator-managed'
credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
compute=ComputeManagementClient(credential,os.environ['AZURE_SUBSCRIPTION_ID'],polling_interval=3)
runtime=AzureInvoiceRuntime(compute_client=compute,image=os.environ['INVOICE_AGENT_IMAGE'],policies='/app/invoice-policies',availability_mode='operator-managed')
client=MssqlProcedureClient(server=os.environ['LEARNINGNEMO_SQL_SERVER'],database='learningnemo',client_id=os.environ['AZURE_CLIENT_ID'],application_name='InvoiceRetention',connection_timeout_seconds=30,connection_attempts=3)
print('RETENTION_RESULT '+json.dumps(asyncio.run(sweep(runtime,client,apply={apply!r}))))
'''


def policy_script(enabled, change):
    policy = {**RETENTION_POLICY, 'enabled': enabled}
    return HOST_PREFIX + f'''
import hashlib,stat
inventory=json.loads(cli('sandbox','list','--output','json'))
assert all(item['phase']=='Stopped' for item in inventory), 'active sandbox; retention configuration unchanged'
directory=pathlib.Path('/etc/learningnemo')
assert directory.is_dir() and not directory.is_symlink() and directory.stat().st_uid==0 and directory.stat().st_mode & 0o022==0
path=directory/'invoice-retention.json'
expected={policy!r}
if path.exists():
    assert not path.is_symlink() and path.stat().st_uid==0 and path.stat().st_mode & 0o022==0
    current=json.loads(path.read_text())
    legacy={{'version':1,'enabled':current.get('enabled'),'successful_stopped_ttl_hours':24}}
    assert type(current.get('enabled'))==bool and (current==legacy or current=={{**expected,'enabled':current['enabled']}})
if {change!r}:
    temporary=directory/'invoice-retention.new'
    with open(os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600),'w') as output:
        json.dump(expected,output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary,path)
assert path.is_file() and json.loads(path.read_text())==expected
archives=pathlib.Path('/var/lib/learningnemo-invoice-retention')
receipts=[]
if archives.exists():
    assert not archives.is_symlink() and archives.stat().st_uid==0 and archives.stat().st_mode & 0o077==0
    for archive in archives.iterdir():
        assert archive.is_dir() and not archive.is_symlink() and archive.stat().st_uid==0 and archive.stat().st_mode & 0o077==0
        manifest=archive/'manifest.json'
        assert manifest.is_file() and not manifest.is_symlink()
        saved=json.loads(manifest.read_text())
        for name,digest in saved['sha256'].items():
            target=archive/name
            assert target.parent==archive and target.is_file() and not target.is_symlink() and target.stat().st_mode & 0o077==0
            assert hashlib.sha256(target.read_bytes()).hexdigest()==digest
        deleted=archive/'deleted.json'
        if deleted.exists():
            receipt=json.loads(deleted.read_text())
            assert all(uuid.UUID(item['id']).hex!=receipt['sandbox_id'] for item in inventory)
        receipts.append({{'run_id':saved['run_id'],'verified':True,'deletion_confirmed':deleted.exists()}})
disk=os.statvfs('/var/lib/learningnemo-invoice-cache/runs')
print('RETENTION_POLICY '+json.dumps({{'policy':expected,'retained':len(inventory),'all_stopped':True,'free_gib':round(disk.f_bavail*disk.f_frsize/1024**3,2),'archives':receipts}}))
PY
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preview', 'enable', 'pause', 'run', 'verify'])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.environ.get('AZURE_SUBSCRIPTION_ID') != SUBSCRIPTION or az('account', 'show')['id'] != SUBSCRIPTION:
        raise ValueError('expected subscription required')
    live = json.loads((STATE / 'invoice-services.live.json').read_text())
    if live['subscription'] != SUBSCRIPTION or live['values'].get('availabilityMode') != 'operator-managed':
        raise ValueError('owned managed invoice release required')
    if args.action in ('enable', 'pause', 'run'):
        if not args.apply or os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'invoice-retention':
            raise ValueError('explicit invoice-retention apply acknowledgment required')
    if args.action in ('preview', 'run'):
        output = remote('operator', sweep_code(args.action == 'run'), 'RETENTION_RESULT')
        receipts = [json.JSONDecoder().raw_decode(line.split('RETENTION_RESULT ', 1)[1])[0] for line in output.splitlines() if 'RETENTION_RESULT ' in line]
        if len(receipts) != 1:
            raise ValueError('retention receipt unconfirmed; no replay')
        record = {**receipts[0], 'image': live['values']['image']}
        save('invoice-retention.' + ('preview' if args.action == 'preview' else 'applied') + '.json', record)
        print(json.dumps(record))
        return
    if args.action == 'enable':
        preview = json.loads((STATE / 'invoice-retention.preview.json').read_text())
        if preview['image'] != live['values']['image'] or preview['status'] != 'preview' or datetime.fromisoformat(preview['checked_at']) < datetime.now(UTC) - timedelta(hours=1):
            raise ValueError('fresh matching preview required before enabling')
    vm = az('vm', 'get-instance-view', '-g', 'rg-learningnemo-saw-dev', '-n', 'vm-learningnemo-saw-dev')
    if vm['tags'].get('owner') != 'learningnemo-portfolio' or not any(item['code'] == 'PowerState/running' for item in vm['instanceView']['statuses']):
        raise ValueError('owned running host required; no implicit boot')
    result = az('vm', 'run-command', 'invoke', '-g', 'rg-learningnemo-saw-dev', '-n', 'vm-learningnemo-saw-dev', '--command-id', 'RunShellScript',
                '--scripts', policy_script(args.action != 'pause', args.action != 'verify'))
    receipts = [json.loads(line.split('RETENTION_POLICY ', 1)[1]) for item in result.get('value', []) for line in item.get('message', '').splitlines() if line.startswith('RETENTION_POLICY ')]
    if len(receipts) != 1:
        raise ValueError('retention policy or archive verification unconfirmed')
    save('invoice-retention.policy.json', {**receipts[0], 'checked_at': datetime.now(UTC).isoformat()})
    print(json.dumps(receipts[0]))


if __name__ == '__main__':
    main()