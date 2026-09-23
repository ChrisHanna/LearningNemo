#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

mode="what-if"
operation="migration"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
template="$phase_dir/migration-stack.bicep"
identity_template="$phase_dir/database-migration-identity.bicep"
regional_identity_template="$phase_dir/database-regional-identity.bicep"
config_file="${MIGRATION_CONFIG_FILE:-$phase_dir/environments/dev.migration.config.json}"
workload_config="${WORKLOAD_CONFIG_FILE:-$phase_dir/environments/dev.workloads.config.json}"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
environment="${LEARNINGNEMO_ENVIRONMENT:-dev}"
runtime_parameters="${RUNTIME_PARAMETERS_FILE:-$state_dir/runtime-$environment.parameters.json}"
network_parameters="${DATABASE_NETWORK_PARAMETERS_FILE:-$state_dir/database-network-$environment.parameters.json}"
database_parameters="${DATABASE_PARAMETERS_FILE:-$phase_dir/environments/dev.database.parameters.json}"
database_state="${DATABASE_PRIVATE_STATE_FILE:-$state_dir/database-$environment.state.json}"
artifact_parameters="${ARTIFACT_PARAMETERS_FILE:-$state_dir/artifacts-$environment.parameters.json}"
artifact_state="${ARTIFACT_PRIVATE_STATE_FILE:-$state_dir/artifacts-$environment.state.json}"
image_file="${WORKLOAD_IMAGE_REFERENCE_FILE:-$state_dir/trusted-runtime-$environment.image.txt}"
release_file="${WORKLOAD_RELEASE_ATTESTATION_FILE:-$state_dir/trusted-runtime-$environment.release.json}"
sbom_file="${WORKLOAD_SBOM_FILE:-$state_dir/trusted-runtime-$environment.sbom.json}"
scan_file="${WORKLOAD_VULNERABILITY_REPORT_FILE:-$state_dir/trusted-runtime-$environment.scan.json}"
signature_file="${WORKLOAD_SIGNATURE_VERIFICATION_FILE:-$state_dir/trusted-runtime-$environment.signature.json}"
approval_schema="${WORKLOAD_APPROVAL_SCHEMA_FILE:-$state_dir/trusted-runtime-$environment.approval-schema.sql}"
ttl_hours="${MIGRATION_TTL_HOURS:-1}"

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
    --probe-target)
      operation="probe-target"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

python3 "$phase_dir/check_toolchain.py"
python3 "$phase_dir/migration_parameters.py" validate "$config_file"
if ! [[ "$ttl_hours" =~ ^([1-9]|1[0-9]|2[0-4])$ ]]; then
  echo "MIGRATION_TTL_HOURS must be an integer from 1 through 24." >&2
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
  "$database_state" \
  "$artifact_parameters" \
  "$artifact_state" \
  "$image_file" \
  "$release_file" \
  "$sbom_file" \
  "$scan_file" \
  "$signature_file" \
  "$approval_schema"; do
  require_private_file "migration prerequisite" "$private_file"
done
python3 "$phase_dir/workload_release.py" \
  --attestation "$release_file" \
  --image-reference-file "$image_file" \
  --sbom "$sbom_file" \
  --vulnerability-report "$scan_file" \
  --signature-verification "$signature_file" \
  --approval-schema "$approval_schema" \
  --source-root "$project_dir"

identities="$(mktemp)"
bundle="$(mktemp)"
parameters="$(mktemp)"
compiled="$(mktemp)"
what_if="$(mktemp)"
deployment="$(mktemp)"
execution="$(mktemp)"
identity_parameters="$(mktemp)"
identity_compiled="$(mktemp)"
regional_identity_compiled="$(mktemp)"
identity_what_if="$(mktemp)"
identity_deployment="$(mktemp)"
failure_execution="$(mktemp)"
failure_logs="$(mktemp)"
failure_state="$state_dir/migration-$environment.failure.json"
identity_overlay_applied=false
restore_regional_identity() {
  az deployment group create \
    --name "$project_name-regional-identity-$environment" \
    --resource-group "$database_resource_group" \
    --template-file "$regional_identity_template" \
    --parameters "@$identity_parameters" \
    --output none
}
capture_probe_logs() {
  local output="$1"
  local execution_name="$2"
  local attempt
  printf '{}\n' > "$output"
  for attempt in {1..10}; do
    if az containerapp job logs show \
      --resource-group "$resource_group" \
      --name "$job_name" \
      --execution "$execution_name" \
      --container migrator \
      --tail 100 \
      --format json \
      --output json > "$output" 2>/dev/null \
      && grep -Eq '\b(PASS|FAIL) [a-z0-9_]+\b' "$output"; then
      return 0
    fi
    if [[ "$attempt" -lt 10 ]]; then
      sleep 3
    fi
  done
  return 1
}
cleanup() {
  local status=$?
  set +e
  local recovery_status=0
  if [[ "$identity_overlay_applied" == true ]]; then
    AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}" \
      LEARNINGNEMO_AZURE_DELETE=wp3-database-migration \
      bash "$phase_dir/remove-migration.sh" --apply --parameters "$parameters" || recovery_status=1
    restore_regional_identity || recovery_status=1
  fi
  rm -f \
    "$identities" \
    "$bundle" \
    "$parameters" \
    "$compiled" \
    "$what_if" \
    "$deployment" \
    "$execution" \
    "$identity_parameters" \
    "$identity_compiled" \
    "$regional_identity_compiled" \
    "$identity_what_if" \
    "$identity_deployment" \
    "$failure_execution" \
    "$failure_logs"
  if [[ "$recovery_status" -ne 0 ]]; then
    echo "FAIL migration cleanup or regional identity restoration failed" >&2
    exit 1
  fi
  exit "$status"
}
trap cleanup EXIT
chmod 600 "$identities" "$bundle" "$parameters" "$identity_parameters"
python3 "$phase_dir/sql_migrations.py" discover --config "$workload_config" --output "$identities"
python3 "$phase_dir/sql_migrations.py" materialize --identities "$identities" --output "$bundle"
expires_at="$(date -u -d "+$ttl_hours hours" +%Y-%m-%dT%H:%M:%SZ)"
python3 "$phase_dir/migration_identity_parameters.py" materialize \
  --database-parameters "$database_parameters" \
  --output "$identity_parameters" \
  --expires-at "$expires_at"
