#!/usr/bin/env bash
set -euo pipefail
run_id="${1:-}"
kind="${2:-planning}"
[[ "$run_id" =~ ^[a-f0-9]{32}$ ]]
[[ "$kind" == planning || "$kind" == execution ]]
RUN_ID="$run_id" KIND="$kind" python3 - <<'PY'
import hashlib
import json
import os
import pathlib
import pwd
import re
import subprocess
import uuid

run_id = os.environ['RUN_ID']
kind = os.environ['KIND']
name = ('ip-' if kind == 'planning' else 'ix-') + run_id[:16]
user = pwd.getpwnam('sawadmin')
environment = {
    'HOME': user.pw_dir,
    'XDG_RUNTIME_DIR': '/run/user/' + str(user.pw_uid),
    'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/' + str(user.pw_uid) + '/bus',
}
def command(*arguments):
    result = subprocess.run(['runuser', '-u', 'sawadmin', '--', 'env', *[key + '=' + value for key, value in environment.items()], *arguments], capture_output=True, text=True, timeout=60)
    return result

inventory_call = command('openshell', '--gateway', 'openshell', 'sandbox', 'list', '--output', 'json')
if inventory_call.returncode:
    raise RuntimeError('OpenShell inventory unavailable')
value = json.loads(inventory_call.stdout)
items = value if isinstance(value, list) else value.get('sandboxes', value.get('items', []))
matches = [item for item in items if item.get('name') == name]
if len(matches) > 1:
    raise RuntimeError('sandbox name is not unique')
record = matches[0] if matches else None
run_directory = pathlib.Path('/var/lib/learningnemo-invoice-cache/runs') / run_id
policy = pathlib.Path('/var/lib/learningnemo-invoice-cache/policies') / (run_id + '.yaml')
def metadata(path):
    return {'exists': path.exists(), 'is_symlink': path.is_symlink(), 'files': sorted(item.name for item in path.iterdir())[:30] if path.is_dir() and not path.is_symlink() else []}
report = {
    'run_id': run_id,
    'kind': kind,
    'name': name,
    'inventory': None if record is None else {key: record.get(key) for key in ('id', 'name', 'phase')},
    'run_directory': metadata(run_directory),
    'policy': {'exists': policy.is_file(), 'is_symlink': policy.is_symlink(), 'sha256': hashlib.sha256(policy.read_bytes()).hexdigest() if policy.is_file() and not policy.is_symlink() else None},
}
if record is not None:
    identifier = str(uuid.UUID(record['id']))
    runtime = pathlib.Path('/home/sawadmin/.local/state/openshell/vm/sandboxes') / identifier
    report['runtime_directory'] = metadata(runtime)
needles = (run_id.encode(), name.encode(), (str(record.get('id')) if record else '').encode())
processes = []
for path in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
    try:
        content = path.read_bytes()[:65536]
    except (FileNotFoundError, PermissionError, OSError):
        continue
    if any(needle and needle in content for needle in needles):
        processes.append(path.parent.name)
report['process_ids'] = processes[:10]
for unit in ('invoice-provision-expire-' + run_id, 'invoice-expire-' + run_id):
    state = command('systemctl', '--user', 'show', unit, '--property=LoadState,ActiveState,SubState', '--output=json')
    report[unit] = {'returncode': state.returncode, 'stdout': state.stdout.strip()[:1000]}
journal = command('journalctl', '--user', '--unit=openshell-gateway.service', '--no-pager', '--lines=800', '--output=cat')
matches = []
if journal.returncode == 0:
    for line in journal.stdout.splitlines():
        if not any(needle.decode(errors='ignore') in line for needle in needles if needle):
            continue
        line = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '<jwt-redacted>', line)
        line = re.sub(r'(?i)Bearer\s+\S+', 'Bearer <redacted>', line)
        matches.append(line[-800:])
report['gateway_journal_matches'] = matches[-20:]
print('INVOICE_RUN_INSPECT ' + json.dumps(report, separators=(',', ':')))
PY
