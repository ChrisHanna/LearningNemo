#!/usr/bin/env bash
set -euo pipefail
umask 077
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase="$root/infra/next-phase"
state="${LEARNINGNEMO_INFRA_STATE_DIR:-$HOME/.local/state/learningnemo}"
mode="${1:---what-if}"
[[ "$mode" == --what-if || "$mode" == --apply ]]
[[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]
[[ "$(az account show --query id -o tsv)" == "$AZURE_SUBSCRIPTION_ID" ]]
if [[ "$mode" == --apply ]]; then [[ "${LEARNINGNEMO_AZURE_APPLY:-}" == cloud-demo ]]; fi
python3 "$phase/check_toolchain.py"
parameters="$state/cloud-demo.parameters.json"
compiled="$state/cloud-demo.template.json"
python3 - "$root" "$state" <<'PY'
import datetime as dt, json, os, re, subprocess, sys
from pathlib import Path
root, state = map(Path, sys.argv[1:])
sys.path.insert(0,str(root/'infra/next-phase'))
from preflight_platform import validate_budget
from availability_policy import dependency_mode
def az(*args):
    return json.loads(subprocess.check_output(['az',*args,'--output','json'],text=True))
account=az('account','show')
assert account['id']==os.environ['AZURE_SUBSCRIPTION_ID']
validate_budget(account, required=True, maximum_amount=50)
environment=az('containerapp','env','show','--resource-group','rg-learningnemo-platform-dev','--name','cae-learningnemo-dev')
registries=az('acr','list','--resource-group','rg-learningnemo-artifacts-dev')
assert len(registries)==1
registry=registries[0]
now=dt.datetime.now(dt.UTC)
availability_mode=dependency_mode((environment,registry))
expiry=None if availability_mode=='operator-managed' else min(now+dt.timedelta(hours=4),*[dt.datetime.fromisoformat(item['tags']['expiresAt'].replace('Z','+00:00')) for item in (environment,registry)])
assert expiry is None or expiry>now+dt.timedelta(hours=1),'Renew environment and registry leases before cloud deployment'
images={name:(state/f'cloud-{name}.image.txt').read_text().strip() for name in ('console','agent')}
assert all(re.fullmatch(re.escape(registry['loginServer'])+r'/learningnemo/cloud-(console|agent)@sha256:[0-9a-f]{64}',image) for image in images.values())
settings=json.loads((root/'.nemo-test-client.json').read_text())
apim=az('apim','show','--resource-group','rg-nemo-agent-dev','--name','apim-nemo-8370187d')
values={
    'location':'eastus','expiresAt':expiry.isoformat(timespec='seconds') if expiry else '', 'availabilityMode':availability_mode,
    'environmentId':environment['id'],'environmentDomain':environment['properties']['defaultDomain'],
    'registryName':registry['name'],'registryServer':registry['loginServer'],
    'consoleImage':images['console'],'agentImage':images['agent'],
    'tenantId':settings['ENTRA_TENANT_ID'],'apiClientId':settings['ENTRA_CLIENT_ID'],
    'publicClientId':settings['ENTRA_PUBLIC_CLIENT_ID'],'apimOrigin':apim['gatewayUrl'],
}
for kind in ('review','incident','execution'):
    name=f'LEARNINGNEMO_{kind.upper()}_ORIGIN'
    origin=os.environ.get(name,'')
    if origin:
        expected=f"https://ca-learningnemo-{kind}-dev.internal.{environment['properties']['defaultDomain']}"
        assert origin==expected,'Human service origin must be the exact named private service in the existing environment'
        assert os.environ.get('LEARNINGNEMO_HUMAN_SERVICES_VERIFIED')=='yes','Verify private service readiness, SQL permissions, and consent before connecting the dashboard'
    values[f'{kind}Origin']=origin
for kind in ('operator','review'):
    origin=os.environ.get(f'LEARNINGNEMO_INVOICE_{kind.upper()}_ORIGIN','')
    if origin:
        assert origin==f"https://ca-nemo-invoice-{kind}-dev.internal.{environment['properties']['defaultDomain']}"
        receipt=json.loads((state/'invoice-services.verified.json').read_text())
        assert receipt['image']==(state/'invoice-services.image.txt').read_text().strip()
        if availability_mode=='operator-managed':
            assert receipt.get('availabilityMode')=='operator-managed' and receipt.get('expiresAt') is None
        else:
            assert dt.datetime.fromisoformat(receipt['expiresAt'])>now+dt.timedelta(minutes=20)
    values[f'invoice{kind.title()}Origin']=origin
assert bool(values['invoiceOperatorOrigin'])==bool(values['invoiceReviewOrigin'])
output=state/'cloud-demo.parameters.json'
output.write_text(json.dumps({'$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#','contentVersion':'1.0.0.0','parameters':{key:{'value':value} for key,value in values.items()}},indent=2))
output.chmod(0o600)
print('PASS cloud availability policy:',availability_mode,values['expiresAt'])
PY
az bicep build --file "$phase/cloud-demo.bicep" --stdout > "$compiled"
python3 "$phase/validate_cloud_demo.py" "$compiled"
az deployment sub validate --name learningnemo-cloud-demo-dev --location eastus --template-file "$phase/cloud-demo.bicep" --parameters "@$parameters" -o none
az deployment sub what-if --name learningnemo-cloud-demo-dev --location eastus --template-file "$phase/cloud-demo.bicep" --parameters "@$parameters" --result-format FullResourcePayloads --no-pretty-print -o json > "$state/cloud-demo.what-if.json"
python3 "$phase/summarize_database_what_if.py" "$state/cloud-demo.what-if.json"
python3 - "$state/cloud-demo.what-if.json" <<'PY'
import json,sys
document=json.load(open(sys.argv[1]))
allowed={'Microsoft.Resources/resourceGroups','Microsoft.ManagedIdentity/userAssignedIdentities','Microsoft.App/containerApps','Microsoft.Authorization/roleAssignments','Microsoft.Authorization/roleDefinitions'}
for change in document['changes']:
    if change['changeType'] in ('Ignore','NoChange'): continue
    assert change['changeType'] in ('Create','Modify','Deploy'), 'Destructive cloud demo change rejected'
    kind=change.get('after',{}).get('type')
    assert kind in allowed, 'Unexpected cloud demo resource type'
    identifier=change['resourceId'].lower()
    assert '/resourcegroups/rg-learningnemo-demo-dev' in identifier or kind.startswith('Microsoft.Authorization/'), 'Unexpected cloud demo scope'
print('PASS cloud demo preview contains only demo resources and scoped authorization changes')
PY
if [[ "$mode" == --what-if ]]; then exit 0; fi
az deployment sub create --name learningnemo-cloud-demo-dev --location eastus --template-file "$phase/cloud-demo.bicep" --parameters "@$parameters" -o json > "$state/cloud-demo.deployment.json" 2> "$state/cloud-demo.deployment-error.log"
python3 - "$state/cloud-demo.deployment.json" <<'PY'
import json,sys
result=json.load(open(sys.argv[1]))
assert result['properties']['provisioningState']=='Succeeded'
print('PASS cloud deployment:',result['properties']['outputs']['dashboardUrl']['value'])
PY