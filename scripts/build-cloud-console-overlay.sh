#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "${LEARNINGNEMO_AZURE_APPLY:-}" == cloud-console-overlay-build ]]
[[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]
[[ "$(az account show --query id -o tsv)" == "$AZURE_SUBSCRIPTION_ID" ]]
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
state="${LEARNINGNEMO_INFRA_STATE_DIR:-$HOME/.local/state/learningnemo}"
registry=crlearningnemodevgruyrc4qwdvvm
server="$registry.azurecr.io"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
tar -C "$root" -cf - \
  containers/cloud-console-overlay.Dockerfile \
  src/task_agent/console/app.py \
  src/task_agent/console/cloud.py \
  src/task_agent/console/public_demo_auth.py \
  src/task_agent/console/remote_invoice.py \
  src/task_agent/console/static/index.html \
  src/task_agent/console/static/invoice-view.js \
  src/task_agent/console/static/invoice-scene.js \
  src/task_agent/console/static/invoice-evidence.js \
  src/task_agent/console/static/invoice-experience.js \
  src/task_agent/console/static/invoice-workflow.js \
  src/task_agent/console/static/learningnemo.js \
  src/task_agent/console/static/live-workspace.js \
  src/task_agent/console/static/mission.js \
  src/task_agent/console/static/mission-flow.js \
  src/task_agent/console/static/pattern.js \
  src/task_agent/console/static/invoice.css \
  src/task_agent/console/static/openai-theme.css \
  src/task_agent/console/static/session.js | tar -C "$stage" -xf -
revision="$(find "$stage" -type f -print0 | sort -z | xargs -0 sha256sum | sed "s|$stage/||" | sha256sum | cut -d' ' -f1)"
image="learningnemo/cloud-console:invoice-proof-${revision:0:16}"
az acr build --registry "$registry" --image "$image" --file containers/cloud-console-overlay.Dockerfile --platform linux/amd64 "$stage" > "$state/cloud-console-overlay-build.log" 2>&1
digest="$(az acr repository show --name "$registry" --image "$image" --query digest -o tsv)"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
printf '%s/learningnemo/cloud-console@%s\n' "$server" "$digest" > "$state/cloud-console-overlay.image.txt"
cp "$state/cloud-console-overlay.image.txt" "$state/cloud-console.image.txt"
printf '%s\n' "$revision" > "$state/cloud-console-overlay.source-sha256.txt"
printf 'PASS ServiceOps-preserving invoice proof overlay built and pinned\n'
