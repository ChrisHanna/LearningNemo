"""Cache the pinned OpenShell bootstrap image without leaving registry egress enabled."""

import json
import os
from pathlib import Path

from diagnostic_attempt import DiagnosticAttempt
from renew_preserved_workspace import normalized_rules
from resume_workspace_sandboxes import pin_registry
from smoke_invoice_sandbox import RUN,GROUP,NSG,ROOT,STATE


SCRIPT=r'''#!/usr/bin/env bash
set -euo pipefail
cd /home/sawadmin
pin_line='__GHCR_IP__ ghcr.io # learningnemo-registry-pin-__PIN_ID__'
if grep -E '(^|[[:space:]])ghcr\.io([[:space:]]|$)' /etc/hosts; then exit 1; fi
trap "sed -i '/ # learningnemo-registry-pin-__PIN_ID__$/d' /etc/hosts" EXIT
printf '\n%s\n' "$pin_line" >> /etc/hosts
python3 - <<'PY'
import http.client,json,os,pwd,socket,urllib.parse
user=pwd.getpwnam('sawadmin');os.initgroups('sawadmin',user.pw_gid);os.setgid(user.pw_gid);os.setuid(user.pw_uid)
class Local(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout)
        self.sock.connect('/run/user/1000/podman/podman.sock')
image='ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:c2a43bb0d765774e2790b3babfb20997bb2eac7b4bf4c6d7d8661e99817bf904'
connection=Local('localhost',timeout=480)
connection.request('POST','/v4.0.0/libpod/images/pull?'+urllib.parse.urlencode({'reference':image,'policy':'missing'}))
response=connection.getresponse();assert response.status==200
for line in response:
    value=json.loads(line);assert not value.get('error'),'bootstrap image pull failed'
connection.close()
connection=Local('localhost',timeout=30)
connection.request('GET','/v1.40/images/'+urllib.parse.quote(image,safe='')+'/json')
response=connection.getresponse();assert response.status==200
value=json.loads(response.read());assert image in value['RepoDigests']
print('PASS BOOTSTRAP_CACHED '+image)
PY
'''


def main():
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY')!='invoice-bootstrap-stage':raise ValueError('explicit bootstrap staging acknowledgement required')
    attempt=DiagnosticAttempt(STATE/'invoice-bootstrap-staging','invoice-bootstrap-staging')
    if attempt.command('account',['account','show'])['id']!=os.environ['AZURE_SUBSCRIPTION_ID']:raise ValueError('subscription mismatch')
    dns=attempt.command('dns',[*RUN,"#!/usr/bin/env bash\npython3 -c \"import json,socket; print('REGISTRY_DNS '+json.dumps({host:sorted({item[4][0] for item in socket.getaddrinfo(host,443,family=socket.AF_INET,type=socket.SOCK_STREAM)}) for host in ('ghcr.io','pkg-containers.githubusercontent.com')}))\""])
    script,addresses=pin_registry(SCRIPT,dns,attempt.run_id)
    rule_args=['network','nsg','rule','list','-g',GROUP,'--nsg-name',NSG]
    before=attempt.command('before',rule_args)
    if any(row['name']=='allow-bootstrap-ghcr-https' for row in before):raise ValueError('existing registry exception requires review')
    path=attempt.directory/'parameters.json';path.write_text(json.dumps({'parameters':{'registryAddresses':{'value':addresses}}}))
    args=['-g',GROUP,'-n','invoice-bootstrap-cache','--template-file',str(ROOT/'infra/next-phase/workspace-registry-bootstrap.bicep'),'--parameters','@'+str(path)]
    preview=attempt.command('preview',['deployment','group','what-if',*args,'--no-pretty-print'])
    for change in preview['changes']:
        if change['changeType'] in ('Ignore','NoChange'):continue
        if change['changeType']!='Create' or not change['resourceId'].lower().endswith('/securityrules/allow-bootstrap-ghcr-https'):raise ValueError('unexpected registry staging change')
    try:
        attempt.command('allow',['deployment','group','create',*args])
        response=attempt.command('cache',[*RUN,script],timeout=600)
        if not any('PASS BOOTSTRAP_CACHED ' in item.get('message','') for item in response.get('value',[])):raise RuntimeError('bootstrap cache unconfirmed')
    finally:
        attempt.command('remove',['network','nsg','rule','delete','-g',GROUP,'--nsg-name',NSG,'-n','allow-bootstrap-ghcr-https'])
        if normalized_rules(before)!=normalized_rules(attempt.command('after',rule_args)):raise RuntimeError('network differs after bootstrap cache')
    print('PASS pinned bootstrap cached; original network rules restored')


if __name__=='__main__':main()