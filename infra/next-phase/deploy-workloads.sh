#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

mode="what-if"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
template="$phase_dir/workloads.bicep"
config_file="${WORKLOAD_CONFIG_FILE:-$phase_dir/environments/dev.workloads.config.json}"
runtime_parameters_file="${RUNTIME_PARAMETERS_FILE:-$phase_dir/environments/dev.runtime.parameters.json}"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
environment="dev"
auth_file="${WORKLOAD_AUTH_FILE:-$state_dir/workloads-$environment.auth.json}"
image_reference_file="${WORKLOAD_IMAGE_REFERENCE_FILE:-$state_dir/trusted-runtime-$environment.image.txt}"
release_attestation="${WORKLOAD_RELEASE_ATTESTATION_FILE:-$state_dir/trusted-runtime-$environment.release.json}"
sbom_file="${WORKLOAD_SBOM_FILE:-$state_dir/trusted-runtime-$environment.sbom.json}"
vulnerability_report="${WORKLOAD_VULNERABILITY_REPORT_FILE:-$state_dir/trusted-runtime-$environment.scan.json}"
signature_verification="${WORKLOAD_SIGNATURE_VERIFICATION_FILE:-$state_dir/trusted-runtime-$environment.signature.json}"
approval_schema="${WORKLOAD_APPROVAL_SCHEMA_FILE:-$state_dir/trusted-runtime-$environment.approval-schema.sql}"
database_state="${DATABASE_PRIVATE_STATE_FILE:-$state_dir/database-$environment.state.json}"
artifact_state="${ARTIFACT_PRIVATE_STATE_FILE:-$state_dir/artifacts-$environment.state.json}"
artifact_parameters="${ARTIFACT_PARAMETERS_FILE:-$state_dir/artifacts-$environment.parameters.json}"
network_parameters="${DATABASE_NETWORK_PARAMETERS_FILE:-$state_dir/database-network-$environment.parameters.json}"
ttl_hours="${WORKLOAD_TTL_HOURS:-1}"

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
    --config)
      config_file="$2"
      shift 2
      ;;
    --runtime-parameters)
      runtime_parameters_file="$2"
      shift 2
      ;;
    --auth-file)
      auth_file="$2"
      shift 2
      ;;
    --image-reference-file)
      image_reference_file="$2"
      shift 2
      ;;
    --release-attestation)
      release_attestation="$2"
      shift 2
      ;;
    --sbom)
      sbom_file="$2"
      shift 2
      ;;
    --vulnerability-report)
      vulnerability_report="$2"
      shift 2
      ;;
    --signature-verification)
      signature_verification="$2"
      shift 2
      ;;
    --approval-schema)
      approval_schema="$2"
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

require_private_file() {
  local label="$1"
  local path="$2"
  if [[ -z "$path" || ! -f "$path" ]]; then
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
    echo "$label must be readable and writable only by its owner (mode 0600)." >&2
    exit 2
  fi
}

require_private_file "WORKLOAD_AUTH_FILE" "$auth_file"
require_private_file "WORKLOAD_IMAGE_REFERENCE_FILE" "$image_reference_file"
require_private_file "WORKLOAD_RELEASE_ATTESTATION_FILE" "$release_attestation"
require_private_file "WORKLOAD_SBOM_FILE" "$sbom_file"
require_private_file "WORKLOAD_VULNERABILITY_REPORT_FILE" "$vulnerability_report"
require_private_file "WORKLOAD_SIGNATURE_VERIFICATION_FILE" "$signature_verification"
require_private_file "WORKLOAD_APPROVAL_SCHEMA_FILE" "$approval_schema"
require_private_file "DATABASE_PRIVATE_STATE_FILE" "$database_state"
require_private_file "ARTIFACT_PRIVATE_STATE_FILE" "$artifact_state"
require_private_file "ARTIFACT_PARAMETERS_FILE" "$artifact_parameters"
require_private_file "DATABASE_NETWORK_PARAMETERS_FILE" "$network_parameters"
if ! [[ "$ttl_hours" =~ ^([1-9]|1[0-9]|2[0-4])$ ]]; then
  echo "WORKLOAD_TTL_HOURS must be an integer from 1 through 24." >&2
  exit 2
