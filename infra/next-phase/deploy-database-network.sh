#!/usr/bin/env bash
if [[ -f "${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}/invoice-availability.policy.json" ]]; then
  printf 'Operator-managed invoice availability is configured; timed network deployment is blocked.\n' >&2
  exit 1
fi
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

mode="what-if"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
template="$phase_dir/database-network-stack.bicep"
network_template="$phase_dir/database-network.bicep"
base_template="$phase_dir/database-stack.bicep"
config_file="${DATABASE_NETWORK_CONFIG_FILE:-$phase_dir/environments/dev.database-network.config.json}"
database_parameters="${DATABASE_PARAMETERS_FILE:-$phase_dir/environments/dev.database.parameters.json}"
platform_parameters="${PLATFORM_PARAMETERS_FILE:-$phase_dir/environments/dev.platform.parameters.json}"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
database_state="${DATABASE_PRIVATE_STATE_FILE:-}"
ttl_hours="${DATABASE_NETWORK_TTL_HOURS:-8}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      mode="apply"
      shift
      ;;
    --what-if)
      mode="what-if"
      shift
      ;;
    --config)
      config_file="$2"
      shift 2
      ;;
    --database-state)
      database_state="$2"
      shift 2
      ;;
    --ttl-hours)
      ttl_hours="$2"
      shift 2
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
location="$(get_config location)"
project_name="$(get_config projectName)"
network_resource_group="$(get_config databaseNetworkResourceGroupName)"
if [[ -z "$database_state" ]]; then
  database_state="$state_dir/database-$environment.state.json"
fi

require_private_file() {
  local label="$1"
  local path="$2"
  if [[ ! -f "$path" ]]; then
    echo "$label must name an existing private file." >&2
    exit 2
  fi
  local resolved
  resolved="$(realpath "$path")"
  if [[ "$resolved" == "$project_dir"/* ]]; then
    echo "$label must be stored outside the repository." >&2
    exit 2
  fi
  local permissions
  permissions="$(stat -c '%a' "$resolved")"
  if (( (8#$permissions & 077) != 0 )); then
    echo "$label must be readable and writable only by its owner." >&2
    exit 2
  fi
}

require_private_file "DATABASE_PRIVATE_STATE_FILE" "$database_state"
if ! [[ "$ttl_hours" =~ ^([1-9]|1[0-9]|2[0-4])$ ]]; then
  echo "DATABASE_NETWORK_TTL_HOURS must be an integer from 1 through 24." >&2
  exit 2
fi
expires_at="$(date -u -d "+$ttl_hours hours" +%Y-%m-%dT%H:%M:%SZ)"

materialized_parameters="$(mktemp)"
compiled_template="$(mktemp)"
compiled_network="$(mktemp)"
compiled_base="$(mktemp)"
what_if_result="$(mktemp)"
deployment_result="$(mktemp)"
cleanup() {
  rm -f \
    "$materialized_parameters" \
    "$compiled_template" \
    "$compiled_network" \
    "$compiled_base" \
    "$what_if_result" \
    "$deployment_result"
}
trap cleanup EXIT
chmod 600 "$materialized_parameters"
python3 "$phase_dir/database_network_parameters.py" materialize \
  "$config_file" \
  "$materialized_parameters" \
  --database-state "$database_state" \
  --expires-at "$expires_at"

preflight_args=(
  --config "$config_file"
  --parameters "$materialized_parameters"
  --database-parameters "$database_parameters"
  --platform-parameters "$platform_parameters"
)
if [[ "$mode" == "apply" ]]; then
  preflight_args+=(--require-budget)
fi
python3 "$phase_dir/preflight_database_network.py" "${preflight_args[@]}"
python3 "$phase_dir/verify_database.py" --parameters "$database_parameters"

az bicep build --file "$base_template" --stdout > "$compiled_base"
az bicep build --file "$network_template" --stdout > "$compiled_network"
az bicep build --file "$template" --stdout > "$compiled_template"
python3 "$phase_dir/validate-database.py" "$compiled_base" "$compiled_network"

deployment_name="$project_name-database-network-$environment"
az deployment sub validate \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$materialized_parameters" \
  --output none

echo "Running WP3 database network what-if. No resources will be changed."
az deployment sub what-if \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$materialized_parameters" \
  --result-format ResourceIdOnly \
  --no-pretty-print \
  --output json > "$what_if_result"
python3 "$phase_dir/summarize_database_what_if.py" "$what_if_result"
if [[ "$mode" == "what-if" ]]; then
  echo "What-if complete. Re-run with --apply and the explicit acknowledgement to deploy."
  exit 0
fi

if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Apply blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription." >&2
  exit 2
fi
active_subscription="$(az account show --query id --output tsv)"
if [[ "$active_subscription" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Apply blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi
unset active_subscription
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "wp3-database-network" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=wp3-database-network for this command only." >&2
  exit 2
fi

echo "Deploying an expiring SQL private endpoint and DNS overlay through Bicep for at most $ttl_hours hour(s)."
az deployment sub create \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$materialized_parameters" \
  --output json > "$deployment_result"

mkdir -p "$state_dir"
state_parameters="$state_dir/database-network-$environment.parameters.json"
state_manifest="$state_dir/database-network-$environment.manifest.json"
install -m 600 "$materialized_parameters" "$state_parameters"
python3 "$phase_dir/record_database_network.py" \
  --deployment-result "$deployment_result" \
  --template "$compiled_template" \
  --parameters "$materialized_parameters" \
  --config "$config_file" \
  --output "$state_manifest"
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  python3 "$phase_dir/verify_database_network.py" --parameters "$state_parameters"
echo "WP3 SQL private-network overlay deployed and verified in its dedicated resource group."
echo "Sanitized evidence and owner-only parameters are stored in $state_dir."