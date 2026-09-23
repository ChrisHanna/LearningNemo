"""Authorize only the owned invoice coordinator to acquire verifier API tokens."""

import argparse
from datetime import UTC, datetime
import importlib.util
import json
import os
from pathlib import Path
from uuid import UUID, uuid5


ROOT = Path(__file__).parents[2]
STATE = Path.home()/'.local/state/learningnemo'
SPEC = importlib.util.spec_from_file_location('invoice_identity_helpers', ROOT/'infra/create-entra-approver.py')
IDENTITY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IDENTITY)


def merge_role(roles, api_id):
    role = {'id': str(uuid5(UUID(api_id), 'application-role:Invoice.Verify')), 'value': 'Invoice.Verify',
        'allowedMemberTypes': ['Application'], 'isEnabled': True, 'displayName': 'Invoice verification caller',
        'description': 'Allows the dedicated invoice coordinator to request independent invoice verification. No human approval or execution authority.'}
    matches = [existing for existing in roles if existing['id'] == role['id'] or existing['value'] == role['value']]
    if matches:
        if len(matches) != 1 or any(matches[0].get(key) != value for key, value in role.items()):
            raise ValueError('invoice verifier role collision')
        return roles, role['id']
    return [*roles, role], role['id']


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--apply', action='store_true'); args = parser.parse_args()
    live = json.loads((STATE/'invoice-services.live.json').read_text())
    account = IDENTITY.az(['account','show'])
    if account['id'] != os.environ['AZURE_SUBSCRIPTION_ID'] or account['id'] != live['subscription'] or account['tenantId'] != live['values']['tenantId']:
        raise ValueError('tenant/subscription mismatch')
    owned = IDENTITY.az(['identity','show','-g','rg-learningnemo-invoice-dev','-n','id-learningnemo-invoice-operator-dev'])
    if owned['principalId'] != live['principals']['operator']['objectId'] or owned['clientId'] != live['principals']['operator']['clientId']:
        raise ValueError('coordinator identity differs')
    if owned.get('tags', {}).get('purpose') != 'invoice-agent-workflow' or owned['tags'].get('owner') != 'learningnemo-portfolio':
        raise ValueError('coordinator ownership differs')
    api = IDENTITY.az(['ad','app','show','--id',live['values']['apiClientId']])
    resource = IDENTITY.az(['ad','sp','show','--id',api['appId']])
    roles, role_id = merge_role(api['appRoles'], api['appId'])
    path = f"servicePrincipals/{owned['principalId']}/appRoleAssignments"
    assignments = IDENTITY.graph('GET', path)
    if assignments.get('@odata.nextLink'):
        raise ValueError('unexpected assignment pagination')
    selected = [row for row in assignments['value'] if row['resourceId'] == resource['id']]
    if any(row['appRoleId'] != role_id for row in selected) or len(selected) > 1:
        raise ValueError('coordinator has unexpected API authority')
    print('PLAN application-only Invoice.Verify role for one owned coordinator; preserve human roles and consent', flush=True)
    if not args.apply:
        return
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'invoice-verifier-identity':
        raise ValueError('explicit invoice-verifier-identity acknowledgement required')
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')
    IDENTITY.write_private(STATE/f'invoice-verifier-identity.before-{stamp}.json', {'api':api,'assignments':assignments}, exclusive=True)
    if roles != api['appRoles']:
        IDENTITY.graph('PATCH', f"applications/{api['id']}", {'appRoles':roles})
    if not selected:
        IDENTITY.graph('POST', path, {'principalId':owned['principalId'],'resourceId':resource['id'],'appRoleId':role_id})
    after = IDENTITY.az(['ad','app','show','--id',api['appId']])
    observed = [{key:value for key,value in row.items() if key != 'origin'} for row in after['appRoles']]
    expected = [{key:value for key,value in row.items() if key != 'origin'} for row in roles]
    if sorted(observed,key=lambda row:row['id']) != sorted(expected,key=lambda row:row['id']):
        raise ValueError('API role readback differs')
    for field in ('api','identifierUris','signInAudience','requiredResourceAccess'):
        if after.get(field) != api.get(field):
            raise ValueError('unrelated API configuration changed')
    verified = IDENTITY.graph('GET', path)
    if verified.get('@odata.nextLink') or {row['appRoleId'] for row in verified['value'] if row['resourceId']==resource['id']} != {role_id}:
        raise ValueError('coordinator assignment unconfirmed')
    previous = {row['id']:row for row in assignments['value'] if row['resourceId'] != resource['id']}
    current = {row['id']:row for row in verified['value'] if row['resourceId'] != resource['id']}
    if previous != current:
        raise ValueError('unrelated workload assignment changed')
    IDENTITY.write_private(STATE/'invoice-verifier-identity.verified.json', {'principalId':owned['principalId'],'resourceId':resource['id'],
        'roleId':role_id,'checkedAt':datetime.now(UTC).isoformat(),'humanRolesUnchanged':True})
    print('PASS invoice verifier-only workload assignment verified; human roles and API consent preserved')


if __name__ == '__main__': main()