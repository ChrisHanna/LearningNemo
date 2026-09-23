#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

mode="what-if"
strict_future_providers=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
template="$phase_dir/foundation.bicep"
parameter_file="${FOUNDATION_PARAMETERS_FILE:-$phase_dir/environments/dev.parameters.json}"
ttl_days="${SAW_FOUNDATION_TTL_DAYS:-7}"

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
    --strict-future-providers)
      strict_future_providers=true
      shift
      ;;
    --parameters)
      parameter_file="$2"
      shift 2
      ;;
    --ttl-days)
      ttl_days="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

python3 "$phase_dir/foundation_parameters.py" validate "$parameter_file"
get_parameter() {
  python3 "$phase_dir/foundation_parameters.py" get "$parameter_file" "$1"
}
location="$(get_parameter location)"
environment="$(get_parameter environment)"
project_name="$(get_parameter projectName)"

if ! [[ "$ttl_days" =~ ^[1-7]$ ]]; then
  echo "SAW_FOUNDATION_TTL_DAYS must be an integer from 1 through 7." >&2
  exit 2
fi
expires_on="$(date -u -d "+$ttl_days days" +%F)"

materialized_parameters="$(mktemp)"
python3 "$phase_dir/foundation_parameters.py" materialize \
  "$parameter_file" \
  "$materialized_parameters" \
  --expires-on "$expires_on"

preflight_args=(--parameters "$materialized_parameters")
if [[ "$strict_future_providers" == true ]]; then
  preflight_args+=(--strict-future-providers)
fi

python3 "$phase_dir/preflight.py" "${preflight_args[@]}"

compiled_template="$(mktemp)"
deployment_result="$(mktemp)"
what_if_result="$(mktemp)"
cleanup() {
  rm -f "$compiled_template" "$materialized_parameters" "$deployment_result" "$what_if_result"
}
trap cleanup EXIT

az bicep build --file "$template" --stdout > "$compiled_template"
python3 "$phase_dir/validate-foundation.py" "$compiled_template"

deployment_name="$project_name-foundation-$environment"

az deployment sub validate \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$materialized_parameters" \
  --output none

if [[ "$mode" == "what-if" ]]; then
  echo "Running subscription what-if. No resources will be changed."
  az deployment sub what-if \
    --name "$deployment_name" \
    --location "$location" \
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
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "network-foundation" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=network-foundation for this command only." >&2
  exit 2
fi

echo "Deploying only resource groups, VNets, subnets, NSGs, and an empty route table."
echo "No VM, firewall, NAT gateway, public IP, Container App, registry, or database is in this template."
az deployment sub create \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$materialized_parameters" \
  --output json > "$deployment_result"

state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
mkdir -p "$state_dir"
state_parameters="$state_dir/foundation-$environment.parameters.json"
state_manifest="$state_dir/foundation-$environment.manifest.json"
install -m 600 "$materialized_parameters" "$state_parameters"
python3 "$phase_dir/record_foundation.py" \
  --deployment-result "$deployment_result" \
  --template "$compiled_template" \
  --parameters "$materialized_parameters" \
  --parameter-source "$parameter_file" \
  --output "$state_manifest"
python3 "$phase_dir/verify_foundation.py" --parameters "$materialized_parameters"

echo "Network foundation deployed. The SAW perimeter remains POC NSG-only until the firewall phase."
echo "Resolved non-secret parameters and hashes are stored in $state_dir for audit and repeatability."