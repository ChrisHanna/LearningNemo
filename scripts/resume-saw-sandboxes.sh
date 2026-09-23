#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
pin_id='__PIN_ID__'
action='__ACTION__'
[[ "$pin_id" =~ ^[0-9a-f]{32}$ ]]
log_dir="/var/lib/learningnemo-saw/attempts/$pin_id"
install -d -m 0700 /var/lib/learningnemo-saw/attempts
mkdir -m 0700 "$log_dir"
started=$(date -u '+%Y-%m-%d %H:%M:%S')
printf '%s' '__LOG_HELPER_B64__' | base64 -d > "$log_dir/sanitize.py"
printf '%s' '__RESOLVER_HELPER_B64__' | base64 -d > "$log_dir/repair-resolver.sh"
printf '%s' '__BOOTSTRAP_HELPER_B64__' | base64 -d > "$log_dir/repair-bootstrap.py"
stage=prerequisites
state=''
pin_added=false
gateway_stopped=false
emit() {
  python3 - "$log_dir/events.raw" "$pin_id" "$stage" "$1" "${2:-0}" <<'PY'
import datetime,json,sys
path,run,stage,outcome,code=sys.argv[1:]
event={'runId':run,'time':datetime.datetime.now(datetime.UTC).isoformat(),'stage':stage,'outcome':outcome,'exitCode':int(code)}
with open(path,'a') as output: output.write(json.dumps(event)+'\n')
print('SANDBOX_EVENT '+json.dumps(event,separators=(',',':')))
PY
}
cleanup() {
  original_status=$?
  trap - EXIT ERR
  set +e
  emit "$([[ "$original_status" == 0 ]] && echo returned || echo failed)" "$original_status"
  if [[ "$gateway_stopped" == true ]]; then
    start_gateway
    gateway_status=$?
    stage=gateway-restore
    emit "$([[ "$gateway_status" == 0 ]] && echo returned || echo failed)" "$gateway_status"
    [[ "$gateway_status" == 0 || "$original_status" != 0 ]] || original_status=92
  fi
  if [[ "$pin_added" == true ]]; then
    sed -i "/ # learningnemo-registry-pin-$pin_id\$/d" /etc/hosts
    pin_status=$?
    stage=host-pin-cleanup
    if grep -Fq " # learningnemo-registry-pin-$pin_id" /etc/hosts; then pin_status=1; fi
    emit "$([[ "$pin_status" == 0 ]] && echo verified || echo failed)" "$pin_status"
    if [[ "$pin_status" != 0 && "$original_status" == 0 ]]; then original_status=90; fi
  fi
  if [[ -n "${admin_uid:-}" && -n "${admin_home:-}" ]]; then
    timeout 15 runuser -u sawadmin -- env HOME="$admin_home" XDG_RUNTIME_DIR="/run/user/$admin_uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$admin_uid/bus" journalctl --user -u openshell-gateway.service --since "$started" -n 160 --no-pager -o short-iso > "$log_dir/gateway.raw" 2>&1
    timeout 15 runuser -u sawadmin -- env HOME="$admin_home" XDG_RUNTIME_DIR="/run/user/$admin_uid" openshell --gateway openshell sandbox list --output json > "$log_dir/inventory-after.raw" 2>&1
    index=0
    while IFS= read -r console; do
      index=$((index+1))
      tail -c 49152 "$console" > "$log_dir/console-$index.raw" 2>&1
    done < <(find "$admin_home/.local/state/openshell/vm" -maxdepth 4 -type f -name rootfs-console.log -newermt "$started" -print 2>/dev/null | head -3)
  fi
  [[ -z "$state" ]] || rm -f "$state"
  python3 "$log_dir/sanitize.py" --bundle "$log_dir"
  bundle_status=$?
  if [[ "$bundle_status" != 0 ]]; then
    printf 'SANDBOX_LOG_CAPTURE_FAILED %s\n' "$pin_id"
    [[ "$original_status" != 0 ]] || original_status=91
  fi
  exit "$original_status"
}
trap cleanup EXIT
emit started
[[ "$action" == create || "$action" == start-existing || "$action" == repair-resolvers || "$action" == rebootstrap ]]
admin_uid=$(id -u sawadmin)
admin_home=$(getent passwd sawadmin | cut -d: -f6)
run_user() { runuser -u sawadmin -- env HOME="$admin_home" XDG_RUNTIME_DIR="/run/user/$admin_uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$admin_uid/bus" "$@"; }
start_gateway() {
  run_user systemctl --user start openshell-gateway.service
  run_user curl -sS --retry 20 --retry-connrefused --retry-delay 1 --retry-max-time 30 --max-time 3 \
    --cacert "$admin_home/.local/state/openshell/tls/ca.crt" \
    --cert "$admin_home/.local/state/openshell/tls/client/tls.crt" \
    --key "$admin_home/.local/state/openshell/tls/client/tls.key" \
    -o /dev/null https://127.0.0.1:17670/
}
[[ "$(dpkg-query -W -f='${Version}' openshell)" == '0.0.116-1' ]]
next=$(systemctl show learningnemo-saw-expire.timer -p NextElapseUSecRealtime --value)
[[ "$(date -u -d "$next" +%s)" -gt "$(( $(date -u +%s) + 600 ))" ]]
state=$(mktemp)
pin_line='__GHCR_IP__ ghcr.io # learningnemo-registry-pin-__PIN_ID__'
hosts_backup="/var/lib/learningnemo-saw/hosts-before-$pin_id"
cp -a /etc/hosts "$hosts_backup"
stage=gateway-check
emit started
run_user openshell --gateway openshell status --output json > "$state"
python3 - "$state" <<'PY'
import json,sys
value=json.load(open(sys.argv[1]))
assert value.get('status')=='connected' and value.get('authentication',{}).get('status')=='authenticated'
PY
cp "$state" "$log_dir/gateway-before.raw"
stage=inventory-check
emit started
run_user openshell --gateway openshell sandbox list --output json > "$state"
cp "$state" "$log_dir/inventory-before.raw"
python3 - "$state" "$action" <<'PY'
import json,sys
value=json.load(open(sys.argv[1])); items=value if isinstance(value,list) else value.get('sandboxes',value.get('items',[]))
if sys.argv[2]=='create':
  assert items==[], 'preserve existing sandboxes; inspect instead of replacing'
