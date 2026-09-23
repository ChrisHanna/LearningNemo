#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

admin_user="__ADMIN_USER__"
expires_at="__EXPIRES_AT__"
openshell_version="__OPENSHELL_VERSION__"
package_url="__OPENSHELL_PACKAGE_URL__"
package_sha256="__OPENSHELL_PACKAGE_SHA256__"
sandbox_image="__SANDBOX_IMAGE__"
supervisor_image="__SUPERVISOR_IMAGE__"
sql_server="__SQL_SERVER__"
diagnostic_host="__DIAGNOSTIC_HOST__"
remediation_host="__REMEDIATION_HOST__"
state_dir="/var/lib/learningnemo-saw"
evidence_file="$state_dir/readiness.json"
failure_category="saw_unexpected_bootstrap_failed"

mkdir -p "$state_dir"
touch "$state_dir/bootstrap.log"
chmod 0600 "$state_dir/bootstrap.log"
exec > >(tee -a "$state_dir/bootstrap.log") 2>&1

fail() {
  local line="${2:-${BASH_LINENO[0]:-unknown}}"
  trap - ERR
  printf 'FAIL %s line=%s\n' "$1" "$line" >&2
  exit 1
}
trap 'fail "$failure_category" "$LINENO"' ERR

[[ "$(uname -m)" == "x86_64" ]] || fail saw_host_architecture_failed
[[ -c /dev/kvm ]] || fail saw_nested_virtualization_failed
[[ "$(stat -fc %T /sys/fs/cgroup)" == "cgroup2fs" ]] || fail saw_cgroup_v2_failed
source /etc/os-release
[[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]] || fail saw_host_image_failed
id "$admin_user" >/dev/null 2>&1 || fail saw_admin_user_failed
date -u -d "$expires_at" >/dev/null 2>&1 || fail saw_expiration_failed
if [[ "$(date -u -d "$expires_at" +%s)" -le "$(date -u +%s)" ]]; then
  fail saw_expiration_failed
fi
printf 'PASS saw_host_prerequisites\n'

for required_command in awk base64 curl dpkg dpkg-query getent grep loginctl python3 runuser sed setpriv sha256sum ssh systemctl timeout; do
  command -v "$required_command" >/dev/null 2>&1 || fail saw_host_tooling_failed
done
failure_category="openshell_package_download_failed"
package_path="$(mktemp --suffix=.deb)"
trap 'rm -f "$package_path"' EXIT
printf 'PASS saw_bootstrap_egress_started\n'
curl -fLsS \
  --retry 2 \
  --retry-all-errors \
  --connect-timeout 10 \
  --max-time 180 \
  --max-redirs 5 \
  -o "$package_path" \
  "$package_url" \
  >> "$state_dir/bootstrap.log" 2>&1 \
  || fail openshell_package_download_failed
printf 'PASS saw_bootstrap_egress_verified\n'
printf '%s  %s\n' "$package_sha256" "$package_path" | sha256sum -c --quiet \
  || fail openshell_package_hash_failed
failure_category="openshell_package_install_failed"
dpkg --force-confdef --force-confnew -i "$package_path" \
  >> "$state_dir/bootstrap.log" 2>&1 \
  || fail openshell_package_install_failed
failure_category="openshell_package_version_failed"
installed_version="$(dpkg-query -W -f='${Version}' openshell)"
[[ "$installed_version" == "$openshell_version-1" ]] || fail openshell_package_version_failed
printf 'PASS openshell_package_verified\n'

admin_uid="$(id -u "$admin_user")"
admin_home="$(getent passwd "$admin_user" | cut -d: -f6)"
runtime_dir="/run/user/$admin_uid"
getent group kvm >/dev/null 2>&1 || fail saw_kvm_group_missing
failure_category="openshell_runtime_permission_failed"
usermod -aG kvm "$admin_user"
driver_dir="/usr/libexec/openshell"
[[ -x "$driver_dir/openshell-driver-vm" ]] || fail openshell_vm_driver_missing

install -d -m 0700 -o "$admin_user" -g "$admin_user" \
  "$admin_home/.config/openshell" \
  "$admin_home/.config/systemd/user/openshell-gateway.service.d" \
  "$admin_home/.config/learningnemo" \
  "$admin_home/.local/share/openshell" \
  "$admin_home/.local/state/openshell/verification" \
  "$admin_home/.local/state/openshell/vm"
