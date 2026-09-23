"""Repair a cloned OS disk offline; preserve the original disk and all resources."""

from datetime import UTC, datetime, timedelta
import argparse
import hashlib
import json
import os
from pathlib import Path

from diagnostic_attempt import DiagnosticAttempt
from preflight_platform import validate_budget


ROOT = Path(__file__).resolve().parents[2]
STATE = Path.home() / '.local/state/learningnemo'
GROUP = 'rg-learningnemo-saw-dev'
REPAIR_GROUP = 'rg-learningnemo-repair-dev'
VM = 'vm-learningnemo-saw-dev'
DISK = 'osdisk-learningnemo-saw-retained-repair'


def validate_stopped_vm(vm):
    if vm.get('tags', {}).get('owner') != 'learningnemo-portfolio' or vm['tags'].get('project') != 'learningnemo':
        raise ValueError('workspace ownership mismatch')
    if not any(item['code'] == 'PowerState/deallocated' for item in vm['instanceView']['statuses']):
        raise ValueError('original VM must remain deallocated during offline repair')
    if vm['storageProfile']['osDisk']['name'] != 'osdisk-vm-learningnemo-saw-dev':
        raise ValueError('unexpected original disk; inspect prior recovery before retrying')


def check_preview(preview, subscription):
    expected = {
        'Microsoft.Network/publicIPAddresses/pip-learningnemo-repair-egress',
        'Microsoft.Network/natGateways/nat-learningnemo-repair',
        'Microsoft.Network/networkSecurityGroups/nsg-learningnemo-repair',
        'Microsoft.Network/virtualNetworks/vnet-learningnemo-repair',
        'Microsoft.Network/networkInterfaces/nic-learningnemo-repair',
        'Microsoft.Compute/virtualMachines/vm-learningnemo-repair',
    }
    allowed = {f'/subscriptions/{subscription}/resourceGroups/{REPAIR_GROUP}/providers/{item}'.casefold() for item in expected}
    for change in preview['changes']:
        if change['changeType'] in {'NoChange', 'Ignore'}:
            continue
        if change['changeType'] != 'Create' or change['resourceId'].casefold() not in allowed:
            raise ValueError('unexpected offline helper preview')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'offline-retained-repair':
        raise ValueError('explicit offline-retained-repair acknowledgement required')
    attempt = DiagnosticAttempt(STATE / 'offline-repairs', 'retained-os-repair')
    command = attempt.command
    print('OFFLINE_REPAIR', attempt.directory, flush=True)
    account = command('account', ['account', 'show'])
    if account['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    validate_budget(account, required=True, maximum_amount=50)
    vm = command('original-vm', ['vm', 'get-instance-view', '-g', GROUP, '-n', VM])
    validate_stopped_vm(vm)
    exists = command('helper-group-exists', ['group', 'exists', '-n', REPAIR_GROUP])
    if exists and not args.resume:
        raise ValueError('repair group already exists; inspect saved attempt rather than replacing resources')
    disk_id = vm['storageProfile']['osDisk']['managedDisk']['id']
    disk = command('original-disk', ['disk', 'show', '--ids', disk_id])
    if disk.get('diskSizeGB') != 64 or disk.get('osType') != 'Linux' or disk.get('hyperVGeneration') != 'V2':
        raise ValueError('unexpected disk type or size')
    expiry = (datetime.now(UTC) + timedelta(hours=4)).isoformat()
    tags = ['owner=learningnemo-portfolio', 'project=learningnemo', 'purpose=offline-retained-repair', 'retention=owner-testing-hold', 'expiresAt=' + expiry]
    if args.resume:
        group = command('helper-group', ['group', 'show', '-n', REPAIR_GROUP])
        snapshot = command('original-snapshot', ['snapshot', 'show', '-g', REPAIR_GROUP, '-n', 'snapshot-learningnemo-saw-original'])
        copied = command('copy-os-disk', ['disk', 'show', '-g', REPAIR_GROUP, '-n', DISK])
        if any(item.get('tags', {}).get('purpose') != 'offline-retained-repair' or item['tags'].get('owner') != 'learningnemo-portfolio' for item in (group, snapshot, copied)):
            raise ValueError('repair resource ownership mismatch')
        if snapshot['creationData']['sourceResourceId'].casefold() != disk_id.casefold() or copied['creationData']['sourceResourceId'].casefold() != snapshot['id'].casefold():
            raise ValueError('repair disk lineage mismatch')
    else:
        command('create-helper-group', ['group', 'create', '-n', REPAIR_GROUP, '-l', 'eastus', '--tags', *tags])
        snapshot = command('original-snapshot', ['snapshot', 'create', '-g', REPAIR_GROUP, '-n', 'snapshot-learningnemo-saw-original', '--source', disk_id, '--incremental', 'true', '--tags', *tags])
        copied = command('copy-os-disk', ['disk', 'create', '-g', REPAIR_GROUP, '-n', DISK, '--source', snapshot['id'], '--sku', 'StandardSSD_LRS', '--tags', *tags])
    values = {'repairDiskId': copied['id'], 'sshPublicKey': vm['osProfile']['linuxConfiguration']['ssh']['publicKeys'][0]['keyData'],
              'imageReference': {key: vm['storageProfile']['imageReference'][key] for key in ('publisher', 'offer', 'sku', 'version')}, 'expiresAt': expiry}
    path = attempt.directory / 'helper.parameters.json'
    with open(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as output:
        json.dump({'parameters': {key: {'value': value} for key, value in values.items()}}, output)
    deployment = ['-g', REPAIR_GROUP, '-n', 'learningnemo-offline-repair', '--template-file', str(ROOT / 'infra/next-phase/retained-repair-host.bicep'), '--parameters', '@' + str(path)]
    if not args.resume:
        preview = command('helper-preview', ['deployment', 'group', 'what-if', *deployment, '--result-format', 'FullResourcePayloads', '--no-pretty-print'])
        check_preview(preview, account['id'])
        command('helper-deploy', ['deployment', 'group', 'create', *deployment], timeout=900)
    helper = command('helper-vm', ['vm', 'show', '-g', REPAIR_GROUP, '-n', 'vm-learningnemo-repair'])
    if helper.get('identity') or helper.get('tags', {}).get('owner') != 'learningnemo-portfolio' or helper['storageProfile']['dataDisks'][0]['managedDisk']['id'].casefold() != copied['id'].casefold():
        raise ValueError('unexpected helper identity or attached disk')
    script = (ROOT / 'scripts/repair-retained-os.sh').read_text().replace('__REPAIR_ID__', attempt.run_id)
    receipt = command('offline-guest-repair', ['vm', 'run-command', 'invoke', '-g', REPAIR_GROUP, '-n', 'vm-learningnemo-repair', '--command-id', 'RunShellScript', '--scripts', script])
    messages = '\n'.join(entry.get('message', '') for entry in receipt.get('value', []))
    if f'PASS OFFLINE_REPAIR_{attempt.run_id}' not in messages:
        raise ValueError('offline repair receipt missing; original VM and disk remain untouched')
    lines = [line for line in messages.splitlines() if line.startswith('OFFLINE_REPAIR ')]
    if len(lines) != 1:
        raise ValueError('ambiguous offline repair receipt')
    repaired = json.loads(lines[0].split(' ', 1)[1])
    if repaired['repairId'] != attempt.run_id or repaired['expiryDeletesWorkspaces'] is not False or repaired['bootTimerDisabled'] is not True:
        raise ValueError('offline repair did not verify retention')
    command('detach-repaired-disk', ['vm', 'disk', 'detach', '-g', REPAIR_GROUP, '--vm-name', 'vm-learningnemo-repair', '-n', DISK])
    command('stop-helper-compute', ['vm', 'deallocate', '-g', REPAIR_GROUP, '-n', 'vm-learningnemo-repair'])
    detached = command('repaired-disk', ['disk', 'show', '--ids', copied['id']])
    if detached.get('managedBy') or detached['diskSizeGB'] != disk['diskSizeGB'] or detached.get('encryption') != disk.get('encryption'):
        raise ValueError('repaired disk not detached or compatible')
    current = command('pre-swap-vm', ['vm', 'get-instance-view', '-g', GROUP, '-n', VM])
    validate_stopped_vm(current)
    command('preserve-original-disk', ['vm', 'update', '-g', GROUP, '-n', VM, '--set', 'storageProfile.osDisk.deleteOption=Detach'])
    command('swap-repaired-disk', ['vm', 'update', '-g', GROUP, '-n', VM, '--os-disk', copied['id']])
    current = command('post-swap-vm', ['vm', 'get-instance-view', '-g', GROUP, '-n', VM])
    if current['storageProfile']['osDisk']['managedDisk']['id'].casefold() != copied['id'].casefold() or not any(item['code'] == 'PowerState/deallocated' for item in current['instanceView']['statuses']):
        raise ValueError('disk swap state not verified')
    original = command('preserved-original', ['disk', 'show', '--ids', disk_id])
    if original.get('managedBy'):
        raise ValueError('original disk unexpectedly attached')
    record = {**repaired, 'vmId': current['id'], 'diskId': copied['id'], 'originalDiskId': disk_id, 'snapshotId': snapshot['id'], 'scriptSha256': hashlib.sha256(script.encode()).hexdigest(), 'checkedAt': datetime.now(UTC).isoformat()}
    target = STATE / 'offline-retention.verified.json'
    with open(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as output:
        json.dump(record, output, indent=2)
    print('PASS offline repair and OS swap verified; original disk, snapshot, helper, and VM retained', flush=True)


if __name__ == '__main__':
    main()