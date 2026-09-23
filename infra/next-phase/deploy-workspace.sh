#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

mode="what-if"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
config_file="${WORKSPACE_CONFIG_FILE:-$phase_dir/environments/dev.workspace.config.json}"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
environment="${LEARNINGNEMO_ENVIRONMENT:-dev}"
foundation_parameters="${FOUNDATION_PARAMETERS_FILE:-$state_dir/foundation-$environment.parameters.json}"
database_state="${DATABASE_PRIVATE_STATE_FILE:-$state_dir/database-$environment.state.json}"
runtime_parameters="${RUNTIME_PARAMETERS_FILE:-$state_dir/runtime-$environment.parameters.json}"
network_parameters="${DATABASE_NETWORK_PARAMETERS_FILE:-$state_dir/database-network-$environment.parameters.json}"
artifact_state="${ARTIFACT_PRIVATE_STATE_FILE:-$state_dir/artifacts-$environment.state.json}"
workload_parameters="${WORKLOAD_PARAMETERS_FILE:-$state_dir/workloads-$environment.parameters.json}"
ttl_hours="${WORKSPACE_TTL_HOURS:-1}"
template="$phase_dir/workspace.bicep"
openshell_template="$phase_dir/workspace-openshell-bootstrap.bicep"
lock_template="$phase_dir/workspace-runtime-lock.bicep"
egress_template="$phase_dir/workspace-bootstrap-egress.bicep"
subnet_template="$phase_dir/workspace-subnet-egress.bicep"

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
python3 "$phase_dir/workspace_parameters.py" validate "$config_file"
if ! [[ "$ttl_hours" =~ ^[1-4]$ ]]; then
  echo "WORKSPACE_TTL_HOURS must be an integer from 1 through 4." >&2
  exit 2
