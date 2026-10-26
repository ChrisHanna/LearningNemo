"""Launch, collect and remove a dedicated read-only SQL administrator audit job."""

import argparse
import base64
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zlib

import requests

from task_agent.control.invoice_catalog import GRANTS

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / '.local/state/learningnemo'
GROUP = 'rg-learningnemo-invoice-dev'
JOB = 'job-learningnemo-sql-audit-1025'


def az(*args):
    output = subprocess.check_output(['az', *args, '-o', 'json', '--only-show-errors'], text=True, timeout=180)
    return json.loads(output) if output.strip() else None


def save(name, value):
    path = STATE / name
    path.write_text(json.dumps(value, indent=2, default=str))
    path.chmod(0o600)
    return path


def expected_schema():
    result = {'receipts': {}, 'modules': {}, 'objects': [], 'grants': {}}
    paths = sorted((ROOT / 'infra/next-phase/sql').glob('*.sql')) + sorted((ROOT / 'infra/next-phase/review-service').glob('*.sql'))
    objects = set()
    pattern = r'(?is)\bCREATE\s+(?:OR\s+ALTER\s+)?(PROCEDURE|PROC|VIEW|FUNCTION)\s+([\w]+\.[\w]+)\b'
    for path in paths:
        source = path.read_text()
        key = ('human-' if path.parent.name == 'review-service' else '') + path.name
        result['receipts'][key] = hashlib.sha256(source.encode()).hexdigest()
        objects.update(re.findall(r'(?i)\bCREATE\s+TABLE\s+([\w]+\.[\w]+)', source))
        for batch in re.split(r'(?im)^\s*GO\s*$', source):
            match = re.search(pattern, batch)
            if match:
                name = match.group(2)
                objects.add(name)
                result['modules'][name] = batch[match.start():].strip()
    result['objects'] = sorted(objects)
    for kind, groups in {'operator': ('controller','incident','jobs'), 'review': ('review',),
                         'planning': ('diagnostic','inference'), 'execution': ('broker','inference'),
                         'verifier': ('verifier',), 'simulator': ('simulator',)}.items():
        result['grants']['id-learningnemo-invoice-'+kind+'-dev'] = sorted({proc for group in groups for proc in GRANTS[group]})
    return result


