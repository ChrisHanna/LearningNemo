"""Preview or remove only the cloud dashboard/agent/controller and their grants."""

import argparse
import json
import os
import subprocess


def az(*args):
    response = subprocess.run(['az', *args, '--output', 'json'], capture_output=True, text=True, check=True)
    return json.loads(response.stdout) if response.stdout.strip() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    subscription = os.environ['AZURE_SUBSCRIPTION_ID']
    assert az('account', 'show')['id'] == subscription
    group = 'rg-learningnemo-demo-dev'
    if not az('group', 'exists', '--name', group):
        print('INFO cloud demo resource group absent; check retained role definitions separately')
        return
    metadata = az('group', 'show', '--name', group)
    assert metadata['tags']['owner'] == 'learningnemo-portfolio'
    assert metadata['tags']['project'] == 'learningnemo'
    expected = {
        'id-learningnemo-cloud-dashboard-dev': 'Microsoft.ManagedIdentity/userAssignedIdentities',
        'id-learningnemo-cloud-controller-dev': 'Microsoft.ManagedIdentity/userAssignedIdentities',
        'id-learningnemo-cloud-agent-dev': 'Microsoft.ManagedIdentity/userAssignedIdentities',
        'ca-learningnemo-dashboard-dev': 'Microsoft.App/containerApps',
        'ca-learningnemo-controller-dev': 'Microsoft.App/containerApps',
        'ca-learningnemo-agent-dev': 'Microsoft.App/containerApps',
    }
    resources = az('resource', 'list', '--resource-group', group)
    assert all(expected.get(item['name']) == item['type'] for item in resources)
    assignments = []
    for item in resources:
        if item['type'] != 'Microsoft.ManagedIdentity/userAssignedIdentities':
            continue
        identity = az('identity', 'show', '--resource-group', group, '--name', item['name'])
        assignments.extend(az('role', 'assignment', 'list', '--assignee-object-id', identity['principalId'], '--all'))
    allowed_groups = ('rg-learningnemo-saw-dev', 'rg-learningnemo-platform-dev', 'rg-learningnemo-artifacts-dev', 'rg-nemo-agent-dev')
    for assignment in assignments:
        assert any(f'/resourcegroups/{name}/' in (assignment['scope'].lower() + '/') for name in allowed_groups)
    role_names = (
        'LearningNeMo cloud workspace observer',
        'LearningNeMo workspace proof transport',
        'LearningNeMo diagnostic endpoint reader',
    )
    roles = [role for name in role_names for role in az('role', 'definition', 'list', '--name', name)]
    print(f'Preview: {len(resources)} demo resources, {len(assignments)} demo-identity grants, {len(roles)} custom role definitions')
    if not args.apply:
        return
    assert os.environ.get('LEARNINGNEMO_AZURE_DELETE') == 'cloud-demo'
    for assignment in assignments:
        az('role', 'assignment', 'delete', '--ids', assignment['id'])
    for role in roles:
        remaining = az('role', 'assignment', 'list', '--role', role['name'], '--all')
        assert not remaining, 'Custom role still assigned; do not delete shared permissions'
        az('role', 'definition', 'delete', '--name', role['name'])
    az('group', 'delete', '--name', group, '--yes')
    assert not az('group', 'exists', '--name', group)
    print('PASS cloud demo group and scoped grants removed; SAW and existing services untouched')


if __name__ == '__main__':
    main()