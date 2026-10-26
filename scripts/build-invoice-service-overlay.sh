#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "${LEARNINGNEMO_AZURE_APPLY:-}" == invoice-service-overlay-build ]]
[[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]
[[ "$(az account show --query id -o tsv)" == "$AZURE_SUBSCRIPTION_ID" ]]
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
state="${LEARNINGNEMO_INFRA_STATE_DIR:-$HOME/.local/state/learningnemo}"
registry=crlearningnemodevgruyrc4qwdvvm
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
tar -C "$root" -cf - containers/invoice-service-overlay.Dockerfile src/task_agent/console/invoice_service.py src/task_agent/control/invoice_repository.py src/task_agent/control/invoice_governance.py src/task_agent/control/invoice_contract.py src/task_agent/console/invoice_deployed.py | tar -C "$stage" -xf -
revision="$(find "$stage" -type f -print0 | sort -z | xargs -0 sha256sum | sed "s|$stage/||" | sha256sum | cut -d' ' -f1)"
image="learningnemo/invoice-services:guest-cleanup-${revision:0:16}"
az acr build --registry "$registry" --image "$image" --file containers/invoice-service-overlay.Dockerfile --platform linux/amd64 "$stage" > "$state/invoice-service-overlay-build.log" 2>&1
digest="$(az acr repository show --name "$registry" --image "$image" --query digest -o tsv)"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
printf '%s.azurecr.io/learningnemo/invoice-services@%s\n' "$registry" "$digest" > "$state/invoice-service-overlay.image.txt"
printf '%s\n' "$revision" > "$state/invoice-service-overlay.source-sha256.txt"
printf 'PASS dependency-preserving invoice service overlay built and pinned\n'