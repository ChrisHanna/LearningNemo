#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
repair_id='__REPAIR_ID__'
[[ "$repair_id" =~ ^[a-f0-9]{32}$ ]]
disk=/dev/disk/azure/scsi1/lun0
[[ -b "$disk" ]]
mountpoint=/mnt/learningnemo-retained-repair
install -d -m 0700 "$mountpoint"
mountpoint -q "$mountpoint" && exit 20
mounted=false
trap 'status=$?; if [[ "$mounted" == true ]]; then umount "$mountpoint" || status=21; fi; exit "$status"' EXIT
mapfile -t candidates < <(lsblk --json -p -o NAME,FSTYPE,TYPE "$disk" | python3 -c 'import json,sys; print("\n".join(item["name"] for item in json.load(sys.stdin)["blockdevices"][0].get("children",[]) if item["type"]=="part" and item.get("fstype")=="ext4"))')
partition=''
for candidate in "${candidates[@]}"; do
  mount -o ro,noload "$candidate" "$mountpoint"
  mounted=true
  if [[ -f "$mountpoint/etc/hostname" && "$(cat "$mountpoint/etc/hostname")" == vm-learningnemo-saw-dev ]]; then
    [[ -z "$partition" ]]
    partition="$candidate"
  fi
  umount "$mountpoint"
  mounted=false
done
[[ -n "$partition" ]]
mount -o ro,noload "$partition" "$mountpoint"
mounted=true
[[ "$(cat "$mountpoint/etc/hostname")" == vm-learningnemo-saw-dev ]]
[[ -d "$mountpoint/home/sawadmin/.local/state/openshell/vm" ]]
[[ -f "$mountpoint/usr/local/sbin/learningnemo-saw-expire" && ! -L "$mountpoint/usr/local/sbin/learningnemo-saw-expire" ]]
umount "$mountpoint"
mounted=false
mount -o rw "$partition" "$mountpoint"
mounted=true
backup="$mountpoint/var/lib/learningnemo-saw/offline-repair-$repair_id"
mkdir -m 0700 "$backup"
cp -a "$mountpoint/usr/local/sbin/learningnemo-saw-expire" "$backup/"
cp -a "$mountpoint/etc/systemd/system/learningnemo-saw-expire.timer" "$backup/"
cp -a "$mountpoint/etc/systemd/system/learningnemo-saw-expire.service" "$backup/"
find "$mountpoint/home/sawadmin/.local/state/openshell/vm/sandboxes" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort > "$backup/sandboxes-before.txt"
cat > "$mountpoint/usr/local/sbin/learningnemo-saw-expire" <<'HANDLER'
#!/usr/bin/env bash
set -euo pipefail
for sandbox in planning-demo execution-demo probe-demo; do
  runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus timeout 30 openshell --gateway openshell sandbox stop "$sandbox" >/dev/null 2>&1 || true
done
printf 'Lease ended; sandbox data and Azure resources retained for owner testing.\n'
HANDLER
chmod 0700 "$mountpoint/usr/local/sbin/learningnemo-saw-expire"
python3 - "$mountpoint" "$repair_id" <<'PY'
import hashlib,json,os,pathlib,sys
root=pathlib.Path(sys.argv[1])
timer=root/'etc/systemd/system/timers.target.wants/learningnemo-saw-expire.timer'
if timer.is_symlink():
    assert pathlib.Path(os.readlink(timer)).name=='learningnemo-saw-expire.timer'
    timer.unlink()
else:
    assert not timer.exists(), 'unexpected non-symlink timer enablement'
handler=root/'usr/local/sbin/learningnemo-saw-expire'
assert 'sandbox delete' not in handler.read_text() and 'poweroff' not in handler.read_text()
record={'repairId':sys.argv[2],'expiryDeletesWorkspaces':False,'bootTimerDisabled':True,'handlerSha256':hashlib.sha256(handler.read_bytes()).hexdigest()}
path=root/'var/lib/learningnemo-saw/offline-retention.verified.json'
with path.open('x') as output: json.dump(record,output)
path.chmod(0o600)
print('OFFLINE_REPAIR '+json.dumps(record,separators=(',',':')))
PY
find "$mountpoint/home/sawadmin/.local/state/openshell/vm/sandboxes" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort > "$backup/sandboxes-after.txt"
cmp "$backup/sandboxes-before.txt" "$backup/sandboxes-after.txt"
sync
umount "$mountpoint"
mounted=false
printf 'PASS OFFLINE_REPAIR_%s\n' "$repair_id"