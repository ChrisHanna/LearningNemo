#!/usr/bin/env bash
set -euo pipefail
cd /home/sawadmin
admin_uid=$(id -u sawadmin)
if runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR="/run/user/$admin_uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$admin_uid/bus" systemctl --user is-active --quiet podman.service; then
    printf 'FAIL stop image cache before reclamation\n' >&2
    exit 1
fi
python3 - <<'PY'
import json,pathlib,shutil
path=pathlib.Path('/var/lib/learningnemo-invoice-cache/data/storage')
assert path.resolve()==path and path.is_dir() and not path.is_symlink()
for relative in ('vfs-images/images.json','vfs-containers/containers.json','overlay-images/images.json','overlay-containers/containers.json'):
    ledger=path/relative
    if ledger.exists():
        assert not ledger.is_symlink() and json.loads(ledger.read_text())==[], 'registered image/container prevents reclamation'
assert not path.is_mount()
shutil.rmtree(path)
print('PASS reclaimed only unregistered failed invoice-image cache; OpenShell storage untouched')
PY
df -B1 --output=avail /var/lib/learningnemo-invoice-cache