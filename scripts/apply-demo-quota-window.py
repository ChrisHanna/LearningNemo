"""Apply one leased, self-expiring quota migration in an isolated admin job."""

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

ROOT=Path(__file__).resolve().parents[1]
STATE=Path.home()/'.local/state/learningnemo'
GROUP='rg-learningnemo-invoice-dev'
JOB='job-learningnemo-quota-1025'
MIGRATION='021_invoice_demo_quota_window.sql'

REMOTE = r'''
import base64,json,os,re,hashlib,uuid,zlib
from datetime import UTC,datetime
from task_agent.control.mssql_client import build_connection_string,managed_identity_connect
expiry=datetime.fromisoformat(os.environ['MIGRATION_LEASE'])
assert 0<(expiry-datetime.now(UTC)).total_seconds()<=3600,'Migration lease expired'
assert datetime.now(UTC)<datetime(2026,10,27,4,tzinfo=UTC),'Demo window already expired'
source=zlib.decompress(base64.b64decode(os.environ['QUOTA_SQL'])).decode()
digest=hashlib.sha256(source.encode()).hexdigest()
assert digest==os.environ['QUOTA_SHA256']
identifier='human-021_invoice_demo_quota_window.sql'
server=os.environ['LEARNINGNEMO_SQL_SERVER'];identity=os.environ['AZURE_CLIENT_ID']
cs=build_connection_string(server=server,database='learningnemo',client_id=identity,application_name='LearningNeMoDemoQuotaMigration')
def connect():return managed_identity_connect(cs,identity,timeout_seconds=90,server=server)
def rows(connection,sql,*parameters):
    with connection.cursor() as cursor:
        cursor.execute(sql,*parameters)
        if cursor.description is None:return []
        names=[column[0] for column in cursor.description]
        return [dict(zip(names,row)) for row in cursor.fetchall()]
def transaction(connection):
    rows(connection,'SET XACT_ABORT ON; SET LOCK_TIMEOUT 15000; BEGIN TRANSACTION;')
    rows(connection,"DECLARE @lock int; EXEC @lock=sys.sp_getapplock @Resource=N'learningnemo-human-migrations',@LockMode=N'Exclusive',@LockOwner=N'Transaction',@LockTimeout=15000; IF @lock<0 THROW 52090,'migration_busy',1;")
def rollback(connection):rows(connection,'IF @@TRANCOUNT>0 ROLLBACK TRANSACTION;')
def install(connection,sql_source=source):
    for batch in re.split(r'(?im)^\s*GO\s*$',sql_source):
        if batch.strip():rows(connection,batch)
def ledger(connection):return rows(connection,'SELECT JobId,GuestHash,CreatedAt FROM control.InvoiceGuestLaunches WITH(TABLOCKX,HOLDLOCK) ORDER BY JobId')
def admit(connection,job,guest):
    result=rows(connection,'EXEC control.usp_admit_invoice_guest_launch @job_id=?,@guest_hash=?',job,guest)
    assert len(result)==1
    return result[0]
def normalized(text):return re.sub(r'\s+',' ',re.sub(r'(?i)^\s*CREATE\s+(?:OR\s+ALTER\s+)?PROCEDURE\s+','PROCEDURE ',text)).strip().rstrip(';')
expected=next(batch.strip() for batch in re.split(r'(?im)^\s*GO\s*$',source) if 'CREATE OR ALTER PROCEDURE' in batch)
test_guest=hashlib.sha256(uuid.uuid4().bytes).hexdigest()
with connect() as connection:
    connection.autocommit=True
    transaction(connection)
    try:
        before=ledger(connection)
        install(connection)
        current=rows(connection,'SELECT COUNT(*) AS total FROM control.InvoiceGuestLaunches WHERE CreatedAt>=CONVERT(date,SYSUTCDATETIME())')[0]['total']
        assert current<=96,'Insufficient headroom for rollback-only quota verification'
        first=uuid.uuid4().hex
        for index in range(4):
            result=admit(connection,first if index==0 else uuid.uuid4().hex,test_guest)
            assert result['admitted'] and result['visitor_limit'] is None and result['global_limit']==100
        repeat=admit(connection,first,test_guest)
        assert repeat['visitor_launches_last_hour']==4,'Duplicate job counted twice'
        for _ in range(100-current-4):assert admit(connection,uuid.uuid4().hex,test_guest)['admitted']
        denied=admit(connection,uuid.uuid4().hex,test_guest)
        assert not denied['admitted'] and denied['reason']=='daily' and denied['global_launches_today']==100
        install(connection,source.replace('2026-10-27T04:00:00.000','2000-01-01T00:00:00.000'))
        expired=admit(connection,uuid.uuid4().hex,test_guest)
        assert not expired['admitted'] and expired['visitor_limit']==3 and expired['global_limit']==25
    finally:rollback(connection)
    assert rows(connection,'SELECT COUNT(*) AS total FROM control.InvoiceGuestLaunches WHERE GuestHash=?',test_guest)[0]['total']==0
    transaction(connection)
    try:
        before=ledger(connection)
        receipts=rows(connection,'SELECT MigrationId,ContentHash FROM control.SchemaMigrations ORDER BY MigrationId')
        old=dict((row['MigrationId'],row['ContentHash']) for row in receipts)
        permissions=rows(connection,'SELECT grantee_principal_id,class,major_id,minor_id,permission_name,state FROM sys.database_permissions ORDER BY grantee_principal_id,class,major_id,minor_id,permission_name,state')
        if identifier in old:
            assert old[identifier]==digest,'Applied migration source differs'
        else:
            install(connection)
            rows(connection,'INSERT control.SchemaMigrations(MigrationId,ContentHash) VALUES(?,?)',identifier,digest)
        assert ledger(connection)==before,'Quota accounting changed during DDL'
        after=rows(connection,'SELECT MigrationId,ContentHash FROM control.SchemaMigrations ORDER BY MigrationId')
        persisted=dict((row['MigrationId'],row['ContentHash']) for row in after)
        assert all(persisted[name]==value for name,value in old.items()) and persisted[identifier]==digest
        assert permissions==rows(connection,'SELECT grantee_principal_id,class,major_id,minor_id,permission_name,state FROM sys.database_permissions ORDER BY grantee_principal_id,class,major_id,minor_id,permission_name,state')
        definition=rows(connection,"SELECT definition,execute_as_principal_id FROM sys.sql_modules WHERE object_id=OBJECT_ID(N'control.usp_admit_invoice_guest_launch')")[0]
        assert normalized(definition['definition'])==normalized(expected) and definition['execute_as_principal_id']==-2
        rows(connection,'COMMIT TRANSACTION;')
    except BaseException:
        rollback(connection);raise
with connect() as connection:
    connection.autocommit=True
    receipt=rows(connection,'SELECT ContentHash FROM control.SchemaMigrations WHERE MigrationId=?',identifier)
    assert len(receipt)==1 and receipt[0]['ContentHash']==digest
    existing=rows(connection,'SELECT TOP(1) JobId,GuestHash FROM control.InvoiceGuestLaunches ORDER BY CreatedAt DESC')[0]
    result=admit(connection,existing['JobId'],existing['GuestHash'])
    assert result['admitted'] and result['visitor_limit'] is None and result['global_limit']==100
    report={'migration_id':identifier,'source_sha256':digest,'global_daily_limit':result['global_limit'],
        'per_guest_hourly_limit':result['visitor_limit'],'launches_today':result['global_launches_today'],
        'window_expires_utc':'2026-10-27T04:00:00Z','after_window':{'visitor_limit':3,'global_limit':25},
        'existing_launch_records_preserved':len(before),'quota_test_rows_rolled_back':True,
        'four_launches_same_guest_allowed':True,'duplicate_job_not_recounted':True,'launch_101_blocked':True,
        'expiry_fallback_verified':True,'permissions_unchanged':True,'migration_verified_independent_connection':True}
payload=base64.b64encode(zlib.compress(json.dumps(report).encode())).decode()
print('QUOTA_WINDOW '+payload,flush=True)
from http.server import BaseHTTPRequestHandler,HTTPServer
class Result(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.end_headers();self.wfile.write(json.dumps(report).encode())
HTTPServer(('127.0.0.1',8766),Result).serve_forever()
'''