python3 "$phase_dir/migration_parameters.py" materialize \
  "$config_file" \
  "$parameters" \
  --database-state "$database_state" \
  --artifact-state "$artifact_state" \
  --image-reference-file "$image_file" \
  --bundle "$bundle" \
  --expires-at "$expires_at"
python3 "$phase_dir/preflight_migration.py" \
  --config "$config_file" \
  --parameters "$parameters" \
  --runtime-parameters "$runtime_parameters" \
  --network-parameters "$network_parameters"
python3 "$phase_dir/verify_database.py" --parameters "$database_parameters"
python3 "$phase_dir/verify_database_network.py" --parameters "$network_parameters"
python3 "$phase_dir/verify_artifacts.py" --parameters "$artifact_parameters"
az bicep build --file "$template" --stdout > "$compiled"
python3 "$phase_dir/validate-migration.py" "$compiled"
az bicep build --file "$identity_template" --stdout > "$identity_compiled"
python3 "$phase_dir/validate-migration-identity.py" "$identity_compiled"
az bicep build --file "$regional_identity_template" --stdout > "$regional_identity_compiled"
python3 "$phase_dir/validate-database-regional-identity.py" "$regional_identity_compiled"

get_config() {
  python3 "$phase_dir/migration_parameters.py" get-config "$config_file" "$1"
}
location="$(get_config location)"
project_name="$(get_config projectName)"
resource_group="$(get_config migrationResourceGroupName)"
job_name="caj-$project_name-migrate-$environment"
deployment_name="$project_name-sql-migration-$environment"
identity_deployment_name="$project_name-migration-identity-$environment"
database_resource_group="$(python3 "$phase_dir/database_parameters.py" "$database_parameters" >/dev/null; python3 - "$database_parameters" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["parameters"]["databaseResourceGroupName"]["value"])
PY
)"
az deployment group validate \
  --name "$identity_deployment_name" \
  --resource-group "$database_resource_group" \
  --template-file "$identity_template" \
  --parameters "@$identity_parameters" \
  --output none
echo "Running temporary SQL migration identity what-if. No resources will be changed."
az deployment group what-if \
  --name "$identity_deployment_name" \
  --resource-group "$database_resource_group" \
  --template-file "$identity_template" \
  --parameters "@$identity_parameters" \
  --result-format FullResourcePayloads \
  --no-pretty-print \
  --output json > "$identity_what_if"
python3 "$phase_dir/summarize_database_what_if.py" "$identity_what_if"
az deployment sub validate \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$parameters" \
  --output none
echo "Running WP3 migration job what-if. No resources will be changed."
az deployment sub what-if \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$parameters" \
  --result-format FullResourcePayloads \
  --no-pretty-print \
  --output json > "$what_if"
python3 "$phase_dir/summarize_database_what_if.py" "$what_if"
if [[ "$mode" == "what-if" ]]; then
  echo "What-if complete. Re-run with --apply and the explicit acknowledgement to migrate."
  exit 0
fi
if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Apply blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription." >&2
  exit 2
