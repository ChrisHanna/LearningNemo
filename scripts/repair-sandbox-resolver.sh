#!/usr/bin/env bash
set -euo pipefail
umask 077
mode="${1:-planning}"
[[ "$mode" == planning || "$mode" == execution || "$mode" == probe ]]
run_user() { runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "$@"; }
[[ "$(dpkg-query -W -f='${Version}' openshell)" == '0.0.116-1' ]]
next=$(systemctl show learningnemo-saw-expire.timer -p NextElapseUSecRealtime --value)
[[ "$(date -u -d "$next" +%s)" -gt "$(( $(date -u +%s) + 600 ))" ]]
repair_id=$(cat /proc/sys/kernel/random/uuid)
backup="/var/lib/learningnemo-saw/resolver-$repair_id"
install -d -m 0700 "$backup"
run_user openshell --gateway openshell sandbox list --output json > "$backup/before.json"
sandbox_id=$(python3 - "$backup/before.json" "$mode-demo" <<'PY'
import json,sys,uuid
items=json.load(open(sys.argv[1]))
selected=[item for item in items if item['name']==sys.argv[2]]
assert len(selected)==1 and selected[0]['phase'] in {'Ready','Stopped'}
print(uuid.UUID(selected[0]['id']))
PY
)
overlay="/home/sawadmin/.local/state/openshell/vm/sandboxes/$sandbox_id/overlay.ext4"
[[ -f "$overlay" && ! -L "$overlay" ]]
if python3 - "$backup/before.json" "$sandbox_id" <<'PY'
import json,sys
raise SystemExit(0 if next(item['phase'] for item in json.load(open(sys.argv[1])) if item['id']==sys.argv[2])=='Ready' else 1)
PY
then
  run_user timeout 90 openshell --gateway openshell sandbox stop "$mode-demo"
fi
run_user openshell --gateway openshell sandbox list --output json > "$backup/stopped.json"
python3 - "$backup/stopped.json" "$sandbox_id" "$overlay" <<'PY'
import json,pathlib,sys
selected=[item for item in json.load(open(sys.argv[1])) if item['id']==sys.argv[2]]
assert len(selected)==1 and selected[0]['phase']=='Stopped', selected
overlay=pathlib.Path(sys.argv[3]).resolve()
for descriptor in pathlib.Path('/proc').glob('[0-9]*/fd/*'):
    try:
        assert descriptor.resolve()!=overlay, 'overlay still open by a process'
    except (FileNotFoundError,PermissionError):
        continue
PY
mountpoint="$backup/mount"
mkdir "$mountpoint"
mounted=false
cleanup() {
  status=$?
  trap - EXIT
  if [[ "$mounted" == true ]]; then umount "$mountpoint" || status=92; fi
  exit "$status"
}
trap cleanup EXIT
mount -o loop,rw "$overlay" "$mountpoint"
mounted=true
[[ -d "$mountpoint/upper/etc" && ! -L "$mountpoint/upper/etc" ]]
resolver="$mountpoint/upper/etc/resolv.conf"
[[ -f "$resolver" && ! -L "$resolver" ]]
cp -a "$resolver" "$backup/resolv.conf.before"
[[ ! -s "$resolver" ]] || grep -Eq '^nameserver (8\.8\.8\.8|8\.8\.4\.4|168\.63\.129\.16)$' "$resolver"
printf 'nameserver 168.63.129.16\noptions timeout:2 attempts:2\n' > "$resolver"
chmod 0644 "$resolver"
chown root:root "$resolver"
cp -a "$resolver" "$backup/resolv.conf.after"
sync
umount "$mountpoint"
mounted=false
run_user timeout 90 openshell --gateway openshell sandbox start "$mode-demo"
run_user openshell --gateway openshell sandbox list --output json > "$backup/after.json"
python3 - "$backup/after.json" "$sandbox_id" <<'PY'
import json,sys
selected=[item for item in json.load(open(sys.argv[1])) if item['id']==sys.argv[2]]
assert len(selected)==1 and selected[0]['phase']=='Ready', selected
PY
printf 'PASS resolver_repaired %s %s evidence=%s\n' "$mode-demo" "$sandbox_id" "$backup"