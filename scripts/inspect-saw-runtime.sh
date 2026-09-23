#!/usr/bin/env bash
set -euo pipefail
admin_uid=$(id -u sawadmin)
admin_home=$(getent passwd sawadmin | cut -d: -f6)
run_user() { runuser -u sawadmin -- env HOME="$admin_home" XDG_RUNTIME_DIR="/run/user/$admin_uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$admin_uid/bus" "$@"; }
printf 'GATEWAY_SERVICE '
run_user systemctl --user is-active openshell-gateway.service || true
printf 'TIMER '
systemctl show learningnemo-saw-expire.timer -p NextElapseUSecRealtime --value
printf 'PACKAGE '
dpkg-query -W -f='${Version}\n' openshell
python3 - "$admin_home/.config/learningnemo" <<'PY'
import hashlib,sys
from pathlib import Path
for path in sorted(Path(sys.argv[1]).glob('*-policy.yaml')):
	content=path.read_bytes()
	print('POLICY',path.name,'bytes',len(content),'CRLF',content.count(b'\r\n'),'raw',hashlib.sha256(content).hexdigest(),'LF',hashlib.sha256(content.replace(b'\r\n',b'\n')).hexdigest())
PY
run_user openshell --gateway openshell status --output json | python3 -c 'import json,sys; value=json.load(sys.stdin); print("GATEWAY", value.get("status"), value.get("authentication",{}).get("status"))'
run_user openshell --gateway openshell sandbox list --output json | python3 -c 'import json,sys; value=json.load(sys.stdin); items=value if isinstance(value,list) else value.get("sandboxes",value.get("items",[])); print("SANDBOX_COUNT",len(items)); [print("SANDBOX",item.get("name"),item.get("status",item.get("state"))) for item in items]'