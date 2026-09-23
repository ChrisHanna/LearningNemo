"""Apply the exact Planning HTTPS policy correction and run its fixed proof."""

import base64
import hashlib
import json
import os
from pathlib import Path
import sys

from diagnostic_attempt import DiagnosticAttempt


def main():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / 'src'))
    from task_agent.console.live_workspace import LiveWorkspace
    attempt = DiagnosticAttempt(Path.home() / '.local/state/learningnemo/proof-attempts', 'tls-policy')
    subscription = os.environ['AZURE_SUBSCRIPTION_ID']
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'planning-tls-policy' or attempt.command('account', ['account', 'show'])['id'] != subscription:
        raise ValueError('explicit policy update acknowledgement and matching subscription required')
    workspace = LiveWorkspace(subscription)
    workspace._az = lambda arguments, timeout=30: attempt.command('workspace-query', arguments, timeout=timeout)
    if not workspace.check()['readyForProbe']:
        raise ValueError('runtime policy and lease must verify')
    app = attempt.command('diagnostic-endpoint', ['containerapp', 'show', '-g', 'rg-learningnemo-platform-dev', '-n', 'ca-learningnemo-diagnostic-dev'])
    host = app['properties']['configuration']['ingress']['fqdn']
    policy = (root / 'infra/next-phase/openshell/planning-policy.yaml').read_bytes().replace(b'__DIAGNOSTIC_HOST__', host.encode())
    previous = policy.replace(b'        tls: terminate\r\n', b'').replace(b'        tls: terminate\n', b'')
    assert previous != policy
    script = f'''#!/usr/bin/env bash
set -euo pipefail
umask 077
target=/home/sawadmin/.config/learningnemo/planning-policy.yaml
[[ "$(sha256sum "$target" | cut -d' ' -f1)" == '{hashlib.sha256(previous).hexdigest()}' ]]
backup=/var/lib/learningnemo-saw/planning-policy-before-{attempt.run_id}
cp -a "$target" "$backup"
temporary=$(mktemp)
trap 'rm -f "$temporary"' EXIT
printf '%s' '{base64.b64encode(policy).decode()}' | base64 -d > "$temporary"
install -o sawadmin -g sawadmin -m 0400 "$temporary" "$target"
runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus timeout 90 openshell --gateway openshell policy set planning-demo --policy "$target" --wait --timeout 60
printf 'PASS TLS_POLICY_{attempt.run_id}\\n'
'''
    result = attempt.command('tls-policy', ['vm', 'run-command', 'invoke', '-g', 'rg-learningnemo-saw-dev', '-n', 'vm-learningnemo-saw-dev', '--command-id', 'RunShellScript', '--scripts', script], timeout=150)
    if not any('PASS TLS_POLICY_' + attempt.run_id in entry.get('message', '') for entry in result.get('value', [])):
        print(json.dumps(result), flush=True)
        raise ValueError('policy acknowledgement absent')
    proof = workspace.run(attempt.run_id)
    attempt.record('proof', proof)
    print(json.dumps(proof, indent=2), flush=True)
    print('EVIDENCE', attempt.directory, flush=True)
    if proof['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()