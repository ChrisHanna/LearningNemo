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
python3 - <<'PY'
import json
import pathlib
import stat

gate = pathlib.Path('/etc/learningnemo/invoice-availability.json')
if not gate.exists():
	print('AVAILABILITY_GATE missing')
else:
	metadata = gate.lstat()
	print('AVAILABILITY_GATE', json.dumps({
		'content': json.loads(gate.read_text()),
		'is_file': gate.is_file(),
		'is_symlink': gate.is_symlink(),
		'mode': stat.S_IMODE(metadata.st_mode),
		'uid': metadata.st_uid,
	}, separators=(',', ':')))
PY
inventory="$(run_user openshell --gateway openshell sandbox list --output json)"
INVENTORY="$inventory" python3 - <<'PY'
import json
import os
import pathlib

value = json.loads(os.environ['INVENTORY'])
items = value if isinstance(value, list) else value.get('sandboxes', value.get('items', []))
print('SANDBOX_COUNT', len(items))
for item in items:
	phase = item.get('phase', item.get('status', item.get('state')))
	print('SANDBOX', item.get('name'), phase)
	if phase == 'Stopped':
		continue
	details = {key: item.get(key) for key in ('id', 'name', 'phase', 'reason', 'message', 'error', 'exit_code') if item.get(key) is not None}
	print('SANDBOX_NONSTOPPED', json.dumps(details, separators=(',', ':')))
	identifier = str(item.get('id', ''))
	name = str(item.get('name', ''))
	directory = pathlib.Path('/home/sawadmin/.local/state/openshell/vm/sandboxes') / identifier
	if not directory.is_dir() or directory.is_symlink():
		print('QUIESCENCE', json.dumps({'directory': 'invalid'}, separators=(',', ':')))
		continue
	files = sorted(path.name for path in directory.iterdir())
	overlay = directory / 'overlay.ext4'
	open_fds = []
	for descriptor in pathlib.Path('/proc').glob('[0-9]*/fd/*'):
		try:
			if descriptor.resolve() == overlay.resolve():
				open_fds.append(str(descriptor))
		except (FileNotFoundError, PermissionError):
			pass
	processes = []
	for command in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
		try:
			text = command.read_bytes().replace(b'\0', b' ').decode(errors='replace')
		except (FileNotFoundError, PermissionError):
			continue
		if identifier in text or name in text:
			processes.append(command.parent.name)
	print('QUIESCENCE', json.dumps({
		'files': files[:20],
		'file_count': len(files),
		'main_process_exited': (directory / 'main-process-exited').exists(),
		'open_overlay_fds': open_fds[:10],
		'process_ids': processes[:10],
		'stopped_marker': (directory / 'stopped').exists(),
	}, separators=(',', ':')))
PY