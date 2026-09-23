"""Verify cloud service placement and anonymous security boundaries, not a user rehearsal."""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
from availability_policy import managed


def az(*args):
    result = subprocess.run(['az', *args, '--output', 'json'], capture_output=True, text=True, check=True, timeout=90)
    return json.loads(result.stdout)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parameters', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    values = {name: entry['value'] for name, entry in json.loads(args.parameters.read_text())['parameters'].items()}
    subscription = os.environ['AZURE_SUBSCRIPTION_ID']
    require(az('account', 'show')['id'] == subscription, 'Subscription mismatch')
    operator_managed=values.get('availabilityMode')=='operator-managed'
    if operator_managed:
        require(not values['expiresAt'], 'Managed console must not have an expiry')
    else:
        require(dt.datetime.fromisoformat(values['expiresAt'].replace('Z', '+00:00')) > dt.datetime.now(dt.UTC), 'Cloud demo lease expired')
    expected = {'dashboard': (True, values['consoleImage']), 'controller': (False, values['consoleImage']), 'agent': (False, values['agentImage'])}
    records = {}
    for service, (external, image) in expected.items():
        name = f'ca-learningnemo-{service}-dev'
        app = az('containerapp', 'show', '--resource-group', 'rg-learningnemo-demo-dev', '--name', name)
        properties = app['properties']
        if operator_managed: require(managed(app), f'{service} availability policy differs')
        require(properties['provisioningState'] == 'Succeeded', f'{service} provisioning failed')
        require(properties['runningStatus'] == 'Running', f'{service} is not running')
        require(properties.get('latestReadyRevisionName') == properties.get('latestRevisionName'), f'{service} latest revision is not ready')
        require(properties['environmentId'].lower() == values['environmentId'].lower(), f'{service} environment mismatch')
        require(properties['configuration']['ingress']['external'] is external, f'{service} ingress boundary differs')
        require(properties['configuration']['ingress']['allowInsecure'] is False, f'{service} allows insecure HTTP')
        require(properties['template']['containers'][0]['image'] == image, f'{service} image digest differs')
        require(properties['template']['scale']['maxReplicas'] == 1, f'{service} scale bound differs')
        identity = az('identity', 'show', '--resource-group', 'rg-learningnemo-demo-dev', '--name', f'id-learningnemo-cloud-{service}-dev')
        require(set(key.lower() for key in app['identity']['userAssignedIdentities']) == {identity['id'].lower()}, f'{service} identity differs')
        grants = az('role', 'assignment', 'list', '--assignee-object-id', identity['principalId'], '--all')
        if service == 'dashboard':
            require(len(grants) == 1 and grants[0]['roleDefinitionId'].endswith('7f951dda-4ed3-4680-a7ca-43fe172d538d'), 'Dashboard must have only AcrPull')
            environment = {item['name']:item.get('value') for item in properties['template']['containers'][0].get('env',[])}
            for kind in ('operator','review'):
                require(environment.get('LEARNINGNEMO_INVOICE_'+kind.upper()+'_ORIGIN','') == values.get('invoice'+kind.title()+'Origin',''), 'Invoice private origin differs')
        if service == 'agent':
            require(len(grants) == 2, 'Agent permission count differs')
            require(any(grant['scope'].endswith('/secrets/llm-gateway-client-key') for grant in grants), 'Agent secret scope differs')
        records[service] = {'running': True, 'publicIngress': external, 'imagePinned': True}
    origin = f"https://ca-learningnemo-dashboard-dev.{values['environmentDomain']}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    with opener.open(origin + '/api/bootstrap', timeout=30) as response:
        bootstrap = json.load(response)
        require(bootstrap['hosting'] == 'azure', 'Dashboard is not in cloud hosting mode')
        require(bootstrap.get('invoiceEnabled',False) == bool(values.get('invoiceOperatorOrigin')), 'Invoice mode differs')
        if values.get('invoiceOperatorOrigin'):
            require(bootstrap.get('reviewEnabled') is True, 'Invoice review scope not configured')
        require('Secure' in response.headers.get('Set-Cookie', ''), 'Session cookie is not Secure')
        require(response.headers.get('Strict-Transport-Security') == 'max-age=31536000', 'HSTS absent')
        require("default-src 'self'" in response.headers.get('Content-Security-Policy', ''), 'CSP absent')
    for path in ('/api/showcase', '/api/capabilities', *(['/api/invoices/plans','/api/invoices/jobs/'+'0'*32] if values.get('invoiceOperatorOrigin') else [])):
        try:
            opener.open(origin + path, timeout=20)
        except urllib.error.HTTPError as error:
            require(error.code == 401, 'Unexpected anonymous response')
        else:
            raise RuntimeError('Protected cloud data allowed anonymous access')
    result = {
        'schemaVersion': 1, 'verifiedAt': dt.datetime.now(dt.UTC).isoformat(timespec='seconds'),
        'expiresAt': values['expiresAt'] or None, 'availabilityMode':values.get('availabilityMode','leased'), 'dashboardUrl': origin, 'services': records,
        'anonymousAccessDenied': True, 'userPersonaRehearsal': 'not-verified',
        'workspaceIncidentIntegration': 'not-verified',
    }
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    args.output.chmod(0o600)
    print('PASS cloud service placement, image pins, identity boundaries, and anonymous denial')
    print('INFO user-role rehearsal and full incident journey are separate acceptance gates')
    print(origin)


if __name__ == '__main__':
    main()