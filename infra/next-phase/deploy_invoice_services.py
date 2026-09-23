"""Guarded invoice-only identities and service deployment; existing releases remain untouched."""

import argparse
from datetime import UTC,datetime,timedelta
import json
import hashlib
import os
from pathlib import Path
import re
from uuid import UUID,uuid5
import subprocess

from deploy_human_services import az,save,parameters
from preflight_platform import validate_budget
from availability_policy import dependency_mode


ROOT=Path(__file__).parents[2]
STATE=Path.home()/'.local/state/learningnemo'
GROUP='rg-learningnemo-invoice-dev'
KINDS=('operator','review','planning','execution','verifier','simulator')
PULL='7f951dda-4ed3-4680-a7ca-43fe172d538d'
SECRET_USER='4633458b-17de-408a-b874-0445c86b69e6'


def validate_preview(preview,subscription,deploy_apps):
    expected_types={'Microsoft.ManagedIdentity/userAssignedIdentities'}|({'Microsoft.App/containerApps'} if deploy_apps else set())
    for change in preview['changes']:
        if change['changeType'] in ('Ignore','NoChange'): continue
        if change['changeType'] not in ('Create','Modify','Deploy') or change.get('after',{}).get('type') not in expected_types:
            raise ValueError('unexpected invoice deployment change')
        if not change['resourceId'].lower().startswith(f'/subscriptions/{subscription}/resourcegroups/{GROUP}/'.lower()):
            raise ValueError('invoice change outside dedicated group')


def deploy(values,subscription):
    path=save('invoice-services.parameters.json',parameters(values))
    args=['-g',GROUP,'-n','learningnemo-invoice-services','--template-file',str(ROOT/'infra/next-phase/invoice-services.bicep'),'--parameters','@'+str(path)]
    preview=az('deployment','group','what-if',*args,'--no-pretty-print')
    validate_preview(preview,subscription,values['deployApps'])
    save('invoice-services.what-if.json',preview)
    result=az('deployment','group','create',*args)
    if result['properties']['provisioningState']!='Succeeded': raise RuntimeError('invoice deployment unconfirmed')


def grant(subscription,identity,scope,role,suffix):
    identifier=str(uuid5(UUID(subscription),scope+identity+suffix))
    existing=az('role','assignment','list','--scope',scope,'--assignee-object-id',identity)
    if not any(row['scope'].lower()==scope.lower() and row['roleDefinitionId'].endswith(role) for row in existing):
        az('role','assignment','create','--name',identifier,'--assignee-object-id',identity,'--assignee-principal-type','ServicePrincipal','--role',role,'--scope',scope)


