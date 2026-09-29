#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "${LEARNINGNEMO_AZURE_APPLY:-}" == cloud-controller-runtime-overlay-build ]]
[[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]
[[ "$(az account show --query id -o tsv)" == "$AZURE_SUBSCRIPTION_ID" ]]
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
state="${LEARNINGNEMO_INFRA_STATE_DIR:-$HOME/.local/state/learningnemo}"
registry=crlearningnemodevgruyrc4qwdvvm
server="$registry.azurecr.io"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
tar -C "$root" -cf - \
  containers/cloud-controller-runtime-overlay.Dockerfile \
  src/task_agent/console/live_workspace.py | tar -C "$stage" -xf -
revision="$(find "$stage" -type f -print0 | sort -z | xargs -0 sha256sum | sed "s|$stage/||" | sha256sum | cut -d' ' -f1)"
image="learningnemo/cloud-console:runtime-verifier-${revision:0:16}"
az acr build --registry "$registry" --image "$image" --file containers/cloud-controller-runtime-overlay.Dockerfile --platform linux/amd64 "$stage" > "$state/cloud-controller-runtime-overlay-build.log" 2>&1
digest="$(az acr repository show --name "$registry" --image "$image" --query digest -o tsv)"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
printf '%s/learningnemo/cloud-console@%s\n' "$server" "$digest" > "$state/cloud-controller-runtime-overlay.image.txt"
printf '%s\n' "$revision" > "$state/cloud-controller-runtime-overlay.source-sha256.txt"
printf 'PASS controller runtime verifier overlay built and pinned\n'
