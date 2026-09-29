#!/usr/bin/env bash
set -euo pipefail
umask 077
run_id="${1:-}"
failed_identity="${2:-}"
unreferenced_identity="${3:-}"
acknowledgement="${4:-}"
[[ "$run_id" =~ ^[a-f0-9]{32}$ ]]
[[ "$failed_identity" =~ ^[a-f0-9]{64}$ ]]
[[ "$unreferenced_identity" =~ ^[a-f0-9]{64}$ ]]
[[ "$failed_identity" != "$unreferenced_identity" ]]
[[ "$acknowledgement" == reclaim-unreferenced-openshell-cache ]]
RUN_ID="$run_id" FAILED_IDENTITY="$failed_identity" UNREFERENCED_IDENTITY="$unreferenced_identity" python3 - <<'PY'
import json
import os
import pathlib
import pwd
import re
import shutil
import subprocess
import time
import uuid

run_id = os.environ['RUN_ID']
failed_identity = os.environ['FAILED_IDENTITY']
unreferenced_identity = os.environ['UNREFERENCED_IDENTITY']
vm_root = pathlib.Path('/home/sawadmin/.local/state/openshell/vm')
images = vm_root / 'images'
sandboxes = vm_root / 'sandboxes'
receipts = pathlib.Path('/var/lib/learningnemo-saw/image-cache-reclamation')
for path in (vm_root, images, sandboxes):
    if not path.is_dir() or path.is_symlink():
        raise RuntimeError('OpenShell storage path invalid')