elif sys.argv[2]=='rebootstrap':
  assert len(items)==3 and {item['name'] for item in items}=={'planning-demo','execution-demo','probe-demo'}
  assert all(item['phase'] in {'Ready','Stopped','Error'} for item in items)
elif sys.argv[2]=='repair-resolvers':
  assert len(items) in (2,3) and {item['name'] for item in items} in ({'execution-demo','probe-demo'},{'planning-demo','execution-demo','probe-demo'})
  assert all(item['phase'] in {'Ready','Stopped'} for item in items)
else:
  assert len(items)==3 and {item['name'] for item in items}=={'planning-demo','execution-demo','probe-demo'}
  assert all(item['phase'] in {'Ready','Stopped'} for item in items)
PY
stage=policy-hashes
emit started
sha256sum "$admin_home/.config/learningnemo/"*-policy.yaml > "$log_dir/policy-hashes.raw"
if [[ "$action" == rebootstrap ]]; then
  python3 - "$admin_home/.config/learningnemo" "$log_dir" <<'PY'
import base64,pathlib,sys
policies={'planning':'__PLANNING_POLICY_B64__','execution':'__EXECUTION_POLICY_B64__','probe':'__PROBE_POLICY_B64__'}
for mode,encoded in policies.items():
    path=pathlib.Path(sys.argv[1])/(mode+'-policy.yaml')
    current=path.read_bytes()
    expected=base64.b64decode(encoded)
    old=expected.replace(b'        tls: terminate\r\n',b'').replace(b'        tls: terminate\n',b'')
    assert current in (expected,old), 'unexpected policy drift'
    (pathlib.Path(sys.argv[2])/(mode+'-policy.before')).write_bytes(current)
    path.write_bytes(expected)
PY
fi
printf '%s  %s\n' '__PLANNING_HASH__' "$admin_home/.config/learningnemo/planning-policy.yaml" '__EXECUTION_HASH__' "$admin_home/.config/learningnemo/execution-policy.yaml" '__PROBE_HASH__' "$admin_home/.config/learningnemo/probe-policy.yaml" | sha256sum -c --quiet
if grep -E '(^|[[:space:]])ghcr\.io([[:space:]]|$)' /etc/hosts; then
  printf 'FAIL existing_registry_host_override\n' >&2
  exit 1
