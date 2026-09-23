#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "$(id -u)" == 0 ]]
[[ "$(. /etc/os-release; printf '%s' "$VERSION_ID")" == 24.04 ]]
id sawadmin >/dev/null
cd /home/sawadmin
if ! command -v podman >/dev/null; then
    source_file=/etc/apt/sources.list.d/ubuntu.sources
    [[ -f "$source_file" ]]
    source_copy=$(mktemp --suffix=.sources)
    trap 'rm -f "$source_copy"' EXIT
    [[ "${LEARNINGNEMO_PACKAGE_MAINTENANCE:-}" == bounded-host-maintenance ]]
    sed -e 's|http://security.ubuntu.com/ubuntu|http://azure.archive.ubuntu.com/ubuntu|g' "$source_file" > "$source_copy"
    if grep '^URIs:' "$source_copy" | grep -v '^URIs: http://azure.archive.ubuntu.com/ubuntu/*$'; then
        printf 'FAIL unsupported package mirror; no network exception created\n' >&2
        exit 1
    fi
    apt-get -o Dir::Etc::sourcelist="$source_copy" -o Dir::Etc::sourceparts=- \
        -o APT::Update::Error-Mode=any -o Acquire::Retries=0 -o Acquire::https::Timeout=20 update
    DEBIAN_FRONTEND=noninteractive apt-get -o Dir::Etc::sourcelist="$source_copy" -o Dir::Etc::sourceparts=- \
        -o Acquire::Retries=0 -o Acquire::https::Timeout=20 install -y --no-install-recommends podman uidmap slirp4netns
fi
admin_uid=$(id -u sawadmin)
systemctl disable --now podman.socket podman.service podman-auto-update.timer podman-auto-update.service podman-restart.service podman-clean-transient.service
cache_directory=/var/lib/learningnemo-invoice-cache
owned_directory() {
    local path="$1" expected_uid="$2" owner="$3" group="$4" mode="$5"
    [[ ! -L "$path" ]]
    if [[ -e "$path" ]]; then
        [[ -d "$path" && "$(stat -c '%u' "$path")" == "$expected_uid" && "$(stat -c '%a' "$path")" == "$mode" ]]
    else
        install -d -m "$mode" -o "$owner" -g "$group" "$path"
    fi
}
owned_directory "$cache_directory" 0 root root 755
for directory in config data cache state; do
    owned_directory "$cache_directory/$directory" "$admin_uid" sawadmin sawadmin 700
done
owned_directory "$cache_directory/units" 0 root root 755
owned_directory "/run/user/$admin_uid/learningnemo-invoice-cache" "$admin_uid" sawadmin sawadmin 700
storage_config="$cache_directory/config/storage.conf"
service_override="$cache_directory/units/10-learningnemo-cache.conf"
[[ ! -L "$storage_config" && ! -L "$service_override" ]]
cat > "$storage_config" <<EOF
[storage]
driver = "overlay"
runroot = "/run/user/$admin_uid/learningnemo-invoice-cache/run"
graphroot = "$cache_directory/data/overlay-storage"
EOF
chown sawadmin:sawadmin "$storage_config"
chmod 0600 "$storage_config"
cat > "$service_override" <<EOF
[Service]
Environment=XDG_CONFIG_HOME=$cache_directory/config
Environment=XDG_DATA_HOME=$cache_directory/data
Environment=XDG_CACHE_HOME=$cache_directory/cache
Environment=XDG_STATE_HOME=$cache_directory/state
Environment=CONTAINERS_STORAGE_CONF=$storage_config
WorkingDirectory=$cache_directory
ExecStart=
ExecStart=/usr/bin/podman --root $cache_directory/data/overlay-storage --runroot /run/user/$admin_uid/learningnemo-invoice-cache/run --storage-driver overlay system service
EOF
chmod 0644 "$service_override"
systemd_directory=/home/sawadmin/.config/systemd/user/podman.service.d
[[ ! -L "$systemd_directory" ]]
if [[ ! -d "$systemd_directory" ]]; then
    install -d -m 0755 -o sawadmin -g sawadmin "$systemd_directory"
fi
override_link="$systemd_directory/10-learningnemo-cache.conf"
if [[ -e "$override_link" || -L "$override_link" ]]; then
    [[ -L "$override_link" && "$(readlink "$override_link")" == "$service_override" ]]
else
    ln -s "$service_override" "$override_link"
fi
run_user() {
    runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR="/run/user/$admin_uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$admin_uid/bus" "$@"
}
installed=false
cleanup_cache() {
    [[ -z "${source_copy:-}" ]] || rm -f "$source_copy"
    if [[ "$installed" != true ]]; then
        run_user systemctl --user disable --now podman.socket podman.service || true
    fi
}
trap cleanup_cache EXIT
run_user test -r "$storage_config"
run_user systemctl --user stop podman.socket podman.service
run_user systemctl --user daemon-reload
run_user systemctl --user enable --now podman.socket
socket="/run/user/$admin_uid/podman/podman.sock"
[[ -S "$socket" && "$(stat -c '%u' "$socket")" == "$admin_uid" ]]
run_user curl --fail --silent --show-error --unix-socket "$socket" http://localhost/_ping | grep -x OK
run_user curl --fail --silent --show-error --unix-socket "$socket" http://localhost/v4.0.0/libpod/info | \
    python3 -c 'import json,sys; info=json.load(sys.stdin); observed={"rootless":info["host"]["security"]["rootless"],"graphRoot":info["store"]["graphRoot"],"driver":info["store"]["graphDriverName"],"runRoot":info["store"]["runRoot"]}; print(json.dumps(observed),flush=True); assert observed["rootless"] is True, "cache is not rootless"; assert observed["graphRoot"] == "/var/lib/learningnemo-invoice-cache/data/overlay-storage", "cache storage path differs"; assert observed["runRoot"] == sys.argv[1], "cache runtime path differs"; assert observed["driver"] == "overlay", "cache driver differs"; print("PASS rootless API uses dedicated image storage")' "/run/user/$admin_uid/learningnemo-invoice-cache/run"
if systemctl is-active --quiet podman.socket || systemctl is-active --quiet podman.service; then
    printf 'FAIL unexpected rootful Podman service active\n' >&2
    exit 1
fi
installed=true
printf 'PASS rootless image cache ready; MicroVM driver and sandbox policies unchanged\n'