chown -R "$admin_user:$admin_user" "$admin_home/.local/state/openshell"
chmod 0700 \
  "$admin_home/.local/share/openshell" \
  "$admin_home/.local/state/openshell" \
  "$admin_home/.local/state/openshell/vm"

failure_category="openshell_policy_render_failed"
base64 -d > "$admin_home/.config/learningnemo/planning-policy.yaml" <<'POLICY'
__PLANNING_POLICY_B64__
POLICY
base64 -d > "$admin_home/.config/learningnemo/execution-policy.yaml" <<'POLICY'
__EXECUTION_POLICY_B64__
POLICY
base64 -d > "$admin_home/.config/learningnemo/probe-policy.yaml" <<'POLICY'
__PROBE_POLICY_B64__
POLICY
if grep -Eq '__[A-Z0-9_]+__' "$admin_home/.config/learningnemo/"*.yaml; then
  fail openshell_policy_render_failed
fi
chmod 0400 "$admin_home/.config/learningnemo/"*.yaml
chown "$admin_user:$admin_user" "$admin_home/.config/learningnemo/"*.yaml

tls_dir="$admin_home/.local/state/openshell/tls"
jwt_dir="$tls_dir/jwt"

cat > "$admin_home/.config/systemd/user/openshell-gateway.service.d/override.conf" <<'EOF'
[Service]
UMask=0022
ExecStartPre=
ExecStartPre=/usr/bin/openshell-gateway generate-certs --output-dir ${OPENSHELL_LOCAL_TLS_DIR} --server-san host.openshell.internal --server-san host.containers.internal
EOF
chown -R "$admin_user:$admin_user" "$admin_home/.config/systemd"
chmod 0700 "$admin_home/.config/systemd" "$admin_home/.config/systemd/user" \
  "$admin_home/.config/systemd/user/openshell-gateway.service.d"
chmod 0400 "$admin_home/.config/systemd/user/openshell-gateway.service.d/override.conf"

cat > "$admin_home/.config/openshell/gateway.toml" <<EOF
[openshell]
version = 1

[openshell.gateway]
name = "learningnemo-$admin_user"
bind_address = "127.0.0.1:17670"
log_level = "info"
compute_drivers = ["vm"]
default_image = "$sandbox_image"
supervisor_image = "$supervisor_image"
sandbox_namespace = "learningnemo"
guest_tls_ca = "$tls_dir/ca.crt"
guest_tls_cert = "$tls_dir/client/tls.crt"
guest_tls_key = "$tls_dir/client/tls.key"
ssh_session_ttl_secs = 900
policy_validation_failure_mode = "fail_closed"
grpc_rate_limit_requests = 120
grpc_rate_limit_window_seconds = 60

[openshell.gateway.gateway_jwt]
signing_key_path = "$jwt_dir/signing.pem"
public_key_path = "$jwt_dir/public.pem"
kid_path = "$jwt_dir/kid"
gateway_id = "learningnemo"
ttl_secs = 900

[openshell.gateway.auth]
allow_unauthenticated_users = false

[openshell.gateway.mtls_auth]
enabled = true

[openshell.drivers.vm]
state_dir = "$admin_home/.local/state/openshell/vm"
driver_dir = "$driver_dir"
default_image = "$sandbox_image"
bootstrap_image = "$sandbox_image"
grpc_endpoint = "https://host.openshell.internal:17670"
vcpus = __SANDBOX_VCPUS__
mem_mib = __SANDBOX_MEMORY_MIB__
overlay_disk_mib = __SANDBOX_OVERLAY_MIB__
krun_log_level = 1
EOF
chown "$admin_user:$admin_user" "$admin_home/.config/openshell/gateway.toml"
chmod 0400 "$admin_home/.config/openshell/gateway.toml"