fi
stage=host-pin
emit started
printf '\n%s\n' "$pin_line" >> /etc/hosts
pin_added=true
python3 - '__GHCR_IP__' <<'PY' > "$log_dir/pinned-dns.raw"
import socket
import sys
addresses=sorted({item[4][0] for item in socket.getaddrinfo('ghcr.io',443,family=socket.AF_INET,type=socket.SOCK_STREAM)})
print('CURRENT_GHCR',addresses,'PINNED',sys.argv[1])
assert addresses==[sys.argv[1]], 'registry DNS does not match the allowed pinned address'
PY
curl_format='{"httpCode":"%{http_code}","remoteIp":"%{remote_ip}","tlsVerify":"%{ssl_verify_result}","dnsSeconds":%{time_namelookup},"connectSeconds":%{time_connect},"tlsSeconds":%{time_appconnect},"totalSeconds":%{time_total}}\n'
for actor in root openshell-user; do
  stage="registry-$actor"
  emit started
  exit_code=0
  if [[ "$actor" == root ]]; then
    curl -sS --connect-timeout 5 --max-time 15 -o /dev/null -w "$curl_format" https://ghcr.io/v2/ > "$log_dir/$stage.raw" 2> "$log_dir/$stage-error.raw" || exit_code=$?
  else
    run_user curl -sS --connect-timeout 5 --max-time 15 -o /dev/null -w "$curl_format" https://ghcr.io/v2/ > "$log_dir/$stage.raw" 2> "$log_dir/$stage-error.raw" || exit_code=$?
  fi
  emit returned "$exit_code"
  [[ "$exit_code" == 0 ]]
  python3 - "$log_dir/$stage.raw" '__GHCR_IP__' <<'PY'
import json,sys
result=json.load(open(sys.argv[1]))
assert result['httpCode']=='401' and result['tlsVerify']=='0' and result['remoteIp']==sys.argv[2], 'registry control failed'
PY
done
if [[ "$action" == rebootstrap ]]; then
  stage=rebootstrap
  emit started
  export PYTHONPATH=/var/lib/learningnemo-saw/repair-dependencies/protobuf.zip
  export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
  python3 -c 'import cryptography,google.protobuf'
  backup="/var/lib/learningnemo-saw/rebootstrap-$pin_id"
  mkdir -m 0700 "$backup"
  cp "$log_dir/inventory-before.raw" "$backup/inventory.json"
  run_user systemctl --user stop openshell-gateway.service
  gateway_stopped=true
  [[ "$(run_user systemctl --user is-active openshell-gateway.service || true)" == inactive ]]
  python3 "$log_dir/repair-bootstrap.py" "$backup" > "$log_dir/rebootstrap.raw" 2>&1
  start_gateway
  gateway_stopped=false
  run_user openshell --gateway openshell sandbox list --output json > "$log_dir/inventory-before.raw"
  python3 - "$log_dir/inventory-before.raw" <<'PY'
import json,sys
items=json.load(open(sys.argv[1]))
assert len(items)==3 and all(item['phase']=='Stopped' for item in items), 'retained workspaces must restore stopped'
PY
fi
for mode in planning execution probe; do
  stage="create-$mode"
  emit started
  phase=$(python3 - "$log_dir/inventory-before.raw" "$mode-demo" <<'PY'
import json,sys
print(next((item['phase'] for item in json.load(open(sys.argv[1])) if item['name']==sys.argv[2]),'Absent'))
PY
)
  if [[ "$action" == create || "$phase" == Absent ]]; then
    run_user env OPENSHELL_PROVISION_TIMEOUT=150 timeout 180 openshell --gateway openshell sandbox create \
      --name "$mode-demo" --from '__SANDBOX_IMAGE__' --policy "$admin_home/.config/learningnemo/$mode-policy.yaml" \
      --cpu 1 --memory 1Gi --detach -- /bin/sleep infinity > "$log_dir/$stage.raw" 2>&1
  else
    if [[ "$phase" != Ready && "$action" != repair-resolvers ]]; then
      run_user env OPENSHELL_PROVISION_TIMEOUT=150 timeout 180 openshell --gateway openshell sandbox start "$mode-demo" >> "$log_dir/$stage.raw" 2>&1
    fi
  fi
  if [[ "$action" == repair-resolvers || "$action" == create ]]; then
    stage="resolver-$mode"
    emit started
    bash "$log_dir/repair-resolver.sh" "$mode" > "$log_dir/$stage.raw" 2>&1
  fi
  if [[ "$action" == rebootstrap ]]; then
    run_user openshell --gateway openshell policy set "$mode-demo" --policy "$admin_home/.config/learningnemo/$mode-policy.yaml" --wait --timeout 60 >> "$log_dir/$stage.raw" 2>&1
  fi
  emit returned
  printf 'PASS resumed_%s\n' "$mode"
done
stage=provisioning-complete
printf 'PASS restored_three_pinned_sandboxes\n'