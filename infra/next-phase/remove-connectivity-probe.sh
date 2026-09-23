#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
config_file="${CONNECTIVITY_PROBE_CONFIG_FILE:-$phase_dir/environments/dev.connectivity-probe.config.json}"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
artifact_state="${ARTIFACT_PRIVATE_STATE_FILE:-$state_dir/artifacts-dev.state.json}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      apply=true
      shift
      ;;
    --config)
      config_file="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

python3 "$phase_dir/connectivity_probe_parameters.py" validate "$config_file"
get_config() {
  python3 "$phase_dir/connectivity_probe_parameters.py" get-config "$config_file" "$1"
}
environment="$(get_config environment)"
project_name="$(get_config projectName)"
resource_group="$(get_config probeResourceGroupName)"
platform_resource_group="$(get_config platformResourceGroupName)"
stack_name="$project_name-connectivity-probe-$environment"
identity_name="id-$project_name-diagnostic-$environment"
stack_exists=false
if az stack sub show --name "$stack_name" --output none 2>/dev/null; then
  stack_exists=true
fi
group_exists="$(az group exists --name "$resource_group" --output tsv)"
if [[ "$stack_exists" == false && "$group_exists" != "true" ]]; then
  rm -f "$state_dir/connectivity-probe-$environment.parameters.json"
  echo "Connectivity probe stack is absent; no deletion is needed."
  exit 0
fi
if [[ "$stack_exists" == false ]]; then
  echo "Delete blocked: probe resource group exists without its owning deployment stack." >&2
  exit 1
fi
if [[ "$apply" != true ]]; then
  echo "Dry run only. No resources were changed."
  echo "Use --apply with LEARNINGNEMO_AZURE_DELETE=wp3-connectivity-probe."
  exit 0
fi
if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" || "$(az account show --query id --output tsv)" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Delete blocked: expected subscription is absent or differs." >&2
  exit 1
fi
if [[ "${LEARNINGNEMO_AZURE_DELETE:-}" != "wp3-connectivity-probe" ]]; then
  echo "Delete blocked. Set LEARNINGNEMO_AZURE_DELETE=wp3-connectivity-probe for this command only." >&2
  exit 2
fi

principal_id="$(az identity show \
  --resource-group "$platform_resource_group" \
  --name "$identity_name" \
  --query principalId \
  --output tsv)"
if [[ "$group_exists" == "true" ]]; then
  snapshot_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  python3 "$phase_dir/snapshot_foundation.py" \
    --resource-group "$resource_group" \
    --output "$state_dir/pre-delete-connectivity-probe-$environment-$snapshot_timestamp.json"
fi
registry_id=""
if [[ -f "$artifact_state" ]]; then
  registry_id="$(python3 - "$artifact_state" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    f"/subscriptions/{{subscription}}/resourceGroups/{state['resourceGroupName']}"
    f"/providers/Microsoft.ContainerRegistry/registries/{state['registryName']}"
)
PY
)"
  registry_id="${registry_id/\{subscription\}/$AZURE_SUBSCRIPTION_ID}"
fi

az stack sub delete \
  --name "$stack_name" \
  --action-on-unmanage deleteAll \
  --resources-without-delete-support fail \
  --yes \
  --output none
if az stack sub show --name "$stack_name" --output none 2>/dev/null; then
  echo "Delete incomplete: connectivity probe deployment stack remains." >&2
  exit 1
fi
if [[ "$(az group exists --name "$resource_group" --output tsv)" == "true" ]]; then
  echo "Delete incomplete: connectivity probe resource group remains." >&2
  exit 1
fi
if ! az identity show \
  --resource-group "$platform_resource_group" \
  --name "$identity_name" \
  --output none 2>/dev/null; then
  echo "Delete incomplete: existing diagnostic identity was removed." >&2
  exit 1
fi
if [[ -n "$registry_id" ]]; then
  assignment_count="$(az role assignment list \
    --scope "$registry_id" \
    --assignee-object-id "$principal_id" \
    --query 'length(@)' \
    --output tsv)"
  if [[ "$assignment_count" != "1" ]]; then
    echo "Delete incomplete: existing diagnostic AcrPull assignment differs." >&2
    exit 1
  fi
fi
rm -f "$state_dir/connectivity-probe-$environment.parameters.json"
echo "Connectivity probe stack, group, and job were removed; diagnostic identity and AcrPull remain."