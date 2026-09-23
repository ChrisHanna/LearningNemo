#!/usr/bin/env python3
"""Recreate expired sandboxes only after matching the preserved policy hashes."""

import hashlib
import argparse
import base64
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import ipaddress
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diagnostic_attempt import DiagnosticAttempt
from collect_runtime_diagnostics import collect_guest


ROOT = Path(__file__).resolve().parents[2]
STATE = Path.home() / '.local/state/learningnemo'


def az(*args):
    result = subprocess.run(['az', *args, '-o', 'json', '--only-show-errors'], capture_output=True, text=True, timeout=600, check=True)
    return json.loads(result.stdout)


def registry_addresses(response):
    lines = [line for item in response.get('value', []) for line in item.get('message', '').splitlines() if line.startswith('REGISTRY_DNS ')]
    if len(lines) != 1:
        raise ValueError('one VM registry DNS receipt required')
    addresses = json.loads(lines[0].split(' ', 1)[1])
    if set(addresses) != {'ghcr.io', 'pkg-containers.githubusercontent.com'}:
        raise ValueError('unexpected registry hostname')
    result = set()
    for values in addresses.values():
        if not isinstance(values, list) or not values:
            raise ValueError('missing registry address')
        for value in values:
            address = ipaddress.IPv4Address(value)
            if not address.is_global:
                raise ValueError('registry address must be public')
            result.add(str(address) + '/32')
    if not 1 <= len(result) <= 12:
        raise ValueError('bounded registry address set unavailable')
    return sorted(result)


def pin_registry(script, response, nonce):
    addresses = registry_addresses(response)
    line = next(line for item in response['value'] for line in item.get('message', '').splitlines() if line.startswith('REGISTRY_DNS '))
    ghcr = sorted(json.loads(line.split(' ', 1)[1])['ghcr.io'])[0]
    if len(nonce) != 32 or any(letter not in '0123456789abcdef' for letter in nonce):
        raise ValueError('invalid host pin identifier')
    return script.replace('__GHCR_IP__', str(ipaddress.IPv4Address(ghcr))).replace('__PIN_ID__', nonce), addresses


