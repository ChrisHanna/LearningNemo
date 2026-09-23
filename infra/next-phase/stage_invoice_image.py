"""Stage one pinned private image; transfer its pull credential in an encrypted envelope."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from diagnostic_attempt import DiagnosticAttempt


STATE = Path.home() / '.local/state/learningnemo'
REGISTRY = 'crlearningnemodevgruyrc4qwdvvm.azurecr.io'
REPOSITORY = 'learningnemo/invoice-agent'
VM_ARGS = ['vm', 'run-command', 'invoke', '-g', 'rg-learningnemo-saw-dev', '-n', 'vm-learningnemo-saw-dev', '--command-id', 'RunShellScript', '--scripts']


def seal(public_pem, plaintext, run_id):
    public = serialization.load_pem_public_key(public_pem.encode('ascii'))
    key, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    encrypted = AESGCM(key).encrypt(nonce, plaintext.encode('utf-8'), run_id.encode('ascii'))
    return {name: base64.b64encode(value).decode('ascii') for name, value in [('key', wrapped), ('nonce', nonce), ('ciphertext', encrypted)]}


def prepare_script(run_id):
    if not re.fullmatch('[a-f0-9]{32}', run_id):
        raise ValueError('invalid staging attempt')
    return '''#!/usr/bin/env bash
set -euo pipefail
umask 077
python3 - <<'PY'
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import json,os
directory=Path('/var/lib/learningnemo-invoice-staging')
directory.mkdir(mode=0o700,exist_ok=True)
assert not directory.is_symlink() and directory.stat().st_uid==0
path=directory/'__RUN__'
path.mkdir(mode=0o700)
key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
with open(os.open(path/'key.pem',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as output:
    output.write(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
print('INVOICE_IMAGE_KEY '+json.dumps({'run_id':'__RUN__','public_key':public}))
PY
'''.replace('__RUN__', run_id)


def pull_script(run_id, image, envelope):
    if not re.fullmatch('[a-f0-9]{32}', run_id) or not re.fullmatch(re.escape(REGISTRY + '/' + REPOSITORY) + r'@sha256:[a-f0-9]{64}', image):
        raise ValueError('invalid pinned image staging context')
    payload = base64.b64encode(json.dumps(envelope).encode()).decode('ascii')
    script = '''#!/usr/bin/env bash
set -euo pipefail
cd /home/sawadmin
python3 - <<'PY'
import base64,http.client,json,os,pathlib,pwd,socket,time,urllib.parse
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
path=pathlib.Path('/var/lib/learningnemo-invoice-staging/__RUN__/key.pem')
try:
    assert not path.is_symlink() and path.stat().st_uid==0 and time.time()-path.stat().st_mtime<300
    private=serialization.load_pem_private_key(path.read_bytes(),password=None)
    envelope=json.loads(base64.b64decode('__PAYLOAD__'))
    key=private.decrypt(base64.b64decode(envelope['key']),padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    credential=AESGCM(key).decrypt(base64.b64decode(envelope['nonce']),base64.b64decode(envelope['ciphertext']),b'__RUN__').decode()
finally:
    path.unlink(missing_ok=True)
user=pwd.getpwnam('sawadmin')
os.initgroups('sawadmin',user.pw_gid);os.setgid(user.pw_gid);os.setuid(user.pw_uid)
class Local(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/run/user/'+str(user.pw_uid)+'/podman/podman.sock')
image='__IMAGE__'
connection=Local('localhost',timeout=600)
auth=base64.b64encode(json.dumps({'username':'00000000-0000-0000-0000-000000000000','password':credential,'serveraddress':'__REGISTRY__'}).encode()).decode()
connection.request('POST','/v4.0.0/libpod/images/pull?'+urllib.parse.urlencode({'reference':image,'policy':'missing'}),headers={'X-Registry-Auth':auth})
response=connection.getresponse()
assert response.status==200, 'private image pull rejected'
total=0
for line in response:
    total+=len(line)
    assert total<4*1024*1024, 'image pull output exceeded budget'
    value=json.loads(line)
    assert not value.get('error'), 'private image pull failed; credential not logged'
connection.close()
connection=Local('localhost',timeout=30)
connection.request('GET','/v1.40/images/'+urllib.parse.quote(image,safe='')+'/json')
response=connection.getresponse()
assert response.status==200, 'image not visible to OpenShell-compatible API'
value=json.loads(response.read(65536))
assert image in value.get('RepoDigests',[]), 'cached image digest differs'
print('PASS INVOICE_IMAGE '+json.dumps({'run_id':'__RUN__','image':image,'image_id':value['Id'],'rootless_uid':os.getuid()}))
PY
'''
    for name, value in {'__RUN__': run_id, '__PAYLOAD__': payload, '__IMAGE__': image, '__REGISTRY__': REGISTRY}.items():
        script = script.replace(name, value)
    return script


def extract(response, marker):
    lines = [line for item in response.get('value', []) for line in item.get('message', '').splitlines() if line.startswith(marker)]
    if len(lines) != 1:
        raise RuntimeError('verified staging receipt missing')
    return json.loads(lines[0][len(marker):])


def main():
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'invoice-image-stage':
        raise ValueError('explicit image staging acknowledgement required')
    attempt = DiagnosticAttempt(STATE / 'invoice-image-staging', 'invoice-image-staging')
    account = attempt.command('account', ['account', 'show'])
    if account['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    vm = attempt.command('workspace', ['vm', 'show', '-g', 'rg-learningnemo-saw-dev', '-n', 'vm-learningnemo-saw-dev'])
    if vm.get('tags', {}).get('owner') != 'learningnemo-portfolio':
        raise ValueError('workspace ownership differs')
    image = (STATE / 'invoice-agent.image.txt').read_text().strip()
    key = extract(attempt.command('prepare', [*VM_ARGS, prepare_script(attempt.run_id)]), 'INVOICE_IMAGE_KEY ')
    try:
        result = subprocess.run(['az', 'acr', 'login', '--name', REGISTRY.split('.')[0], '--expose-token', '-o', 'json'],
                                capture_output=True, text=True, check=True, timeout=60)
        login = json.loads(result.stdout)
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            response = client.post('https://' + REGISTRY + '/oauth2/token', data={'grant_type': 'refresh_token',
                'service': REGISTRY, 'scope': 'repository:' + REPOSITORY + ':pull', 'refresh_token': login['accessToken']})
            if response.status_code != 200:
                raise RuntimeError('repository-scoped pull credential unavailable')
            credential = response.json()['access_token']
        envelope = seal(key['public_key'], credential, attempt.run_id)
        response = attempt.command('pull', [*VM_ARGS, pull_script(attempt.run_id, image, envelope)], timeout=900)
        receipt = extract(response, 'PASS INVOICE_IMAGE ')
        if receipt['image'] != image or receipt['run_id'] != attempt.run_id or receipt['rootless_uid'] != 1000:
            raise RuntimeError('cached image receipt differs')
        attempt.record('verified-image', receipt)
        target = STATE / 'invoice-image-staged.verified.json'
        with open(os.open(target, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), 'w') as output:
            json.dump(receipt, output)
        print('PASS pinned private image staged through rootless cache; no credentials stored in sandbox or logs')
    finally:
        cleanup = "#!/usr/bin/env bash\nset -euo pipefail\nrm -f /var/lib/learningnemo-invoice-staging/" + attempt.run_id + '/key.pem\n'
        attempt.command('remove-attempt-key', [*VM_ARGS, cleanup])


if __name__ == '__main__':
    main()