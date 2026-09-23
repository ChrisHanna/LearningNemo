#!/usr/bin/env python3
"""Renew the owned SAW timer without replacing its VM, disk, or sandbox policy."""

import argparse
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from preflight_platform import validate_budget
from diagnostic_attempt import DiagnosticAttempt
from retention import require_safe_retained_boot


ROOT = Path(__file__).resolve().parents[2]
STATE = Path.home() / '.local/state/learningnemo'
GROUP = 'rg-learningnemo-saw-dev'
VM = 'vm-learningnemo-saw-dev'


def az(*args):
    result = subprocess.run(['az', *args, '-o', 'json', '--only-show-errors'], capture_output=True, text=True, timeout=600)
    if result.returncode:
        save('workspace-renewal.error.json', {'stderr': result.stderr})
        raise RuntimeError('Azure renewal operation failed; private evidence retained')
    return json.loads(result.stdout) if result.stdout.strip() else None


def save(name, value):
    path = STATE / name
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
        json.dump(value, stream, indent=2)
    path.chmod(0o600)
    return path


def timer_script(expiry, nonce):
    if not 300 < (expiry - datetime.now(UTC)).total_seconds() <= 7200 or len(nonce) != 32 or any(letter not in '0123456789abcdef' for letter in nonce):
        raise ValueError('invalid renewal context')
    return (ROOT / 'scripts/renew-saw-timer.sh').read_text().replace('__EXPIRY__', expiry.isoformat()).replace('__NONCE__', nonce)


def normalized_rules(rules):
    return {rule['name']: {key: value for key, value in rule.items() if key not in {'etag', 'provisioningState'}} for rule in rules}


