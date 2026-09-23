#!/usr/bin/env bash
set -euo pipefail
umask 077
admin_uid=$(id -u sawadmin)
admin_home=$(getent passwd sawadmin | cut -d: -f6)
run_user() { runuser -u sawadmin -- env HOME="$admin_home" XDG_RUNTIME_DIR="/run/user/$admin_uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$admin_uid/bus" "$@"; }
record="/var/lib/learningnemo-saw/planning-failed-$(date -u +%Y%m%dT%H%M%SZ).json"
run_user openshell --gateway openshell sandbox list --output json > "$record"
sandbox_id=$(python3 - "$record" <<'PY'
import json,sys,uuid
value=json.load(open(sys.argv[1])); items=value if isinstance(value,list) else value.get('sandboxes',value.get('items',[]))
selected=[item for item in items if item.get('name')=='planning-demo']
assert len(selected)==1 and selected[0]['phase']=='Error'
assert all(item['name'] in {'planning-demo','execution-demo','probe-demo'} for item in items)
print(uuid.UUID(selected[0]['id']))
PY
)
source="$admin_home/.local/state/openshell/vm/sandboxes/$sandbox_id"
backup="${record%.json}"
[[ -d "$source" && ! -L "$source" ]]
cp -a --reflink=auto --sparse=always "$source" "$backup"
[[ "$(sha256sum "$source/overlay.ext4" | cut -d' ' -f1)" == "$(sha256sum "$backup/overlay.ext4" | cut -d' ' -f1)" ]]
run_user openshell --gateway openshell sandbox delete planning-demo >/dev/null
printf 'PASS removed_only_failed_planning backup=%s\n' "$backup"