#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
parameter_file="${PLATFORM_PARAMETERS_FILE:-$phase_dir/environments/dev.platform.parameters.json}"

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
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

python3 "$phase_dir/platform_parameters.py" validate "$parameter_file"
get_parameter() {
  python3 "$phase_dir/platform_parameters.py" get "$parameter_file" "$1"
}
project_name="$(get_parameter projectName)"
environment="$(get_parameter environment)"
resource_group="$(get_parameter platformResourceGroupName)"
runtime_environment="$(get_parameter containerAppsEnvironmentName)"

az account show --output none
python3 "$phase_dir/verify_identities.py" --parameters "$parameter_file"
if az resource show \
  --resource-group "$resource_group" \
  --name "$runtime_environment" \
  --resource-type Microsoft.App/managedEnvironments \
  --api-version 2026-01-01 \
  --output none 2>/dev/null; then
  echo "Delete blocked: remove the runtime envelope before persistent identities." >&2
  exit 1
fi

if [[ "$apply" != true ]]; then
  echo "Dry run only. No resources were changed."
  echo "Use --apply with LEARNINGNEMO_AZURE_DELETE=platform-identities to delete only the six identities."
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
if [[ "${LEARNINGNEMO_AZURE_DELETE:-}" != "platform-identities" ]]; then
  echo "Delete blocked. Set LEARNINGNEMO_AZURE_DELETE=platform-identities for this command only." >&2
  exit 2
fi

state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
snapshot_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$phase_dir/snapshot_foundation.py" \
  --resource-group "$resource_group" \
  --output "$state_dir/pre-delete-identities-$environment-$snapshot_timestamp.json"

identity_suffixes=(control diagnostic remediation verifier query-runner workspace-controller)
for suffix in "${identity_suffixes[@]}"; do
  identity_name="id-$project_name-$suffix-$environment"
  echo "Deleting persistent service identity: $identity_name"
  az resource delete \
    --resource-group "$resource_group" \
    --name "$identity_name" \
    --resource-type Microsoft.ManagedIdentity/userAssignedIdentities \
    --api-version 2024-11-30
done

remaining="$(az resource list \
  --resource-group "$resource_group" \
  --query "[?type=='Microsoft.ManagedIdentity/userAssignedIdentities'] | length(@)" \
  --output tsv)"
if [[ "$remaining" != "0" ]]; then
  echo "Delete incomplete: one or more persistent identities remain." >&2
  exit 1
fi
echo "Persistent identities deleted. The platform group and WP1 network were preserved."