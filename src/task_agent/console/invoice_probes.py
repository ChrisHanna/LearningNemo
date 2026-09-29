"""Fixed sandbox network probes; no model, SQL token, or plan authority is issued."""

import asyncio
import json
import re
from uuid import UUID

from task_agent.console.invoice_remote_runtime import HOST_PREFIX
from task_agent.control.invoice_sandbox import sandbox_name


INVESTIGATIONS = {
    'query-draft': 'planning',
    'allowed-file': 'planning',
    'denied-file': 'planning',
    'sql-denied': 'planning',
    'write-app-denied': 'planning',
    'approved-api': 'planning',
    'external-api-denied': 'planning',
    'symlink-escape-denied': 'planning',
}

PLANNING_HOST = 'ca-nemo-invoice-planning-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io'


def _has_denial_evidence(result, *terms):
    return any(isinstance(line, str) and 'OCSF' in line and 'DENIED' in line and
               all(str(term) in line for term in terms)
               for line in result.get('denial_evidence', []))


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


def investigation_script(scenario, run_id, sandbox_id, *, sql_host=None):
    if scenario not in INVESTIGATIONS or not re.fullmatch('[a-f0-9]{32}', run_id):
        raise ValueError('fixed investigation context required')
    if scenario == 'sql-denied' and (not isinstance(sql_host, str) or not re.fullmatch(r'[a-z0-9-]{1,63}\.database\.windows\.net', sql_host)):
        raise ValueError('fixed Azure SQL host required')
    identifier = str(UUID(sandbox_id))
    name = sandbox_name(INVESTIGATIONS[scenario], run_id)
    if scenario == 'query-draft':
        worker = '''import hashlib,json,os,pathlib
path=pathlib.Path('/tmp/investigation.sql')
query='SELECT TOP (25) order_id, amount_cents, imported_at FROM invoice_lab.invoices ORDER BY imported_at DESC'
path.write_text(query+'\\n',encoding='utf-8')
content=path.read_text(encoding='utf-8').strip()
result={'scenario':'query-draft','outcome':'allowed','operation':'generate-read-query','path':str(path),'query_class':'SELECT','statement_hash':hashlib.sha256(content.encode()).hexdigest(),'bytes':len(content.encode()),'query_executed':False}
print('INVESTIGATION_RESULT '+json.dumps({'uid':os.getuid(),**result}))
'''
    elif scenario == 'allowed-file':
        worker = '''import hashlib,json,os,pathlib
path=pathlib.Path('/app/configs/invoice-planning.yml')
content=path.read_bytes()
result={'scenario':'allowed-file','outcome':'allowed','operation':'read-file','path':str(path),'content_hash':hashlib.sha256(content).hexdigest(),'bytes':len(content)}
print('INVESTIGATION_RESULT '+json.dumps({'uid':os.getuid(),**result}))
'''
    elif scenario == 'denied-file':
        worker = '''import errno,json,os,pathlib
path=pathlib.Path('/boundary/private-investigation.txt')
try:
    path.read_bytes()
    outcome,category='unexpectedly-allowed','filesystem-policy-failed'
except PermissionError as error:
    outcome,category='policy-denied','filesystem-policy-denied' if error.errno in (errno.EACCES,errno.EPERM) else 'filesystem-error'
except OSError:
    outcome,category='unconfirmed','filesystem-error'
result={'scenario':'denied-file','outcome':outcome,'operation':'read-file','path':str(path),'error_category':category}
print('INVESTIGATION_RESULT '+json.dumps({'uid':os.getuid(),**result}))
'''
    elif scenario == 'sql-denied':
        worker = f'''import errno,json,os,socket
host={sql_host!r}
try:
    connection=socket.create_connection((host,1433),timeout=10)
    connection.close()
    outcome,category='unexpectedly-allowed','network-policy-failed'
except socket.gaierror:
    outcome,category='unconfirmed','dns-failed'
except TimeoutError:
    outcome,category='unconfirmed','network-timeout'
except PermissionError as error:
    outcome,category='policy-denied','network-policy-denied' if error.errno in (errno.EACCES,errno.EPERM) else 'network-error'
except OSError as error:
    outcome='policy-denied' if error.errno in (errno.EACCES,errno.EPERM) else 'unconfirmed'
    category='network-policy-denied' if outcome=='policy-denied' else 'network-error'
result={{'scenario':'sql-denied','outcome':outcome,'operation':'connect-sql','destination':host,'destination_port':1433,'error_category':category,'database_capability_issued':False}}
print('INVESTIGATION_RESULT '+json.dumps({{'uid':os.getuid(),**result}}))
'''
    elif scenario == 'write-app-denied':
        worker = '''import errno,json,os,pathlib
path=pathlib.Path('/app/investigation-write-attempt.txt')
try:
    path.write_text('bounded write probe\\n',encoding='utf-8')
    outcome,category='unexpectedly-allowed','filesystem-policy-failed'
    path.unlink(missing_ok=True)
except PermissionError as error:
    outcome,category='policy-denied','filesystem-policy-denied' if error.errno in (errno.EACCES,errno.EPERM) else 'filesystem-error'
except OSError:
    outcome,category='unconfirmed','filesystem-error'
result={'scenario':'write-app-denied','outcome':outcome,'operation':'write-file','path':str(path),'error_category':category}
print('INVESTIGATION_RESULT '+json.dumps({'uid':os.getuid(),**result}))
'''
    elif scenario == 'approved-api':
        worker = f'''import json,os,httpx
host={PLANNING_HOST!r}
route='/v2/invoice/tools/invoice_summary'
try:
    with httpx.Client(timeout=20,follow_redirects=False,trust_env=True) as client:
        response=client.post('https://'+host+route,json={{}})
    status=response.status_code
    outcome,category=('allowed','service-authentication-required') if status==401 else ('unconfirmed','unexpected-http-status')
except httpx.HTTPError:
    status=0
    outcome,category='unconfirmed','network-error'
result={{'scenario':'approved-api','outcome':outcome,'operation':'call-planning-api','destination':host,'destination_port':443,'method':'POST','route':route,'http_status':status,'credential_issued':False,'error_category':category}}
print('INVESTIGATION_RESULT '+json.dumps({{'uid':os.getuid(),**result}}))
'''
    elif scenario == 'external-api-denied':
        worker = '''import json,os,httpx
host='example.com'
try:
    with httpx.Client(timeout=20,follow_redirects=False,trust_env=True) as client:
        response=client.get('https://'+host+'/')
    status=response.status_code
    outcome,category='unexpectedly-allowed','network-policy-failed'
except httpx.ProxyError as error:
    status=403 if '403' in str(error) else 0
    outcome,category=('policy-denied','network-policy-denied') if status==403 else ('unconfirmed','network-error')
except httpx.HTTPError:
    status=0
    outcome,category='unconfirmed','network-error'
result={'scenario':'external-api-denied','outcome':outcome,'operation':'call-external-api','destination':host,'destination_port':443,'method':'GET','route':'/','http_status':status,'credential_issued':False,'error_category':category}
print('INVESTIGATION_RESULT '+json.dumps({'uid':os.getuid(),**result}))
'''
    else:
        worker = '''import errno,json,os,pathlib
path=pathlib.Path('/tmp/private-investigation-link')
target=pathlib.Path('/boundary/private-investigation.txt')
path.unlink(missing_ok=True)
path.symlink_to(target)
try:
    path.read_bytes()
    outcome,category='unexpectedly-allowed','filesystem-policy-failed'
except PermissionError as error:
    outcome,category='policy-denied','filesystem-policy-denied' if error.errno in (errno.EACCES,errno.EPERM) else 'filesystem-error'
except OSError:
    outcome,category='unconfirmed','filesystem-error'
finally:
    path.unlink(missing_ok=True)
result={'scenario':'symlink-escape-denied','outcome':outcome,'operation':'follow-symlink','path':str(path),'target':str(target),'link_created':True,'link_removed':not path.exists(),'error_category':category}
print('INVESTIGATION_RESULT '+json.dumps({'uid':os.getuid(),**result}))
'''
    denial_destination = sql_host if scenario == 'sql-denied' else 'example.com' if scenario == 'external-api-denied' else None
    denial_port = 1433 if scenario == 'sql-denied' else 443 if scenario == 'external-api-denied' else None
    compile(worker, f'<invoice-investigation-{scenario}>', 'exec')
    return HOST_PREFIX + f'''
directory=pathlib.Path('/var/lib/learningnemo-invoice-cache/runs/{run_id}')
record=json.loads(cli('sandbox','get',{name!r},'--output','json'))
assert record['name']=={name!r} and uuid.UUID(record['id']).hex=={sandbox_id!r} and record['phase']=='Ready'
receipt=directory/'investigation-receipt.json'
if receipt.exists():
    result=json.loads(receipt.read_text())
    assert result['run_id']=={run_id!r} and result['sandbox_id']=={sandbox_id!r} and result['scenario']=={scenario!r}
    print('INVESTIGATION_RECEIPT '+json.dumps(result))
    raise SystemExit(0)
with open(os.open(directory/'investigation-started',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as marker: marker.write({scenario!r})
console=pathlib.Path('/home/sawadmin/.local/state/openshell/vm/sandboxes/{identifier}/rootfs-console.log')
offset=console.stat().st_size
output=cli('sandbox','exec','--name',{name!r},'--no-tty','--timeout','60','--','/opt/venv/bin/python','-c',{worker!r},timeout=90)
rows=[json.loads(line.split('INVESTIGATION_RESULT ',1)[1]) for line in output.splitlines() if line.startswith('INVESTIGATION_RESULT ')]
assert len(rows)==1
with console.open() as source:
    source.seek(offset)
    lines=source.read(65536).splitlines()
denial_destination={denial_destination!r}
denial_port={denial_port!r}
denials=[] if denial_destination is None else [line for line in lines if 'OCSF' in line and 'DENIED' in line and denial_destination in line and str(denial_port) in line and not any(value in line.lower() for value in ('dns resolution failed','timeout','ssrf'))]
result={{'run_id':{run_id!r},'sandbox_id':{sandbox_id!r},**rows[0],'denial_evidence':denials[-4:]}}
with open(os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as destination: json.dump(result,destination)
print('INVESTIGATION_RECEIPT '+json.dumps(result))
PY
'''


