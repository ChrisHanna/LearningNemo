#!/usr/bin/env python3
"""Apply explicit runtime SNAT without changing the workspace network ACL."""

from datetime import UTC, datetime
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from preflight_platform import validate_budget
from retention import require_cleanup_released
from availability_policy import managed


ROOT = Path(__file__).resolve().parents[2]
STATE = Path.home() / '.local/state/learningnemo'
GROUP = 'rg-learningnemo-saw-dev'


def az(*args):
    result = subprocess.run(['az', *args, '-o', 'json', '--only-show-errors'], capture_output=True, text=True, timeout=600, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def save(name, value):
    path = STATE / name
    with open(os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), 'w') as output:
        json.dump(value, output, indent=2)
    path.chmod(0o600)
    return path


def check_preview(preview, subscription):
    prefix = f'/subscriptions/{subscription}/resourcegroups/{GROUP}/providers/microsoft.network/'
    allowed = {prefix + 'publicipaddresses/pip-learningnemo-saw-runtime-dev',
               prefix + 'natgateways/nat-learningnemo-saw-runtime-dev',
               prefix + 'virtualnetworks/vnet-learningnemo-saw-dev/subnets/snet-workspace'}
    for change in preview['changes']:
        if change['changeType'] in ('Ignore', 'NoChange'):
            continue
        if change['changeType'] not in ('Create', 'Modify', 'Deploy') or change['resourceId'].lower() not in allowed:
            raise ValueError('unexpected runtime egress preview change')


