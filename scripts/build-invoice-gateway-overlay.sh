#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "${LEARNINGNEMO_AZURE_APPLY:-}" == invoice-gateway-overlay-build ]]
[[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]
[[ "$(az account show --query id -o tsv)" == "$AZURE_SUBSCRIPTION_ID" ]]
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
state="${LEARNINGNEMO_INFRA_STATE_DIR:-$HOME/.local/state/learningnemo}"
registry=crlearningnemodevgruyrc4qwdvvm
base="$(az containerapp show -g rg-learningnemo-invoice-dev -n ca-nemo-invoice-planning-dev --query properties.template.containers[0].image -o tsv)"
execution="$(az containerapp show -g rg-learningnemo-invoice-dev -n ca-nemo-invoice-execution-dev --query properties.template.containers[0].image -o tsv)"
[[ "$base" =~ ^crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-services@sha256:[a-f0-9]{64}$ ]]
[[ "$base" == "$execution" ]]
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
tar -C "$root" -cf - containers/invoice-gateway-overlay.Dockerfile src/task_agent/control/invoice_model.py src/task_agent/control/invoice_rails.py src/task_agent/console/invoice_gateway.py | tar -C "$stage" -xf -
revision="$(find "$stage" -type f -print0 | sort -z | xargs -0 sha256sum | sed "s|$stage/||" | sha256sum | cut -d' ' -f1)"
image="learningnemo/invoice-services:tool-rails-${revision:0:16}"
az acr build --registry "$registry" --image "$image" --file containers/invoice-gateway-overlay.Dockerfile --build-arg "BASE_IMAGE=$base" --platform linux/amd64 "$stage" > "$state/invoice-gateway-overlay-build.log" 2>&1
digest="$(az acr repository show --name "$registry" --image "$image" --query digest -o tsv)"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
printf '%s.azurecr.io/learningnemo/invoice-services@%s\n' "$registry" "$digest" > "$state/invoice-gateway-overlay.image.txt"
printf '%s\n' "$base" > "$state/invoice-gateway-overlay.base-image.txt"
printf 'PASS dependency-preserving tool-rail gateway overlay built and pinned\n'