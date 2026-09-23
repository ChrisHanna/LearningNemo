#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json,socket
hosts=('ghcr.io','pkg-containers.githubusercontent.com','ca-learningnemo-diagnostic-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io')
addresses={host:sorted({record[4][0] for record in socket.getaddrinfo(host,443,family=socket.AF_INET,type=socket.SOCK_STREAM)}) for host in hosts}
print('REGISTRY_DNS '+json.dumps(addresses,separators=(',',':')))
PY
printf 'HOST_ROUTE\n'
ip route show
printf 'HOST_FIREWALL\n'
iptables -S OUTPUT
printf 'APP_CONNECT\n'
for actor in root sawadmin; do
  printf 'HOST_ACTOR %s\n' "$actor"
  runuser -u "$actor" -- curl -sS --connect-timeout 5 --max-time 20 -o /dev/null -w 'HTTP=%{http_code} IP=%{remote_ip} TLS=%{ssl_verify_result} CONNECT=%{time_connect} TOTAL=%{time_total}\n' 'https://ca-learningnemo-diagnostic-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io/v1/diagnostics/current' || true
done
python3 - <<'PY'
import importlib.util,pathlib,subprocess
helpers=list(pathlib.Path('/var/lib/learningnemo-saw/attempts').glob('*/sanitize.py'))
helper=max(helpers,key=lambda path:path.stat().st_mtime)
spec=importlib.util.spec_from_file_location('diagnostics',helper)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
result=subprocess.run(['runuser','-u','sawadmin','--','env','HOME=/home/sawadmin','XDG_RUNTIME_DIR=/run/user/1000','DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus','journalctl','--user','-u','openshell-gateway.service','--since','-20min','-n','100','--no-pager','-o','cat'],capture_output=True,text=True,check=True)
lines=[line for line in result.stdout.splitlines() if any(word in line.lower() for word in ('error','warn','connect','denied','dns'))]
print('GATEWAY_ERRORS')
print(module.sanitize('\n'.join(lines[-5:]))[-900:])
print('VM_LOGS')
for path in pathlib.Path('/home/sawadmin/.local/state/openshell/vm').glob('**/rootfs-console.log'):
  if path.stat().st_mtime < __import__('time').time()-1800:
    continue
  with path.open('rb') as stream:
    stream.seek(max(0,path.stat().st_size-20000))
    lines=stream.read().decode(errors='replace').splitlines()
  selected=[line for line in lines if any(word in line.lower() for word in ('error','warn','proxy','dns','denied','connect'))]
  print(path.parent.name,module.sanitize('\n'.join(selected[-5:]))[-650:])
PY