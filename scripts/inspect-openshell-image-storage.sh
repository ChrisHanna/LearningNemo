#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json
import os
import pathlib
import subprocess

vm_root = pathlib.Path('/home/sawadmin/.local/state/openshell/vm')
root = vm_root / 'images'
if not root.is_dir() or root.is_symlink():
    raise RuntimeError('OpenShell image directory invalid')
disk = os.statvfs(root)
entries = []
for path in root.iterdir():
    if path.is_symlink():
        entries.append({'name': path.name, 'symlink': True})
        continue
    result = subprocess.run(['du', '-sx', '-B1', str(path)], capture_output=True, text=True, timeout=60, check=True)
    size = int(result.stdout.split()[0])
    open_fds = []
    for descriptor in pathlib.Path('/proc').glob('[0-9]*/fd/*'):
        try:
            resolved = descriptor.resolve()
        except (FileNotFoundError, PermissionError):
            continue
        if resolved == path or path in resolved.parents:
            open_fds.append(str(descriptor))
    process_ids = []
    needle = str(path).encode()
    for command in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
        try:
            content = command.read_bytes()[:65536]
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if needle in content:
            process_ids.append(command.parent.name)
    entries.append({'name': path.name, 'bytes': size, 'is_dir': path.is_dir(),
        'children': sorted(child.name for child in path.iterdir())[:30] if path.is_dir() else [],
        'mtime': path.stat().st_mtime, 'open_fds': open_fds[:10], 'process_ids': process_ids[:10]})
referenced_caches = set()
reference_count = 0
for sandbox in (vm_root / 'sandboxes').iterdir():
    if not sandbox.is_dir() or sandbox.is_symlink():
        continue
    identity = sandbox / 'image-identity'
    if identity.is_file() and not identity.is_symlink() and identity.stat().st_size <= 1024:
        value = identity.read_text(errors='replace').strip()
        marker = ':sha256:'
        if marker in value:
            digest = value.rsplit(marker, 1)[1]
            if len(digest) == 64 and all(character in '0123456789abcdef' for character in digest):
                referenced_caches.add('sandbox-prepared-rootfs-ext4-umoci-v3-openshell-0.0.116-sha256-' + digest)
                reference_count += 1
report = {'free_bytes': disk.f_bavail * disk.f_frsize, 'entries': sorted(entries, key=lambda item: item.get('bytes', 0), reverse=True),
    'referenced_image_caches': sorted(referenced_caches), 'retained_sandbox_reference_count': reference_count}
print('OPENSHELL_IMAGE_STORAGE ' + json.dumps(report, separators=(',', ':')))
PY
