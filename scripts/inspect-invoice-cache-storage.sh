#!/usr/bin/env bash
set -euo pipefail
cd /home/sawadmin
du -sx -B1 /var/lib/learningnemo-invoice-cache/data/*
df -B1 --output=avail /var/lib/learningnemo-invoice-cache
if command -v fuse-overlayfs >/dev/null; then
    fuse-overlayfs --version
fi
grep -w overlay /proc/filesystems
python3 - <<'PY'
import json,pathlib
root=pathlib.Path('/var/lib/learningnemo-invoice-cache/data/storage')
for relative in ('vfs-images/images.json','vfs-containers/containers.json'):
    path=root/relative
    if path.exists():
        rows=json.loads(path.read_text())
        print(json.dumps({'file':relative,'count':len(rows),'ids':[row.get('id') for row in rows]}))
PY