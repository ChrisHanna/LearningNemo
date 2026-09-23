"""Fixed sandbox network probes; no model, SQL token, or plan authority is issued."""

import asyncio
import json
import re
from uuid import UUID

from task_agent.console.invoice_remote_runtime import HOST_PREFIX
from task_agent.control.invoice_sandbox import sandbox_name


def probe_script(kind, run_id, sandbox_id, *, attached=False):
    if kind not in ('planning', 'execution') or not re.fullmatch('[a-f0-9]{32}', run_id):
        raise ValueError('fixed probe context required')
    identifier = str(UUID(sandbox_id))
    name = sandbox_name(kind, run_id)
    host = f'ca-nemo-invoice-{kind}-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io'
    permitted = 'invoice_summary' if kind == 'planning' else 'execute_step'
    forbidden = 'execute_step' if kind == 'planning' else 'invoice_summary'
    body = {'step_id': 1, 'operation': 'invoice.rebuild-total.v1', 'target': '0'*32, 'expected_revision': 1, 'duplicate_set_hash': None}
    worker = f'''import json,os,httpx
assert os.getuid()!=0
results=[]
with httpx.Client(timeout=20,follow_redirects=False,trust_env=True) as client:
    for tool in ({permitted!r},{forbidden!r}):
        try:
            response=client.post('https://{host}/v2/invoice/tools/'+tool,json={body!r} if tool=='execute_step' else {{}})
            results.append({{'tool':tool,'status':response.status_code}})
        except httpx.ProxyError as error:
            results.append({{'tool':tool,'status':403 if '403' in str(error) else 0}})
print('PROBE_REQUESTS '+json.dumps({{'uid':os.getuid(),'requests':results}}))
'''
    return HOST_PREFIX + f'''
directory=pathlib.Path('/var/lib/learningnemo-invoice-cache/runs/{run_id}')
record=json.loads(cli('sandbox','get',{name!r},'--output','json'))
assert record['name']=={name!r} and uuid.UUID(record['id']).hex=={sandbox_id!r} and record['phase']=='Ready', 'bound sandbox is not running; no restart'
if {attached!r}:
    assert not (directory/'key.pem').exists() and not (directory/'manifest.json').exists()
    assert json.loads((directory/'exit.json').read_text())=={{'exit_code':0}}, 'agent has not finished successfully'
    assert json.loads((directory/'launch-receipt.json').read_text())['sandbox_id']=={sandbox_id!r}
receipt=directory/'probe-receipt.json'
if receipt.exists():
    result=json.loads(receipt.read_text())
    assert result['run_id']=={run_id!r} and result['sandbox_id']=={sandbox_id!r}
    print('PROBE_RESULT '+json.dumps(result))
    raise SystemExit(0)
with open(os.open(directory/'probe-started',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as marker: marker.write({kind!r})
path=pathlib.Path('/home/sawadmin/.local/state/openshell/vm/sandboxes/{identifier}/rootfs-console.log')
offset=path.stat().st_size
output=cli('sandbox','exec','--name',{name!r},'--no-tty','--timeout','60','--','/opt/venv/bin/python','-c',{worker!r},timeout=90)
rows=[json.loads(line.split('PROBE_REQUESTS ',1)[1]) for line in output.splitlines() if line.startswith('PROBE_REQUESTS ')]
assert len(rows)==1
with path.open() as source:
    source.seek(offset)
    lines=source.read(65536).splitlines()
denials=[line for line in lines if 'DENIED' in line and {forbidden!r} in line and 'OCSF' in line and not any(value in line.lower() for value in ('dns resolution failed','timeout','ssrf'))]
result={{'run_id':{run_id!r},'sandbox_id':{sandbox_id!r},**rows[0],'denial_evidence':denials[-2:]}}
with open(os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as destination: json.dump(result,destination)
print('PROBE_RESULT '+json.dumps(result))
PY
'''


async def run_prepared_probe(runtime, kind, run_id, prepared):
    if prepared.get('run_id') != run_id or prepared.get('name') != sandbox_name(kind, run_id):
        raise ValueError('probe must use the exact agent run sandbox')
    output = await asyncio.to_thread(runtime.command, probe_script(kind, run_id, prepared['sandbox_id'], attached=True), 120)
    records = [json.loads(line.split('PROBE_RESULT ', 1)[1]) for line in output.splitlines() if line.startswith('PROBE_RESULT ')]
    if len(records) != 1:
        raise RuntimeError('bound probe result unconfirmed; no replay')
    result = records[0]
    control, forbidden = ('invoice_summary', 'execute_step') if kind == 'planning' else ('execute_step', 'invoice_summary')
    if result.get('run_id') != run_id or result.get('sandbox_id') != prepared['sandbox_id'] or type(result.get('uid')) is not int or result['uid'] <= 0:
        raise RuntimeError('bound probe identity differs')
    expected = [{'tool': control, 'status': 401}, {'tool': forbidden, 'status': 403}]
    confirmed = result.get('requests') == expected and any('OCSF' in line and 'DENIED' in line and forbidden in line for line in result.get('denial_evidence', []))
    return {**result, 'kind': kind, 'outcome': 'denied' if confirmed else 'unconfirmed', 'enforced_by': 'OpenShell',
            'policy_hash': prepared['policy_hash'], 'actor': 'controlled-probe', 'agent_requested': False,
            'probe_capability_issued': False, 'scope': 'same-agent-sandbox', 'agent_authority_revoked': True,
            'sandbox_stopped': False}


async def run_probe(runtime, kind, run_id, emit):
    await emit('Preparing isolated sandbox')
    prepared = await asyncio.to_thread(runtime.prepare, kind, run_id)
    try:
        await emit('Policy and sandbox identity confirmed')
        output = await asyncio.to_thread(runtime.command, probe_script(kind, run_id, prepared['sandbox_id']), 120)
        records = [json.loads(line.split('PROBE_RESULT ', 1)[1]) for line in output.splitlines() if line.startswith('PROBE_RESULT ')]
        if len(records) != 1:
            raise RuntimeError('probe observation unconfirmed')
        result = records[0]
        if result['run_id'] != run_id or result['sandbox_id'] != prepared['sandbox_id'] or result['uid'] == 0:
            raise RuntimeError('probe identity differs')
        requests = result['requests']
        confirmed = len(requests) == 2 and requests[0]['status'] == 401 and requests[1]['status'] == 403 and bool(result['denial_evidence'])
        return {**result, 'outcome': 'denied' if confirmed else 'unconfirmed', 'enforced_by': 'OpenShell',
            'policy_hash': prepared['policy_hash'], 'actor': 'controlled-probe', 'agent_requested': False,
            'database_capability_issued': False, 'scope': kind + ' sandbox route policy', 'sandbox_stopped': True}
    finally:
        await asyncio.to_thread(runtime.stop, kind, run_id)
        script = HOST_PREFIX + f"record=json.loads(cli('sandbox','get',{sandbox_name(kind,run_id)!r},'--output','json'))\nassert uuid.UUID(record['id']).hex=={prepared['sandbox_id']!r} and record['phase']=='Stopped'\nprint('PROBE_STOP_CONFIRMED')\nPY\n"
        stopped = await asyncio.to_thread(runtime.command, script, 60)
        if 'PROBE_STOP_CONFIRMED' not in stopped: raise RuntimeError('probe stop unconfirmed')
        await emit('Sandbox stop confirmed; retained')