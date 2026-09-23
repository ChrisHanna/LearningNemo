#!/usr/bin/env bash
set -euo pipefail
cd /home/sawadmin
admin_uid=$(id -u sawadmin)
socket="/run/user/$admin_uid/podman/podman.sock"
[[ -S "$socket" && "$(stat -c '%u' "$socket")" == "$admin_uid" ]]
run_user() {
    runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR="/run/user/$admin_uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$admin_uid/bus" "$@"
}
run_user curl --fail --silent --show-error --unix-socket "$socket" http://localhost/v1.40/version | \
    python3 -c 'import json,sys; value=json.load(sys.stdin); assert value.get("ApiVersion"); print("PASS Docker-compatible image API responds")'
run_user curl --fail --silent --show-error --unix-socket "$socket" http://localhost/v1.40/images/json | \
    python3 -c 'import json,sys; value=json.load(sys.stdin); assert isinstance(value,list); print("PASS cache image inventory readable; image count="+str(len(value)))'
run_user systemctl --user is-active --quiet openshell-gateway.service
if systemctl is-active --quiet podman.socket || systemctl is-active --quiet podman.service; then
    printf 'FAIL rootful image service active\n' >&2
    exit 1
fi
printf 'PASS original OpenShell gateway remains active; cache access is host-user local\n'