def az(*args):
    value=subprocess.check_output(['az',*args,'-o','json','--only-show-errors'],text=True,timeout=180)
    return json.loads(value) if value.strip() else None


def save(name,value):
    path=STATE/('demo-quota.'+name+'.json')
    with os.fdopen(os.open(path,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600),'w') as output:json.dump(value,output,indent=2,default=str)
    return path


def start():
    assert os.environ['LEARNINGNEMO_AZURE_APPLY']=='demo-quota-window'
    assert az('account','show')['id']==os.environ['AZURE_SUBSCRIPTION_ID']
    admin=az('identity','show','-g','rg-learningnemo-data-dev','-n','id-learningnemo-sql-admin-dev')
    operator=az('identity','show','-g',GROUP,'-n','id-learningnemo-invoice-operator-dev')
    app=az('containerapp','show','-g',GROUP,'-n','ca-nemo-invoice-operator-dev')
    env={item['name']:item.get('value') for item in app['properties']['template']['containers'][0]['env']}
    source=(ROOT/'infra/next-phase/review-service'/MIGRATION).read_text()
    digest=hashlib.sha256(source.encode()).hexdigest()
    image=(STATE/'cloud-human.image.txt').read_text().strip()
    assert re.fullmatch(r'crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/cloud-human@sha256:[a-f0-9]{64}',image)
    lease=(datetime.now(UTC)+timedelta(minutes=40)).isoformat()
    code="exec(__import__('zlib').decompress(__import__('base64').b64decode('"+base64.b64encode(zlib.compress(REMOTE.encode())).decode()+"')))"
    document={'location':'eastus','tags':{'owner':'learningnemo','purpose':'bounded-demo-quota-migration','expiresAt':lease},
        'identity':{'type':'UserAssigned','userAssignedIdentities':{admin['id']:{},operator['id']:{}}},
        'properties':{'environmentId':app['properties']['environmentId'],
            'configuration':{'triggerType':'Manual','replicaTimeout':600,'replicaRetryLimit':0,
                'manualTriggerConfig':{'parallelism':1,'replicaCompletionCount':1},'registries':[{'server':image.split('/')[0],'identity':operator['id']}]},
            'template':{'containers':[{'name':'quota','image':image,'resources':{'cpu':0.25,'memory':'0.5Gi'},'command':['python','-u','-c',code],
                'env':[{'name':name,'value':value} for name,value in {
                    'AZURE_CLIENT_ID':admin['clientId'],'LEARNINGNEMO_SQL_SERVER':env['LEARNINGNEMO_SQL_SERVER'],
                    'MIGRATION_LEASE':lease,'QUOTA_SQL':base64.b64encode(zlib.compress(source.encode())).decode(),'QUOTA_SHA256':digest}.items()]}]}}}
    save('cleanup',{'job':JOB,'admin_id':admin['id'],'isolation_scope':admin.get('isolationScope')})
    path=save('job',document)
    az('containerapp','job','create','-g',GROUP,'-n',JOB,'--yaml',str(path))
    execution=az('containerapp','job','start','-g',GROUP,'-n',JOB)
    save('execution',execution)
    print('STARTED bounded quota migration:',execution['name'])


