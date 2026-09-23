#!/usr/bin/env bash
set -euo pipefail
umask 077
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
state="${LEARNINGNEMO_INFRA_STATE_DIR:-$HOME/.local/state/learningnemo}"
tools="${LEARNINGNEMO_TOOL_DIR:-$HOME/.local/share/learningnemo/bin}"
case "${1:-all}" in
  all) kinds=(console agent) ;;
  console) kinds=(console) ;;
  agent) kinds=(agent) ;;
  human) kinds=(human) ;;
  *) echo "Expected all, console, agent, or human" >&2; exit 2 ;;
esac
[[ "${LEARNINGNEMO_AZURE_APPLY:-}" == cloud-demo-build ]]
[[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]
[[ "$(az account show --query id -o tsv)" == "$AZURE_SUBSCRIPTION_ID" ]]
registry="$(az acr list --resource-group rg-learningnemo-artifacts-dev --query '[0].name' -o tsv)"
server="$(az acr show --name "$registry" --query loginServer -o tsv)"
[[ -n "$registry" && -n "$server" ]]
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
tar -C "$root" --exclude='__pycache__' --exclude='*.pyc' -cf - \
  containers/cloud-console.Dockerfile containers/cloud-console.requirements.lock \
  containers/cloud-agent.Dockerfile containers/cloud-agent.requirements.lock \
  containers/cloud-human.Dockerfile containers/human-services.requirements.lock \
  scripts/apply-human-migrations.py scripts/verify-invoice-sql.py scripts/apply-invoice-migrations.py infra/next-phase/review-service \
  configs/agent.yml scripts/run-cloud-agent.py src pyproject.toml README.md \
  docs/build-and-reproduce.md docs/capability-demo.md docs/openshell-bootstrap-diagnostic-record.md | tar -C "$stage" -xf -
revision="$(find "$stage" -type f -print0 | sort -z | xargs -0 sha256sum | sed "s|$stage/||" | sha256sum | cut -d' ' -f1)"
for kind in "${kinds[@]}"; do
  image="learningnemo/cloud-$kind:source-${revision:0:16}"
  echo "Building cloud $kind image from allowlisted context"
  az acr build --registry "$registry" --image "$image" --file "containers/cloud-$kind.Dockerfile" --platform linux/amd64 "$stage" > "$state/cloud-$kind-build.log" 2>&1
  digest="$(az acr repository show --name "$registry" --image "$image" --query digest -o tsv)"
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
  printf '%s/%s@%s\n' "$server" "learningnemo/cloud-$kind" "$digest" > "$state/cloud-$kind.image.txt"
  echo "PASS cloud $kind image built and pinned"
  printf '%s\n' "$revision" > "$state/cloud-$kind.source-sha256.txt"
done
if [[ "${1:-all}" == all ]]; then
  printf '%s\n' "$revision" > "$state/cloud-demo.source-sha256.txt"
fi