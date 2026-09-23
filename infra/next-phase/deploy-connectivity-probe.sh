#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

mode="what-if"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
template="$phase_dir/connectivity-probe-stack.bicep"
config_file="${CONNECTIVITY_PROBE_CONFIG_FILE:-$phase_dir/environments/dev.connectivity-probe.config.json}"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
environment="${LEARNINGNEMO_ENVIRONMENT:-dev}"
runtime_parameters="${RUNTIME_PARAMETERS_FILE:-$state_dir/runtime-$environment.parameters.json}"
network_parameters="${DATABASE_NETWORK_PARAMETERS_FILE:-$state_dir/database-network-$environment.parameters.json}"
artifact_parameters="${ARTIFACT_PARAMETERS_FILE:-$state_dir/artifacts-$environment.parameters.json}"
artifact_state="${ARTIFACT_PRIVATE_STATE_FILE:-$state_dir/artifacts-$environment.state.json}"
database_state="${DATABASE_PRIVATE_STATE_FILE:-$state_dir/database-$environment.state.json}"
image_file="${WORKLOAD_IMAGE_REFERENCE_FILE:-$state_dir/trusted-runtime-$environment.image.txt}"
release_file="${WORKLOAD_RELEASE_ATTESTATION_FILE:-$state_dir/trusted-runtime-$environment.release.json}"
sbom_file="${WORKLOAD_SBOM_FILE:-$state_dir/trusted-runtime-$environment.sbom.json}"
scan_file="${WORKLOAD_VULNERABILITY_REPORT_FILE:-$state_dir/trusted-runtime-$environment.scan.json}"
signature_file="${WORKLOAD_SIGNATURE_VERIFICATION_FILE:-$state_dir/trusted-runtime-$environment.signature.json}"
approval_schema="${WORKLOAD_APPROVAL_SCHEMA_FILE:-$state_dir/trusted-runtime-$environment.approval-schema.sql}"
ttl_hours="${CONNECTIVITY_PROBE_TTL_HOURS:-1}"

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
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

python3 "$phase_dir/check_toolchain.py"
python3 "$phase_dir/connectivity_probe_parameters.py" validate "$config_file"
if ! [[ "$ttl_hours" =~ ^[1-4]$ ]]; then
  echo "CONNECTIVITY_PROBE_TTL_HOURS must be an integer from 1 through 4." >&2
  exit 2