loginctl terminate-user "$admin_user" >/dev/null 2>&1 || true
loginctl enable-linger "$admin_user"
systemctl start "user@$admin_uid.service"
run_user() {
  runuser -u "$admin_user" -- env \
    HOME="$admin_home" \
    XDG_RUNTIME_DIR="$runtime_dir" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus" \
    "$@"
}
run_user systemctl --user daemon-reload
failure_category="openshell_unit_enable_failed"
run_user systemctl --user enable openshell-gateway.service
failure_category="openshell_service_start_failed"
if ! run_user systemctl --user restart openshell-gateway.service; then
  run_user journalctl --user -u openshell-gateway.service --no-pager -n 80 \
    >> "$state_dir/bootstrap.log" 2>&1 || true
  fail openshell_service_start_failed
fi
failure_category="openshell_gateway_readiness_failed"
[[ -f "$jwt_dir/signing.pem" && -f "$tls_dir/client/tls.crt" && -f "$tls_dir/client/tls.key" ]] \
  || fail openshell_gateway_readiness_failed

gateway_inventory="$state_dir/gateways.json"
run_user openshell gateway list --output json > "$gateway_inventory"
gateway_registration="$(python3 - "$gateway_inventory" <<'PY'
import json
import sys

items = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(items, list):
  print("mismatch")
  raise SystemExit()
matches = [item for item in items if isinstance(item, dict) and item.get("name") == "openshell"]
if not matches:
  print("absent")
elif len(matches) == 1 and all((
  matches[0].get("endpoint") == "https://127.0.0.1:17670",
  matches[0].get("type") == "local",
  matches[0].get("auth") == "mtls",
)):
  print("registered")
else:
  print("mismatch")
PY
)"
case "$gateway_registration" in
  absent)
    run_user openshell gateway add https://127.0.0.1:17670 \
      --local --name openshell >/dev/null
    ;;
  registered) ;;
  *)
    fail openshell_gateway_readiness_failed
    ;;
esac
run_user openshell gateway select openshell >/dev/null
gateway_status="$state_dir/gateway-status.json"
for _attempt in {1..60}; do
  if run_user openshell --gateway openshell status --output json > "$gateway_status" 2>/dev/null \
    && python3 - "$gateway_status" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
authentication = value.get("authentication") if isinstance(value, dict) else None
raise SystemExit(0 if (
  value.get("gateway") == "openshell"
  and value.get("status") == "connected"
  and isinstance(authentication, dict)
  and authentication.get("status") == "authenticated"
) else 1)
PY
  then
    break
  fi
  [[ "$_attempt" -lt 60 ]] || fail openshell_gateway_readiness_failed
  sleep 2
done
printf 'PASS openshell_gateway_authenticated\n'

for sandbox in planning-demo execution-demo probe-demo; do
  run_user openshell sandbox delete "$sandbox" >/dev/null 2>&1 || true
done

create_sandbox() {
  local name="$1"
  local policy="$2"
  run_user env OPENSHELL_PROVISION_TIMEOUT=180 openshell sandbox create \
    --name "$name" \
    --from "$sandbox_image" \
    --policy "$policy" \
    --cpu 1 \
    --memory 1Gi \
    --detach \
    -- /bin/sh -lc 'while :; do sleep 300; done' >/dev/null
}

failure_category="openshell_sandbox_inventory_failed"
create_sandbox planning-demo "$admin_home/.config/learningnemo/planning-policy.yaml"
create_sandbox execution-demo "$admin_home/.config/learningnemo/execution-policy.yaml"
create_sandbox probe-demo "$admin_home/.config/learningnemo/probe-policy.yaml"
run_user openshell sandbox list --output json > "$state_dir/sandboxes.json"
sandbox_count="$(python3 - "$state_dir/sandboxes.json" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
if isinstance(value, list):
  print(len(value))
elif isinstance(value, dict):
  items = value.get("sandboxes") or value.get("items") or []
  print(len(items) if isinstance(items, list) else -1)
else:
  print(-1)
PY
)"
[[ "$sandbox_count" == "3" ]] \
  || fail openshell_sandbox_inventory_failed
printf 'PASS openshell_three_microvm_sandboxes\n'