def investigation_confirmed(scenario, result):
    digest = re.compile(r'^[a-f0-9]{64}$')
    if scenario == 'query-draft':
        return result.get('outcome') == 'allowed' and result.get('path') == '/tmp/investigation.sql' and result.get('query_class') == 'SELECT' and result.get('query_executed') is False and bool(digest.fullmatch(result.get('statement_hash', '')))
    if scenario == 'allowed-file':
        return result.get('outcome') == 'allowed' and result.get('path') == '/app/configs/invoice-planning.yml' and bool(digest.fullmatch(result.get('content_hash', '')))
    if scenario == 'denied-file':
        return result.get('outcome') == 'policy-denied' and result.get('path') == '/boundary/private-investigation.txt' and result.get('error_category') == 'filesystem-policy-denied'
    if scenario == 'sql-denied':
        destination = result.get('destination', '')
        return result.get('outcome') == 'policy-denied' and bool(re.fullmatch(r'[a-z0-9-]{1,63}\.database\.windows\.net', destination)) and result.get('destination_port') == 1433 and result.get('error_category') == 'network-policy-denied' and result.get('database_capability_issued') is False and _has_denial_evidence(result, destination, 1433)
    if scenario == 'write-app-denied':
        return result.get('outcome') == 'policy-denied' and result.get('path') == '/app/investigation-write-attempt.txt' and result.get('error_category') == 'filesystem-policy-denied'
    if scenario == 'approved-api':
        return result.get('outcome') == 'allowed' and result.get('destination') == PLANNING_HOST and result.get('destination_port') == 443 and result.get('method') == 'POST' and result.get('route') == '/v2/invoice/tools/invoice_summary' and result.get('http_status') == 401 and result.get('credential_issued') is False
    if scenario == 'external-api-denied':
        return result.get('outcome') == 'policy-denied' and result.get('destination') == 'example.com' and result.get('destination_port') == 443 and result.get('error_category') == 'network-policy-denied' and result.get('credential_issued') is False and _has_denial_evidence(result, 'example.com', 443)
    if scenario == 'symlink-escape-denied':
        return result.get('outcome') == 'policy-denied' and result.get('path') == '/tmp/private-investigation-link' and result.get('target') == '/boundary/private-investigation.txt' and result.get('link_created') is True and result.get('link_removed') is True and result.get('error_category') == 'filesystem-policy-denied'
    return False


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
    confirmed = result.get('requests') == expected and _has_denial_evidence(result, forbidden)
    return {**result, 'kind': kind, 'outcome': 'denied' if confirmed else 'unconfirmed', 'enforced_by': 'OpenShell',
            'policy_hash': prepared['policy_hash'], 'actor': 'controlled-probe', 'agent_requested': False,
            'probe_capability_issued': False, 'scope': 'same-agent-sandbox', 'agent_authority_revoked': True,
            'executed_in_sandbox': True, 'sandbox_runtime': 'OpenShell MicroVM', 'sandbox_executor': '/opt/venv/bin/python',
            'sandbox_stopped': False, 'sandbox_retained': False}