fi
python3 "$phase_dir/workload_parameters.py" validate "$config_file"
python3 "$phase_dir/runtime_parameters.py" validate "$runtime_parameters_file"
python3 "$phase_dir/workload_release.py" \
  --attestation "$release_attestation" \
  --image-reference-file "$image_reference_file" \
  --sbom "$sbom_file" \
  --vulnerability-report "$vulnerability_report" \
  --signature-verification "$signature_verification" \
  --approval-schema "$approval_schema" \
  --source-root "$project_dir"

get_config() {
  python3 "$phase_dir/workload_parameters.py" get-config "$config_file" "$1"
}
environment="$(get_config environment)"
project_name="$(get_config projectName)"
resource_group="$(get_config platformResourceGroupName)"
expires_at="$(date -u -d "+$ttl_hours hours" +%Y-%m-%dT%H:%M:%SZ)"

materialized_parameters="$(mktemp)"
compiled_template="$(mktemp)"
deployment_result="$(mktemp)"
what_if_result="$(mktemp)"
cleanup() {
  rm -f "$materialized_parameters" "$compiled_template" "$deployment_result" "$what_if_result"
}
trap cleanup EXIT
chmod 600 "$materialized_parameters"
python3 "$phase_dir/workload_parameters.py" materialize \
  "$config_file" \
  "$materialized_parameters" \
  --auth-file "$auth_file" \
  --image-reference-file "$image_reference_file" \
  --database-state "$database_state" \
  --artifact-state "$artifact_state" \
  --expires-at "$expires_at"

preflight_args=(
  --config "$config_file"
  --parameters "$materialized_parameters"
  --runtime-parameters "$runtime_parameters_file"
  --artifact-parameters "$artifact_parameters"
  --network-parameters "$network_parameters"
)
if [[ "$mode" == "apply" ]]; then
  preflight_args+=(--require-budget)
fi
python3 "$phase_dir/preflight_workloads.py" "${preflight_args[@]}"
python3 "$phase_dir/verify_database.py" \
  --parameters "$phase_dir/environments/dev.database.parameters.json"
python3 "$phase_dir/verify_database_network.py" --parameters "$network_parameters"
python3 "$phase_dir/verify_artifacts.py" --parameters "$artifact_parameters"
az bicep build --file "$template" --stdout > "$compiled_template"
python3 "$phase_dir/validate-workloads.py" "$compiled_template"

deployment_name="$project_name-trusted-workers-$environment"
az deployment group validate \
  --name "$deployment_name" \
  --resource-group "$resource_group" \
  --template-file "$template" \
  --parameters "@$materialized_parameters" \
  --output none

if [[ "$mode" == "what-if" ]]; then
  echo "Running WP2b resource-group what-if. No resources will be changed."
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
  echo "Apply blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription." >&2
  exit 2
fi
active_subscription="$(az account show --query id --output tsv)"
if [[ "$active_subscription" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Apply blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi
unset active_subscription
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "trusted-workloads" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=trusted-workloads for this command only." >&2
  exit 2
fi

echo "Deploying four digest-pinned trusted workers with minReplicas=0 for at most $ttl_hours hour(s)."
az deployment group create \
  --name "$deployment_name" \
  --resource-group "$resource_group" \
  --template-file "$template" \
  --parameters "@$materialized_parameters" \
  --output json > "$deployment_result"

mkdir -p "$state_dir"
state_parameters="$state_dir/workloads-$environment.parameters.json"
state_manifest="$state_dir/workloads-$environment.manifest.json"
install -m 600 "$materialized_parameters" "$state_parameters"
python3 "$phase_dir/record_workloads.py" \
  --deployment-result "$deployment_result" \
  --template "$compiled_template" \
  --parameters "$materialized_parameters" \
  --config "$config_file" \
  --auth-file "$auth_file" \
  --image-reference-file "$image_reference_file" \
  --release-attestation "$release_attestation" \
  --sbom "$sbom_file" \
  --vulnerability-report "$vulnerability_report" \
  --signature-verification "$signature_verification" \
  --approval-schema "$approval_schema" \
  --output "$state_manifest"
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  python3 "$phase_dir/verify_workloads.py" \
    --config "$config_file" \
    --parameters "$state_parameters"

echo "WP2b trusted workers deployed and verified. Private identifiers remain in owner-only state."
echo "The sanitized evidence manifest is stored in $state_manifest."