def prepare():
    subscription=os.environ['AZURE_SUBSCRIPTION_ID']; account=az('account','show')
    if account['id']!=subscription: raise ValueError('subscription mismatch')
    validate_budget(account,required=True,maximum_amount=50)
    environment=az('containerapp','env','show','-g','rg-learningnemo-platform-dev','-n','cae-learningnemo-dev')
    registry=az('acr','show','-g','rg-learningnemo-artifacts-dev','-n','crlearningnemodevgruyrc4qwdvvm')
    network=az('group','show','-n','rg-learningnemo-data-network-dev')
    availability_mode=dependency_mode((environment,registry,network))
    expiry=None if availability_mode=='operator-managed' else min(datetime.now(UTC)+timedelta(minutes=100),*[datetime.fromisoformat(item['tags']['expiresAt'].replace('Z','+00:00')) for item in (environment,registry,network)])
    if expiry is not None and expiry<=datetime.now(UTC)+timedelta(minutes=30): raise ValueError('renew dependency leases before invoice deployment')
    if any(item['tags'].get('owner')!='learningnemo-portfolio' for item in (environment,registry,network)): raise ValueError('dependency ownership mismatch')
    settings=json.loads((ROOT/'.nemo-test-client.json').read_text())
    server=az('sql','server','list','-g','rg-learningnemo-data-dev')[0]
    current=az('containerapp','show','-g','rg-learningnemo-demo-dev','-n','ca-learningnemo-agent-dev')
    env={entry['name']:entry.get('value') for entry in current['properties']['template']['containers'][0]['env']}
    image=(STATE/'invoice-services.image.txt').read_text().strip(); agent_image=(STATE/'invoice-agent.image.txt').read_text().strip()
    for value,repository in ((image,'invoice-services'),(agent_image,'invoice-agent')):
        if not re.fullmatch(re.escape(registry['loginServer']+'/learningnemo/'+repository)+r'@sha256:[a-f0-9]{64}',value): raise ValueError('owned pinned image required')
    values=dict(location='eastus',environmentId=environment['id'],registryServer=registry['loginServer'],image=image,agentImage=agent_image,
        expiresAt=expiry.isoformat() if expiry else '',availabilityMode=availability_mode,tenantId=settings['ENTRA_TENANT_ID'],apiClientId=settings['ENTRA_CLIENT_ID'],publicClientId=settings['ENTRA_PUBLIC_CLIENT_ID'],
        sqlServer=server['fullyQualifiedDomainName'],modelOrigin=env['OPENAI_BASE_URL'],guardrailOrigin=env['OPENAI_GUARDRAIL_BASE_URL'],subscriptionId=subscription,deployApps=False)
    if az('group','exists','-n',GROUP):
        if az('group','show','-n',GROUP).get('tags',{}).get('purpose')!='invoice-agent-workflow': raise ValueError('resource group collision')
    else:
        az('group','create','-n',GROUP,'-l','eastus','--tags','owner=learningnemo-portfolio','project=learningnemo','purpose=invoice-agent-workflow',*(['expiresAt='+expiry.isoformat()] if expiry else ['availabilityMode=operator-managed','disposable=false']))
    deploy(values,subscription)
    identities={kind:az('identity','show','-g',GROUP,'-n','id-learningnemo-invoice-'+kind+'-dev') for kind in KINDS}
    for kind,identity in identities.items():
        if kind!='simulator':grant(subscription,identity['principalId'],registry['id'],PULL,'invoice-pull')
        if kind in ('planning','execution'):
            secret=f'/subscriptions/{subscription}/resourceGroups/rg-nemo-agent-dev/providers/Microsoft.KeyVault/vaults/kvnemo8370187d/secrets/llm-gateway-client-key'
            grant(subscription,identity['principalId'],secret,SECRET_USER,'invoice-model-key')
    role_id=str(uuid5(UUID(subscription),'learningnemo-invoice-controller'))
    vm_scope=f'/subscriptions/{subscription}/resourceGroups/rg-learningnemo-saw-dev/providers/Microsoft.Compute/virtualMachines/vm-learningnemo-saw-dev'
    definition={'Name':'LearningNeMo invoice workspace command','Id':role_id,'IsCustom':True,'Description':'Fixed invoice controller on the retained SAW VM',
        'Actions':['Microsoft.Compute/virtualMachines/read','Microsoft.Compute/virtualMachines/runCommand/action'],'NotActions':[],
        'DataActions':[],'NotDataActions':[],'AssignableScopes':[f'/subscriptions/{subscription}/resourceGroups/rg-learningnemo-saw-dev']}
    existing=az('role','definition','list','--name',definition['Name'])
    if not existing:
        existing=[az('role','definition','create','--role-definition',json.dumps(definition))]
    if len(existing)!=1 or existing[0]['permissions']!=[{'actions':definition['Actions'],'notActions':[],'dataActions':[],'notDataActions':[], 'condition':None,'conditionVersion':None}]:
        raise ValueError('invoice controller role definition differs')
    grant(subscription,identities['operator']['principalId'],vm_scope,existing[0]['name'],'invoice-controller')
    live={'subscription':subscription,'values':values,'principals':{kind:{'clientId':identity['clientId'],'objectId':identity['principalId']} for kind,identity in identities.items()}}
    save('invoice-services.live.json',live)
    print('PASS invoice identities and scoped grants prepared; apps not activated')


