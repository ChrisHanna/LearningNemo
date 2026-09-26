"""Fixed Azure Run Command bridge to the retained OpenShell host."""

import asyncio
import base64
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import textwrap

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from task_agent.control.canonical import content_hash
from task_agent.control.invoice_sandbox import IMAGE, SandboxCapacityError, sandbox_name


HOST_PREFIX = '''#!/usr/bin/env bash
set -euo pipefail
cd /home/sawadmin
python3 - <<'PY'
import base64,json,os,pathlib,pwd,subprocess,uuid
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding,rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
user=pwd.getpwnam('sawadmin')
environment={'HOME':user.pw_dir,'XDG_RUNTIME_DIR':'/run/user/'+str(user.pw_uid),'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/'+str(user.pw_uid)+'/bus'}
def cli(*args,timeout=300):
    result=subprocess.run(['runuser','-u','sawadmin','--','env',*[key+'='+value for key,value in environment.items()],'openshell','--gateway','openshell',*args],capture_output=True,text=True,timeout=timeout)
    if result.returncode: raise RuntimeError('fixed OpenShell operation failed')
    return result.stdout
'''


CAPABILITY_ENV = 'INVOICE_RUN_CAPABILITY'


def provider_name(run_id):
    return 'invoice-run-' + run_id


def provider_block(kind, run_id, profile):
    """Host code that mints the run capability into an OpenShell provider; only its hash leaves the VM."""
    if profile.get('id') != 'learningnemo-invoice-' + kind:
        raise ValueError('invoice provider profile differs from the run role')
    encoded = base64.b64encode(json.dumps(profile, sort_keys=True).encode()).decode()
    return f'''
import hashlib,secrets
provider={provider_name(run_id)!r}
expected_profile=json.loads(base64.b64decode({encoded!r}))
def binding(profile):
    return ({{item['name']:sorted(item.get('env_vars') or []) for item in profile.get('credentials') or []}},
            sorted((item.get('host'),item.get('port'),item.get('path')) for item in profile.get('endpoints') or []))
def secret_cli(*args,secret,timeout=120):
    result=subprocess.run(['runuser','-u','sawadmin','--','env',*[key+'='+value for key,value in environment.items()],'openshell','--gateway','openshell',*args],capture_output=True,text=True,timeout=timeout,env={{**os.environ,{CAPABILITY_ENV!r}:secret}})
    if result.returncode: raise RuntimeError('fixed OpenShell credential operation failed')
try:
    current=json.loads(cli('provider','profile','export',expected_profile['id'],'-o','json'))
except RuntimeError:
    profile_file=policies/({run_id!r}+'-provider-profile.json')
    with open(os.open(profile_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644),'w') as output: json.dump(expected_profile,output)
    os.chmod(profile_file,0o644)
    cli('provider','profile','import','-f',str(profile_file))
    current=json.loads(cli('provider','profile','export',expected_profile['id'],'-o','json'))
assert binding(current)==binding(expected_profile), 'provider profile credential binding drift'
capability={run_id!r}+'.'+secrets.token_urlsafe(32)
capability_hash=hashlib.sha256(capability.encode('ascii')).hexdigest()
secret_cli('provider','create','--name',provider,'--type',expected_profile['id'],'--credential',{CAPABILITY_ENV!r},secret=capability)
del capability
mediation={{'capability_hash':capability_hash,'provider':provider}}
provider_arguments=['--provider',provider]
'''


def expire_provider_script(run_id):
    """Host code that expires a stopped run's provider credential; absent providers were never minted."""
    name = provider_name(run_id)
    return f'''try:
    cli('provider','update',{name!r},'--credential-expires-at',{CAPABILITY_ENV!r}+'=1')
except RuntimeError:
    try: cli('provider','get',{name!r})
    except RuntimeError: pass
    else: raise
'''