fi
require_private_file() {
  local label="$1"
  local path="$2"
  if [[ ! -f "$path" ]]; then
    echo "$label must name an existing private file." >&2
    exit 2
  fi
  local permissions
  permissions="$(stat -c '%a' "$path")"
  if (( (8#$permissions & 077) != 0 )); then
    echo "$label must be owner-only." >&2
    exit 2
  fi
}
for private_file in \
  "$runtime_parameters" \
  "$network_parameters" \
  "$artifact_parameters" \
  "$artifact_state" \
  "$database_state" \
  "$image_file" \
  "$release_file" \
  "$sbom_file" \
  "$scan_file" \
  "$signature_file" \
  "$approval_schema"; do
  require_private_file "connectivity probe prerequisite" "$private_file"
done
python3 "$phase_dir/workload_release.py" \
  --attestation "$release_file" \
  --image-reference-file "$image_file" \
  --sbom "$sbom_file" \
  --vulnerability-report "$scan_file" \
  --signature-verification "$signature_file" \
  --approval-schema "$approval_schema" \
  --source-root "$project_dir"

parameters="$(mktemp)"
compiled="$(mktemp)"
what_if="$(mktemp)"
execution="$(mktemp)"
logs="$(mktemp)"
stack_started=false
cleanup() {
  local status=$?
  set +e
  local cleanup_status=0
  if [[ "$stack_started" == true ]]; then
    AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}" \
      LEARNINGNEMO_AZURE_DELETE=wp3-connectivity-probe \
      bash "$phase_dir/remove-connectivity-probe.sh" --apply --config "$config_file" || cleanup_status=1
  fi
  rm -f "$parameters" "$compiled" "$what_if" "$execution" "$logs"
  if [[ "$cleanup_status" -ne 0 ]]; then
    echo "FAIL connectivity probe cleanup failed" >&2
    exit 1
  fi
  exit "$status"
}
trap cleanup EXIT
chmod 600 "$parameters" "$execution" "$logs"
expires_at="$(date -u -d "+$ttl_hours hours" +%Y-%m-%dT%H:%M:%SZ)"
python3 "$phase_dir/connectivity_probe_parameters.py" materialize \
  "$config_file" \
  "$parameters" \
  --database-state "$database_state" \
  --artifact-state "$artifact_state" \
  --image-reference-file "$image_file" \
  --expires-at "$expires_at"
python3 "$phase_dir/preflight_connectivity_probe.py" \
  --config "$config_file" \
  --parameters "$parameters" \
  --runtime-parameters "$runtime_parameters" \
  --network-parameters "$network_parameters"
python3 "$phase_dir/verify_platform.py" --parameters "$runtime_parameters"
python3 "$phase_dir/verify_database_network.py" --parameters "$network_parameters"
python3 "$phase_dir/verify_artifacts.py" --parameters "$artifact_parameters"
az bicep build --file "$template" --stdout > "$compiled"
python3 "$phase_dir/validate-connectivity-probe.py" "$compiled"

get_config() {
  python3 "$phase_dir/connectivity_probe_parameters.py" get-config "$config_file" "$1"
}
location="$(get_config location)"
project_name="$(get_config projectName)"
probe_environment="$(get_config environment)"
resource_group="$(get_config probeResourceGroupName)"
stack_name="$project_name-connectivity-probe-$probe_environment"
job_name="caj-$project_name-netprobe-$probe_environment"
az deployment sub validate \
  --name "$stack_name-preview" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$parameters" \
  --output none
echo "Running connectivity probe what-if. No resources will be changed."
az deployment sub what-if \
  --name "$stack_name-preview" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$parameters" \
  --result-format FullResourcePayloads \
  --no-pretty-print \
  --output json > "$what_if"
python3 "$phase_dir/summarize_database_what_if.py" "$what_if"
python3 "$phase_dir/validate_connectivity_probe_what_if.py" "$what_if"
if [[ "$mode" == "what-if" ]]; then
  echo "What-if complete. Re-run with --apply and the explicit acknowledgement to probe."
  exit 0
fi
if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" || "$(az account show --query id --output tsv)" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Apply blocked: expected subscription is absent or differs." >&2
  exit 1
fi
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "wp3-connectivity-probe" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=wp3-connectivity-probe for this command only." >&2
  exit 2
fi

mkdir -p "$state_dir"
state_parameters="$state_dir/connectivity-probe-$probe_environment.parameters.json"
result_file="$state_dir/connectivity-probe-$probe_environment.result.json"
install -m 600 "$parameters" "$state_parameters"
stack_started=true
az stack sub create \
  --name "$stack_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$parameters" \
  --action-on-unmanage deleteAll \
  --resources-without-delete-support fail \
  --deny-settings-mode none \
  --description "Expiring LearningNeMo private SQL connectivity probe" \
  --yes \
  --output none
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  python3 "$phase_dir/verify_connectivity_probe.py" \
    --parameters "$state_parameters" \
    --stack-name "$stack_name"
execution_name="$(az containerapp job start \
  --resource-group "$resource_group" \
  --name "$job_name" \
  --query name \
  --output tsv)"
if [[ -z "$execution_name" ]]; then
  echo "FAIL connectivity probe returned no execution name" >&2
  exit 1
fi
wait_status=0
python3 "$phase_dir/wait_migration_execution.py" \
  --resource-group "$resource_group" \
  --job-name "$job_name" \
  --execution-name "$execution_name" \
  --timeout-seconds 240 \
  --interval-seconds 5 || wait_status=$?
az containerapp job execution show \
  --resource-group "$resource_group" \
  --name "$job_name" \
  --job-execution-name "$execution_name" \
  --output json > "$execution" || printf '{}\n' > "$execution"
az containerapp job logs show \
  --resource-group "$resource_group" \
  --name "$job_name" \
  --execution "$execution_name" \
  --container probe \
  --tail 100 \
  --format json \
  --output json > "$logs" 2>/dev/null || printf '{}\n' > "$logs"
result_status=0
python3 "$phase_dir/summarize_connectivity_probe.py" \
  --execution "$execution" \
  --logs "$logs" \
  --output "$result_file" || result_status=$?
if [[ "$wait_status" -ne 0 || "$result_status" -ne 0 ]]; then
  echo "FAIL connectivity probe did not establish private SQL TCP reachability" >&2
  exit 1
fi
echo "PASS connectivity probe established private DNS and TCP 1433 reachability"