fi
if [[ "$(az account show --query id --output tsv)" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Apply blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "wp3-database-migration" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=wp3-database-migration for this command only." >&2
  exit 2
fi

echo "Deploying one bounded SQL migration job through Bicep."
mkdir -p "$state_dir"
rm -f "$failure_state"
az deployment group create \
  --name "$identity_deployment_name" \
  --resource-group "$database_resource_group" \
  --template-file "$identity_template" \
  --parameters "@$identity_parameters" \
  --output json > "$identity_deployment"
identity_overlay_applied=true
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  python3 "$phase_dir/verify_migration_identity.py" \
    --parameters "$identity_parameters" \
    --resource-group "$database_resource_group"
az deployment sub create \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$parameters" \
  --output json > "$deployment"
start_arguments=(
  --resource-group "$resource_group"
  --name "$job_name"
)
if [[ "$operation" == "probe-target" ]]; then
  sql_server_hostname="$(python3 - "$parameters" <<'PY'
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
print(document["parameters"]["sqlServerHostname"]["value"])
PY
)"
  target_database="$(get_config databaseName)"
  sql_admin_client_id="$(az identity show \
    --resource-group "$database_resource_group" \
    --name "id-$project_name-sql-admin-$environment" \
    --query clientId \
    --output tsv)"
  if ! [[ "$sql_admin_client_id" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]]; then
    echo "SQL admin probe identity client ID is invalid." >&2
    exit 1
  fi
  start_arguments+=(
    --container-name migrator
    --image "$(tr -d '\r\n' < "$image_file")"
    --command python3
    --args /opt/learningnemo/scripts/probe-sql-odbc.py
    --env-vars
    "LEARNINGNEMO_SQL_SERVER=$sql_server_hostname"
    "LEARNINGNEMO_SQL_DATABASE=$target_database"
    "AZURE_CLIENT_ID=$sql_admin_client_id"
  )
fi
execution_name="$(az containerapp job start "${start_arguments[@]}" --query name --output tsv)"
if [[ -z "$execution_name" ]]; then
  echo "Migration job did not return an execution name." >&2
  exit 1
fi
wait_status=0
python3 "$phase_dir/wait_migration_execution.py" \
  --resource-group "$resource_group" \
  --job-name "$job_name" \
  --execution-name "$execution_name" \
  --timeout-seconds 600 \
  --interval-seconds 5 || wait_status=$?
if [[ "$operation" == "probe-target" ]]; then
  az containerapp job execution show \
    --resource-group "$resource_group" \
    --name "$job_name" \
    --job-execution-name "$execution_name" \
    --output json > "$failure_execution" || true
  capture_probe_logs "$failure_logs" "$execution_name" || true
  python3 "$phase_dir/summarize_sql_admin_probe.py" \
    --execution "$failure_execution" \
    --logs "$failure_logs"
  LEARNINGNEMO_AZURE_DELETE=wp3-database-migration \
    bash "$phase_dir/remove-migration.sh" --apply --parameters "$parameters"
  restore_regional_identity
  AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
    python3 "$phase_dir/verify_database.py" --parameters "$database_parameters"
  identity_overlay_applied=false
  echo "PASS disposable SQL admin target probe was removed and regional isolation restored."
  exit 0
fi
if [[ "$wait_status" -ne 0 ]]; then
  az containerapp job execution show \
    --resource-group "$resource_group" \
    --name "$job_name" \
    --job-execution-name "$execution_name" \
    --output json > "$failure_execution" || true
  az containerapp job logs show \
    --resource-group "$resource_group" \
    --name "$job_name" \
    --execution "$execution_name" \
    --container migrator \
    --tail 100 \
    --format json \
    --output json > "$failure_logs" 2>/dev/null || true
  python3 "$phase_dir/summarize_migration_failure.py" \
    --execution "$failure_execution" \
    --logs "$failure_logs" \
    --output "$failure_state" || true
  exit 1
fi
az containerapp job execution show \
  --resource-group "$resource_group" \
  --name "$job_name" \
  --job-execution-name "$execution_name" \
  --output json > "$execution"

mkdir -p "$state_dir"
state_parameters="$state_dir/migration-$environment.parameters.json"
state_execution="$state_dir/migration-$environment.execution.json"
state_manifest="$state_dir/migration-$environment.manifest.json"
install -m 600 "$parameters" "$state_parameters"
install -m 600 "$execution" "$state_execution"
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  python3 "$phase_dir/verify_migration.py" \
    --parameters "$state_parameters" \
    --execution-name "$execution_name"
python3 "$phase_dir/record_migration.py" \
  --deployment-result "$deployment" \
  --execution-result "$execution" \
  --template "$compiled" \
  --parameters "$parameters" \
  --config "$config_file" \
  --release-attestation "$release_file" \
  --image-reference-file "$image_file" \
  --sbom "$sbom_file" \
  --vulnerability-report "$scan_file" \
  --signature-verification "$signature_file" \
  --approval-schema "$approval_schema" \
  --output "$state_manifest"
rm -f "$failure_state"
LEARNINGNEMO_AZURE_DELETE=wp3-database-migration \
  bash "$phase_dir/remove-migration.sh" --apply --parameters "$state_parameters"
restore_regional_identity
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  python3 "$phase_dir/verify_database.py" --parameters "$database_parameters"
identity_overlay_applied=false
echo "WP3 Azure SQL migrations applied; one-shot compute was removed and identity isolation restored."