def prepare_script(kind, run_id, image, policy, availability_mode='leased', provider_profile=None):
    name = sandbox_name(kind, run_id)
    if availability_mode not in ('leased','operator-managed'):
        raise ValueError('explicit workspace availability mode required')
    if not IMAGE.fullmatch(image):
        raise ValueError('pinned invoice image required')
    encoded = base64.b64encode(policy.encode()).decode()
    if availability_mode == 'operator-managed':
        admission = '''
gate=pathlib.Path('/etc/learningnemo/invoice-availability.json')
assert gate.is_file() and not gate.is_symlink()
assert gate.stat().st_uid==0 and gate.stat().st_mode & 0o022==0
assert gate.parent.stat().st_uid==0 and gate.parent.stat().st_mode & 0o022==0
assert json.loads(gate.read_text())=={'mode':'operator-managed','admission_enabled':True}, 'workspace admission paused'
assert subprocess.run(['systemctl','is-active','--quiet','learningnemo-saw-expire.timer']).returncode!=0
assert subprocess.run(['systemctl','is-enabled','--quiet','learningnemo-saw-expire.timer']).returncode!=0
'''
    else:
        admission = '''
deadline=subprocess.check_output(['systemctl','show','learningnemo-saw-expire.timer','-p','NextElapseUSecRealtime','--value'],text=True).strip()
deadline_seconds=int(subprocess.check_output(['date','-u','-d',deadline,'+%s'],text=True))
assert deadline_seconds-int(__import__('time').time())>1260, 'workspace lease too short'
'''
    provider = textwrap.indent(provider_block(kind, run_id, provider_profile), '    ') if provider_profile is not None else ''
    return HOST_PREFIX + f'''
name={name!r}
{admission}
import fcntl
lifecycle=open(os.open('/run/lock/learningnemo-invoice-lifecycle.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600),'r+')
assert os.fstat(lifecycle.fileno()).st_uid==0 and os.fstat(lifecycle.fileno()).st_mode & 0o077==0
fcntl.flock(lifecycle,fcntl.LOCK_EX)
inventory=json.loads(cli('sandbox','list','--output','json'))
assert isinstance(inventory,list)
if len(inventory)>=24:
    print('INVOICE_ADMISSION_BLOCKED '+json.dumps({{'run_id':{run_id!r},'reason':'sandbox-capacity','retained':len(inventory),'limit':24}}))
    raise SystemExit(0)
root=pathlib.Path('/var/lib/learningnemo-invoice-cache/runs')
root.mkdir(mode=0o700,exist_ok=True)
directory=root/{run_id!r}
directory.mkdir(mode=0o700)
policies=pathlib.Path('/var/lib/learningnemo-invoice-cache/policies')
policies.mkdir(mode=0o755,exist_ok=True)
policy=policies/({run_id!r}+'.yaml')
with open(os.open(policy,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644),'wb') as output: output.write(base64.b64decode({encoded!r}))
os.chmod(policy,0o644)
disk=os.statvfs(root)
assert disk.f_bavail*disk.f_frsize>=4*1024**3, 'retained workspace free-space floor reached'
assert all(item['phase']=='Stopped' for item in inventory), 'another sandbox is active'
assert all(item['name']!=name for item in inventory)
watchdog=subprocess.run(['runuser','-u','sawadmin','--','env',*[key+'='+value for key,value in environment.items()],'systemd-run','--user','--unit','invoice-provision-expire-'+{run_id!r},'--on-active','1200s','openshell','--gateway','openshell','sandbox','stop',name],capture_output=True,timeout=30)
assert watchdog.returncode==0
mediation={{}}
provider_arguments=[]
try:
{provider}
    cli('sandbox','create','--name',name,'--from',{image!r},'--policy',str(policy),*provider_arguments,'--detach','--','/bin/sleep','infinity',timeout=540)
    record=json.loads(cli('sandbox','get',name,'--output','json'))
    assert record['name']==name and record['phase']=='Ready'
    sandbox_id=str(uuid.UUID(record['id']))
    overlay=pathlib.Path('/home/sawadmin/.local/state/openshell/vm/sandboxes')/sandbox_id/'overlay.ext4'
    assert overlay.is_file() and not overlay.is_symlink()
    cli('sandbox','stop',name)
    assert json.loads(cli('sandbox','get',name,'--output','json'))['phase']=='Stopped'
    for descriptor in pathlib.Path('/proc').glob('[0-9]*/fd/*'):
        try: assert descriptor.resolve()!=overlay.resolve(), 'overlay still open'
        except (FileNotFoundError,PermissionError): pass
    mountpoint=directory/'resolver-mount'
    mountpoint.mkdir(mode=0o700)
    subprocess.run(['mount','-o','loop,rw',str(overlay),str(mountpoint)],check=True,capture_output=True,timeout=30)
    try:
        resolver=mountpoint/'upper/etc/resolv.conf'
        assert resolver.is_file() and not resolver.is_symlink() and not resolver.parent.is_symlink()
        original=resolver.read_text()
        servers=[line.split()[1] for line in original.splitlines() if line.startswith('nameserver ')]
        assert all(server in ('8.8.8.8','8.8.4.4','168.63.129.16') for server in servers)
        (directory/'resolv.conf.before').write_text(original)
        resolver.write_text('nameserver 168.63.129.16\\noptions timeout:2 attempts:2\\n')
        os.chmod(resolver,0o644)
        os.chown(resolver,0,0)
    finally:
        subprocess.run(['umount',str(mountpoint)],check=True,capture_output=True,timeout=30)
    cli('sandbox','start',name,timeout=90)
    record=json.loads(cli('sandbox','get',name,'--output','json'))
    assert str(uuid.UUID(record['id']))==sandbox_id and record['phase']=='Ready'
    applied=cli('sandbox','get',name,'--policy-only')
    timer=subprocess.run(['runuser','-u','sawadmin','--','env',*[key+'='+value for key,value in environment.items()],'systemd-run','--user','--unit','invoice-expire-'+{run_id!r},'--on-active','600s','openshell','--gateway','openshell','sandbox','stop',name],capture_output=True,timeout=30)
    assert timer.returncode==0
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    with open(os.open(directory/'key.pem',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as output:
        output.write(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    print('INVOICE_PREPARED '+json.dumps({{'run_id':{run_id!r},'sandbox_id':uuid.UUID(record['id']).hex,'name':name,'policy':applied,'public_key':public,'image':{image!r},**mediation}}))
except Exception:
    try: cli('sandbox','stop',name)
    except Exception: pass
    if mediation:
        try: cli('provider','update',mediation['provider'],'--credential-expires-at',{CAPABILITY_ENV!r}+'=1')
        except Exception: pass
    raise
PY
'''


