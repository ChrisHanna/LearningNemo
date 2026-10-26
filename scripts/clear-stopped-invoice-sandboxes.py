"""Explicit administrator reset: archive and remove an exact stopped inventory."""

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import zlib

from task_agent.console.invoice_remote_runtime import HOST_PREFIX


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    subscription = os.environ['AZURE_SUBSCRIPTION_ID']
    account = subprocess.check_output(['az', 'account', 'show', '--query', 'id', '-o', 'tsv'], text=True).strip()
    if account != subscription:
        raise RuntimeError('Azure subscription differs')
    expected = json.loads(args.snapshot.read_text())['inventory'] if args.apply else None
    helper = Path(__file__).resolve().parents[1] / 'src/task_agent/console/invoice_retention_host.py'
    packed = base64.b64encode(zlib.compress(helper.read_bytes())).decode()
    script = HOST_PREFIX + f'''
import fcntl,zlib
exec(zlib.decompress(base64.b64decode({packed!r})))
apply={args.apply!r}
expected={expected!r}
lock=open(os.open('/run/lock/learningnemo-invoice-lifecycle.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600),'r+')
assert os.fstat(lock.fileno()).st_uid==0 and os.fstat(lock.fileno()).st_mode & 0o077==0
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
def inventory():
    return sorted(json.loads(cli('sandbox','list','--output','json')),key=lambda item:item['id'])
def binding(items):
    return [{{'id':item['id'],'name':item['name'],'phase':item['phase']}} for item in items]
records=inventory()
assert all(item.get('phase')=='Stopped' and re.fullmatch(r'i[px]-[a-f0-9]{{16}}',item.get('name','')) for item in records), 'Active, unconfirmed, or non-invoice sandbox present; no deletion'
now=datetime.now(UTC)
archives=Path('/var/lib/learningnemo-invoice-cache')/('admin-cleanup-'+now.strftime('%Y%m%dT%H%M%S%fZ'))
sandboxes=Path('/home/sawadmin/.local/state/openshell/vm/sandboxes')
summary={{'inventory':binding(records),'deleted':[],'apply':apply,'checked_at':now.isoformat()}}
if apply:
    assert binding(records)==expected, 'Inventory changed since preview; no deletion'
    private_directory(archives,0)
    write_once(archives/'before.json',encoded(records))
    # Archive every target before deleting the first one. Run/SQL evidence stays intact.
    for record in records:
        archive_stopped_sandbox(record,cli,sandboxes,archives,0,now,minimum_age_hours=0)
    remaining=records[:]
    for record in records:
        assert binding(inventory())==binding(remaining), 'Inventory changed; stop without replay'
        exact=json.loads(cli('sandbox','get',record['name'],'--output','json'))
        assert exact['id']==record['id'] and exact['phase']=='Stopped'
        identifier=UUID(record['id']).hex
        attempt=archives/('stopped-'+identifier)/'admin-delete-attempt.json'
        write_once(attempt,encoded({{'id':record['id'],'name':record['name'],'requested_at':datetime.now(UTC).isoformat()}}))
        cli('sandbox','delete',record['name'])
        remaining=[item for item in remaining if item['id']!=record['id']]
        assert binding(inventory())==binding(remaining), 'Deletion unconfirmed; do not replay'
        summary['deleted'].append(identifier)
        write_once(archives/('stopped-'+identifier)/'admin-deleted.json',encoded({{'sandbox_id':identifier,'confirmed_at':datetime.now(UTC).isoformat()}}))
    assert inventory()==[], 'Final inventory is not empty'
    summary['providers_removed']=garbage_collect_providers(cli,[],Path('/var/lib/learningnemo-invoice-cache/policies'))
    summary['archive']=str(archives)
    write_once(archives/'result.json',encoded(summary))
summary['remaining_count']=len(inventory())
disk=os.statvfs('/var/lib/learningnemo-invoice-cache')
summary['free_gib']=round(disk.f_bavail*disk.f_frsize/1024**3,2)
print('ADMIN_SANDBOX_RESET '+json.dumps(summary,separators=(',',':')))
PY
'''
    response = json.loads(subprocess.check_output([
        'az', 'vm', 'run-command', 'invoke', '-g', 'rg-learningnemo-saw-dev',
        '-n', 'vm-learningnemo-saw-dev', '--command-id', 'RunShellScript',
        '--scripts', script, '-o', 'json',
    ], text=True, timeout=1200))
    output = '\n'.join(item.get('message', '') for item in response.get('value', []))
    receipts = [json.loads(line.split(' ', 1)[1]) for line in output.splitlines() if line.startswith('ADMIN_SANDBOX_RESET ')]
    if len(receipts) != 1:
        raise RuntimeError('Reset unconfirmed; inspect VM run-command output, do not replay: ' + output[-2500:])
    result = receipts[0]
    if not args.apply:
        args.snapshot.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        args.snapshot.write_text(json.dumps(result, indent=2))
        args.snapshot.chmod(0o600)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()