def start():
    if az('account','show')['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise RuntimeError('Subscription differs')
    admin = az('identity','show','-g','rg-learningnemo-data-dev','-n','id-learningnemo-sql-admin-dev')
    operator = az('identity','show','-g',GROUP,'-n','id-learningnemo-invoice-operator-dev')
    app = az('containerapp','show','-g',GROUP,'-n','ca-nemo-invoice-operator-dev')
    env = {item['name']:item.get('value') for item in app['properties']['template']['containers'][0]['env']}
    source = (ROOT / 'scripts/audit-sql-readonly.py').read_text()
    compile(source, '<sql-audit>', 'exec')
    encoded = base64.b64encode(zlib.compress(source.encode())).decode()
    expected = base64.b64encode(zlib.compress(json.dumps(expected_schema()).encode())).decode()
    image = (STATE/'cloud-human.image.txt').read_text().strip()
    assert re.fullmatch(r'crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/cloud-human@sha256:[a-f0-9]{64}', image)
    document = {
        'location':'eastus', 'tags':{'owner':'learningnemo','purpose':'read-only-sql-audit','disposable':'true','expiresAt':(datetime.now(UTC)+timedelta(hours=1)).isoformat()},
        'identity':{'type':'UserAssigned','userAssignedIdentities':{admin['id']:{},operator['id']:{}}},
        'properties':{
            'environmentId':app['properties']['environmentId'],
            'configuration':{'triggerType':'Manual','replicaTimeout':300,'replicaRetryLimit':0,
                'manualTriggerConfig':{'parallelism':1,'replicaCompletionCount':1},
                'registries':[{'server':image.split('/')[0],'identity':operator['id']}]},
            'template':{'containers':[{'name':'auditor','image':image,'resources':{'cpu':0.25,'memory':'0.5Gi'},
                'command':['python','-u','-c',"exec(__import__('zlib').decompress(__import__('base64').b64decode('"+encoded+"')))"],
                'env':[{'name':'AZURE_CLIENT_ID','value':admin['clientId']},
                       {'name':'LEARNINGNEMO_SQL_SERVER','value':env['LEARNINGNEMO_SQL_SERVER']},
                       {'name':'SQL_AUDIT_EXPECTED','value':expected}]}]},
        },
    }
    save('sql-audit.cleanup.json', {'job':JOB,'resource_group':GROUP,'admin_isolation_scope':admin.get('isolationScope'),'admin_id':admin['id']})
    path = save('sql-audit.job.json',document)
    az('containerapp','job','create','-g',GROUP,'-n',JOB,'--yaml',str(path))
    execution = az('containerapp','job','start','-g',GROUP,'-n',JOB)
    save('sql-audit.execution.json',execution)
    print('STARTED read-only SQL audit:',execution['name'])


def collect():
    execution = json.loads((STATE/'sql-audit.execution.json').read_text())['name']
    job = az('containerapp','job','show','-g',GROUP,'-n',JOB)
    resource = 'https://management.azure.com' + job['id']
    api = '2023-11-02-preview'
    replicas = az('rest','--method','get','--url',resource+'/executions/'+execution+'/replicas?api-version='+api)['value']
    if not replicas:
        raise RuntimeError('No audit replica logs available')
    token = az('rest','--method','post','--url',resource+'/getAuthToken?api-version='+api)['properties']['token']
    endpoint = job['properties']['eventStreamEndpoint'].split('/subscriptions/')[0]
    subscription = job['id'].split('/')[2]
    url = endpoint+'/subscriptions/'+subscription+'/resourceGroups/'+GROUP+'/jobs/'+JOB+'/executions/'+execution+'/replicas/'+replicas[0]['name']+'/containers/auditor/logstream'
    lines = []
    with requests.get(url,headers={'Authorization':'Bearer '+token},
                      params={'follow':'true','output':'json','tailLines':300},timeout=120,stream=True) as response:
        response.raise_for_status()
        for raw in response.iter_lines():
            if not raw:
                continue
            line = raw.decode()
            lines.append(line)
            if re.search(r'SQL_AUDIT (\d+)/\1 ',line):
                break
    logs = '\n'.join(lines)
    (STATE/'sql-audit.logs.jsonl').write_text(logs)
    (STATE/'sql-audit.logs.jsonl').chmod(0o600)
    chunks = {}
    total = None
    for line in logs.splitlines():
        item = json.loads(line)
        text = item.get('Log','')
        match = re.search(r'SQL_AUDIT (\d+)/(\d+) ([A-Za-z0-9+/=]+)', text)
        if match:
            index, total = int(match[1]),int(match[2])
            chunks[index] = match[3]
    if total is None or set(chunks) != set(range(1,total+1)):
        raise RuntimeError('Audit result incomplete; inspect saved logs before cleanup')
    report = json.loads(zlib.decompress(base64.b64decode(''.join(chunks[index] for index in sorted(chunks)))))
    save('sql-audit.result.json',report)
    print(json.dumps({key:report[key] for key in ('database','migration_comparison','missing_objects','module_binding_errors','invoice_grant_drift','errors')},indent=2))
    print('RESULT saved:',STATE/'sql-audit.result.json')


def cleanup():
    az('containerapp','job','delete','-g',GROUP,'-n',JOB,'--yes')
    note = json.loads((STATE/'sql-audit.cleanup.json').read_text())
    admin = az('identity','show','--ids',note['admin_id'])
    assert admin.get('isolationScope') == note['admin_isolation_scope']
    jobs = az('containerapp','job','list','-g',GROUP)
    assert not any(item['name'] == JOB for item in jobs)
    save('sql-audit.cleanup-completed.json',note)
    (STATE/'sql-audit.cleanup.json').unlink()
    print('PASS audit job removed; existing identity isolation unchanged')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('start','collect','cleanup'))
    args = parser.parse_args()
    {'start':start,'collect':collect,'cleanup':cleanup}[args.action]()