#!/usr/bin/env bash
set -euo pipefail
cd /home/sawadmin
admin_uid=$(id -u sawadmin)
runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR="/run/user/$admin_uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$admin_uid/bus" systemctl --user disable --now podman.socket podman.service
if systemctl is-active --quiet podman.socket || systemctl is-active --quiet podman.service; then
    printf 'FAIL unintended rootful image service active\n' >&2
    exit 1
fi
if runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR="/run/user/$admin_uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$admin_uid/bus" systemctl --user is-active --quiet podman.socket; then
    printf 'FAIL rootless image socket still active\n' >&2
    exit 1
fi
printf 'PASS invoice image cache disabled; installed packages and existing OpenShell resources retained\n'