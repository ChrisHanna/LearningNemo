"""Reviewed transition to operator-managed invoice availability; never delete resources."""

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys

from availability_policy import managed, managed_tags
from deploy_human_services import az, save
from preflight_platform import validate_budget

ROOT=Path(__file__).parents[2]
STATE=Path.home()/'.local/state/learningnemo'
GROUP='rg-learningnemo-saw-dev'
VM='vm-learningnemo-saw-dev'
RESOURCES={
    'rg-learningnemo-platform-dev':['cae-learningnemo-dev',*[f'ca-learningnemo-{kind}-dev' for kind in ('diagnostic','query-runner','remediation','verifier')]],
    'rg-learningnemo-artifacts-dev':['crlearningnemodevgruyrc4qwdvvm'],
    'rg-learningnemo-data-network-dev':['pe-learningnemo-sql-dev','privatelink.database.windows.net','privatelink.database.windows.net/link-learningnemo-dev'],
    'rg-learningnemo-demo-dev':[*[f'ca-learningnemo-{kind}-dev' for kind in ('dashboard','agent','controller')],*[f'id-learningnemo-cloud-{kind}-dev' for kind in ('dashboard','agent','controller')]],
    'rg-learningnemo-invoice-dev':[*[f'ca-nemo-invoice-{kind}-dev' for kind in ('operator','review','planning','execution','verifier')],*[f'id-learningnemo-invoice-{kind}-dev' for kind in ('operator','review','planning','execution','verifier','simulator')]],
    GROUP:[VM,'nic-'+VM,'id-learningnemo-saw-runtime-dev','nat-learningnemo-saw-runtime-dev','pip-learningnemo-saw-runtime-dev'],
}


def host_script(enabled, apply):
    sys.path.insert(0,str(ROOT/'src'))
    from task_agent.console.invoice_remote_runtime import HOST_PREFIX
    return HOST_PREFIX+f'''
import stat
inventory=json.loads(cli('sandbox','list','--output','json'))
assert all(item['phase']=='Stopped' for item in inventory), 'stop or reconcile active runs before maintenance'
directory=pathlib.Path('/etc/learningnemo')
gate=directory/'invoice-availability.json'
expected={{'mode':'operator-managed','admission_enabled':{enabled!r}}}
if {apply!r}:
    directory.mkdir(mode=0o755,exist_ok=True)
    assert not directory.is_symlink() and directory.stat().st_uid==0 and directory.stat().st_mode & 0o022==0
    backup=pathlib.Path('/var/lib/learningnemo-saw/operator-managed-before.json')
    if not backup.exists():
        before={{'policy':gate.read_text() if gate.exists() else None,'timer':subprocess.run(['systemctl','cat','learningnemo-saw-expire.timer'],capture_output=True,text=True).stdout}}
        with open(os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as output:json.dump(before,output)
    subprocess.run(['systemctl','disable','--now','learningnemo-saw-expire.timer'],check=True,capture_output=True)
    temporary=directory/'invoice-availability.json.new'
    with open(os.open(temporary,os.O_CREAT|os.O_TRUNC|os.O_WRONLY|os.O_NOFOLLOW,0o600),'w') as output:json.dump(expected,output)
    os.chown(temporary,0,0)
    os.chmod(temporary,0o600)
    os.replace(temporary,gate)
assert gate.is_file() and not gate.is_symlink() and gate.stat().st_uid==0 and gate.stat().st_mode & 0o022==0
assert json.loads(gate.read_text())==expected
assert subprocess.run(['systemctl','is-active','--quiet','learningnemo-saw-expire.timer']).returncode!=0
assert subprocess.run(['systemctl','is-enabled','--quiet','learningnemo-saw-expire.timer']).returncode!=0
print('AVAILABILITY_HOST '+json.dumps({{'mode':'operator-managed','admission_enabled':{enabled!r},'stopped_sandboxes':len(inventory),'global_timer_disabled':True,'per_run_watchdogs_preserved':True}}))
PY
'''


def host(enabled, apply):
    result=az('vm','run-command','invoke','-g',GROUP,'-n',VM,'--command-id','RunShellScript','--scripts',host_script(enabled,apply))
    rows=[json.loads(line.split('AVAILABILITY_HOST ',1)[1]) for entry in result.get('value',[]) for line in entry.get('message','').splitlines() if line.startswith('AVAILABILITY_HOST ')]
    if len(rows)!=1:raise ValueError('host policy verification failed; do not admit runs')
    return rows[0]


def inventory():
    result=[]
    for group,names in RESOURCES.items():
        resources={item['name']:item for item in az('resource','list','-g',group)}
        result.extend(resources[name] for name in names)
        if group!=GROUP:result.append(az('group','show','-n',group))
    for item in result:managed_tags(item)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['enable','verify','pause','shutdown-host'])
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    account=az('account','show')
    if account['id']!=os.environ['AZURE_SUBSCRIPTION_ID']:raise ValueError('subscription mismatch')
    validate_budget(account,required=True,maximum_amount=50)
    vm=az('vm','get-instance-view','-g',GROUP,'-n',VM)
    if not any(item['code']=='PowerState/running' for item in vm['instanceView']['statuses']):
        raise ValueError('host must be running; use reviewed retained-boot procedure first')
    resources=inventory()
    if args.action=='verify':
        if not all(managed(item) for item in resources):raise ValueError('resource policy differs')
        if vm['tags'].get('admission')!='enabled':raise ValueError('host admission is paused')
        receipt=host(True,False)
        save('invoice-availability.verified.json',{'checkedAt':datetime.now(UTC).isoformat(),'host':receipt,'resourceCount':len(resources),'mode':'operator-managed'})
        print('PASS operator-managed resource policy and root-owned host gate; all sandboxes stopped')
        return
    print('Availability action:',args.action,'resources:',len(resources),'deletions: 0')
    if not args.apply:return
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY')!='operator-managed-invoice':raise ValueError('explicit availability change acknowledgement required')
    save('invoice-availability.before-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S')+'.json',resources)
    receipt=host(args.action=='enable',True)
    targets=resources if args.action=='enable' else [vm]
    for item in targets:
        tags=managed_tags(item)
        if item['id'].casefold()==vm['id'].casefold():tags['admission']='enabled' if args.action=='enable' else 'paused'
        if tags!=item.get('tags'):
            az('tag','update','--resource-id',item['id'],'--operation','Replace','--tags',*[key+'='+str(value) for key,value in tags.items()])
        current=az('tag','list','--resource-id',item['id'])['properties']['tags']
        if current!=tags:raise ValueError('availability tags differ after update')
    save('invoice-availability.policy.json',{'mode':'operator-managed','host':receipt,'subscription':account['id'],'updatedAt':datetime.now(UTC).isoformat()})
    if args.action=='shutdown-host':
        az('vm','deallocate','-g',GROUP,'-n',VM)
        current=az('vm','get-instance-view','-g',GROUP,'-n',VM)
        if not any(item['code']=='PowerState/deallocated' for item in current['instanceView']['statuses']):raise ValueError('host shutdown unconfirmed')
    print('PASS explicit availability policy applied; no sandbox, disk, or resource deleted')


if __name__=='__main__':main()