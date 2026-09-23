#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
expired_only=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
config_file="${WORKLOAD_CONFIG_FILE:-$phase_dir/environments/dev.workloads.config.json}"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"

python3 "$phase_dir/check_toolchain.py"
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

python3 "$phase_dir/workload_parameters.py" validate "$config_file"
get_config() {
  python3 "$phase_dir/workload_parameters.py" get-config "$config_file" "$1"
}
project_name="$(get_config projectName)"
environment="$(get_config environment)"
resource_group="$(get_config platformResourceGroupName)"
state_parameters="$state_dir/workloads-$environment.parameters.json"

az account show --output none
cleanup_args=(--config "$config_file")
if [[ -f "$state_parameters" ]]; then
  cleanup_args+=(--parameters "$state_parameters")
fi
python3 "$phase_dir/check_workload_cleanup.py" "${cleanup_args[@]}"

if [[ ! -f "$state_parameters" ]]; then
  echo "Trusted workers are absent; no deletion is needed."
  exit 0
fi
if [[ "$expired_only" == true ]]; then
  expires_at="$(python3 "$phase_dir/workload_parameters.py" get-expiration "$state_parameters")"
  expiry_status="$(python3 "$phase_dir/runtime_parameters.py" status --expires-at "$expires_at")"
  if [[ "$expiry_status" == "active" ]]; then
    echo "Trusted workers are not expired; no deletion is eligible."
    exit 0
  fi
  if [[ "$expiry_status" != "expired" ]]; then
    echo "Delete blocked: workload expiration status is unknown." >&2
    exit 1
  fi
  echo "Trusted workers are expired and eligible for guarded deletion."
fi

if [[ "$apply" != true ]]; then
  echo "Dry run only. No resources were changed."
  echo "Use --apply with LEARNINGNEMO_AZURE_DELETE=trusted-workloads to remove only the four workers."
  exit 0
fi
if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Delete blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription." >&2
  exit 2
fi
active_subscription="$(az account show --query id --output tsv)"
if [[ "$active_subscription" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Delete blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi
unset active_subscription
if [[ "${LEARNINGNEMO_AZURE_DELETE:-}" != "trusted-workloads" ]]; then
  echo "Delete blocked. Set LEARNINGNEMO_AZURE_DELETE=trusted-workloads for this command only." >&2
  exit 2
fi

snapshot_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$phase_dir/snapshot_foundation.py" \
  --resource-group "$resource_group" \
  --output "$state_dir/pre-delete-workloads-$environment-$snapshot_timestamp.json"

for mode in diagnostic query-runner remediation verifier; do
  app_name="ca-$project_name-$mode-$environment"
  if az resource show \
    --resource-group "$resource_group" \
    --name "$app_name" \
    --resource-type Microsoft.App/containerApps \
    --api-version 2025-01-01 \
    --output none 2>/dev/null; then
    echo "Deleting owned $mode worker."
    az resource delete \
      --resource-group "$resource_group" \
      --name "$app_name" \
      --resource-type Microsoft.App/containerApps \
      --api-version 2025-01-01
    az resource wait \
      --deleted \
      --resource-group "$resource_group" \
      --name "$app_name" \
      --resource-type Microsoft.App/containerApps \
      --api-version 2025-01-01 \
      --interval 5 \
      --timeout 300
  fi
done

python3 "$phase_dir/check_workload_cleanup.py" --config "$config_file"
rm -f "$state_parameters"
echo "Four trusted workers and their auth children are absent. WP1/WP2a resources were preserved."