fi
require_private_file() {
  local path="$1"
  [[ -f "$path" ]] || { echo "Workspace prerequisite must exist." >&2; exit 2; }
  local permissions
  permissions="$(stat -c '%a' "$path")"
  (( (8#$permissions & 077) == 0 )) || { echo "Workspace prerequisite must be owner-only." >&2; exit 2; }
}
for private_file in \
  "$foundation_parameters" \
  "$database_state" \
  "$runtime_parameters" \
  "$network_parameters" \
  "$artifact_state" \
  "$workload_parameters"; do
  require_private_file "$private_file"
done

get_config() {
  python3 "$phase_dir/workspace_parameters.py" get-config "$config_file" "$1"
}
project_name="$(get_config projectName)"
workspace_environment="$(get_config environment)"
location="$(get_config location)"
resource_group="$(get_config sawResourceGroupName)"
workspace_stack="$project_name-saw-workspace-$workspace_environment"
openshell_stack="$project_name-openshell-bootstrap-$workspace_environment"
lock_stack="$project_name-saw-runtime-lock-$workspace_environment"
bootstrap_stack="$project_name-saw-bootstrap-egress-$workspace_environment"
workspace_subnet_prefix="$(python3 "$phase_dir/foundation_parameters.py" get "$foundation_parameters" sawWorkspaceSubnetPrefix)"
owner_tag="$(get_config ownerTag)"
additional_tags="$(python3 - "$config_file" <<'PY'
import json
import sys
print(json.dumps(json.load(open(sys.argv[1], encoding="utf-8"))["additionalTags"], separators=(",", ":")))
PY
)"
ssh_key="$state_dir/workspace-$workspace_environment.ssh"
ssh_public_key="$ssh_key.pub"
if [[ ! -f "$ssh_key" || ! -f "$ssh_public_key" ]]; then
  rm -f "$ssh_key" "$ssh_public_key"
  ssh-keygen -q -t ed25519 -a 100 -N '' -C "learningnemo-saw-$workspace_environment" -f "$ssh_key"
  chmod 600 "$ssh_key" "$ssh_public_key"
fi
require_private_file "$ssh_key"
require_private_file "$ssh_public_key"

requested_expires_at="$(date -u -d "+$ttl_hours hours" +%Y-%m-%dT%H:%M:%SZ)"
expires_at="$(python3 - \
  "$requested_expires_at" \
  "$foundation_parameters" \
  "$runtime_parameters" \
  "$network_parameters" \
  "$artifact_state" \
  "$workload_parameters" <<'PY'
import datetime as dt
import json
import sys


def expiration(path: str) -> dt.datetime:
    document = json.load(open(path, encoding="utf-8"))
    parameters = document.get("parameters") if isinstance(document, dict) else None
    if isinstance(parameters, dict) and "expiresAt" in parameters:
        value = parameters["expiresAt"]["value"]
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if isinstance(parameters, dict) and "expiresOn" in parameters:
        day = dt.date.fromisoformat(str(parameters["expiresOn"]["value"]))
        return dt.datetime.combine(day, dt.time(23, 59, 59), tzinfo=dt.UTC)
    value = document.get("expiresAt")
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


requested = dt.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
bounded = min(requested, *(expiration(path) for path in sys.argv[2:]))
print(bounded.astimezone(dt.UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"))
PY
)"

parameters="$(mktemp)"
base_parameters="$(mktemp)"
openshell_parameters="$(mktemp)"
compiled="$(mktemp)"
openshell_compiled="$(mktemp)"
lock_compiled="$(mktemp)"
egress_compiled="$(mktemp)"
subnet_compiled="$(mktemp)"
what_if="$(mktemp)"
lock_what_if="$(mktemp)"
bootstrap_what_if="$(mktemp)"
egress_what_if="$(mktemp)"
subnet_what_if="$(mktemp)"
stack_error="$(mktemp)"
workspace_started=false
openshell_started=false
lock_started=false
bootstrap_started=false
cleanup() {
  local status=$?
  set +e
  local cleanup_status=0
  if [[ "$status" -ne 0 && "$workspace_started" == true ]]; then
    if python3 "$phase_dir/capture_workspace_diagnostics.py" \
      --resource-group "$resource_group" \
      --vm-name "$(get_config vmName)" \
      --deployment-prefix "$openshell_stack" \
      --output "$state_dir/workspace-$workspace_environment.diagnostics.json"; then
      az deployment group create \
        --name "$project_name-saw-bootstrap-detach-$workspace_environment" \
        --resource-group "$resource_group" \
        --template-file "$subnet_template" \
        --parameters "${subnet_parameters[@]}" attachBootstrapEgress=false \
        --output none || cleanup_status=1
      if az stack group show \
        --resource-group "$resource_group" \
        --name "$bootstrap_stack" \
        --output none 2>/dev/null; then
        az stack group delete \
          --resource-group "$resource_group" \
          --name "$bootstrap_stack" \
          --action-on-unmanage deleteAll \
          --resources-without-delete-support fail \
          --yes \
          --output none || cleanup_status=1
      fi
      az vm deallocate \
        --resource-group "$resource_group" \
        --name "$(get_config vmName)" \
        --no-wait >/dev/null 2>&1 || cleanup_status=1
      echo "INFO failed workspace was diagnosed, bootstrap NAT removed, deallocated, and preserved."
      echo "INFO inspect owner-only diagnostics before running remove-workspace.sh."
    else
      cleanup_status=1
      echo "FAIL diagnostic capture failed; workspace and bootstrap egress were left untouched." >&2
    fi
  elif [[ "$workspace_started" == true || "$openshell_started" == true || "$lock_started" == true || "$bootstrap_started" == true ]]; then
    AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}" \
      LEARNINGNEMO_AZURE_DELETE=wp5-wp6-saw-openshell \
      bash "$phase_dir/remove-workspace.sh" --apply --config "$config_file" || cleanup_status=1
  fi
  rm -f \
    "$parameters" "$base_parameters" "$openshell_parameters" \
    "$compiled" "$openshell_compiled" "$lock_compiled" "$egress_compiled" "$subnet_compiled" \
    "$what_if" "$lock_what_if" "$bootstrap_what_if" \
    "$egress_what_if" "$subnet_what_if" "$stack_error"
  if [[ "$cleanup_status" -ne 0 ]]; then
    echo "FAIL workspace diagnostic capture, preservation, NAT removal, or cleanup failed" >&2
    exit 1
  fi
  exit "$status"
}
trap cleanup EXIT
chmod 600 "$parameters" "$base_parameters" "$openshell_parameters"
python3 "$phase_dir/workspace_parameters.py" materialize \
  "$config_file" \
  "$parameters" \
  --database-state "$database_state" \
  --ssh-public-key "$ssh_public_key" \
  --expires-at "$expires_at"
python3 "$phase_dir/workspace_parameters.py" project \
  "$parameters" "$base_parameters" --kind base
python3 "$phase_dir/workspace_parameters.py" project \
  "$parameters" "$openshell_parameters" --kind bootstrap
preflight_args=(
  --config "$config_file"
  --parameters "$parameters"
  --foundation-parameters "$foundation_parameters"
  --dependency "$runtime_parameters"
  --dependency "$network_parameters"
  --dependency "$artifact_state"
  --dependency "$workload_parameters"
)
if [[ "$mode" == "apply" ]]; then
  preflight_args+=(--require-budget)
fi
python3 "$phase_dir/preflight_workspace.py" "${preflight_args[@]}"
python3 "$phase_dir/verify_foundation.py" --parameters "$foundation_parameters"
az bicep build --file "$template" --stdout > "$compiled"
python3 "$phase_dir/validate-workspace.py" "$compiled"
az bicep build --file "$openshell_template" --stdout > "$openshell_compiled"
python3 "$phase_dir/validate-workspace-openshell-bootstrap.py" "$openshell_compiled"
az bicep build --file "$lock_template" --stdout > "$lock_compiled"
python3 "$phase_dir/validate-workspace-runtime-lock.py" "$lock_compiled"
az bicep build --file "$egress_template" --stdout > "$egress_compiled"
python3 "$phase_dir/validate-workspace-bootstrap-egress.py" "$egress_compiled"
az bicep build --file "$subnet_template" --stdout > "$subnet_compiled"
python3 "$phase_dir/validate-workspace-subnet-egress.py" "$subnet_compiled"

egress_parameters=(
  location="$location"
  environment="$workspace_environment"
  projectName="$project_name"
  expiresAt="$expires_at"
  ownerTag="$owner_tag"
  additionalTags="$additional_tags"
)
subnet_parameters=(
  environment="$workspace_environment"
  projectName="$project_name"
  workspaceSubnetPrefix="$workspace_subnet_prefix"
)
az deployment group validate \
  --name "$bootstrap_stack-preview" \
  --resource-group "$resource_group" \
  --template-file "$egress_template" \
  --parameters "${egress_parameters[@]}" \
  --output none
echo "Running temporary SAW bootstrap-egress what-if. No resources will be changed."
az deployment group what-if \
  --name "$bootstrap_stack-preview" \
  --resource-group "$resource_group" \
  --template-file "$egress_template" \
  --parameters "${egress_parameters[@]}" \
  --result-format FullResourcePayloads \
  --no-pretty-print \
  --output json > "$egress_what_if"
python3 "$phase_dir/summarize_database_what_if.py" "$egress_what_if"
python3 "$phase_dir/validate_workspace_egress_what_if.py" "$egress_what_if" --mode resources
az deployment group validate \
  --name "$project_name-saw-bootstrap-attach-preview-$workspace_environment" \
  --resource-group "$resource_group" \
  --template-file "$subnet_template" \
  --parameters "${subnet_parameters[@]}" attachBootstrapEgress=true \
  --output none
echo "Running temporary SAW subnet-egress association what-if. No resources will be changed."
az deployment group what-if \
  --name "$project_name-saw-bootstrap-attach-preview-$workspace_environment" \
  --resource-group "$resource_group" \
  --template-file "$subnet_template" \
  --parameters "${subnet_parameters[@]}" attachBootstrapEgress=true \
  --result-format FullResourcePayloads \
  --no-pretty-print \
  --output json > "$subnet_what_if"
python3 "$phase_dir/summarize_database_what_if.py" "$subnet_what_if"
python3 "$phase_dir/validate_workspace_egress_what_if.py" "$subnet_what_if" --mode association

az deployment group validate \
  --name "$workspace_stack-preview" \
  --resource-group "$resource_group" \
  --template-file "$template" \
  --parameters "@$base_parameters" \
  --output none
echo "Running SAW workspace what-if. No resources will be changed."
az deployment group what-if \
  --name "$workspace_stack-preview" \
  --resource-group "$resource_group" \
  --template-file "$template" \
  --parameters "@$base_parameters" \
  --result-format FullResourcePayloads \
  --no-pretty-print \
  --output json > "$what_if"
python3 "$phase_dir/summarize_database_what_if.py" "$what_if"
python3 "$phase_dir/validate_workspace_what_if.py" "$what_if"
az deployment group validate \
  --name "$lock_stack-preview" \
  --resource-group "$resource_group" \
  --template-file "$lock_template" \
  --parameters environment="$workspace_environment" projectName="$project_name" \
  --output none
echo "Running SAW runtime-lock what-if. No resources will be changed."
az deployment group what-if \
  --name "$lock_stack-preview" \
  --resource-group "$resource_group" \
  --template-file "$lock_template" \
  --parameters environment="$workspace_environment" projectName="$project_name" \
  --result-format FullResourcePayloads \
  --no-pretty-print \
  --output json > "$lock_what_if"
python3 "$phase_dir/summarize_database_what_if.py" "$lock_what_if"
python3 "$phase_dir/validate_workspace_lock_what_if.py" "$lock_what_if"
if [[ "$mode" == "what-if" ]]; then
  echo "What-if complete. Re-run with --apply and the explicit acknowledgement to deploy the SAW."
  exit 0
fi
if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" || "$(az account show --query id --output tsv)" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Apply blocked: expected subscription is absent or differs." >&2
  exit 1
fi
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "wp5-wp6-saw-openshell" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=wp5-wp6-saw-openshell for this command only." >&2
  exit 2
fi

state_parameters="$state_dir/workspace-$workspace_environment.parameters.json"
result_file="$state_dir/workspace-$workspace_environment.result.json"
install -m 600 "$parameters" "$state_parameters"
bootstrap_started=true
if ! az stack group create \
  --name "$bootstrap_stack" \
  --resource-group "$resource_group" \
  --template-file "$egress_template" \
  --parameters "${egress_parameters[@]}" \
  --action-on-unmanage deleteAll \
  --resources-without-delete-support fail \
  --deny-settings-mode none \
  --description "Temporary outbound NAT for SAW and OpenShell bootstrap only" \
  --yes \
  --output none > /dev/null 2> "$stack_error"; then
  echo "FAIL temporary workspace bootstrap egress deployment failed" >&2
  exit 1
fi
az deployment group create \
  --name "$project_name-saw-bootstrap-attach-$workspace_environment" \
  --resource-group "$resource_group" \
  --template-file "$subnet_template" \
  --parameters "${subnet_parameters[@]}" attachBootstrapEgress=true \
  --output none
workspace_started=true
if ! az stack group create \
  --name "$workspace_stack" \
  --resource-group "$resource_group" \
  --template-file "$template" \
  --parameters "@$base_parameters" \
  --action-on-unmanage deleteAll \
  --resources-without-delete-support fail \
  --deny-settings-mode none \
  --description "Expiring LearningNeMo Secure Agent Workspace and OpenShell MicroVMs" \
  --yes \
  --output none > /dev/null 2> "$stack_error"; then
  echo "FAIL workspace stack deployment or OpenShell bootstrap failed" >&2
  python3 "$phase_dir/summarize_workspace_failure.py" \
    --resource-group "$resource_group" \
    --deployment-prefix "$workspace_stack" \
    --output "$state_dir/workspace-$workspace_environment.failure.json" || true
  exit 1
fi
package_url="$(get_config openShellPackageUrl)"
if ! az vm run-command invoke \
  --resource-group "$resource_group" \
  --name "$(get_config vmName)" \
  --command-id RunShellScript \
  --scripts \
    "set -euo pipefail" \
    "for command in curl sha256sum dpkg dpkg-query python3 systemctl loginctl runuser getent; do command -v \"\$command\" >/dev/null; done" \
    "curl -fLsSI --retry 1 --retry-all-errors --connect-timeout 10 --max-time 60 '$package_url' >/dev/null" \
  --output none; then
  echo "FAIL private SAW host tooling or temporary bootstrap egress probe failed" >&2
  exit 1
fi
echo "PASS private SAW host tooling and temporary bootstrap egress are ready"
az deployment group what-if \
  --name "$openshell_stack-preview" \
  --resource-group "$resource_group" \
  --template-file "$openshell_template" \
  --parameters "@$openshell_parameters" \
  --result-format FullResourcePayloads \
  --no-pretty-print \
  --output json > "$bootstrap_what_if"
python3 "$phase_dir/summarize_database_what_if.py" "$bootstrap_what_if"
python3 "$phase_dir/validate_workspace_bootstrap_what_if.py" "$bootstrap_what_if"
openshell_started=true
if ! az stack group create \
  --name "$openshell_stack" \
  --resource-group "$resource_group" \
  --template-file "$openshell_template" \
  --parameters "@$openshell_parameters" \
  --action-on-unmanage deleteAll \
  --resources-without-delete-support fail \
  --deny-settings-mode none \
  --description "Expiring LearningNeMo Secure Agent Workspace and OpenShell MicroVMs" \
  --yes \
  --output none > /dev/null 2> "$stack_error"; then
  echo "FAIL workspace OpenShell bootstrap failed" >&2
  python3 "$phase_dir/summarize_workspace_failure.py" \
    --resource-group "$resource_group" \
    --deployment-prefix "$openshell_stack" \
    --output "$state_dir/workspace-$workspace_environment.failure.json" || true
  exit 1
fi
az deployment group create \
  --name "$project_name-saw-bootstrap-detach-$workspace_environment" \
  --resource-group "$resource_group" \
  --template-file "$subnet_template" \
  --parameters "${subnet_parameters[@]}" attachBootstrapEgress=false \
  --output none
az stack group delete \
  --resource-group "$resource_group" \
  --name "$bootstrap_stack" \
  --action-on-unmanage deleteAll \
  --resources-without-delete-support fail \
  --yes \
  --output none
bootstrap_started=false
lock_started=true
if ! az stack group create \
  --name "$lock_stack" \
  --resource-group "$resource_group" \
  --template-file "$lock_template" \
  --parameters environment="$workspace_environment" projectName="$project_name" \
  --action-on-unmanage deleteAll \
  --resources-without-delete-support fail \
  --deny-settings-mode none \
  --description "Post-bootstrap SAW runtime egress lock" \
  --yes \
  --output none > /dev/null 2> "$stack_error"; then
  echo "FAIL workspace runtime-lock deployment failed" >&2
  exit 1
fi
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  python3 "$phase_dir/verify_workspace.py" \
    --parameters "$state_parameters" \
    --workspace-stack-name "$workspace_stack" \
    --bootstrap-stack-name "$openshell_stack" \
    --lock-stack-name "$lock_stack" \
    --workspace-template "$compiled" \
    --bootstrap-template "$openshell_compiled" \
    --lock-template "$lock_compiled" \
    --output "$result_file"
workspace_started=false
openshell_started=false
lock_started=false
echo "PASS SAW OpenShell runtime portfolio POC is deployed and expires no later than $expires_at."
echo "The owner-only workspace evidence is stored in $result_file."