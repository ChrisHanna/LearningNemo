#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

mode="what-if"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
template="$phase_dir/database-stack.bicep"
network_template="$phase_dir/database-network.bicep"
parameter_file="${DATABASE_PARAMETERS_FILE:-$phase_dir/environments/dev.database.parameters.json}"
platform_parameters="${PLATFORM_PARAMETERS_FILE:-$phase_dir/environments/dev.platform.parameters.json}"

if [[ -f "$HOME/.local/state/learningnemo/sql-paid-overage.request.json" ]]; then
  echo "Free-only base deployment blocked: retained SQL has an irreversible paid-overage transition. Verify with configure_sql_paid_overage.py; do not reapply AutoPause." >&2
  exit 2
fi

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

python3 "$phase_dir/check_toolchain.py"
python3 "$phase_dir/database_parameters.py" "$parameter_file"
compiled_base="$(mktemp)"
compiled_network="$(mktemp)"
what_if_result="$(mktemp)"
deployment_result="$(mktemp)"
cleanup() {
  rm -f "$compiled_base" "$compiled_network" "$what_if_result" "$deployment_result"
}
trap cleanup EXIT

az bicep build --file "$template" --stdout > "$compiled_base"
az bicep build --file "$network_template" --stdout > "$compiled_network"
python3 "$phase_dir/validate-database.py" "$compiled_base" "$compiled_network"
preflight_args=(
  --parameters "$parameter_file"
  --platform-parameters "$platform_parameters"
)
if [[ "$mode" == "apply" ]]; then
  preflight_args+=(--require-budget)
fi
python3 "$phase_dir/preflight_database.py" "${preflight_args[@]}"

get_parameter() {
  python3 - "$parameter_file" "$1" <<'PY'
import json
import sys
document = json.load(open(sys.argv[1], encoding="utf-8"))
value = document["parameters"][sys.argv[2]]["value"]
if not isinstance(value, str):
    raise SystemExit("requested parameter is not a string")
print(value)
PY
}
location="$(get_parameter location)"
environment="$(get_parameter environment)"
resource_group="$(get_parameter databaseResourceGroupName)"
deployment_name="learningnemo-database-$location-$environment"

az deployment sub validate \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$parameter_file" \
  --output none

echo "Running WP3 database subscription what-if. No resources will be changed."
az deployment sub what-if \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$parameter_file" \
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
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "wp3-database" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=wp3-database for this command only." >&2
  exit 2
fi

echo "Deploying the Entra-only free-limit WP3 database base through Bicep."
az deployment sub create \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$parameter_file" \
  --output json > "$deployment_result"

server_names="$(az resource list \
  --resource-group "$resource_group" \
  --resource-type Microsoft.Sql/servers \
  --query '[].name' \
  --output tsv)"
if [[ -z "$server_names" || "$server_names" == *$'\n'* ]]; then
  echo "Apply incomplete: exact SQL server inventory could not be resolved." >&2
  exit 1
fi

state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
mkdir -p "$state_dir"
manifest="$state_dir/database-$environment.manifest.json"
private_state="$state_dir/database-$environment.state.json"
python3 "$phase_dir/record_database.py" \
  --deployment-result "$deployment_result" \
  --template "$compiled_base" \
  --parameters "$parameter_file" \
  --server-name "$server_names" \
  --manifest "$manifest" \
  --private-state "$private_state"
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  python3 "$phase_dir/verify_database.py" --parameters "$parameter_file"
echo "WP3 database base deployed and verified. It has no public endpoint or schema-migration compute."
echo "Sanitized evidence and owner-only endpoint state are stored in $state_dir."