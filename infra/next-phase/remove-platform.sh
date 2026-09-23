#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
expired_only=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
parameter_file="${RUNTIME_PARAMETERS_FILE:-$phase_dir/environments/dev.runtime.parameters.json}"

python3 "$phase_dir/check_toolchain.py"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      apply=true
      shift
      ;;
    --parameters)
      parameter_file="$2"
      shift 2
      ;;
    --expired-only)
      expired_only=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

python3 "$phase_dir/runtime_parameters.py" validate "$parameter_file"
get_parameter() {
  python3 "$phase_dir/runtime_parameters.py" get "$parameter_file" "$1"
}
project_name="$(get_parameter projectName)"
environment="$(get_parameter environment)"
resource_group="$(get_parameter platformResourceGroupName)"
managed_environment="$(get_parameter containerAppsEnvironmentName)"
infrastructure_group="mrg-$project_name-container-apps-$environment"

az account show --output none
python3 "$phase_dir/check_platform_cleanup.py" --parameters "$parameter_file"

if [[ "$expired_only" == true ]]; then
  if ! az resource show \
    --resource-group "$resource_group" \
    --name "$managed_environment" \
    --resource-type Microsoft.App/managedEnvironments \
    --api-version 2026-01-01 \
    --output none 2>/dev/null; then
    echo "Runtime envelope is absent; no deletion is needed."
    exit 0
  fi
  expires_at="$(az resource show \
    --resource-group "$resource_group" \
    --name "$managed_environment" \
    --resource-type Microsoft.App/managedEnvironments \
    --api-version 2026-01-01 \
    --query tags.expiresAt \
    --output tsv)"
  if ! expiry_status="$(python3 "$phase_dir/runtime_parameters.py" status --expires-at "$expires_at")"; then
    echo "Delete blocked: runtime expiresAt tag is missing or invalid." >&2
    exit 1
  fi
  if [[ "$expiry_status" == "expired" ]]; then
    echo "Runtime envelope is expired and eligible for guarded deletion."
  elif [[ "$expiry_status" == "active" ]]; then
    echo "Runtime envelope is not expired; no deletion is eligible."
    exit 0
  else
    echo "Delete blocked: runtime expiration status is unknown." >&2
    exit 1
  fi
fi

if [[ "$apply" != true ]]; then
  echo "Dry run only. No resources were changed."
  echo "Use --apply with LEARNINGNEMO_AZURE_DELETE=platform-runtime-envelope to remove only the runtime envelope."
  exit 0
fi

if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Delete blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription before any mutation." >&2
  exit 2
fi
active_subscription="$(az account show --query id --output tsv)"
if [[ "$active_subscription" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Delete blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi
unset active_subscription
if [[ "${LEARNINGNEMO_AZURE_DELETE:-}" != "platform-runtime-envelope" ]]; then
  echo "Delete blocked. Set LEARNINGNEMO_AZURE_DELETE=platform-runtime-envelope for this command only." >&2
  exit 2
fi

state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
snapshot_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$phase_dir/snapshot_foundation.py" \
  --resource-group "$resource_group" \
  --output "$state_dir/pre-delete-platform-$environment-$snapshot_timestamp.json"
if [[ "$(az group exists --name "$infrastructure_group" --output tsv)" == "true" ]]; then
  python3 "$phase_dir/snapshot_foundation.py" \
    --resource-group "$infrastructure_group" \
    --output "$state_dir/pre-delete-managed-platform-$environment-$snapshot_timestamp.json"
fi

if az resource show \
  --resource-group "$resource_group" \
  --name "$managed_environment" \
  --resource-type Microsoft.App/managedEnvironments \
  --api-version 2026-01-01 \
  --output none 2>/dev/null; then
  echo "Deleting the empty WP2a Container Apps environment."
  az resource delete \
    --resource-group "$resource_group" \
    --name "$managed_environment" \
    --resource-type Microsoft.App/managedEnvironments \
    --api-version 2026-01-01
fi

if [[ "$(az group exists --name "$infrastructure_group" --output tsv)" == "true" ]]; then
  echo "Waiting for Azure to remove the Container Apps managed infrastructure group."
  if ! az group wait \
    --deleted \
    --resource-group "$infrastructure_group" \
    --interval 15 \
    --timeout 900; then
    echo "Delete incomplete: the Azure-managed infrastructure group still exists." >&2
    exit 1
  fi
fi

remaining="$(az resource list \
  --resource-group "$resource_group" \
  --query "[?type=='Microsoft.App/managedEnvironments'] | length(@)" \
  --output tsv)"
if [[ "$remaining" != "0" ]]; then
  echo "Delete incomplete: the runtime environment remains." >&2
  exit 1
fi
if [[ "$(az group exists --name "$infrastructure_group" --output tsv)" == "true" ]]; then
  echo "Delete incomplete: the Azure-managed infrastructure group remains." >&2
  exit 1
fi

echo "Runtime envelope and managed infrastructure group deleted. The six identities and WP1 network foundation were preserved."