verification_ssh_dir="$admin_home/.local/state/openshell/verification"
admin_gid="$(id -g "$admin_user")"
prepare_sandbox_ssh() {
  local name="$1"
  local config="$verification_ssh_dir/$name.conf"
  run_user openshell sandbox ssh-config "$name" > "$config"
  chown "$admin_user:$admin_user" "$config"
  chmod 0600 "$config"
  awk '$1 == "Host" { print $2; exit }' "$config" | grep -Eq '^[A-Za-z0-9._-]+$'
}
run_sandbox_script() {
  local name="$1"
  local timeout_seconds="$2"
  local script="$3"
  local config="$verification_ssh_dir/$name.conf"
  local alias_name
  local payload
  alias_name="$(awk '$1 == "Host" { print $2; exit }' "$config")"
  payload="$(printf '%s' "$script" | base64 -w0)"
  timeout --signal=TERM --kill-after=2s "${timeout_seconds}s" \
    setpriv --reuid="$admin_uid" --regid="$admin_gid" --init-groups \
    env HOME="$admin_home" XDG_RUNTIME_DIR="$runtime_dir" \
      DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus" \
    ssh -n -F "$config" -T -o BatchMode=yes -o ConnectTimeout=10 "$alias_name" \
      "printf '%s' '$payload' | base64 -d | /bin/sh" >/dev/null
}
for sandbox in planning-demo execution-demo probe-demo; do
  prepare_sandbox_ssh "$sandbox" || fail openshell_sandbox_verification_transport_failed
done

