"""Verify retained Execution and Probe boundaries without workload credentials."""

import base64
import json
import os
from pathlib import Path
import re
import shlex
import sys

from diagnostic_attempt import DiagnosticAttempt


IDS = {'planning-demo': '4e820315-1007-4411-9bf1-00ba31fcc66f', 'execution-demo': '1260a3ec-8c9c-4bf2-afda-83461caa4757', 'probe-demo': '1040b726-8656-4c7d-9f25-f7e739dcfe63'}


def proof_script(diagnostic, remediation, nonce):
    if not re.fullmatch('[a-f0-9]{32}', nonce) or not all(re.fullmatch(r'[a-z0-9.-]+\.azurecontainerapps\.io', host) for host in (diagnostic, remediation)):
        raise ValueError('unexpected proof inputs')
    script = '''#!/bin/bash
set -euo pipefail
umask 077
run_user() { runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "$@"; }
config=$(mktemp)
trap 'rm -f "$config"' EXIT
chown sawadmin:sawadmin "$config"
run_user openshell --gateway openshell sandbox list --output json | python3 -c '
import json,sys
items=json.load(sys.stdin)
assert {item["name"]:item["id"] for item in items} == ''' + repr(IDS).replace("'", '"') + '''
assert all(item["phase"]=="Ready" for item in items)
print("RETAINED_INVENTORY three_original_ids_ready")'
'''
    for sandbox, host, path in (('execution-demo', remediation, '/v1/remediations/execute'), ('probe-demo', diagnostic, '/v1/diagnostics/current')):
        guest = f'''set -eu
uid=$(id -u)
[ "$uid" = 998 ]
if /usr/bin/python3 -c 'import os; os.setuid(0)' >/dev/null 2>&1; then exit 9; fi
/bin/grep -qx 'nameserver 168.63.129.16' /etc/resolv.conf
for method in GET POST; do
  metrics=$(/usr/bin/curl -sS --connect-timeout 5 --max-time 30 -o /dev/null -w '%{{http_connect}} %{{http_code}}' -X "$method" 'https://{host}{path}') || true
    printf 'BOUNDARY_{nonce} {sandbox} %s %s %s denied resolver-azure\\n' "$uid" "$method" "$metrics"
done
'''
        command = shlex.quote("printf '%s' '" + base64.b64encode(guest.encode()).decode() + "' | base64 -d | /bin/sh")
        script += f'''chown root:root "$config"
    run_user openshell --gateway openshell sandbox ssh-config {sandbox} > "$config"
    chown sawadmin:sawadmin "$config"
alias_name=$(awk '$1 == "Host" {{print $2; exit}}' "$config")
[[ "$alias_name" =~ ^[A-Za-z0-9._-]+$ ]]
timeout --signal=TERM --kill-after=2s 85s setpriv --reuid=1000 --regid=1000 --init-groups env HOME=/home/sawadmin XDG_RUNTIME_DIR=/run/user/1000 ssh -n -F "$config" -T -o BatchMode=yes -o ConnectTimeout=10 "$alias_name" {command}
'''
    return script


def parse_result(response, nonce):
    messages = '\n'.join(item.get('message', '') for item in response.get('value', []))
    matches = re.findall(rf'^BOUNDARY_{re.escape(nonce)} (execution-demo|probe-demo) 998 (GET|POST) (\d{{3}}) (\d{{3}}) denied resolver-azure$', messages, re.M)
    observed = {(name, method): (connect, http) for name, method, connect, http in matches}
    expected = {('execution-demo', 'GET'): ('200', '403'), ('execution-demo', 'POST'): ('200', '401'), ('probe-demo', 'GET'): ('403', '000'), ('probe-demo', 'POST'): ('403', '000')}
    if len(matches) != 4 or observed != expected or 'RETAINED_INVENTORY three_original_ids_ready' not in messages:
        raise ValueError('boundary checks incomplete or different; no success assumed')
    return {'status': 'passed', 'inventory': IDS, 'uid': 998, 'resolver': '168.63.129.16', 'privilegeEscalation': 'denied', 'execution': {'get': 403, 'post': 401, 'upstreamReached': True}, 'probe': {'getConnect': 403, 'postConnect': 403, 'upstreamReached': False}, 'workloadCredentialsSupplied': False, 'remediationPerformed': False}


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
    from task_agent.console.live_workspace import LiveWorkspace
    attempt = DiagnosticAttempt(Path.home() / '.local/state/learningnemo/boundary-attempts', 'retained-boundaries')
    subscription = os.environ['AZURE_SUBSCRIPTION_ID']
    if attempt.command('account', ['account', 'show'])['id'] != subscription:
        raise ValueError('subscription mismatch')
    workspace = LiveWorkspace(subscription)
    workspace._az = lambda arguments, timeout=30: attempt.command('workspace-query', arguments, timeout=timeout)
    if not workspace.check()['readyForProbe']:
        raise ValueError('workspace does not admit proof')
    hosts = [attempt.command('endpoint-' + name, ['containerapp', 'show', '-g', 'rg-learningnemo-platform-dev', '-n', 'ca-learningnemo-' + name + '-dev'])['properties']['configuration']['ingress']['fqdn'] for name in ('diagnostic', 'remediation')]
    script = proof_script(*hosts, attempt.run_id)
    print('ATTEMPT', attempt.directory, flush=True)
    response = attempt.command('boundary-probe', ['vm', 'run-command', 'invoke', '-g', 'rg-learningnemo-saw-dev', '-n', 'vm-learningnemo-saw-dev', '--command-id', 'RunShellScript', '--scripts', script], timeout=240)
    proof = parse_result(response, attempt.run_id)
    attempt.record('proof', proof)
    print(json.dumps(proof, indent=2), flush=True)


if __name__ == '__main__':
    main()