user = pwd.getpwnam('sawadmin')
environment = {'HOME': user.pw_dir, 'XDG_RUNTIME_DIR': '/run/user/' + str(user.pw_uid), 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/' + str(user.pw_uid) + '/bus'}
def cli(*arguments):
    result = subprocess.run(['runuser', '-u', 'sawadmin', '--', 'env', *[key + '=' + value for key, value in environment.items()], 'openshell', '--gateway', 'openshell', *arguments], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('OpenShell inventory unavailable')
    return result.stdout

def inventory():
    value = json.loads(cli('sandbox', 'list', '--output', 'json'))
    return value if isinstance(value, list) else value.get('sandboxes', value.get('items', []))

def quiescent(item):
    if item.get('phase') == 'Stopped':
        return True
    if item.get('phase') != 'Error':
        return False
    try:
        identifier = str(uuid.UUID(item['id']))
        name = item['name']
    except (KeyError, ValueError, TypeError):
        return False
    runtime = sandboxes / identifier
    if runtime.exists() or runtime.is_symlink():
        return False
    needles = (identifier.encode(), name.encode())
    for command in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
        try:
            content = command.read_bytes()[:65536]
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if any(needle in content for needle in needles):
            return False
    return True

def referenced_caches():
    values = set()
    for sandbox in sandboxes.iterdir():
        identity = sandbox / 'image-identity'
        if not sandbox.is_dir() or sandbox.is_symlink() or not identity.is_file() or identity.is_symlink() or identity.stat().st_size > 1024:
            continue
        value = identity.read_text(errors='replace').strip()
        marker = ':sha256:'
        if marker in value:
            digest = value.rsplit(marker, 1)[1]
            if re.fullmatch(r'[a-f0-9]{64}', digest):
                values.add('sandbox-prepared-rootfs-ext4-umoci-v3-openshell-0.0.116-sha256-' + digest)
    return values

def busy(path):
    needle = str(path).encode()
    for command in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
        try:
            if needle in command.read_bytes()[:65536]:
                return True
        except (FileNotFoundError, PermissionError, OSError):
            pass
    for descriptor in pathlib.Path('/proc').glob('[0-9]*/fd/*'):
        try:
            resolved = descriptor.resolve()
        except (FileNotFoundError, PermissionError):
            continue
        if resolved == path or path in resolved.parents:
            return True
    return False

before = inventory()
if not all(quiescent(item) for item in before):
    raise RuntimeError('active or ambiguous sandbox blocks cache reclamation')
expected_name = 'ip-' + run_id[:16]
failed = [item for item in before if item.get('name') == expected_name]
if len(failed) != 1 or failed[0].get('phase') != 'Error' or (sandboxes / str(uuid.UUID(failed[0]['id']))).exists():
    raise RuntimeError('exact quiescent failed run required')
prefix = 'sandbox-prepared-rootfs-ext4-umoci-v3-openshell-0.0.116-sha256-'
failed_final = images / (prefix + failed_identity)
staging = list(images.glob(prefix + failed_identity + '.staging-*'))
unreferenced = images / (prefix + unreferenced_identity)
if len(staging) != 1 or not re.fullmatch(re.escape(prefix + failed_identity) + r'\.staging-[0-9]+-[0-9]+', staging[0].name):
    raise RuntimeError('exact failed staging directory required')
if not failed_final.is_dir() or failed_final.is_symlink() or list(failed_final.iterdir()):
    raise RuntimeError('failed final cache placeholder is not empty')
if not staging[0].is_dir() or staging[0].is_symlink() or sorted(item.name for item in staging[0].iterdir()) != ['source-rootfs.tar']:
    raise RuntimeError('failed staging structure differs')
if not unreferenced.is_dir() or unreferenced.is_symlink() or sorted(item.name for item in unreferenced.iterdir()) != ['rootfs.ext4']:
    raise RuntimeError('unreferenced cache structure differs')
references = referenced_caches()
if failed_final.name in references or unreferenced.name in references:
    raise RuntimeError('retained sandbox references cache target')
for target in (failed_final, staging[0], unreferenced):
    if busy(target):
        raise RuntimeError('cache target is in use')
receipts.mkdir(mode=0o700, exist_ok=True)
if receipts.is_symlink() or receipts.stat().st_uid != 0 or receipts.stat().st_mode & 0o077:
    raise RuntimeError('reclamation receipt directory is not private')
attempt = receipts / (run_id + '.attempt.json')
completed = receipts / (run_id + '.completed.json')
if attempt.exists() or completed.exists():
    raise RuntimeError('prior cache reclamation attempt requires inspection; no replay')
disk = os.statvfs(images)
free_before = disk.f_bavail * disk.f_frsize
targets = [failed_final, staging[0], unreferenced]
payload = {'run_id': run_id, 'sandbox_id': uuid.UUID(failed[0]['id']).hex, 'sandbox_name': expected_name,
    'targets': [target.name for target in targets], 'free_before': free_before, 'requested_at': time.time(),
    'sandbox_inventory': {uuid.UUID(item['id']).hex: item['name'] for item in before}}
with open(os.open(attempt, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600), 'w') as output:
    json.dump(payload, output, separators=(',', ':'))
    output.flush()
    os.fsync(output.fileno())
for target in targets:
    shutil.rmtree(target)
after = inventory()
if {uuid.UUID(item['id']).hex: item['name'] for item in after} != payload['sandbox_inventory']:
    raise RuntimeError('sandbox inventory changed during cache reclamation')
if any(target.exists() or target.is_symlink() for target in targets):
    raise RuntimeError('cache reclamation unconfirmed')
disk = os.statvfs(images)
free_after = disk.f_bavail * disk.f_frsize
if free_after <= free_before or free_after < 12 * 1024**3:
    raise RuntimeError('reclaimed free space is insufficient')
result = {**payload, 'free_after': free_after, 'completed_at': time.time()}
with open(os.open(completed, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600), 'w') as output:
    json.dump(result, output, separators=(',', ':'))
    output.flush()
    os.fsync(output.fileno())
print('PASS reclaimed only failed staging, empty placeholder, and one unreferenced image cache ' + json.dumps({'run_id': run_id, 'free_before': free_before, 'free_after': free_after, 'sandbox_count': len(after)}))
PY
