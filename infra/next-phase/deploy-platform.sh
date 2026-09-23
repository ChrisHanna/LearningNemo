#!/usr/bin/env bash
if [[ -f "${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}/invoice-availability.policy.json" ]]; then
  printf 'Operator-managed invoice availability is configured; timed platform deployment is blocked.\n' >&2
  exit 1
fi
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

mode="what-if"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
template="$phase_dir/platform.bicep"
parameter_file="${RUNTIME_PARAMETERS_FILE:-$phase_dir/environments/dev.runtime.parameters.json}"
ttl_hours="${PLATFORM_RUNTIME_TTL_HOURS:-8}"

python3 "$phase_dir/check_toolchain.py"
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

python3 "$phase_dir/runtime_parameters.py" validate "$parameter_file"
get_parameter() {
  python3 "$phase_dir/runtime_parameters.py" get "$parameter_file" "$1"
}
location="$(get_parameter location)"
environment="$(get_parameter environment)"
project_name="$(get_parameter projectName)"
resource_group="$(get_parameter platformResourceGroupName)"

if ! [[ "$ttl_hours" =~ ^([1-9]|1[0-9]|2[0-4])$ ]]; then
  echo "PLATFORM_RUNTIME_TTL_HOURS must be an integer from 1 through 24." >&2
  exit 2
fi
expires_at="$(date -u -d "+$ttl_hours hours" +%Y-%m-%dT%H:%M:%SZ)"
materialized_parameters="$(mktemp)"
python3 "$phase_dir/runtime_parameters.py" materialize \
  "$parameter_file" \
  "$materialized_parameters" \
  --expires-at "$expires_at"

compiled_template="$(mktemp)"
deployment_result="$(mktemp)"
what_if_result="$(mktemp)"
cleanup() {
  rm -f "$compiled_template" "$deployment_result" "$what_if_result" "$materialized_parameters"
}
trap cleanup EXIT

preflight_args=(--parameters "$materialized_parameters")
preflight_args+=(--allow-workloads)
if [[ "$mode" == "apply" ]]; then
  preflight_args+=(--require-budget)
fi
python3 "$phase_dir/preflight_platform.py" "${preflight_args[@]}"

az bicep build --file "$template" --stdout > "$compiled_template"
python3 "$phase_dir/validate-platform.py" "$compiled_template"

deployment_name="$project_name-runtime-envelope-$environment"
az deployment group validate \
  --name "$deployment_name" \
  --resource-group "$resource_group" \
  --template-file "$template" \
  --parameters "@$materialized_parameters" \
  --output none

if [[ "$mode" == "what-if" ]]; then
  echo "Running WP2a resource-group what-if. No resources will be changed."
  az deployment group what-if \
    --name "$deployment_name" \
    --resource-group "$resource_group" \
    --template-file "$template" \
    --parameters "@$materialized_parameters" \
    --result-format ResourceIdOnly \
    --no-pretty-print \
    --output json > "$what_if_result"
  python3 "$phase_dir/summarize_what_if.py" "$what_if_result"
  echo "What-if complete. Re-run with --apply and the explicit acknowledgement to deploy."
  exit 0
fi

if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Apply blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription before any mutation." >&2
  exit 2
fi
active_subscription="$(az account show --query id --output tsv)"
if [[ "$active_subscription" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Apply blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi
unset active_subscription
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "platform-runtime-envelope" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=platform-runtime-envelope for this command only." >&2
  exit 2
fi

echo "Deploying one VNet-integrated external Consumption runtime envelope for at most $ttl_hours hour(s)."
echo "Azure will create a managed infrastructure group with a load balancer and public IP resources that incur standing charges."
echo "No app, job, persisted logs, ACR, Key Vault, SQL, VM, firewall, or NAT gateway is in this template."
az deployment group create \
  --name "$deployment_name" \
  --resource-group "$resource_group" \
  --template-file "$template" \
  --parameters "@$materialized_parameters" \
  --output json > "$deployment_result"

state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
mkdir -p "$state_dir"
state_parameters="$state_dir/runtime-$environment.parameters.json"
state_manifest="$state_dir/runtime-$environment.manifest.json"
install -m 600 "$materialized_parameters" "$state_parameters"
python3 "$phase_dir/record_platform.py" \
  --deployment-result "$deployment_result" \
  --template "$compiled_template" \
  --parameters "$materialized_parameters" \
  --parameter-source "$parameter_file" \
  --output "$state_manifest"
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  python3 "$phase_dir/verify_platform.py" --parameters "$state_parameters" --allow-workloads

echo "WP2a VNet-integrated trusted platform base deployed and verified. It contains no running application workload."
echo "Non-secret parameters and hashes are stored in $state_dir for audit and repeatability."