def run(attempt, start_existing=False, repair_resolvers=False, rebootstrap=False):
    subscription = os.environ['AZURE_SUBSCRIPTION_ID']
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'resume-owned-sandboxes' or az('account', 'show')['id'] != subscription:
        raise ValueError('expected subscription and resume-owned-sandboxes acknowledgement required')
    sys.path.insert(0, str(ROOT / 'src'))
    from task_agent.console.live_workspace import LiveWorkspace
    workspace = LiveWorkspace(subscription)
    workspace._az = lambda arguments, timeout=30: attempt.command('workspace-query', arguments, timeout=timeout)
    snapshot = workspace.check()
    attempt.record('workspace-snapshot', snapshot)
    if not snapshot['readyForProbe']:
        print(json.dumps(snapshot), flush=True)
        raise ValueError('workspace must pass identity, network and real lease checks')
    script = (ROOT / 'scripts/resume-saw-sandboxes.sh').read_text()
    script = script.replace('__ACTION__', 'rebootstrap' if rebootstrap else 'repair-resolvers' if repair_resolvers else 'start-existing' if start_existing else 'create')
    script = script.replace('__BOOTSTRAP_HELPER_B64__', base64.b64encode((ROOT / 'scripts/repair-sandbox-bootstrap.py').read_bytes()).decode())
    script = script.replace('__RESOLVER_HELPER_B64__', base64.b64encode((ROOT / 'scripts/repair-sandbox-resolver.sh').read_bytes()).decode())
    script = script.replace('__LOG_HELPER_B64__', base64.b64encode((ROOT / 'infra/next-phase/diagnostic_attempt.py').read_bytes()).decode())
    policy_hashes = {}
    for mode, app, placeholder in [('planning','diagnostic','__DIAGNOSTIC_HOST__'), ('execution','remediation','__REMEDIATION_HOST__'), ('probe',None,None)]:
        policy = (ROOT / f'infra/next-phase/openshell/{mode}-policy.yaml').read_bytes().decode('utf-8')
        if app:
            host = attempt.command('worker-endpoint', ['containerapp','show','-g','rg-learningnemo-platform-dev','-n',f'ca-learningnemo-{app}-dev'])['properties']['configuration']['ingress']['fqdn']
            policy = policy.replace(placeholder, host)
        policy_hashes[mode] = hashlib.sha256(policy.encode()).hexdigest()
        script = script.replace(f'__{mode.upper()}_HASH__', policy_hashes[mode])
        script = script.replace(f'__{mode.upper()}_POLICY_B64__', base64.b64encode(policy.encode()).decode())
    config = json.loads((ROOT / 'infra/next-phase/environments/dev.workspace.config.json').read_text())
    script = script.replace('__SANDBOX_IMAGE__', config['sandboxImageReference'])
    attempt.record('expected-runtime', {'policyHashes': policy_hashes, 'sandboxImage': config['sandboxImageReference']})
    dns_script = """#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json,socket
hosts=('ghcr.io','pkg-containers.githubusercontent.com')
print('REGISTRY_DNS '+json.dumps({host:sorted({item[4][0] for item in socket.getaddrinfo(host,443,family=socket.AF_INET,type=socket.SOCK_STREAM)}) for host in hosts}))
PY
"""
    dns_receipt = attempt.command('registry-dns', ['vm','run-command','invoke','-g','rg-learningnemo-saw-dev','-n','vm-learningnemo-saw-dev','--command-id','RunShellScript','--scripts',dns_script])
    script, registry_ips = pin_registry(script, dns_receipt, attempt.run_id)
    parameters = {'parameters': {'registryAddresses': {'value': registry_ips}}}
    path = attempt.directory / 'registry.parameters.json'
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
        json.dump(parameters, output)
    attempt.record('registry-rule-input', {'addresses': registry_ips, 'hostPin': script.split("pin_line='")[1].split("'")[0]})
    template = str(ROOT / 'infra/next-phase/workspace-registry-bootstrap.bicep')
    preview = attempt.command('registry-preview', ['deployment','group','what-if','-g','rg-learningnemo-saw-dev','-n','learningnemo-registry-bootstrap-dev',
                 '--template-file',template,'--parameters','@'+str(path),'--result-format','FullResourcePayloads','--no-pretty-print'])
    expected_rule = '/networksecuritygroups/nsg-vnet-learningnemo-saw-dev-workspace/securityrules/allow-bootstrap-ghcr-https'
    for change in preview['changes']:
        if change['changeType'] in ('Ignore','NoChange'): continue
        if change['changeType'] not in ('Create','Modify') or not change['resourceId'].lower().endswith(expected_rule):
            raise ValueError('unexpected registry bootstrap preview')
    rules_before = workspace._az(['network','nsg','rule','list','--resource-group','rg-learningnemo-saw-dev','--nsg-name','nsg-vnet-learningnemo-saw-dev-workspace'])
    primary_error = None
    cleanup_error = None
    guest_started = False
    try:
        attempt.command('registry-apply', ['deployment','group','create','-g','rg-learningnemo-saw-dev','-n','learningnemo-registry-bootstrap-dev','--template-file',template,'--parameters','@'+str(path)])
        attempt.command('registry-rule-observed', ['network','nsg','rule','show','-g','rg-learningnemo-saw-dev','--nsg-name','nsg-vnet-learningnemo-saw-dev-workspace','-n','allow-bootstrap-ghcr-https'])
        attempt.command('effective-nsg', ['network','nic','list-effective-nsg','-g','rg-learningnemo-saw-dev','-n','nic-vm-learningnemo-saw-dev'])
        guest_started = True
        result = attempt.command('sandbox-provision', ['vm','run-command','invoke','-g','rg-learningnemo-saw-dev','-n','vm-learningnemo-saw-dev','--command-id','RunShellScript','--scripts',script])
        message = '\n'.join(entry.get('message','') for entry in result.get('value',[]))
        if 'PASS restored_three_pinned_sandboxes' not in message:
            raise ValueError('guest provisioning failed or output was truncated; inspect per-attempt host logs')
        attempt.event('sandbox-provision', 'verified')
    except Exception as error:
        primary_error = error
        attempt.event('sandbox-provision', 'failed', errorType=type(error).__name__, detail=str(error))
    finally:
        try:
            attempt.command('registry-cleanup', ['network','nsg','rule','delete','-g','rg-learningnemo-saw-dev','--nsg-name','nsg-vnet-learningnemo-saw-dev-workspace','-n','allow-bootstrap-ghcr-https'], timeout=120)
            rules_after = workspace._az(['network','nsg','rule','list','--resource-group','rg-learningnemo-saw-dev','--nsg-name','nsg-vnet-learningnemo-saw-dev-workspace'])
            def policy(items):
                return {item['name']: {key: value for key, value in item.items() if key not in {'etag', 'provisioningState'}} for item in items}
            if policy(rules_after) != policy(rules_before):
                raise ValueError('network policy differs after bootstrap cleanup')
            attempt.event('registry-cleanup', 'verified')
        except Exception as error:
            cleanup_error = error
            attempt.event('registry-cleanup', 'failed', errorType=type(error).__name__, detail=str(error))
        if guest_started:
            try:
                logs = collect_guest(attempt, attempt.run_id)
                attempt.event('guest-logs', 'verified', **logs)
            except Exception as error:
                attempt.event('guest-logs', 'unavailable', errorType=type(error).__name__, detail=str(error),
                    path=f'/var/lib/learningnemo-saw/attempts/{attempt.run_id}',
                    recovery='collect_runtime_diagnostics.py --guest-attempt ' + attempt.run_id)
                if primary_error is None:
                    primary_error = RuntimeError('guest diagnostics not verified; do not run the proof yet')
    if primary_error or cleanup_error:
        raise RuntimeError(f'Attempt {attempt.run_id}: provisioning={"failed" if primary_error else "returned"}; network cleanup={"failed" if cleanup_error else "verified"}; evidence retained') from None
    print('PASS three sandboxes restored from pinned image and matching policies', flush=True)
    proof = workspace.run(attempt.run_id)
    attempt.record('runtime-proof', proof)
    print(json.dumps({'status': proof['status'], 'receipt': proof.get('receipt'), 'cloud': proof.get('cloud')}, indent=2), flush=True)
    if proof['status'] != 'passed':
        raise ValueError('fresh workspace route proof did not pass')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-existing', action='store_true')
    parser.add_argument('--repair-resolvers', action='store_true')
    parser.add_argument('--rebootstrap', action='store_true')
    args = parser.parse_args()
    attempt = DiagnosticAttempt(STATE / 'sandbox-attempts', 'sandbox-resume')
    print(f'ATTEMPT {attempt.run_id} / {attempt.directory}', flush=True)
    try:
        run(attempt, args.start_existing, args.repair_resolvers, args.rebootstrap)
    except Exception as error:
        attempt.event('attempt', 'failed', errorType=type(error).__name__, detail=str(error))
        print(f'FAIL attempt {attempt.run_id}; inspect {attempt.directory}/events.jsonl', flush=True)
        raise SystemExit(1) from None
    attempt.event('attempt', 'verified')


if __name__ == '__main__':
    main()