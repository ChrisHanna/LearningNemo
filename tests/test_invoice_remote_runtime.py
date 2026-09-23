from pathlib import Path
import subprocess

import pytest

from task_agent.console.invoice_remote_runtime import prepare_script


def test_remote_prepare_has_fixed_image_and_short_name():
    policy=(Path(__file__).parents[1]/'infra/next-phase/openshell/invoice-planning-policy.yaml').read_text()
    image='crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-agent@sha256:'+'a'*64
    script=prepare_script('planning','b'*32,image,policy)
    subprocess.run(['bash','-n'],input=script,text=True,check=True)
    compile(script.split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0],'<host-prepare>','exec')
    assert 'ip-'+('b'*16) in script and 'sandbox delete' not in script
    with pytest.raises(ValueError): prepare_script('planning','b'*32,'example.com/x',policy)
    script=prepare_script('planning','b'*32,'crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-agent@sha256:'+'a'*64,policy)
    assert script.index('invoice-provision-expire-') < script.index("cli('sandbox','create'")
    assert 'timeout=540' in script and '>1260' in script
    assert "'--on-active','600s'" in script
    assert "nameserver 168.63.129.16" in script
    assert 'len(inventory)>=24' in script and 'disk.f_bavail*disk.f_frsize>=4*1024**3' in script
    assert script.index('INVOICE_ADMISSION_BLOCKED') < script.index('directory.mkdir') < script.index("cli('sandbox','create'")
    assert script.index("cli('sandbox','stop',name)") < script.index("['mount','-o','loop,rw'")
    assert script.index("['umount'") < script.index("cli('sandbox','start',name")


def test_managed_host_keeps_per_run_watchdogs_and_checks_operator_gate():
    image='crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-agent@sha256:'+'a'*64
    script=prepare_script('planning','b'*32,image,'version: 1','operator-managed')
    compile(script.split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0],'<managed-prepare>','exec')
    assert 'deadline_seconds' not in script
    assert "'admission_enabled':True" in script and 'st_uid==0' in script
    assert 'is-enabled' in script and 'is-active' in script
    assert "'--on-active','1200s'" in script and "'--on-active','600s'" in script
    assert 'len(inventory)>=24' in script and "item['phase']=='Stopped'" in script
    assert 'disk.f_bavail*disk.f_frsize>=4*1024**3' in script


@pytest.mark.asyncio
async def test_remote_execution_scripts_compile_and_report_live_events(tmp_path):
    import json
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from task_agent.console.invoice_remote_runtime import AzureInvoiceRuntime
    from test_invoice_agent import manifest
    context=manifest()
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    runtime=AzureInvoiceRuntime(compute_client=None,image='crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-agent@sha256:'+'a'*64,policies='unused')
    runtime.prepared[context.run_id]={'sandbox_id':context.sandbox_id,'public_key':private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()}
    scripts=[]
    def command(script,timeout=600):
        scripts.append(script)
        source=script.split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        compile(source,'<host-execute>','exec')
        if 'INVOICE_PROCESS_STARTED' in script:
            assert source.index("if not keyfile.exists():") < source.index('serialization.load_pem_private_key')
            assert "'envelope_hash':envelope_hash" in source
            import ast
            tree=ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='write' and node.args and isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,str) and node.args[0].value.startswith('import json,os,pathlib'):
                    compile(node.args[0].value,'<host-worker>','exec')
            import hashlib
            import os
            import pathlib
            import uuid
            from types import SimpleNamespace
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            (tmp_path/'key.pem').write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            launches=[]
            def launch(*args, **kwargs):
                launches.append(args)
                return SimpleNamespace(returncode=0)
            local_source=source.split('import hashlib\n',1)[1].replace('/var/lib/learningnemo-invoice-cache/runs/'+context.run_id,str(tmp_path))
            scope=dict(hashlib=hashlib,os=os,pathlib=pathlib,uuid=uuid,json=json,base64=base64,serialization=serialization,
                hashes=hashes,padding=padding,AESGCM=AESGCM,subprocess=SimpleNamespace(run=launch),
                cli=lambda *args:json.dumps({'id':str(uuid.UUID(context.sandbox_id))}))
            exec(local_source,scope)
            with pytest.raises(SystemExit) as repeated:
                exec(local_source,scope)
            assert repeated.value.code==0 and len(launches)==1
            (tmp_path/'launch-receipt.json').write_text(json.dumps({'envelope_hash':'0'*64,'sandbox_id':context.sandbox_id}))
            with pytest.raises(AssertionError,match='no replay'):
                exec(local_source,scope)
            assert len(launches)==1
            return 'INVOICE_PROCESS_STARTED'
        event={'sequence':1,'source':'agent-runtime','event_type':'agent-finished','run_id':context.run_id,'sandbox_id':context.sandbox_id}
        content=(json.dumps(event)+'\n').encode()
        return 'INVOICE_CHUNK '+json.dumps({'chunk':base64.b64encode(content).decode(),'exit':{'exit_code':0},'size':len(content)})
    runtime.command=command
    events=[]
    async def emit(event):events.append(event)
    await runtime.execute(context,emit)
    assert len(scripts)==2 and events[0]['delivery']=='live-poll'
    assert context.capability.get_secret_value() not in str(scripts)


def test_policy_mismatch_stops_prepared_sandbox(tmp_path):
    import json
    from task_agent.console.invoice_remote_runtime import AzureInvoiceRuntime
    (tmp_path/'invoice-planning-policy.yaml').write_text('version: 1\n')
    runtime=AzureInvoiceRuntime(compute_client=None,image='crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-agent@sha256:'+'a'*64,policies=tmp_path)
    runtime.command=lambda script: 'INVOICE_PREPARED '+json.dumps({'run_id':'b'*32,'policy':'version: 2\n'})
    stopped=[]
    runtime.stop=lambda kind,run_id:stopped.append((kind,run_id))
    with pytest.raises(RuntimeError,match='policy differs'):
        runtime.prepare('planning','b'*32)
    assert stopped==[('planning','b'*32)] and runtime.prepared=={}


@pytest.mark.parametrize('change', [None, {'run_id':'c'*32}, {'retained':23}, {'limit':28}, {'retained':True}])
def test_capacity_receipt_is_bound_and_never_creates_or_restarts(tmp_path, change):
    import json
    from task_agent.console.invoice_remote_runtime import AzureInvoiceRuntime
    from task_agent.control.invoice_sandbox import SandboxCapacityError
    (tmp_path/'invoice-planning-policy.yaml').write_text('version: 1\n')
    runtime=AzureInvoiceRuntime(compute_client=None,image='crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-agent@sha256:'+'a'*64,policies=tmp_path)
    receipt={'run_id':'b'*32,'reason':'sandbox-capacity','retained':24,'limit':24,**(change or {})}
    commands=[]
    def command(script):
        commands.append(script)
        return 'INVOICE_ADMISSION_BLOCKED '+json.dumps(receipt)
    runtime.command=command
    with pytest.raises(SandboxCapacityError if change is None else RuntimeError) as error:
        runtime.prepare('planning','b'*32)
    if change is not None: assert not isinstance(error.value,SandboxCapacityError)
    assert len(commands)==1 and runtime.prepared=={}