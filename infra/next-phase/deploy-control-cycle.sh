#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

mode="what-if"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
config_file="${CONTROL_CYCLE_CONFIG_FILE:-$phase_dir/environments/dev.control-cycle.config.json}"
workload_config="${WORKLOAD_CONFIG_FILE:-$phase_dir/environments/dev.workloads.config.json}"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
environment="${LEARNINGNEMO_ENVIRONMENT:-dev}"
auth_file="${WORKLOAD_AUTH_FILE:-$state_dir/workloads-$environment.auth.json}"
image_file="${WORKLOAD_IMAGE_REFERENCE_FILE:-$state_dir/trusted-runtime-$environment.image.txt}"
release_file="${WORKLOAD_RELEASE_ATTESTATION_FILE:-$state_dir/trusted-runtime-$environment.release.json}"
sbom_file="${WORKLOAD_SBOM_FILE:-$state_dir/trusted-runtime-$environment.sbom.json}"
scan_file="${WORKLOAD_VULNERABILITY_REPORT_FILE:-$state_dir/trusted-runtime-$environment.scan.json}"
signature_file="${WORKLOAD_SIGNATURE_VERIFICATION_FILE:-$state_dir/trusted-runtime-$environment.signature.json}"
approval_schema="${WORKLOAD_APPROVAL_SCHEMA_FILE:-$state_dir/trusted-runtime-$environment.approval-schema.sql}"
database_state="${DATABASE_PRIVATE_STATE_FILE:-$state_dir/database-$environment.state.json}"
artifact_state="${ARTIFACT_PRIVATE_STATE_FILE:-$state_dir/artifacts-$environment.state.json}"
artifact_parameters="${ARTIFACT_PARAMETERS_FILE:-$state_dir/artifacts-$environment.parameters.json}"
network_parameters="${DATABASE_NETWORK_PARAMETERS_FILE:-$state_dir/database-network-$environment.parameters.json}"
runtime_parameters="${RUNTIME_PARAMETERS_FILE:-$state_dir/runtime-$environment.parameters.json}"
workload_parameters="${WORKLOAD_PARAMETERS_FILE:-$state_dir/workloads-$environment.parameters.json}"
migration_manifest="${MIGRATION_MANIFEST_FILE:-$state_dir/migration-$environment.manifest.json}"
migration_config="${MIGRATION_CONFIG_FILE:-$phase_dir/environments/dev.migration.config.json}"
ttl_hours="${CONTROL_CYCLE_TTL_HOURS:-1}"
template="$phase_dir/control-cycle-stack.bicep"

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
python3 "$phase_dir/control_cycle_parameters.py" validate "$config_file"
if ! [[ "$ttl_hours" =~ ^[1-4]$ ]]; then
  echo "CONTROL_CYCLE_TTL_HOURS must be an integer from 1 through 4." >&2
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
  "$auth_file" \
  "$image_file" \
  "$release_file" \
  "$sbom_file" \
  "$scan_file" \
  "$signature_file" \
  "$approval_schema" \
  "$database_state" \
  "$artifact_state" \
  "$artifact_parameters" \
  "$network_parameters" \
  "$runtime_parameters" \
  "$workload_parameters" \
  "$migration_manifest"; do
  require_private_file "control-cycle prerequisite" "$private_file"
done
python3 "$phase_dir/workload_release.py" \
  --attestation "$release_file" \
  --image-reference-file "$image_file" \
  --sbom "$sbom_file" \
  --vulnerability-report "$scan_file" \
  --signature-verification "$signature_file" \
  --approval-schema "$approval_schema" \
  --source-root "$project_dir"
python3 "$phase_dir/verify_migration_evidence.py" \
  --manifest "$migration_manifest" \
  --config "$migration_config" \
  --release-attestation "$release_file" \
  --image-reference-file "$image_file"

parameters="$(mktemp)"
compiled="$(mktemp)"
what_if="$(mktemp)"
execution="$(mktemp)"
logs="$(mktemp)"
stack_error="$(mktemp)"
stack_started=false
cleanup() {
  local status=$?
  set +e
  local cleanup_status=0
  if [[ "$stack_started" == true ]]; then
    AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}" \
      LEARNINGNEMO_AZURE_DELETE=wp3-control-cycle \
      bash "$phase_dir/remove-control-cycle.sh" --apply --config "$config_file" || cleanup_status=1
  fi
  rm -f "$parameters" "$compiled" "$what_if" "$execution" "$logs" "$stack_error"
  if [[ "$cleanup_status" -ne 0 ]]; then
    echo "FAIL control-cycle cleanup failed" >&2
    exit 1
  fi
  exit "$status"
}
trap cleanup EXIT
chmod 600 "$parameters" "$execution" "$logs"
requested_expires_at="$(date -u -d "+$ttl_hours hours" +%Y-%m-%dT%H:%M:%SZ)"
expires_at="$(python3 - \
  "$requested_expires_at" \
  "$runtime_parameters" \
  "$network_parameters" \
  "$workload_parameters" \
  "$artifact_state" <<'PY'
import datetime as dt
import json
import sys


def expiration(path: str) -> dt.datetime:
    document = json.load(open(path, encoding="utf-8"))
    value = document.get("expiresAt")
    if value is None:
        value = document["parameters"]["expiresAt"]["value"]
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