def remove_runtime_egress():
    require_cleanup_released(STATE)
    if os.environ.get('LEARNINGNEMO_AZURE_DELETE') != 'runtime-approved-egress':
        raise ValueError('explicit runtime egress rollback acknowledgement required')
    subscription = os.environ['AZURE_SUBSCRIPTION_ID']
    if az('account','show')['id'] != subscription:
        raise ValueError('selected subscription mismatch')
    owned = json.loads((STATE / 'workspace-runtime-egress.verified.json').read_text())
    nat = az('network','nat','gateway','show','-g',GROUP,'-n','nat-learningnemo-saw-runtime-dev')
    address = az('network','public-ip','show','-g',GROUP,'-n','pip-learningnemo-saw-runtime-dev')
    vm = az('vm','show','-g',GROUP,'-n','vm-learningnemo-saw-dev')
    if nat['id'].casefold() != owned['natId'].casefold() or vm.get('tags',{}).get('owner') != 'learningnemo-portfolio':
        raise ValueError('rollback ownership record mismatch')
    for resource in (nat,address):
        if resource.get('tags',{}).get('owner') != 'learningnemo-portfolio' or resource['tags'].get('purpose') != 'runtime-approved-egress':
            raise ValueError('runtime egress ownership mismatch')
    subnet_args = ('network','vnet','subnet','show','-g',GROUP,'--vnet-name','vnet-learningnemo-saw-dev','-n','snet-workspace')
    subnet = az(*subnet_args)
    if subnet.get('natGateway',{}).get('id','').casefold() != nat['id'].casefold():
        raise ValueError('unexpected subnet association; inspect before rollback')
    rules = az('network','nsg','rule','list','-g',GROUP,'--nsg-name','nsg-vnet-learningnemo-saw-dev-workspace')
    if any(rule['name']=='allow-bootstrap-ghcr-https' for rule in rules):
        raise ValueError('bootstrap exception still present; resolve provisioning first')
    save('workspace-runtime-egress.rollback-before.json', {'vm':vm,'nat':nat,'publicIp':address,'subnet':subnet,'rules':rules})
    az('vm','deallocate','-g',GROUP,'-n','vm-learningnemo-saw-dev')
    az('network','vnet','subnet','update','-g',GROUP,'--vnet-name','vnet-learningnemo-saw-dev','-n','snet-workspace','--remove','natGateway')
    current = az(*subnet_args)
    if current.get('natGateway') or current.get('defaultOutboundAccess') is not False or any(current[name]['id'].casefold()!=subnet[name]['id'].casefold() for name in ('networkSecurityGroup','routeTable')):
        raise ValueError('rollback subnet verification failed')
    az('network','nat','gateway','delete','-g',GROUP,'-n','nat-learningnemo-saw-runtime-dev')
    az('network','public-ip','delete','-g',GROUP,'-n','pip-learningnemo-saw-runtime-dev')
    if az('network','nsg','rule','list','-g',GROUP,'--nsg-name','nsg-vnet-learningnemo-saw-dev-workspace') != rules:
        raise ValueError('network policy changed during rollback')
    view = az('vm','get-instance-view','-g',GROUP,'-n','vm-learningnemo-saw-dev')
    if not any(item['code']=='PowerState/deallocated' for item in view['instanceView']['statuses']):
        raise ValueError('workspace not deallocated')
    remaining = az('resource','list','-g',GROUP)
    if any(item['name'] in (nat['name'],address['name']) for item in remaining):
        raise ValueError('runtime egress resource cleanup incomplete')
    save('workspace-runtime-egress.rollback-verified.json', {'checkedAt':datetime.now(UTC).isoformat(), 'vmPreserved':True, 'vmDeallocated':True, 'natRemoved':True, 'publicIpRemoved':True, 'policyUnchanged':True})
    print('PASS recovery session closed: VM/disk preserved and deallocated, runtime NAT/IP removed, network policy unchanged', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remove', action='store_true')
    args = parser.parse_args()
    if args.remove:
        remove_runtime_egress()
        return
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'runtime-approved-egress':
        raise ValueError('explicit runtime-approved-egress acknowledgement required')
    account = az('account', 'show')
    if account['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    validate_budget(account, required=True, maximum_amount=50)
    vm = az('vm', 'show', '-g', GROUP, '-n', 'vm-learningnemo-saw-dev')
    subnet_args = ('network', 'vnet', 'subnet', 'show', '-g', GROUP, '--vnet-name', 'vnet-learningnemo-saw-dev', '-n', 'snet-workspace')
    subnet = az(*subnet_args)
    rule_args = ('network', 'nsg', 'rule', 'list', '-g', GROUP, '--nsg-name', 'nsg-vnet-learningnemo-saw-dev-workspace')
    rules = az(*rule_args)
    stack = az('stack', 'group', 'show', '-g', GROUP, '-n', 'learningnemo-saw-runtime-lock-dev')
    sys.path.insert(0, str(ROOT / 'src'))
    from task_agent.console.live_workspace import cloud_snapshot
    if cloud_snapshot(vm, stack.get('properties', stack), rules, subnet)['runtimeLock'] != 'deployed':
        raise ValueError('full runtime network deny policy must verify')
    if vm.get('tags', {}).get('owner') != 'learningnemo-portfolio' or subnet['addressPrefix'] != '10.50.0.0/27':
        raise ValueError('workspace ownership or subnet mismatch')
    for property_name, suffix in (('networkSecurityGroup', 'networkSecurityGroups/nsg-vnet-learningnemo-saw-dev-workspace'),
                                  ('routeTable', 'routeTables/rt-vnet-learningnemo-saw-dev-workspace')):
        expected = f'/subscriptions/{account["id"]}/resourceGroups/{GROUP}/providers/Microsoft.Network/{suffix}'
        if subnet[property_name]['id'].casefold() != expected.casefold():
            raise ValueError('subnet policy association differs')
    expected_nat = f'/subscriptions/{account["id"]}/resourceGroups/{GROUP}/providers/Microsoft.Network/natGateways/nat-learningnemo-saw-runtime-dev'
    if subnet.get('natGateway') and subnet['natGateway']['id'].casefold() != expected_nat.casefold():
        raise ValueError('unexpected existing NAT association')
    for resource in az('resource', 'list', '-g', GROUP):
        if resource['name'] in ('pip-learningnemo-saw-runtime-dev', 'nat-learningnemo-saw-runtime-dev'):
            if resource.get('tags', {}).get('owner') != 'learningnemo-portfolio' or resource['tags'].get('purpose') != 'runtime-approved-egress':
                raise ValueError('runtime egress ownership collision')
    availability_mode = 'operator-managed' if managed(vm) else 'leased'
    expiry = None if availability_mode=='operator-managed' else min(datetime.fromisoformat(json.loads((STATE / name).read_text())['parameters']['expiresAt']['value'].replace('Z', '+00:00'))
                 for name in ('runtime-dev.parameters.json', 'workloads-dev.parameters.json', 'database-network-dev.parameters.json'))
    if expiry is not None and not 900 < (expiry - datetime.now(UTC)).total_seconds() <= 7200:
        raise ValueError('runtime egress needs a bounded live dependency window')
    save('workspace-runtime-egress.before.json', {'subnet': subnet, 'rules': rules})
    path = save('workspace-runtime-egress.parameters.json', {'parameters': {'location': {'value': 'eastus'}, 'expiresAt': {'value': expiry.isoformat() if expiry else ''}, 'availabilityMode':{'value':availability_mode}, 'attach': {'value': True}}})
    template = str(ROOT / 'infra/next-phase/workspace-runtime-egress.bicep')
    preview = az('deployment', 'group', 'what-if', '-g', GROUP, '-n', 'learningnemo-runtime-egress-dev', '--template-file', template,
                 '--parameters', '@' + str(path), '--result-format', 'FullResourcePayloads', '--no-pretty-print')
    save('workspace-runtime-egress.what-if.json', preview)
    check_preview(preview, account['id'])
    print('PASS runtime NAT preview: only named outbound translation and subnet association', flush=True)
    result = az('deployment', 'group', 'create', '-g', GROUP, '-n', 'learningnemo-runtime-egress-dev', '--template-file', template, '--parameters', '@' + str(path))
    if result['properties']['provisioningState'] != 'Succeeded' or az(*rule_args) != rules:
        raise ValueError('runtime egress deployment or unchanged NSG verification failed')
    current = az(*subnet_args)
    for property_name in ('networkSecurityGroup', 'routeTable'):
        if current[property_name]['id'].casefold() != subnet[property_name]['id'].casefold():
            raise ValueError('subnet security association changed')
    if current.get('natGateway', {}).get('id', '').casefold() != expected_nat.casefold():
        raise ValueError('runtime translation is not attached')
    save('workspace-runtime-egress.verified.json', {'natId': expected_nat, 'expiresAt': expiry.isoformat() if expiry else None, 'availabilityMode':availability_mode, 'nsgUnchanged': True, 'checkedAt': datetime.now(UTC).isoformat()})
    print('PASS runtime SNAT deployed; inbound, SQL, IMDS, east-west and Internet deny rules unchanged', flush=True)


if __name__ == '__main__':
    main()