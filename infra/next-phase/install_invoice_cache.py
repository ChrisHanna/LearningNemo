"""Install an image-only rootless cache during bounded, recorded host maintenance."""

import ipaddress
import json
import os
from pathlib import Path

from diagnostic_attempt import DiagnosticAttempt
from renew_preserved_workspace import normalized_rules


ROOT = Path(__file__).resolve().parents[2]
GROUP = 'rg-learningnemo-saw-dev'
NSG = 'nsg-vnet-learningnemo-saw-dev-workspace'
RULE = 'allow-invoice-package-maintenance'
PREFLIGHT = '''#!/usr/bin/env bash
set -euo pipefail
runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR=/run/user/1000 openshell --gateway openshell sandbox list --output json | python3 -c 'import json,sys; items=json.load(sys.stdin); assert isinstance(items,list) and all(item["phase"]=="Stopped" for item in items), "every sandbox must be stopped"'
python3 - <<'PY'
import json,socket
addresses=sorted({item[4][0] for item in socket.getaddrinfo('azure.archive.ubuntu.com',80,family=socket.AF_INET,type=socket.SOCK_STREAM)})
print('PACKAGE_MIRROR '+json.dumps(addresses))
PY
'''


def mirror_addresses(response):
    lines = [line for item in response.get('value', []) for line in item.get('message', '').splitlines() if line.startswith('PACKAGE_MIRROR ')]
    if len(lines) != 1:
        raise ValueError('stopped-sandbox and mirror preflight missing')
    addresses = json.loads(lines[0].split(' ', 1)[1])
    if not 1 <= len(addresses) <= 4 or any(not ipaddress.IPv4Address(address).is_global for address in addresses):
        raise ValueError('bounded public mirror addresses required')
    return [address + '/32' for address in addresses]


def validate_preview(preview, subscription):
    target = f'/subscriptions/{subscription}/resourceGroups/{GROUP}/providers/Microsoft.Network/networkSecurityGroups/{NSG}/securityRules/{RULE}'
    for change in preview['changes']:
        if change['changeType'] in ('Ignore', 'NoChange'):
            continue
        if change['changeType'] != 'Create' or change['resourceId'].casefold() != target.casefold():
            raise ValueError('unexpected package-maintenance change')


def main():
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'invoice-host-maintenance':
        raise ValueError('explicit invoice host maintenance acknowledgement required')
    subscription = os.environ['AZURE_SUBSCRIPTION_ID']
    attempt = DiagnosticAttempt(Path.home() / '.local/state/learningnemo/invoice-maintenance', 'invoice-package-maintenance')
    if attempt.command('account', ['account', 'show'])['id'] != subscription:
        raise ValueError('subscription mismatch')
    vm = attempt.command('workspace', ['vm', 'show', '-g', GROUP, '-n', 'vm-learningnemo-saw-dev'])
    if vm.get('tags', {}).get('owner') != 'learningnemo-portfolio':
        raise ValueError('workspace ownership differs')
    rules_args = ['network', 'nsg', 'rule', 'list', '-g', GROUP, '--nsg-name', NSG]
    before = attempt.command('network-before', rules_args)
    if any(rule['name'] == RULE for rule in before):
        raise ValueError('previous package-maintenance exception requires review')
    run_args = ['vm', 'run-command', 'invoke', '-g', GROUP, '-n', 'vm-learningnemo-saw-dev', '--command-id', 'RunShellScript', '--scripts']
    addresses = mirror_addresses(attempt.command('host-preflight', [*run_args, PREFLIGHT], timeout=180))
    parameters = attempt.directory / 'parameters.json'
    parameters.write_text(json.dumps({'parameters': {'mirrorAddresses': {'value': addresses}}}))
    parameters.chmod(0o600)
    deployment = ['-g', GROUP, '-n', 'learningnemo-invoice-packages', '--template-file', str(ROOT / 'infra/next-phase/invoice-package-maintenance.bicep'), '--parameters', '@' + str(parameters)]
    preview = attempt.command('preview', ['deployment', 'group', 'what-if', *deployment, '--no-pretty-print'])
    validate_preview(preview, subscription)
    try:
        attempt.command('apply', ['deployment', 'group', 'create', *deployment])
        script = (ROOT / 'scripts/install-invoice-image-cache.sh').read_text()
        script = script.replace('set -euo pipefail', 'set -euo pipefail\nexport LEARNINGNEMO_PACKAGE_MAINTENANCE=bounded-host-maintenance', 1)
        response = attempt.command('install', [*run_args, script], timeout=600)
        if not any('PASS rootless image cache ready' in item.get('message', '') for item in response.get('value', [])):
            raise RuntimeError('package install did not return a verified receipt')
    finally:
        attempt.command('remove-exception', ['network', 'nsg', 'rule', 'delete', '-g', GROUP, '--nsg-name', NSG, '-n', RULE])
        after = attempt.command('network-after', rules_args)
        if normalized_rules(before) != normalized_rules(after):
            raise RuntimeError('runtime network policy differs after maintenance')
    print('PASS rootless image cache installed; exact original network policy restored; no sandbox or Azure resource deleted')


if __name__ == '__main__':
    main()