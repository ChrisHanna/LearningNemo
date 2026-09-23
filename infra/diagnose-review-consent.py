"""Read only the owned Approver consent and recent client sign-in failures."""

import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('review_consent', ROOT / 'infra/configure-human-consent.py')
CONSENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONSENT)
IDENTITY = CONSENT.IDENTITY


def main():
    settings = json.loads((ROOT / '.nemo-test-client.json').read_text())
    account = IDENTITY.az(['account', 'show'])
    if account['id'] != os.environ['AZURE_SUBSCRIPTION_ID'] or account['tenantId'] != settings['ENTRA_TENANT_ID']:
        raise ValueError('tenant or subscription mismatch')
    record = IDENTITY.read_private(Path.home() / '.local/state/learningnemo/approver-dev.identity.json')
    api = IDENTITY.az(['ad', 'sp', 'show', '--id', settings['ENTRA_CLIENT_ID']])
    client = IDENTITY.az(['ad', 'sp', 'show', '--id', settings['ENTRA_PUBLIC_CLIENT_ID']])
    print('EXPECTED_APPROVER', record['userPrincipalName'], flush=True)
    print('RESOURCE_SCOPES', json.dumps([{key: entry.get(key) for key in ('id', 'value', 'type', 'isEnabled')}
        for entry in api.get('oauth2PermissionScopes', [])]), flush=True)
    query = urlencode({'$filter': f"clientId eq '{client['id']}' and resourceId eq '{api['id']}'"})
    grants = IDENTITY.graph('GET', 'oauth2PermissionGrants?' + query)
    print('CONSENT_GRANTS', json.dumps([{'type': entry['consentType'], 'ownedApprover': entry.get('principalId') == record['userId'],
        'scope': entry['scope']} for entry in grants['value']]), flush=True)
    query = urlencode({'$filter': f"appId eq '{settings['ENTRA_PUBLIC_CLIENT_ID']}' and userId eq '{record['userId']}'", '$top': 5,
        '$select': 'createdDateTime,status,correlationId,resourceDisplayName,appId,userId'})
    try:
        events = IDENTITY.graph('GET', 'auditLogs/signIns?' + query)
    except IDENTITY.ProvisionError as error:
        print('SIGNIN_LOGS_UNAVAILABLE', str(error), flush=True)
        return
    for entry in events['value']:
        print('SIGNIN', json.dumps(entry), flush=True)


if __name__ == '__main__':
    main()