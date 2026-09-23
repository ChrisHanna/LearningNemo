#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "${LEARNINGNEMO_AZURE_APPLY:-}" == invoice-agent-build ]]
[[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]
[[ "$(az account show --query id -o tsv)" == "$AZURE_SUBSCRIPTION_ID" ]]
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
state="$HOME/.local/state/learningnemo"
registry=crlearningnemodevgruyrc4qwdvvm
server="$registry.azurecr.io"
nat_image="$(< "$state/cloud-agent.image.txt")"
[[ "$nat_image" =~ ^crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/cloud-agent@sha256:[a-f0-9]{64}$ ]]
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
tar -C "$root" --exclude='__pycache__' --exclude='*.pyc' -cf - \
  containers/invoice-agent.Dockerfile configs/invoice-planning.yml configs/invoice-execution.yml src/task_agent | tar -C "$stage" -xf -
revision="$(find "$stage" -type f -print0 | sort -z | xargs -0 sha256sum | sed "s|$stage/||" | sha256sum | cut -d' ' -f1)"
image="learningnemo/invoice-agent:source-${revision:0:16}"
az acr build --registry "$registry" --image "$image" --file containers/invoice-agent.Dockerfile \
  --build-arg "NAT_IMAGE=$nat_image" --platform linux/amd64 "$stage" > "$state/invoice-agent-build.log" 2>&1
digest="$(az acr repository show --name "$registry" --image "$image" --query digest -o tsv)"
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]
printf '%s/learningnemo/invoice-agent@%s\n' "$server" "$digest" > "$state/invoice-agent.image.txt"
printf 'PASS private invoice agent image built and digest pinned\n'