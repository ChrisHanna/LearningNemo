"""Reclaim only quiescent, unreferenced prepared-image caches and failed staging."""

import argparse
import base64
import json
from pathlib import Path
import subprocess
import zlib

from task_agent.console.invoice_remote_runtime import HOST_PREFIX


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]/'src/task_agent/console/invoice_retention_host.py'
    packed = base64.b64encode(zlib.compress(source.read_bytes())).decode()
    script = HOST_PREFIX+f'''
import zlib,shutil
exec(zlib.decompress(base64.b64decode({packed!r})))
apply={args.apply!r}
lock=open(os.open('/run/lock/learningnemo-invoice-lifecycle.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600),'r+')
assert os.fstat(lock.fileno()).st_uid==0 and os.fstat(lock.fileno()).st_mode&0o077==0
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
root=Path('/home/sawadmin/.local/state/openshell/vm')
images=root/'images'
sandboxes=root/'sandboxes'
safe_path(images)
before=json.loads(cli('sandbox','list','--output','json'))
assert all(sandbox_quiescent(item,sandboxes) for item in before), 'Active sandbox prevents cache reclamation'
def references():
    result=set()
    for directory in sandboxes.iterdir():
        if not directory.is_dir():continue
        safe_path(directory)
        path=directory/'image-identity'
        if path.is_file():
            value=read_bounded(path,1024).decode().strip()
            assert ':sha256:' in value, 'Unknown image identity; preserve caches'
            digest=value.rsplit(':sha256:',1)[1]
            assert re.fullmatch('[a-f0-9]{{64}}',digest)
            result.add('sandbox-prepared-rootfs-ext4-umoci-v3-openshell-0.0.116-sha256-'+digest)
    return result
def unused(path):
    safe_path(path)
    assert path.is_dir() and not path.is_mount()
    assert not path.is_symlink()
    assert path.name not in references(), 'Sandbox still references prepared image'
    assert path.stat().st_uid==pwd.getpwnam('sawadmin').pw_uid
    children=list(path.iterdir())
    assert all(item.name in ('rootfs.ext4','source-rootfs.tar') and item.is_file() and not item.is_symlink() for item in children), 'Unknown cache contents'
    loops=json.loads(subprocess.check_output(['losetup','--json','--output','NAME,BACK-FILE'],text=True)).get('loopdevices') or []
    assert not any(str(item.get('back-file','')).startswith(str(path)+'/') for item in loops), 'Cache backs an active loop device'
    for descriptor in Path('/proc').glob('[0-9]*/fd/*'):
        try: resolved=descriptor.resolve()
        except (OSError,PermissionError):continue
        assert resolved!=path and path not in resolved.parents, 'Cache is open'
    for command in Path('/proc').glob('[0-9]*/cmdline'):
        try: content=command.read_bytes()[:65536]
        except OSError:continue
        assert str(path).encode() not in content, 'Cache has an active process'
targets=[]
for path in images.iterdir():
    if not re.fullmatch(r'sandbox-prepared-rootfs-ext4-umoci-v3-openshell-0.0.116-sha256-[a-f0-9]{{64}}(?:\\.staging-[0-9]+-[0-9]+)?',path.name):continue
    if path.name in references():continue
    unused(path)
    targets.append(path)
disk=os.statvfs(images)
summary={{'apply':apply,'free_gib_before':round(disk.f_bavail*disk.f_frsize/1024**3,2),'candidates':[path.name for path in targets],'deleted':[],'referenced_preserved':sorted(references())}}
if apply:
    archive=Path('/var/lib/learningnemo-invoice-cache')/('cache-recovery-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'))
    private_directory(archive,0)
    write_once(archive/'before.json',encoded({{'inventory':before,'summary':summary}}))
    for path in targets:
        assert json.loads(cli('sandbox','list','--output','json'))==before, 'Sandbox inventory changed; no further reclamation'
        unused(path)
        shutil.rmtree(path)
        summary['deleted'].append(path.name)
    assert json.loads(cli('sandbox','list','--output','json'))==before
    summary['archive']=str(archive)
    write_once(archive/'result.json',encoded(summary))
disk=os.statvfs(images)
summary['free_gib_after']=round(disk.f_bavail*disk.f_frsize/1024**3,2)
print('CACHE_RECOVERY '+json.dumps(summary,separators=(',',':')))
PY
'''
    result = json.loads(subprocess.check_output(['az','vm','run-command','invoke','-g','rg-learningnemo-saw-dev',
        '-n','vm-learningnemo-saw-dev','--command-id','RunShellScript','--scripts',script,'-o','json'],text=True,timeout=300))
    output = '\n'.join(item.get('message','') for item in result.get('value',[]))
    receipts = [json.loads(line.split(' ',1)[1]) for line in output.splitlines() if line.startswith('CACHE_RECOVERY ')]
    if len(receipts)!=1:
        raise RuntimeError('Cache recovery unconfirmed; inspect host evidence before any retry: '+output[-2500:])
    print(json.dumps(receipts[0],indent=2))


if __name__=='__main__':
    main()