async def run_probe(runtime, kind, run_id, emit, *, sql_host=None):
    await emit('Preparing a fresh OpenShell sandbox', event_type='preparing-sandbox')
    policy_kind = INVESTIGATIONS.get(kind, kind)
    prepared = await asyncio.to_thread(runtime.prepare, policy_kind, run_id)
    try:
        await emit('OpenShell sandbox identity and policy confirmed', event_type='sandbox-bound',
            sandbox_id=prepared['sandbox_id'], policy_hash=prepared['policy_hash'])
        if kind in INVESTIGATIONS:
            await emit('Executing the bounded investigation inside the OpenShell sandbox',
                event_type='investigation-started', sandbox_id=prepared['sandbox_id'])
            output = await asyncio.to_thread(runtime.command, investigation_script(kind, run_id, prepared['sandbox_id'], sql_host=sql_host), 120)
            records = [json.loads(line.split('INVESTIGATION_RECEIPT ', 1)[1]) for line in output.splitlines() if line.startswith('INVESTIGATION_RECEIPT ')]
            if len(records) != 1:
                raise RuntimeError('investigation observation unconfirmed')
            result = records[0]
            if result.get('run_id') != run_id or result.get('sandbox_id') != prepared['sandbox_id'] or type(result.get('uid')) is not int or result['uid'] <= 0:
                raise RuntimeError('investigation identity differs')
            confirmed = investigation_confirmed(kind, result)
            await emit('Bound sandbox execution receipt captured', event_type='investigation-recorded',
                sandbox_id=prepared['sandbox_id'], uid=result['uid'], outcome=result.get('outcome'))
            return {**result, 'outcome': result.get('outcome') if confirmed else 'unconfirmed', 'evidence_mode': 'live',
                'enforced_by': 'OpenShell', 'policy_hash': prepared['policy_hash'], 'actor': 'sandbox-investigation',
                'agent_requested': False, 'model_involved': False, 'executed_in_sandbox': True,
                'sandbox_runtime': 'OpenShell MicroVM', 'sandbox_executor': '/opt/venv/bin/python',
                'sandbox_stopped': True, 'sandbox_retained': True}
        await emit('Executing the controlled route probe inside the OpenShell sandbox',
            event_type='probe-started', sandbox_id=prepared['sandbox_id'])
        output = await asyncio.to_thread(runtime.command, probe_script(kind, run_id, prepared['sandbox_id']), 120)
        records = [json.loads(line.split('PROBE_RESULT ', 1)[1]) for line in output.splitlines() if line.startswith('PROBE_RESULT ')]
        if len(records) != 1:
            raise RuntimeError('probe observation unconfirmed')
        result = records[0]
        if result['run_id'] != run_id or result['sandbox_id'] != prepared['sandbox_id'] or result['uid'] == 0:
            raise RuntimeError('probe identity differs')
        requests = result['requests']
        confirmed = len(requests) == 2 and requests[0]['status'] == 401 and requests[1]['status'] == 403 and _has_denial_evidence(result, requests[1]['tool'])
        await emit('Bound sandbox probe receipt captured', event_type='probe-recorded',
            sandbox_id=prepared['sandbox_id'], uid=result['uid'], outcome='denied' if confirmed else 'unconfirmed')
        return {**result, 'outcome': 'denied' if confirmed else 'unconfirmed', 'enforced_by': 'OpenShell',
            'evidence_mode': 'live', 'policy_hash': prepared['policy_hash'], 'actor': 'controlled-probe', 'agent_requested': False,
            'database_capability_issued': False, 'scope': kind + ' sandbox route policy',
            'executed_in_sandbox': True, 'sandbox_runtime': 'OpenShell MicroVM', 'sandbox_executor': '/opt/venv/bin/python',
            'sandbox_stopped': True, 'sandbox_retained': True}
    finally:
        await asyncio.to_thread(runtime.stop, policy_kind, run_id)
        script = HOST_PREFIX + f"record=json.loads(cli('sandbox','get',{sandbox_name(policy_kind,run_id)!r},'--output','json'))\nassert uuid.UUID(record['id']).hex=={prepared['sandbox_id']!r} and record['phase']=='Stopped'\nprint('PROBE_STOP_CONFIRMED')\nPY\n"
        stopped = await asyncio.to_thread(runtime.command, script, 60)
        if 'PROBE_STOP_CONFIRMED' not in stopped: raise RuntimeError('probe stop unconfirmed')
        await emit('OpenShell sandbox stop confirmed; retained for evidence', event_type='sandbox-stopped',
            sandbox_id=prepared['sandbox_id'])