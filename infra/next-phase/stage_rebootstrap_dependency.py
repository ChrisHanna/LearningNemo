"""Transfer the installed protobuf Python runtime for host-only offline repair."""

import base64
import hashlib
import io
import os
from pathlib import Path
import subprocess
import zipfile

import google.protobuf
from diagnostic_attempt import DiagnosticAttempt


def main():
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'stage-rebootstrap-dependency':
        raise ValueError('explicit staging acknowledgement required')
    attempt = DiagnosticAttempt(Path.home() / '.local/state/learningnemo/dependency-attempts', 'protobuf-repair-runtime')
    if attempt.command('account', ['account', 'show'])['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    package = Path(google.protobuf.__file__).parent
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('google/__init__.py', '')
        for path in sorted(package.rglob('*.py')):
            archive.write(path, 'google/protobuf/' + path.relative_to(package).as_posix())
    payload = content.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    local = attempt.directory / 'protobuf.zip'
    local.write_bytes(payload)
    local.chmod(0o600)
    environment = {**os.environ, 'PYTHONPATH': str(local), 'PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION': 'python'}
    subprocess.run(['python3', '-S', '-c', 'from google.protobuf import descriptor_pb2; descriptor_pb2.FileDescriptorProto(name="check")'], env=environment, check=True)
    root = '/var/lib/learningnemo-saw/repair-dependencies'
    target = root + '/' + digest + '.zip'
    encoded = base64.b64encode(payload).decode()
    scripts = [f'#!/bin/bash\nset -euo pipefail\numask 077\ninstall -d -m 0700 {root}\ntest ! -e {target}.b64\n: > {target}.b64\n']
    scripts += [f'#!/bin/bash\nset -euo pipefail\nprintf %s {encoded[offset:offset+30000]} >> {target}.b64\n' for offset in range(0, len(encoded), 30000)]
    scripts.append(f'''#!/bin/bash
set -euo pipefail
base64 -d {target}.b64 > {target}
printf '%s  %s\\n' {digest} {target} | sha256sum -c --quiet
chmod 0600 {target}
PYTHONPATH={target} PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python python3 -c 'import cryptography; from google.protobuf import descriptor_pb2; descriptor_pb2.FileDescriptorProto(name="check")'
ln -sfn {digest}.zip {root}/protobuf.zip
printf 'PASS PROTOBUF_{digest}\\n'
''')
    for index, script in enumerate(scripts):
        result = attempt.command('dependency-' + str(index), ['vm', 'run-command', 'invoke', '-g', 'rg-learningnemo-saw-dev', '-n', 'vm-learningnemo-saw-dev', '--command-id', 'RunShellScript', '--scripts', script])
        if index == len(scripts) - 1 and not any('PASS PROTOBUF_' + digest in item.get('message', '') for item in result.get('value', [])):
            raise ValueError('dependency verification failed')
    print('PASS verified host-only protobuf runtime', google.protobuf.__version__, digest, flush=True)


if __name__ == '__main__':
    main()