#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "${LEARNINGNEMO_AZURE_APPLY:-}" == invoice-services-build ]]
[[ "$(az account show --query id -o tsv)" == "$AZURE_SUBSCRIPTION_ID" ]]
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
state="$HOME/.local/state/learningnemo"
agent_image="$(< "$state/invoice-agent.image.txt")"
[[ "$agent_image" =~ ^crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-agent@sha256:[a-f0-9]{64}$ ]]
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
tar -C "$root" --exclude='__pycache__' --exclude='*.pyc' -cf - containers/invoice-services.Dockerfile containers/cloud-console.requirements.lock src/task_agent infra/next-phase/openshell/invoice-planning-policy.yaml infra/next-phase/openshell/invoice-execution-policy.yaml infra/next-phase/openshell/invoice-planning-provider-profile.yaml infra/next-phase/openshell/invoice-execution-provider-profile.yaml | tar -C "$stage" -xf -
revision=$(find "$stage" -type f -print0 | sort -z | xargs -0 sha256sum | sed "s|$stage/||" | sha256sum | cut -d' ' -f1)
image="learningnemo/invoice-services:source-${revision:0:16}"
az acr build --registry crlearningnemodevgruyrc4qwdvvm --image "$image" --file containers/invoice-services.Dockerfile --build-arg "INVOICE_AGENT_IMAGE=$agent_image" --platform linux/amd64 "$stage" > "$state/invoice-services-build.log" 2>&1
digest=$(az acr repository show --name crlearningnemodevgruyrc4qwdvvm --image "$image" --query digest -o tsv)
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]
printf 'crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-services@%s\n' "$digest" > "$state/invoice-services.image.txt"
printf 'PASS invoice service image built and pinned\n'