requested = dt.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
bounded = min(requested, *(expiration(path) for path in sys.argv[2:]))
print(bounded.astimezone(dt.UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"))
PY
)"
python3 "$phase_dir/control_cycle_parameters.py" materialize \
  "$config_file" \
  "$parameters" \
  --auth-file "$auth_file" \
  --database-state "$database_state" \
  --artifact-state "$artifact_state" \
  --image-reference-file "$image_file" \
  --expires-at "$expires_at"
python3 "$phase_dir/preflight_control_cycle.py" \
  --config "$config_file" \
  --parameters "$parameters" \
  --runtime-parameters "$runtime_parameters" \
  --network-parameters "$network_parameters" \
  --workload-config "$workload_config" \
  --workload-parameters "$workload_parameters"
python3 "$phase_dir/verify_database.py" --parameters "$phase_dir/environments/dev.database.parameters.json"
python3 "$phase_dir/verify_database_network.py" --parameters "$network_parameters"
python3 "$phase_dir/verify_artifacts.py" --parameters "$artifact_parameters"
python3 "$phase_dir/verify_workloads.py" \
  --config "$workload_config" \
  --parameters "$workload_parameters"
az bicep build --file "$template" --stdout > "$compiled"
python3 "$phase_dir/validate-control-cycle.py" "$compiled"

get_config() {
  python3 "$phase_dir/control_cycle_parameters.py" get-config "$config_file" "$1"
}
location="$(get_config location)"
project_name="$(get_config projectName)"
control_environment="$(get_config environment)"
resource_group="$(get_config controlResourceGroupName)"
stack_name="$project_name-control-cycle-$control_environment"
job_name="caj-$project_name-cycle-$control_environment"
az deployment sub validate \
  --name "$stack_name-preview" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$parameters" \
  --output none
echo "Running control-cycle what-if. No resources will be changed."
az deployment sub what-if \
  --name "$stack_name-preview" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$parameters" \
  --result-format FullResourcePayloads \
  --no-pretty-print \
  --output json > "$what_if"
python3 "$phase_dir/summarize_database_what_if.py" "$what_if"
python3 "$phase_dir/validate_control_cycle_what_if.py" "$what_if"
if [[ "$mode" == "what-if" ]]; then
  echo "What-if complete. Re-run with --apply and the explicit acknowledgement to execute the live cycle."
  exit 0
fi
if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" || "$(az account show --query id --output tsv)" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Apply blocked: expected subscription is absent or differs." >&2
  exit 1
fi
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "wp3-control-cycle" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=wp3-control-cycle for this command only." >&2
  exit 2
fi

state_parameters="$state_dir/control-cycle-$control_environment.parameters.json"
result_file="$state_dir/control-cycle-$control_environment.result.json"
install -m 600 "$parameters" "$state_parameters"
stack_started=true
if ! az stack sub create \
  --name "$stack_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$parameters" \
  --action-on-unmanage deleteAll \
  --resources-without-delete-support fail \
  --deny-settings-mode none \
  --description "Expiring LearningNeMo approval-bound live control cycle" \
  --yes \
  --output none > /dev/null 2> "$stack_error"; then
  echo "FAIL control-cycle stack deployment failed" >&2
  exit 1
fi
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  python3 "$phase_dir/verify_control_cycle.py" \
    --parameters "$state_parameters" \
    --stack-name "$stack_name"
execution_name="$(az containerapp job start \
  --resource-group "$resource_group" \
  --name "$job_name" \
  --query name \
  --output tsv)"
if [[ -z "$execution_name" ]]; then
  echo "FAIL control-cycle job returned no execution name" >&2
  exit 1
fi
wait_status=0
python3 "$phase_dir/wait_migration_execution.py" \
  --resource-group "$resource_group" \
  --job-name "$job_name" \
  --execution-name "$execution_name" \
  --timeout-seconds 600 \
  --interval-seconds 5 || wait_status=$?
az containerapp job execution show \
  --resource-group "$resource_group" \
  --name "$job_name" \
  --job-execution-name "$execution_name" \
  --output json > "$execution" || printf '{}\n' > "$execution"
printf '{}\n' > "$logs"
for attempt in {1..15}; do
  if az containerapp job logs show \
    --resource-group "$resource_group" \
    --name "$job_name" \
    --execution "$execution_name" \
    --container control-cycle \
    --tail 100 \
    --format json \
    --output json > "$logs" 2>/dev/null \
    && grep -Eq '\b(PASS|FAIL) live_workflow_[a-z0-9_]+\b' "$logs"; then
    break
  fi
  if [[ "$attempt" -lt 15 ]]; then
    sleep 3
  fi
done
result_status=0
python3 "$phase_dir/summarize_control_cycle.py" \
  --execution "$execution" \
  --logs "$logs" \
  --release-attestation "$release_file" \
  --image-reference-file "$image_file" \
  --template "$compiled" \
  --parameters "$parameters" \
  --output "$result_file" || result_status=$?
if [[ "$wait_status" -ne 0 || "$result_status" -ne 0 ]]; then
  echo "FAIL live control-cycle execution did not succeed" >&2
  exit 1
fi
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  LEARNINGNEMO_AZURE_DELETE=wp3-control-cycle \
  bash "$phase_dir/remove-control-cycle.sh" --apply --config "$config_file"
stack_started=false
echo "PASS live control cycle completed; disposable compute was removed and evidence was preserved."