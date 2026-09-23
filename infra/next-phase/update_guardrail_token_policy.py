"""Apply only the reviewed token-field normalization to the owned guardrail route."""

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

from deploy_human_services import az, save


def normalized(value):
    root = ET.fromstring(value)
    for element in root.iter():
        element.text = ''.join((element.text or '').split())
        element.tail = None
    return ET.tostring(root)


def main():
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'guardrail-token-normalization':
        raise ValueError('explicit operation-policy acknowledgement required')
    account = az('account','show')
    if account['id'] != os.environ['AZURE_SUBSCRIPTION_ID'] or account['tenantId'] != 'cf23abe6-c7d9-4389-81c8-b9ca34605f56':
        raise ValueError('tenant/subscription mismatch')
    origin = f"https://management.azure.com/subscriptions/{account['id']}/resourceGroups/rg-nemo-agent-dev/providers/Microsoft.ApiManagement/service/apim-nemo-8370187d/apis/llm-router"
    path = origin + '/operations/semantic-guardrail-chat-completions/policies/policy?api-version=2024-05-01'
    api_path = origin + '/policies/policy?api-version=2024-05-01'
    before = az('rest','--method','get','--url',path+'&format=rawxml','--headers','Accept=application/json')
    inherited = az('rest','--method','get','--url',api_path+'&format=rawxml','--headers','Accept=application/json')
    policy = (Path(__file__).parents[1]/'policies/semantic-guardrail.xml').read_text()
    previous = policy.replace('      body.Remove("max_completion_tokens");\n', '')
    if normalized(before['properties']['value']) not in (normalized(previous), normalized(policy)):
        raise ValueError('deployed guardrail policy differs from the reviewed change')
    save('guardrail-token-policy.before.json', before)
    save('guardrail-inherited-policy.before.json', inherited)
    if normalized(before['properties']['value']) != normalized(policy):
        body = save('guardrail-token-policy.request.json', {'properties':{'format':'rawxml','value':policy}})
        az('rest','--method','put','--url',path,'--body','@'+str(body),'--headers','Accept=application/json','Content-Type=application/json')
    after = az('rest','--method','get','--url',path+'&format=rawxml','--headers','Accept=application/json')
    if normalized(after['properties']['value']) != normalized(policy):
        raise ValueError('guardrail policy readback differs')
    if az('rest','--method','get','--url',api_path+'&format=rawxml','--headers','Accept=application/json')['properties'] != inherited['properties']:
        raise ValueError('inherited API policy changed')
    save('guardrail-token-policy.verified.json', {'checkedAt':datetime.now(UTC).isoformat(),'inheritedPolicyUnchanged':True})
    print('PASS only guardrail token normalization applied; inherited API policy unchanged')


if __name__ == '__main__': main()