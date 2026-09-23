#!/usr/bin/env bash
set -euo pipefail
run_user() { runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "$@"; }
run_user openshell policy set --help
python3 - <<'PY'
import importlib.util,pathlib
directory=max(pathlib.Path('/var/lib/learningnemo-saw/attempts').iterdir(),key=lambda path:path.stat().st_mtime)
spec=importlib.util.spec_from_file_location('diagnostics',directory/'sanitize.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for path in directory.glob('resolver-*.raw'):
	print(path.name,module.sanitize(path.read_text())[-2000:])
print('BACKUPS')
for path in sorted(pathlib.Path('/var/lib/learningnemo-saw').glob('resolver-*'),key=lambda path:path.stat().st_mtime)[-2:]:
	print(path.name,[item.name for item in path.iterdir()])
	saved=path/'resolv.conf.before'
	if saved.exists():
		print('SAVED_RESOLVER',module.sanitize(saved.read_text())[:500])
PY