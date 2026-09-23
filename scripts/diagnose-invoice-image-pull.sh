#!/usr/bin/env bash
set -euo pipefail
cd /home/sawadmin
registry=crlearningnemodevgruyrc4qwdvvm.azurecr.io
curl --silent --show-error --max-time 15 --output /dev/null --write-out 'REGISTRY http=%{http_code} ip=%{remote_ip} seconds=%{time_total}\n' "https://$registry/v2/" || true
admin_uid=$(id -u sawadmin)
runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR="/run/user/$admin_uid" \
    curl --silent --show-error --max-time 15 --unix-socket "/run/user/$admin_uid/podman/podman.sock" http://localhost/v1.40/images/json | \
    python3 -c 'import json,sys; rows=json.load(sys.stdin); print(json.dumps({"images":[{"id":row.get("Id"),"digests":row.get("RepoDigests"),"size":row.get("Size")} for row in rows]}))'
df -B1 --output=avail /var/lib/learningnemo-invoice-cache