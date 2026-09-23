#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
config_file="${CONTROL_CYCLE_CONFIG_FILE:-$phase_dir/environments/dev.control-cycle.config.json}"
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

python3 "$phase_dir/control_cycle_parameters.py" validate "$config_file"
get_config() {
  python3 "$phase_dir/control_cycle_parameters.py" get-config "$config_file" "$1"
}
environment="$(get_config environment)"
project_name="$(get_config projectName)"
resource_group="$(get_config controlResourceGroupName)"
platform_resource_group="$(get_config platformResourceGroupName)"
stack_name="$project_name-control-cycle-$environment"
stack_exists=false
if az stack sub show --name "$stack_name" --output none 2>/dev/null; then
  stack_exists=true
fi
group_exists="$(az group exists --name "$resource_group" --output tsv)"
if [[ "$stack_exists" == false && "$group_exists" != "true" ]]; then
  rm -f "$state_dir/control-cycle-$environment.parameters.json"
  echo "Control-cycle stack is absent; no deletion is needed."
  exit 0
fi
if [[ "$stack_exists" == false ]]; then
  echo "Delete blocked: control-cycle group exists without its owning deployment stack." >&2
  exit 1
fi
if [[ "$apply" != true ]]; then
  echo "Dry run only. No resources were changed."
  echo "Use --apply with LEARNINGNEMO_AZURE_DELETE=wp3-control-cycle."
  exit 0
fi
if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" || "$(az account show --query id --output tsv)" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Delete blocked: expected subscription is absent or differs." >&2
  exit 1
fi
if [[ "${LEARNINGNEMO_AZURE_DELETE:-}" != "wp3-control-cycle" ]]; then
  echo "Delete blocked. Set LEARNINGNEMO_AZURE_DELETE=wp3-control-cycle for this command only." >&2
  exit 2
fi

control_name="id-$project_name-control-$environment"
diagnostic_name="id-$project_name-diagnostic-$environment"
control_principal="$(az identity show --resource-group "$platform_resource_group" --name "$control_name" --query principalId --output tsv)"
diagnostic_principal="$(az identity show --resource-group "$platform_resource_group" --name "$diagnostic_name" --query principalId --output tsv)"
if [[ "$group_exists" == "true" ]]; then
  snapshot_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  python3 "$phase_dir/snapshot_foundation.py" \
    --resource-group "$resource_group" \
    --output "$state_dir/pre-delete-control-cycle-$environment-$snapshot_timestamp.json"
fi

az stack sub delete \
  --name "$stack_name" \
  --action-on-unmanage deleteAll \
  --resources-without-delete-support fail \
  --yes \
  --output none
if az stack sub show --name "$stack_name" --output none 2>/dev/null; then
  echo "Delete incomplete: control-cycle deployment stack remains." >&2
  exit 1
fi
if [[ "$(az group exists --name "$resource_group" --output tsv)" == "true" ]]; then
  echo "Delete incomplete: control-cycle resource group remains." >&2
  exit 1
fi
for identity_name in "$control_name" "$diagnostic_name"; do
  if ! az identity show \
    --resource-group "$platform_resource_group" \
    --name "$identity_name" \
    --output none 2>/dev/null; then
    echo "Delete incomplete: a reused platform identity was removed." >&2
    exit 1
  fi
done
for mode in diagnostic query-runner remediation verifier; do
  if ! az containerapp show \
    --resource-group "$platform_resource_group" \
    --name "ca-$project_name-$mode-$environment" \
    --output none 2>/dev/null; then
    echo "Delete incomplete: a trusted worker was removed." >&2
    exit 1
  fi
done

registry_id="$(python3 - "$artifact_state" "$AZURE_SUBSCRIPTION_ID" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    f"/subscriptions/{sys.argv[2]}/resourceGroups/{state['resourceGroupName']}"
    f"/providers/Microsoft.ContainerRegistry/registries/{state['registryName']}"
)
PY
)"
control_assignments="$(az role assignment list \
  --scope "$registry_id" \
  --assignee-object-id "$control_principal" \
  --query 'length(@)' \
  --output tsv)"
diagnostic_assignments="$(az role assignment list \
  --scope "$registry_id" \
  --assignee-object-id "$diagnostic_principal" \
  --query 'length(@)' \
  --output tsv)"
if [[ "$control_assignments" != "0" || "$diagnostic_assignments" != "1" ]]; then
  echo "Delete incomplete: existing registry grant separation differs." >&2
  exit 1
fi
rm -f "$state_dir/control-cycle-$environment.parameters.json"
echo "Control-cycle stack, group, and job were removed; workers, identities, and registry grants remain unchanged."