run_sandbox_script planning-demo 70 "
for attempt in 1 2 3; do
  code=\$(/usr/bin/curl -sS --connect-timeout 5 --max-time 20 -o /dev/null \
    -w '%{http_code}' 'https://$diagnostic_host/v1/diagnostics/current' 2>/dev/null)
  [ \"\$code\" = 401 ] && exit 0
done
exit 1
" || fail openshell_planning_policy_failed
run_sandbox_script planning-demo 25 "
code=\$(/usr/bin/curl -sS --connect-timeout 5 --max-time 12 -o /dev/null \
  -w '%{http_code}' -X POST 'https://$diagnostic_host/v1/diagnostics/current' 2>/dev/null)
[ \"\$code\" = 403 ]
" || fail openshell_planning_route_denial_failed
run_sandbox_script planning-demo 25 "
code=\$(/usr/bin/curl -sS --connect-timeout 5 --max-time 12 -o /dev/null \
  -w '%{http_code}' 'https://$diagnostic_host/v1/diagnostics/not-approved' 2>/dev/null)
[ \"\$code\" = 403 ]
" || fail openshell_planning_route_denial_failed
run_sandbox_script execution-demo 70 "
for attempt in 1 2 3; do
  code=\$(/usr/bin/curl -sS --connect-timeout 5 --max-time 20 -o /dev/null \
    -w '%{http_code}' -X POST 'https://$remediation_host/v1/remediations/execute' 2>/dev/null)
  [ \"\$code\" = 401 ] && exit 0
done
exit 1
" || fail openshell_execution_policy_failed
run_sandbox_script execution-demo 25 "
code=\$(/usr/bin/curl -sS --connect-timeout 5 --max-time 12 -o /dev/null \
  -w '%{http_code}' 'https://$remediation_host/v1/remediations/execute' 2>/dev/null)
[ \"\$code\" = 403 ]
" || fail openshell_execution_route_denial_failed
run_sandbox_script execution-demo 25 "
code=\$(/usr/bin/curl -sS --connect-timeout 5 --max-time 12 -o /dev/null \
  -w '%{http_code}' -X POST 'https://$remediation_host/v1/remediations/not-approved' 2>/dev/null)
[ \"\$code\" = 403 ]
" || fail openshell_execution_route_denial_failed
printf 'PASS openshell_planning_execution_separation\n'

failure_category="openshell_probe_denials_failed"
run_sandbox_script probe-demo 20 '
if /usr/bin/curl -fsS --connect-timeout 3 --max-time 8 https://example.com/ >/dev/null 2>&1; then
  exit 1
fi
' || fail probe_external_egress_failed
run_sandbox_script probe-demo 20 '
if /usr/bin/curl --noproxy "*" -fsS --connect-timeout 3 --max-time 8 \
  -H Metadata:true "http://169.254.169.254/metadata/instance?api-version=2025-04-07" \
  >/dev/null 2>&1; then
  exit 1
fi
' || fail probe_imds_failed
run_sandbox_script probe-demo 20 "
if /usr/bin/python3 -c \
  'import socket, sys; socket.create_connection((sys.argv[1], 1433), 3)' \
  '$sql_server' >/dev/null 2>&1; then
  exit 1
fi
" || fail probe_sql_failed
run_sandbox_script probe-demo 20 '
test ! -e /home/sawadmin/.config/openshell/gateway.toml \
  && test ! -S /run/podman/podman.sock \
  && test ! -S /var/run/docker.sock \
  && test "$(id -u)" != 0 \
  && test "$(stat -c %a /)" = 755
' || fail probe_host_boundary_failed
run_sandbox_script probe-demo 20 '
if /usr/bin/touch /etc/learningnemo-policy-write >/dev/null 2>&1; then
  exit 1
fi
' || fail probe_policy_write_failed
run_sandbox_script probe-demo 20 '
if /usr/bin/python3 -c "import os; os.setuid(0)" >/dev/null 2>&1; then
  exit 1
fi
' || fail probe_privilege_escalation_failed
printf 'PASS openshell_probe_denials\n'

planning_hash="$(sha256sum "$admin_home/.config/learningnemo/planning-policy.yaml" | cut -d' ' -f1)"
execution_hash="$(sha256sum "$admin_home/.config/learningnemo/execution-policy.yaml" | cut -d' ' -f1)"
probe_hash="$(sha256sum "$admin_home/.config/learningnemo/probe-policy.yaml" | cut -d' ' -f1)"
python3 - "$evidence_file" "$expires_at" "$openshell_version" \
  "$planning_hash" "$execution_hash" "$probe_hash" <<'PY'
import json
import os
import sys

path, expires_at, version, planning, execution, probe = sys.argv[1:]
record = {
    "schemaVersion": 1,
    "status": "ready",
    "openShellVersion": version,
    "computeDriver": "microvm",
    "sandboxCount": 3,
    "gatewayAuthentication": "mtls",
    "sandboxJwtTtlSeconds": 900,
    "expiresAt": expires_at,
    "policyHashes": {
        "planning": planning,
        "execution": execution,
        "probe": probe,
    },
    "checks": {
        "noPublicIp": True,
        "nestedVirtualization": True,
        "planningDiagnosticOnly": True,
        "executionBrokerRouteOnly": True,
        "probeExternalEgressDenied": True,
        "probeImdsDenied": True,
        "probeSqlDenied": True,
        "probeHostBoundaryDenied": True,
        "probePolicyWriteDenied": True,
        "probePrivilegeEscalationDenied": True,
    },
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(record, stream, indent=2, sort_keys=True)
    stream.write("\n")
os.chmod(path, 0o600)
PY

cat > /usr/local/sbin/learningnemo-saw-expire <<EOF
#!/usr/bin/env bash
set -euo pipefail
for sandbox in planning-demo execution-demo probe-demo; do
  runuser -u "$admin_user" -- env HOME="$admin_home" XDG_RUNTIME_DIR="$runtime_dir" \\
    DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus" \\
    timeout 30 openshell sandbox stop "\$sandbox" >/dev/null 2>&1 || true
done
  printf 'Workspace lease ended; sandbox disks and Azure resources retained for testing.\n'
EOF
chmod 0700 /usr/local/sbin/learningnemo-saw-expire
calendar_expiry="$(date -u -d "$expires_at" '+%Y-%m-%d %H:%M:%S UTC')"
cat > /etc/systemd/system/learningnemo-saw-expire.service <<'EOF'
[Unit]
Description=Stop expired OpenShell workloads while retaining their workspaces

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/learningnemo-saw-expire
EOF
cat > /etc/systemd/system/learningnemo-saw-expire.timer <<EOF
[Unit]
Description=Enforce the SAW maximum lifetime

[Timer]
OnCalendar=$calendar_expiry
AccuracySec=1min
Persistent=true
Unit=learningnemo-saw-expire.service

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now learningnemo-saw-expire.timer
printf 'PASS saw_maximum_lifetime_armed\n'
failure_category="saw_openshell_ready_failed"
printf 'PASS saw_openshell_ready\n'