def collect():
    execution=json.loads((STATE/'demo-quota.execution.json').read_text())['name']
    job=az('containerapp','job','show','-g',GROUP,'-n',JOB)
    root='https://management.azure.com'+job['id'];api='2023-11-02-preview'
    replicas=az('rest','--method','get','--url',root+'/executions/'+execution+'/replicas?api-version='+api)['value']
    assert replicas,'Quota job replica not ready'
    token=az('rest','--method','post','--url',root+'/getAuthToken?api-version='+api)['properties']['token']
    endpoint=job['properties']['eventStreamEndpoint'].split('/subscriptions/')[0]
    url=endpoint+'/subscriptions/'+job['id'].split('/')[2]+'/resourceGroups/'+GROUP+'/jobs/'+JOB+'/executions/'+execution+'/replicas/'+replicas[0]['name']+'/containers/quota/logstream'
    lines=[]
    with requests.get(url,headers={'Authorization':'Bearer '+token},params={'follow':'true','output':'json','tailLines':300},stream=True,timeout=120) as response:
        response.raise_for_status()
        for raw in response.iter_lines():
            if not raw:continue
            line=raw.decode();lines.append(line)
            text=json.loads(line).get('Log','')
            match=re.search(r'QUOTA_WINDOW ([A-Za-z0-9+/=]+)',text)
            if match:
                report=json.loads(zlib.decompress(base64.b64decode(match[1])))
                save('verified',report);print(json.dumps(report,indent=2));return
    save('incomplete-output',lines)
    raise RuntimeError('Quota change unconfirmed; inspect retained job output, do not assume success')


def cleanup():
    az('containerapp','job','delete','-g',GROUP,'-n',JOB,'--yes')
    note=json.loads((STATE/'demo-quota.cleanup.json').read_text())
    admin=az('identity','show','--ids',note['admin_id'])
    assert admin.get('isolationScope')==note['isolation_scope']
    assert not any(item['name']==JOB for item in az('containerapp','job','list','-g',GROUP))
    save('cleanup-completed',note);(STATE/'demo-quota.cleanup.json').unlink()
    print('PASS temporary quota administrator job removed; identity isolation unchanged')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('start','collect','cleanup'))
    args=parser.parse_args()
    {'start':start,'collect':collect,'cleanup':cleanup}[args.action]()