@contextmanager
def startup_metadata(attempt, subscription, rules):
    rule_name = 'deny-sandbox-host-imds'
    nsg_name = 'nsg-vnet-learningnemo-saw-dev-workspace'
    expected = f'/subscriptions/{subscription}/resourceGroups/{GROUP}/providers/Microsoft.Network/networkSecurityGroups/{nsg_name}/securityRules/{rule_name}'
    template = str(ROOT / 'infra/next-phase/workspace-runtime-lock.bicep')
    deployment = ['-g', GROUP, '-n', 'learningnemo-startup-maintenance-dev', '--template-file', template, '--parameters', 'environment=dev', 'projectName=learningnemo']
    preview = attempt.command('metadata-preview', ['deployment', 'group', 'what-if', *deployment, '--result-format', 'FullResourcePayloads', '--no-pretty-print'], timeout=120)
    for change in preview['changes']:
        if change['changeType'] in ('Ignore', 'NoChange'):
            continue
        if change['changeType'] != 'Modify' or change['resourceId'].casefold() != expected.casefold():
            raise ValueError('unexpected metadata maintenance change')
    attempt.record('network-before', rules)
    try:
        attempt.command('metadata-apply', ['network', 'nsg', 'rule', 'delete', '-g', GROUP, '--nsg-name', nsg_name, '-n', rule_name], timeout=120)
        print('PASS platform metadata block suspended for startup; sandbox admission remains closed', flush=True)
        yield
    finally:
        attempt.command('metadata-cleanup', ['deployment', 'group', 'create', *deployment], timeout=120)
        after = attempt.command('network-after', ['network', 'nsg', 'rule', 'list', '-g', GROUP, '--nsg-name', nsg_name], timeout=60)
        if normalized_rules(after) != normalized_rules(rules):
            raise ValueError('network differs after maintenance cleanup')
        attempt.event('metadata-cleanup', 'verified')
        print('PASS metadata exception removed; original runtime denies restored', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    account = az('account', 'show')
    if account['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    validate_budget(account, required=True, maximum_amount=50)
    vm = az('vm', 'get-instance-view', '-g', GROUP, '-n', VM)
    if vm.get('tags', {}).get('availabilityMode') == 'operator-managed':
        raise ValueError('operator-managed host: use configure_invoice_availability.py; do not restore a global expiry timer')
    if vm.get('tags', {}).get('owner') != 'learningnemo-portfolio' or vm['tags'].get('project') != 'learningnemo':
        raise ValueError('workspace ownership differs')
    nic = az('network', 'nic', 'show', '-g', GROUP, '-n', 'nic-' + VM)
    if any(item.get('publicIPAddress') for item in nic['ipConfigurations']):
        raise ValueError('workspace has public ingress')
    subnet = az('network', 'vnet', 'subnet', 'show', '-g', GROUP, '--vnet-name', 'vnet-learningnemo-saw-dev', '-n', 'snet-workspace')
    if subnet['addressPrefix'] != '10.50.0.0/27':
        raise ValueError('unexpected existing subnet egress')
    runtime_nat = None
    if subnet.get('natGateway'):
        expected_nat = f'/subscriptions/{account["id"]}/resourceGroups/{GROUP}/providers/Microsoft.Network/natGateways/nat-learningnemo-saw-runtime-dev'
        if subnet['natGateway'].get('id', '').casefold() != expected_nat.casefold():
            raise ValueError('unexpected existing subnet NAT')
        runtime_nat = az('network','nat','gateway','show','-g',GROUP,'-n','nat-learningnemo-saw-runtime-dev')
    rules = az('network', 'nsg', 'rule', 'list', '-g', GROUP, '--nsg-name', 'nsg-vnet-learningnemo-saw-dev-workspace')
    sys.path.insert(0, str(ROOT / 'src'))
    from task_agent.console.live_workspace import cloud_snapshot
    stack = az('stack', 'group', 'show', '-g', GROUP, '-n', 'learningnemo-saw-runtime-lock-dev')
    network = cloud_snapshot(vm, stack.get('properties', stack), rules, subnet, runtime_nat)
    if network['runtimeLock'] != 'deployed' or (runtime_nat and network['nat'] != 'runtime-verified'):
        raise ValueError('runtime NSG lock is not verified')
    expiries = []
    for name in ('runtime-dev.parameters.json', 'database-network-dev.parameters.json', 'workloads-dev.parameters.json'):
        document = json.loads((STATE / name).read_text())
        expiries.append(datetime.fromisoformat(document['parameters']['expiresAt']['value'].replace('Z', '+00:00')))
    expiry = min(datetime.now(UTC) + timedelta(hours=1), *expiries).replace(microsecond=0)
    if expiry <= datetime.now(UTC) + timedelta(minutes=15):
        raise ValueError('dependency lifetime too short for workspace recovery')
    nonce = uuid4().hex
    snapshot = save('workspace-renewal.before-' + nonce + '.json', {'vm': vm, 'nic': nic, 'subnet': subnet, 'rules': rules, 'stack': stack, 'runtimeNat': runtime_nat})
    print('PASS preserved workspace snapshot and unchanged NSG; proposed deadline', expiry.isoformat(), flush=True)
    if not args.apply:
        return
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'renew-preserved-saw':
        raise ValueError('renew-preserved-saw acknowledgement required')
    was_running = any(item['code'] == 'PowerState/running' for item in vm['instanceView']['statuses'])
    require_safe_retained_boot(STATE, running=was_running, vm=vm)
    if not was_running and not any(item['code'] == 'PowerState/deallocated' for item in vm['instanceView']['statuses']):
        raise ValueError('maintenance requires a deallocated VM')
    attempt = DiagnosticAttempt(STATE / 'maintenance-attempts', 'workspace-startup', run_id=nonce)
    try:
        with startup_metadata(attempt, account['id'], rules) if not was_running else nullcontext():
            if not was_running:
                attempt.command('vm-start', ['vm', 'start', '-g', GROUP, '-n', VM], timeout=240)
            result = attempt.command('timer-renew', ['vm', 'run-command', 'invoke', '-g', GROUP, '-n', VM, '--command-id', 'RunShellScript', '--scripts', timer_script(expiry, nonce)], timeout=240)
            save('workspace-renewal.timer-' + nonce + '.json', result)
            marker = f'PASS TIMER_{nonce} {int(expiry.timestamp())}'
            if sum(marker in entry.get('message', '') for entry in result.get('value', [])) != 1:
                raise ValueError('verified host shutdown timer receipt absent')
        az('tag', 'update', '--resource-id', vm['id'], '--operation', 'Merge', '--tags', 'expiresAt=' + expiry.isoformat().replace('+00:00', 'Z'))
        current = az('vm', 'show', '-g', GROUP, '-n', VM)
        if datetime.fromisoformat(current['tags']['expiresAt'].replace('Z', '+00:00')) != expiry:
            raise ValueError('workspace tag differs from real shutdown timer')
        save('workspace-renewal.verified.json', {'snapshot': str(snapshot), 'expiresAt': expiry.isoformat(), 'nonce': nonce, 'timerVerified': True})
        print('PASS same VM renewed with verified real shutdown timer; disk and sandbox policy unchanged', flush=True)
    except Exception as error:
        attempt.event('renewal', 'failed', errorType=type(error).__name__, detail=str(error))
        if not was_running:
            attempt.command('failure-deallocate', ['vm', 'deallocate', '-g', GROUP, '-n', VM])
        raise


if __name__ == '__main__':
    main()