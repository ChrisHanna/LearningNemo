#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
expired_only=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
config_file="${DATABASE_NETWORK_CONFIG_FILE:-$phase_dir/environments/dev.database-network.config.json}"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"

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

python3 "$phase_dir/check_toolchain.py"
python3 "$phase_dir/database_network_parameters.py" validate "$config_file"
get_config() {
  python3 "$phase_dir/database_network_parameters.py" get-config "$config_file" "$1"
}
environment="$(get_config environment)"
resource_group="$(get_config databaseNetworkResourceGroupName)"
state_parameters="$state_dir/database-network-$environment.parameters.json"

az account show --output none
cleanup_args=(--config "$config_file")
if [[ -f "$state_parameters" ]]; then
  cleanup_args+=(--parameters "$state_parameters")
fi
python3 "$phase_dir/check_database_network_cleanup.py" "${cleanup_args[@]}"
if [[ "$(az group exists --name "$resource_group" --output tsv)" != "true" ]]; then
  rm -f "$state_parameters"
  echo "WP3 database network overlay is absent; no deletion is needed."
  exit 0
fi
if [[ "$expired_only" == true ]]; then
  expires_at="$(python3 - "$state_parameters" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["parameters"]["expiresAt"]["value"])
PY
)"
  if [[ "$(python3 "$phase_dir/runtime_parameters.py" status --expires-at "$expires_at")" == "active" ]]; then
    echo "WP3 database network overlay is not expired; no deletion is eligible."
    exit 0
  fi
  echo "WP3 database network overlay is expired and eligible for guarded deletion."
fi
if [[ "$apply" != true ]]; then
  echo "Dry run only. No resources were changed."
  echo "Use --apply with LEARNINGNEMO_AZURE_DELETE=wp3-database-network to remove only the network overlay."
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
if [[ "${LEARNINGNEMO_AZURE_DELETE:-}" != "wp3-database-network" ]]; then
  echo "Delete blocked. Set LEARNINGNEMO_AZURE_DELETE=wp3-database-network for this command only." >&2
  exit 2
fi

snapshot_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$phase_dir/snapshot_foundation.py" \
  --resource-group "$resource_group" \
  --output "$state_dir/pre-delete-database-network-$environment-$snapshot_timestamp.json"
az group delete --name "$resource_group" --yes
if [[ "$(az group exists --name "$resource_group" --output tsv)" == "true" ]]; then
  echo "Delete incomplete: WP3 database network group remains." >&2
  exit 1
fi
rm -f "$state_parameters"
echo "WP3 database network overlay removed; the SQL base and trusted VNet were preserved."