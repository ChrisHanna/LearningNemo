"""Create and stop one retained invoice MicroVM; no model or database call."""

import base64
import json
import os
from pathlib import Path
import re

from diagnostic_attempt import DiagnosticAttempt
from renew_preserved_workspace import normalized_rules
from resume_workspace_sandboxes import pin_registry


ROOT = Path(__file__).parents[2]
STATE = Path.home() / '.local/state/learningnemo'
GROUP = 'rg-learningnemo-saw-dev'
NSG = 'nsg-vnet-learningnemo-saw-dev-workspace'
RUN = ['vm','run-command','invoke','-g',GROUP,'-n','vm-learningnemo-saw-dev','--command-id','RunShellScript','--scripts']


def guest_script(run_id, image):
    if not re.fullmatch('[a-f0-9]{32}', run_id) or not re.fullmatch(r'crlearningnemodevgruyrc4qwdvvm\.azurecr\.io/learningnemo/invoice-agent@sha256:[a-f0-9]{64}', image):
        raise ValueError('invalid sandbox smoke context')
    policy = base64.b64encode((ROOT / 'infra/next-phase/openshell/invoice-planning-policy.yaml').read_bytes()).decode()
    return r'''#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /home/sawadmin
directory=/var/lib/learningnemo-saw/invoice-smoke-__RUN__
mkdir -m 0755 "$directory"
policy_directory=/var/lib/learningnemo-invoice-cache/policies
install -d -m 0755 "$policy_directory"
printf '%s' '__POLICY__' | base64 -d > "$policy_directory/__RUN__.yaml"
chmod 0644 "$policy_directory/__RUN__.yaml"
run_id=__RUN__
name="ip-${run_id:0:16}"
run_user() { runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "$@"; }
next=$(systemctl show learningnemo-saw-expire.timer -p NextElapseUSecRealtime --value)
[[ "$(date -u -d "$next" +%s)" -gt "$(( $(date -u +%s) + 600 ))" ]]
run_user openshell --gateway openshell sandbox list --output json > "$directory/before.json"
python3 - "$directory/before.json" <<'PY'
import json,sys
items=json.load(open(sys.argv[1]))
assert all(item['phase']=='Stopped' for item in items), 'all retained sandboxes must be stopped'
assert len(items)<10, 'retention capacity exceeded'
PY
pin_line='__GHCR_IP__ ghcr.io # learningnemo-registry-pin-__PIN_ID__'
if grep -E '(^|[[:space:]])ghcr\.io([[:space:]]|$)' /etc/hosts; then exit 1; fi
created=false
cleanup() {
    status=$?
    trap - EXIT
    if [[ "$created" == true ]]; then run_user openshell --gateway openshell sandbox stop "$name" >> "$directory/stop.log" 2>&1 || status=91; fi
    sed -i '/ # learningnemo-registry-pin-__PIN_ID__$/d' /etc/hosts
    exit "$status"
}
trap cleanup EXIT
printf '\n%s\n' "$pin_line" >> /etc/hosts
created=true
run_user env OPENSHELL_PROVISION_TIMEOUT=300 openshell --gateway openshell sandbox create --name "$name" --from '__IMAGE__' --policy "$policy_directory/__RUN__.yaml" --detach -- /bin/sleep infinity > "$directory/create.log" 2>&1
run_user openshell --gateway openshell sandbox get "$name" --output json > "$directory/observed.json"
run_user openshell --gateway openshell sandbox get "$name" --policy-only > "$directory/applied.yaml"
run_user openshell --gateway openshell sandbox exec --name "$name" --no-tty --timeout 90 -- /opt/venv/bin/python -c 'import os,json,nat; from task_agent.control.invoice_agent import register_tools; register_tools(); assert os.getuid()!=0; print("INVOICE_RUNTIME_READY "+json.dumps({"uid":os.getuid(),"nemoImported":True}))' > "$directory/import.log" 2>&1
cat "$directory/import.log"
python3 - "$directory/observed.json" <<'PY'
import json,sys
record=json.load(open(sys.argv[1]))
print('INVOICE_SANDBOX_METADATA '+json.dumps({key:record.get(key) for key in ('id','name','phase')}))
PY
printf 'PASS invoice_sandbox_smoke __RUN__\n'
'''.replace('__RUN__', run_id).replace('__POLICY__', policy).replace('__IMAGE__', image)


def main():
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'invoice-sandbox-smoke':
        raise ValueError('explicit invoice sandbox smoke acknowledgement required')
    attempt = DiagnosticAttempt(STATE / 'invoice-sandbox-smoke', 'invoice-sandbox-smoke')
    account = attempt.command('account', ['account','show'])
    if account['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    vm = attempt.command('workspace', ['vm','show','-g',GROUP,'-n','vm-learningnemo-saw-dev'])
    if vm.get('tags',{}).get('owner') != 'learningnemo-portfolio':
        raise ValueError('workspace ownership differs')
    image = json.loads((STATE / 'invoice-image-staged.verified.json').read_text())['image']
    script = guest_script(attempt.run_id, image)
    dns = attempt.command('registry-dns', [*RUN, "#!/usr/bin/env bash\npython3 -c \"import json,socket; print('REGISTRY_DNS '+json.dumps({host:sorted({item[4][0] for item in socket.getaddrinfo(host,443,family=socket.AF_INET,type=socket.SOCK_STREAM)}) for host in ('ghcr.io','pkg-containers.githubusercontent.com')}))\""])
    script, addresses = pin_registry(script, dns, attempt.run_id)
    rule_args = ['network','nsg','rule','list','-g',GROUP,'--nsg-name',NSG]
    before = attempt.command('network-before', rule_args)
    if any(item['name']=='allow-bootstrap-ghcr-https' for item in before):
        raise ValueError('existing bootstrap exception must be reviewed')
    parameters = attempt.directory / 'registry.json'
    parameters.write_text(json.dumps({'parameters': {'registryAddresses': {'value': addresses}}}))
    deployment = ['-g',GROUP,'-n','learningnemo-invoice-smoke','--template-file',str(ROOT/'infra/next-phase/workspace-registry-bootstrap.bicep'),'--parameters','@'+str(parameters)]
    preview = attempt.command('preview', ['deployment','group','what-if',*deployment,'--no-pretty-print'])
    for change in preview['changes']:
        if change['changeType'] in ('NoChange','Ignore'):
            continue
        if change['changeType'] != 'Create' or not change['resourceId'].lower().endswith('/securityrules/allow-bootstrap-ghcr-https'):
            raise ValueError('unexpected smoke network change')
    try:
        attempt.command('allow-registry', ['deployment','group','create',*deployment])
        response = attempt.command('sandbox-smoke', [*RUN,script], timeout=600)
        message = '\n'.join(item.get('message','') for item in response.get('value',[]))
        if 'PASS invoice_sandbox_smoke '+attempt.run_id not in message:
            raise RuntimeError('sandbox smoke failed; retained guest log: /var/lib/learningnemo-saw/invoice-smoke-'+attempt.run_id)
        print(message)
    finally:
        attempt.command('remove-registry', ['network','nsg','rule','delete','-g',GROUP,'--nsg-name',NSG,'-n','allow-bootstrap-ghcr-https'])
        after = attempt.command('network-after', rule_args)
        if normalized_rules(before) != normalized_rules(after):
            raise RuntimeError('network policy differs after sandbox smoke')
    print('PASS new retained invoice sandbox imported NeMo; original network rules restored')


if __name__ == '__main__':
    main()