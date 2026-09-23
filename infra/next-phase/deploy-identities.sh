#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

mode="what-if"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
template="$phase_dir/identities.bicep"
parameter_file="${PLATFORM_PARAMETERS_FILE:-$phase_dir/environments/dev.platform.parameters.json}"

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
environment="$(get_parameter environment)"
project_name="$(get_parameter projectName)"
resource_group="$(get_parameter platformResourceGroupName)"

python3 "$phase_dir/preflight_identities.py" --parameters "$parameter_file"

compiled_template="$(mktemp)"
deployment_result="$(mktemp)"
what_if_result="$(mktemp)"
cleanup() {
  rm -f "$compiled_template" "$deployment_result" "$what_if_result"
}
trap cleanup EXIT

az bicep build --file "$template" --stdout > "$compiled_template"
python3 "$phase_dir/validate-identities.py" "$compiled_template"

deployment_name="$project_name-platform-identities-$environment"
az deployment group validate \
  --name "$deployment_name" \
  --resource-group "$resource_group" \
  --template-file "$template" \
  --parameters "@$parameter_file" \
  --output none

if [[ "$mode" == "what-if" ]]; then
  echo "Running identity-only resource-group what-if. No resources will be changed."
  az deployment group what-if \
    --name "$deployment_name" \
    --resource-group "$resource_group" \
    --template-file "$template" \
    --parameters "@$parameter_file" \
    --result-format ResourceIdOnly \
    --no-pretty-print \
    --output json > "$what_if_result"
  python3 "$phase_dir/summarize_what_if.py" "$what_if_result"
  echo "Identity what-if complete. Re-run with --apply and the explicit acknowledgement to deploy."
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
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "platform-identities" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=platform-identities for this command only." >&2
  exit 2
fi

echo "Deploying six regional service identities only. No compute, app, network, database, or logging resource is included."
az deployment group create \
  --name "$deployment_name" \
  --resource-group "$resource_group" \
  --template-file "$template" \
  --parameters "@$parameter_file" \
  --output json > "$deployment_result"

state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
mkdir -p "$state_dir"
state_parameters="$state_dir/identities-$environment.parameters.json"
state_manifest="$state_dir/identities-$environment.manifest.json"
install -m 600 "$parameter_file" "$state_parameters"
python3 "$phase_dir/record_identities.py" \
  --deployment-result "$deployment_result" \
  --template "$compiled_template" \
  --parameters "$parameter_file" \
  --parameter-source "$parameter_file" \
  --output "$state_manifest"
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  python3 "$phase_dir/verify_identities.py" --parameters "$state_parameters"

echo "Persistent identity slice deployed and verified. No runtime compute was created."