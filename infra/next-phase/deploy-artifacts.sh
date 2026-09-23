#!/usr/bin/env bash
if [[ -f "${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}/invoice-availability.policy.json" ]]; then
  printf 'Operator-managed invoice availability is configured; timed registry deployment is blocked.\n' >&2
  exit 1
fi
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

mode="what-if"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
template="$phase_dir/artifact-registry-stack.bicep"
config_file="${ARTIFACT_CONFIG_FILE:-$phase_dir/environments/dev.artifacts.config.json}"
platform_parameters="${PLATFORM_PARAMETERS_FILE:-$phase_dir/environments/dev.platform.parameters.json}"
database_parameters="${DATABASE_PARAMETERS_FILE:-$phase_dir/environments/dev.database.parameters.json}"
ttl_hours="${ARTIFACT_TTL_HOURS:-8}"
verification_scope=()

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
    --ttl-hours)
      ttl_hours="$2"
      shift 2
      ;;
    --allow-human-services)
      verification_scope=(--allow-cloud-demo --allow-human-services)
      shift
      ;;
    --allow-invoice-services)
      verification_scope=(--allow-cloud-demo --allow-human-services --allow-invoice-services)
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

python3 "$phase_dir/check_toolchain.py"
python3 "$phase_dir/artifact_parameters.py" validate "$config_file"
if ! [[ "$ttl_hours" =~ ^([1-9]|1[0-9]|2[0-4])$ ]]; then
  echo "ARTIFACT_TTL_HOURS must be an integer from 1 through 24." >&2
  exit 2
fi
get_config() {
  python3 "$phase_dir/artifact_parameters.py" get-config "$config_file" "$1"
}
location="$(get_config location)"
environment="$(get_config environment)"
project_name="$(get_config projectName)"
resource_group="$(get_config artifactResourceGroupName)"
expires_at="$(date -u -d "+$ttl_hours hours" +%Y-%m-%dT%H:%M:%SZ)"

materialized_parameters="$(mktemp)"
compiled_template="$(mktemp)"
what_if_result="$(mktemp)"
deployment_result="$(mktemp)"
cleanup() {
  rm -f "$materialized_parameters" "$compiled_template" "$what_if_result" "$deployment_result"
}
trap cleanup EXIT
chmod 600 "$materialized_parameters"
python3 "$phase_dir/artifact_parameters.py" materialize \
  "$config_file" \
  "$materialized_parameters" \
  --expires-at "$expires_at"

preflight_args=(
  --config "$config_file"
  --parameters "$materialized_parameters"
  --platform-parameters "$platform_parameters"
  --database-parameters "$database_parameters"
)
if [[ "$mode" == "apply" ]]; then
  preflight_args+=(--require-budget)
fi
python3 "$phase_dir/preflight_artifacts.py" "${preflight_args[@]}"
az bicep build --file "$template" --stdout > "$compiled_template"
python3 "$phase_dir/validate-artifacts.py" "$compiled_template"

deployment_name="$project_name-artifacts-$environment"
az deployment sub validate \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$materialized_parameters" \
  --output none
echo "Running WP3 artifact registry what-if. No resources will be changed."
az deployment sub what-if \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$materialized_parameters" \
  --result-format FullResourcePayloads \
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
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "wp3-artifacts" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=wp3-artifacts for this command only." >&2
  exit 2
fi

echo "Deploying an expiring Basic ACR and five managed-identity pull bindings through Bicep."
az deployment sub create \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$materialized_parameters" \
  --output json > "$deployment_result"

registry_count="$(az acr list --resource-group "$resource_group" --query 'length(@)' --output tsv)"
if [[ "$registry_count" != "1" ]]; then
  echo "Apply incomplete: exact artifact registry inventory could not be resolved." >&2
  exit 1
fi
registry_name="$(az acr list --resource-group "$resource_group" --query '[0].name' --output tsv)"
login_server="$(az acr list --resource-group "$resource_group" --query '[0].loginServer' --output tsv)"

state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
mkdir -p "$state_dir"
state_parameters="$state_dir/artifacts-$environment.parameters.json"
manifest="$state_dir/artifacts-$environment.manifest.json"
private_state="$state_dir/artifacts-$environment.state.json"
install -m 600 "$materialized_parameters" "$state_parameters"
python3 "$phase_dir/record_artifacts.py" \
  --deployment-result "$deployment_result" \
  --template "$compiled_template" \
  --parameters "$materialized_parameters" \
  --config "$config_file" \
  --registry-name "$registry_name" \
  --login-server "$login_server" \
  --manifest "$manifest" \
  --private-state "$private_state"
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  python3 "$phase_dir/verify_artifacts.py" --parameters "$state_parameters" "${verification_scope[@]}"
echo "WP3 artifact registry deployed and verified. Owner-only endpoint state is stored in $state_dir."