class AzureInvoiceRuntime:
    def __init__(self, *, compute_client, image, policies, availability_mode='leased', credential_mode='manifest'):
        if not IMAGE.fullmatch(image):
            raise ValueError('fixed invoice image required')
        if availability_mode not in ('leased','operator-managed'):
            raise ValueError('explicit workspace availability mode required')
        if credential_mode not in ('manifest','provider'):
            raise ValueError('explicit run credential mode required')
        self.availability_mode, self.credential_mode = availability_mode, credential_mode
        self.compute, self.image, self.policies = compute_client, image, Path(policies)
        self.prepared = {}

    def command(self, script, timeout=600):
        operation = self.compute.virtual_machines.begin_run_command('rg-learningnemo-saw-dev', 'vm-learningnemo-saw-dev',
            {'command_id':'RunShellScript','script':script.splitlines()})
        result = operation.result(timeout=timeout)
        return '\n'.join(item.message or '' for item in result.value or [])

    def prepare(self, kind, run_id, lease_seconds=600):
        import yaml
        policy = (self.policies / f'invoice-{kind}-policy.yaml').read_text()
        profile = yaml.safe_load((self.policies / f'invoice-{kind}-provider-profile.yaml').read_text()) if self.credential_mode == 'provider' else None
        result = self.command(prepare_script(kind,run_id,self.image,policy,self.availability_mode,profile))
        records = [json.loads(line[len('INVOICE_PREPARED '):]) for line in result.splitlines() if line.startswith('INVOICE_PREPARED ')]
        blocked = [json.loads(line[len('INVOICE_ADMISSION_BLOCKED '):]) for line in result.splitlines() if line.startswith('INVOICE_ADMISSION_BLOCKED ')]
        if blocked:
            if records or len(blocked) != 1 or set(blocked[0]) != {'run_id','reason','retained','limit'}:
                raise RuntimeError('remote sandbox admission unconfirmed')
            receipt = blocked[0]
            if receipt['run_id'] != run_id or receipt['reason'] != 'sandbox-capacity' or receipt['limit'] != 24 or type(receipt['retained']) is not int or receipt['retained'] < 24:
                raise RuntimeError('remote sandbox admission unconfirmed')
            raise SandboxCapacityError(receipt['retained'], receipt['limit'])
        if len(records)!=1:
            raise RuntimeError('remote sandbox admission unconfirmed')
        record=records[0]
        if record['run_id']!=run_id or yaml.safe_load(record['policy'])!=yaml.safe_load(policy):
            self.stop(kind,run_id)
            raise RuntimeError('observed sandbox policy differs')
        mediated = {'capability_hash','provider'} & set(record)
        if self.credential_mode == 'provider':
            if record.get('provider') != provider_name(run_id) or not re.fullmatch(r'[a-f0-9]{64}', str(record.get('capability_hash'))):
                self.stop(kind,run_id)
                raise RuntimeError('provider-held run capability unconfirmed')
        elif mediated:
            self.stop(kind,run_id)
            raise RuntimeError('unexpected provider-held run capability')
        record['policy_hash']=content_hash(yaml.safe_load(policy))
        self.prepared[run_id]=record
        return {key:record[key] for key in ('run_id','sandbox_id','policy_hash','name','image','capability_hash') if key in record}

    def stop(self, kind, run_id):
        name=sandbox_name(kind,run_id)
        expected = self.prepared.get(run_id, {}).get('sandbox_id')
        revoke = expire_provider_script(run_id) if self.credential_mode == 'provider' else ''
        result=self.command(HOST_PREFIX+f"cli('sandbox','stop',{name!r})\nrecord=json.loads(cli('sandbox','get',{name!r},'--output','json'))\nassert record['name']=={name!r} and record['phase']=='Stopped'\nassert {expected!r} is None or uuid.UUID(record['id']).hex=={expected!r}\npathlib.Path('/var/lib/learningnemo-invoice-cache/runs/{run_id}/key.pem').unlink(missing_ok=True)\n{revoke}print('INVOICE_STOPPED')\nPY\n")
        if 'INVOICE_STOPPED' not in result:
            raise RuntimeError('sandbox stop not confirmed')

    async def execute(self, manifest, on_event):
        record=self.prepared[manifest.run_id]
        if record['sandbox_id']!=manifest.sandbox_id:
            raise ValueError('remote sandbox identity differs')
        if (manifest.capability is None) != (self.credential_mode == 'provider'):
            raise ValueError('run credential mode differs from the manifest')
        private=manifest.model_dump(mode='json')
        if manifest.capability is not None: private['capability']=manifest.capability.get_secret_value()
        public=serialization.load_pem_public_key(record['public_key'].encode())
        key,nonce=AESGCM.generate_key(bit_length=256),__import__('os').urandom(12)
        wrapped=public.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
        encrypted=AESGCM(key).encrypt(nonce,json.dumps(private).encode(),manifest.run_id.encode())
        envelope={name:base64.b64encode(value).decode() for name,value in [('key',wrapped),('nonce',nonce),('body',encrypted)]}
        run_id=manifest.run_id; name=sandbox_name(manifest.kind,run_id)
        worker = f'''import json,os,pathlib,pwd,subprocess
directory=pathlib.Path('/var/lib/learningnemo-invoice-cache/runs/{run_id}')
manifest=directory/'manifest.json'
try:
    content=manifest.read_bytes()
finally:
    manifest.unlink(missing_ok=True)
user=pwd.getpwnam('sawadmin')
environment={{'HOME':user.pw_dir,'XDG_RUNTIME_DIR':'/run/user/'+str(user.pw_uid),'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/'+str(user.pw_uid)+'/bus'}}
code=1
try:
    with open(directory/'events.jsonl','xb') as output,open(directory/'error.log','xb') as errors:
        process=subprocess.run(['runuser','-u','sawadmin','--','env',*[key+'='+value for key,value in environment.items()],'openshell','--gateway','openshell','sandbox','exec','--name',{name!r},'--no-tty','--timeout','540','--','/opt/venv/bin/python','-m','task_agent.control.invoice_agent'],input=content+b'\\n',stdout=output,stderr=errors,timeout=570)
        code=process.returncode
finally:
    temporary=directory/'exit.tmp'
    temporary.write_text(json.dumps({{'exit_code':code}}))
    temporary.replace(directory/'exit.json')
'''
        script=HOST_PREFIX+f'''
import hashlib
directory=pathlib.Path('/var/lib/learningnemo-invoice-cache/runs/{run_id}')
keyfile=directory/'key.pem'
envelope=json.loads({json.dumps(envelope)!r})
envelope_hash=hashlib.sha256(json.dumps(envelope,sort_keys=True).encode()).hexdigest()
receipt=directory/'launch-receipt.json'
if not keyfile.exists():
    assert receipt.is_file() and json.loads(receipt.read_text())=={{'envelope_hash':envelope_hash,'sandbox_id':{manifest.sandbox_id!r}}}, 'launch delivery unconfirmed; no replay'
    print('INVOICE_PROCESS_STARTED')
    raise SystemExit(0)
key=serialization.load_pem_private_key(keyfile.read_bytes(),password=None)
try:
    content=AESGCM(key.decrypt(base64.b64decode(envelope['key']),padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))).decrypt(base64.b64decode(envelope['nonce']),base64.b64decode(envelope['body']),b'{run_id}')
finally:
    keyfile.unlink(missing_ok=True)
manifest=json.loads(content)
assert uuid.UUID(json.loads(cli('sandbox','get',{name!r},'--output','json'))['id']).hex==manifest['sandbox_id']
with open(os.open(directory/'manifest.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as output:output.write(content)
with open(os.open(directory/'worker.py',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as output:output.write({worker!r})
result=subprocess.run(['systemd-run','--unit','invoice-agent-{run_id}','--property','RuntimeMaxSec=600','--property','WorkingDirectory=/home/sawadmin','/usr/bin/python3',str(directory/'worker.py')],capture_output=True,timeout=30)
assert result.returncode==0
with open(os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as output:json.dump({{'envelope_hash':envelope_hash,'sandbox_id':{manifest.sandbox_id!r}}},output)
print('INVOICE_PROCESS_STARTED')
PY
'''
        started=await asyncio.to_thread(self.command,script,90)
        if 'INVOICE_PROCESS_STARTED' not in started:raise RuntimeError('agent launch unconfirmed; do not retry')
        final=None; offset=0; buffer=b''; sequence=0
        async with asyncio.timeout(620):
            while True:
                read_script=HOST_PREFIX+f"directory=pathlib.Path('/var/lib/learningnemo-invoice-cache/runs/{run_id}')\npath=directory/'events.jsonl'\ndata=b''\nif path.exists():\n    assert path.stat().st_size<65536\n    with path.open('rb') as source: source.seek({offset}); data=source.read(1800)\nexit_path=directory/'exit.json'\nprint('INVOICE_CHUNK '+json.dumps({{'chunk':base64.b64encode(data).decode(),'exit':json.loads(exit_path.read_text()) if exit_path.exists() else None,'size':path.stat().st_size if path.exists() else 0}}))\nPY\n"
                output=await asyncio.to_thread(self.command,read_script,90)
                chunks=[json.loads(line.split(' ',1)[1]) for line in output.splitlines() if line.startswith('INVOICE_CHUNK ')]
                if len(chunks)!=1:raise RuntimeError('agent observation disconnected; no replay permitted')
                chunk=chunks[0];data=base64.b64decode(chunk['chunk']);offset+=len(data);buffer+=data
                while b'\n' in buffer:
                    line,buffer=buffer.split(b'\n',1)
                    if not line.startswith(b'{'):continue
                    event=json.loads(line)
                    if event.get('run_id')!=run_id or event.get('sandbox_id')!=manifest.sandbox_id or event.get('source')!='agent-runtime':
                        raise RuntimeError('agent transcript identity differs')
                    sequence+=1
                    if event.get('sequence')!=sequence or sequence>100 or (manifest.capability is not None and manifest.capability.get_secret_value() in line.decode()):
                        raise RuntimeError('invalid or sensitive agent transcript suppressed')
                    await on_event({**event,'provenance':'sandbox-reported','delivery':'live-poll'})
                    if event.get('event_type')=='agent-finished':final=event
                if chunk['exit'] is not None and offset==chunk['size']:
                    if final is None or chunk['exit']!={'exit_code':0}:raise RuntimeError('agent run did not complete')
                    return final
                await asyncio.sleep(2)