def readback_execution_receipt(execution, live, parameters, migrations):
    properties = execution['properties']
    containers = properties['template']['containers']
    if properties['status'] != 'Succeeded' or len(containers) != 1 or properties['template'].get('initContainers'):
        raise ValueError('successful isolated verification execution required')
    container = containers[0]
    if container['command'] != ['python', '/app/scripts/apply-invoice-migrations.py', '--verify-only'] or container['image'] != parameters['image']['value']:
        raise ValueError('pinned readback program differs')
    environment = {item['name']: item.get('value') for item in container['env']}
    expected = {'AZURE_CLIENT_ID': parameters['sqlAdminClientId']['value'], 'LEARNINGNEMO_SQL_SERVER': parameters['sqlServer']['value'],
                'LEARNINGNEMO_SQL_DATABASE': parameters['sqlDatabase']['value'], 'LEARNINGNEMO_MIGRATION_EXPIRES_AT': parameters['expiresAt']['value']}
    if any(environment.get(key) != value for key, value in expected.items()) or json.loads(environment['LEARNINGNEMO_INVOICE_PRINCIPALS']) != live['principals']:
        raise ValueError('readback SQL target or identity differs')
    if not datetime.fromisoformat(properties['startTime']) < datetime.fromisoformat(properties['endTime']) <= datetime.fromisoformat(environment['LEARNINGNEMO_MIGRATION_EXPIRES_AT']):
        raise ValueError('readback did not finish within its authority window')
    return {'principals': live['principals'], 'migrations': migrations, 'verifiedAcrossConnections': True, 'reviewWindowMinutes': 180,
            'approvalWindowMinutes': 30, 'legacyDeadlineCheckPassed': True, 'legacyApprovalDeadlineCheckPassed': True, 'verificationOnly': True,
            'evidence': {'source': 'pinned-verification-job-exit', 'execution': execution['name'], 'image': container['image'], 'finishedAt': properties['endTime']}}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','verify-migration','services']);parser.add_argument('--readback-status',action='store_true');args=parser.parse_args()
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY')!='invoice-services': raise ValueError('explicit invoice service acknowledgement required')
    if args.phase=='prepare': prepare();return
    live=json.loads((STATE/'invoice-services.live.json').read_text())
    if live['subscription']!=os.environ['AZURE_SUBSCRIPTION_ID'] or az('account','show')['id']!=live['subscription']: raise ValueError('subscription mismatch')
    if args.phase=='verify-migration':
        execution=json.loads((STATE/'invoice-migration.execution.json').read_text())['name']
        executions=az('containerapp','job','execution','list','-g','rg-learningnemo-human-dev','-n','caj-learningnemo-human-mig-dev')
        selected=next(row for row in executions if row['name']==execution)
        if selected['properties']['status']!='Succeeded':raise ValueError('invoice migration did not succeed')
        expected={'human-'+name:hashlib.sha256((ROOT/'infra/next-phase/review-service'/name).read_text().encode()).hexdigest() for name in ('008_invoice_lab.sql','009_invoice_workflow.sql','010_invoice_jobs.sql','011_invoice_recovery.sql','012_invoice_retention.sql','013_invoice_review_window.sql','014_invoice_bound_sandbox_test.sql','015_invoice_retention_pressure.sql','016_invoice_doubled_windows.sql')}
        if args.readback_status:
            migration_parameters=json.loads((STATE/'human-migration.parameters.json').read_text())['parameters']
            if migration_parameters['image']['value'] != (STATE/'cloud-human.image.txt').read_text().strip(): raise ValueError('current readback image required')
            records=[readback_execution_receipt(selected,live,migration_parameters,expected)]
        else:
            logs=subprocess.run(['az','containerapp','job','logs','show','-g','rg-learningnemo-human-dev','-n','caj-learningnemo-human-mig-dev','--execution',execution,'--container','migrator','--tail','100','--format','text'],capture_output=True,text=True,check=True,timeout=90)
            records=[json.JSONDecoder().raw_decode(line.split('PASS invoice_migration ',1)[1])[0] for line in logs.stdout.splitlines() if 'PASS invoice_migration ' in line]
        if len(records)!=1 or records[0]['principals']!=live['principals'] or records[0]['migrations']!=expected or records[0].get('verifiedAcrossConnections') is not True:
            raise ValueError('invoice migration receipt differs from release')
        if records[0].get('reviewWindowMinutes') != 180 or records[0].get('approvalWindowMinutes') != 30 or not (type(records[0].get('existingReviewDeadlinesPreserved')) is int or records[0].get('legacyDeadlineCheckPassed') is True):
            raise ValueError('review and approval window migration verification required')
        if not (type(records[0].get('existingApprovalDeadlinesPreserved')) is int or records[0].get('legacyApprovalDeadlineCheckPassed') is True):
            raise ValueError('existing approval deadline preservation required')
        save('invoice-migration.verified.json',records[0])
        print('PASS invoice migration hashes and identities verified across connections')
        return
    receipt=json.loads((STATE/'invoice-migration.verified.json').read_text())
    if receipt['principals']!=live['principals']: raise ValueError('verified invoice SQL identity bindings required')
    expected={'human-'+name:hashlib.sha256((ROOT/'infra/next-phase/review-service'/name).read_text().encode()).hexdigest() for name in ('008_invoice_lab.sql','009_invoice_workflow.sql','010_invoice_jobs.sql','011_invoice_recovery.sql','012_invoice_retention.sql','013_invoice_review_window.sql','014_invoice_bound_sandbox_test.sql','015_invoice_retention_pressure.sql','016_invoice_doubled_windows.sql')}
    if receipt['migrations'] != expected: raise ValueError('current invoice migration receipts required before activating services')
    if (STATE/'human-migration.cleanup.json').exists(): raise ValueError('migration authority cleanup required')
    deploy({**live['values'],'deployApps':True},live['subscription'])
    print('PASS invoice services deployed; runtime verification still required')


if __name__=='__main__':main()