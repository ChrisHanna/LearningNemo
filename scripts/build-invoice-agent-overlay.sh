#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "${LEARNINGNEMO_AZURE_APPLY:-}" == invoice-agent-overlay-build ]]
[[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]
[[ "$(az account show --query id -o tsv)" == "$AZURE_SUBSCRIPTION_ID" ]]
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
state="${LEARNINGNEMO_INFRA_STATE_DIR:-$HOME/.local/state/learningnemo}"
registry=crlearningnemodevgruyrc4qwdvvm
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
tar -C "$root" -cf - containers/invoice-agent-overlay.Dockerfile src/task_agent/control/invoice_agent.py src/task_agent/control/invoice_governance.py configs/invoice-planning.yml configs/invoice-execution.yml | tar -C "$stage" -xf -
revision="$(find "$stage" -type f -print0 | sort -z | xargs -0 sha256sum | sed "s|$stage/||" | sha256sum | cut -d' ' -f1)"
image="learningnemo/invoice-agent:delegation-${revision:0:16}"
az acr build --registry "$registry" --image "$image" --file containers/invoice-agent-overlay.Dockerfile --platform linux/amd64 "$stage" > "$state/invoice-agent-overlay-build.log" 2>&1
digest="$(az acr repository show --name "$registry" --image "$image" --query digest -o tsv)"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
printf '%s.azurecr.io/learningnemo/invoice-agent@%s\n' "$registry" "$digest" > "$state/invoice-agent-overlay.image.txt"
cp "$state/invoice-agent-overlay.image.txt" "$state/invoice-agent.image.txt"
printf '%s\n' "$revision" > "$state/invoice-agent.source-sha256.txt"
printf 'PASS dependency-preserving agent overlay built with signed delegation contract\n'