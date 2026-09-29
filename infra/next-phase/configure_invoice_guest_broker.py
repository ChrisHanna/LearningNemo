"""Authorize only the public demo managed identity to broker bounded guest calls."""

import argparse
from datetime import UTC, datetime
import importlib.util
import json
import os
from pathlib import Path
from uuid import UUID, uuid5


ROOT = Path(__file__).parents[2]
STATE = Path.home() / '.local/state/learningnemo'
SPEC = importlib.util.spec_from_file_location('invoice_identity_helpers', ROOT / 'infra/create-entra-approver.py')
IDENTITY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IDENTITY)


def merge_role(roles, api_id):
    role = {
        'id': str(uuid5(UUID(api_id), 'application-role:Invoice.GuestBroker')),
        'value': 'Invoice.GuestBroker',
        'allowedMemberTypes': ['Application'],
        'isEnabled': True,
        'displayName': 'Invoice public demo guest broker',
        'description': 'Allows the owned public demo identity to broker quota-bound Planning and review requests. No execution authority.',
    }
    matches = [existing for existing in roles if existing['id'] == role['id'] or existing['value'] == role['value']]
    if matches:
        if len(matches) != 1 or any(matches[0].get(key) != value for key, value in role.items()):
            raise ValueError('invoice guest broker role collision')
        return roles, role['id']
    return [*roles, role], role['id']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    account = IDENTITY.az(['account', 'show'])
    if account['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    broker = IDENTITY.az([
        'identity', 'show', '-g', 'rg-learningnemo-demo-dev',
        '-n', 'id-learningnemo-cloud-public-demo-dev',
    ])
    if broker.get('tags', {}).get('identityPurpose') != 'cloud-demo-public-demo':
        raise ValueError('public demo broker ownership differs')
    settings = json.loads((ROOT / '.nemo-test-client.json').read_text())
    api = IDENTITY.az(['ad', 'app', 'show', '--id', settings['ENTRA_CLIENT_ID']])
    resource = IDENTITY.az(['ad', 'sp', 'show', '--id', api['appId']])
    roles, role_id = merge_role(api['appRoles'], api['appId'])
    path = f"servicePrincipals/{broker['principalId']}/appRoleAssignments"
    assignments = IDENTITY.graph('GET', path)
    if assignments.get('@odata.nextLink'):
        raise ValueError('unexpected assignment pagination')
    selected = [row for row in assignments['value'] if row['resourceId'] == resource['id']]
    if any(row['appRoleId'] != role_id for row in selected) or len(selected) > 1:
        raise ValueError('public demo broker has unexpected API authority')
    print('PLAN application-only Invoice.GuestBroker role for the owned public demo identity', flush=True)
    if not args.apply:
        return
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'invoice-guest-broker':
        raise ValueError('explicit invoice-guest-broker acknowledgement required')
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')
    IDENTITY.write_private(
        STATE / f'invoice-guest-broker.before-{stamp}.json',
        {'api': api, 'assignments': assignments},
        exclusive=True,
    )
    if roles != api['appRoles']:
        IDENTITY.graph('PATCH', f"applications/{api['id']}", {'appRoles': roles})
    if not selected:
        IDENTITY.graph('POST', path, {
            'principalId': broker['principalId'],
            'resourceId': resource['id'],
            'appRoleId': role_id,
        })
    verified = IDENTITY.graph('GET', path)
    if verified.get('@odata.nextLink') or {
        row['appRoleId'] for row in verified['value'] if row['resourceId'] == resource['id']
    } != {role_id}:
        raise ValueError('public demo broker assignment unconfirmed')
    IDENTITY.write_private(STATE / 'invoice-guest-broker.verified.json', {
        'clientId': broker['clientId'],
        'principalId': broker['principalId'],
        'resourceId': resource['id'],
        'roleId': role_id,
        'checkedAt': datetime.now(UTC).isoformat(),
        'executionAuthority': False,
    })
    print('PASS public demo guest broker role verified; execution authority absent')


if __name__ == '__main__':
    main()
