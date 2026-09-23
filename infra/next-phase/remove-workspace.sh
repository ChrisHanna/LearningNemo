#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
config_file="${WORKSPACE_CONFIG_FILE:-$phase_dir/environments/dev.workspace.config.json}"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
python3 "$phase_dir/retention.py" check
foundation_parameters="${FOUNDATION_PARAMETERS_FILE:-$state_dir/foundation-dev.parameters.json}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      apply=true
      shift
      ;;
    --config)
      config_file="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

python3 "$phase_dir/workspace_parameters.py" validate "$config_file"
get_config() {
  python3 "$phase_dir/workspace_parameters.py" get-config "$config_file" "$1"
}
environment="$(get_config environment)"
project_name="$(get_config projectName)"
resource_group="$(get_config sawResourceGroupName)"
vm_name="$(get_config vmName)"
workspace_stack="$project_name-saw-workspace-$environment"
openshell_stack="$project_name-openshell-bootstrap-$environment"
lock_stack="$project_name-saw-runtime-lock-$environment"
bootstrap_stack="$project_name-saw-bootstrap-egress-$environment"
subnet_prefix="$(python3 "$phase_dir/foundation_parameters.py" get "$foundation_parameters" sawWorkspaceSubnetPrefix)"
runtime_identity="id-$project_name-saw-runtime-$environment"
nic_name="nic-$vm_name"
os_disk_name="osdisk-$vm_name"
bootstrap_nat="nat-$project_name-saw-bootstrap-$environment"
bootstrap_public_ip="pip-$project_name-saw-bootstrap-$environment"
workspace_nsg="nsg-vnet-$project_name-saw-$environment-workspace"
runtime_rules=(
  deny-sandbox-host-imds
  allow-azure-platform-dns
  allow-approved-azure-https
  deny-general-internet-runtime
)

stack_exists() {
  az stack group show --resource-group "$resource_group" --name "$1" --output none 2>/dev/null
}
workspace_resources_exist() {
  az vm show --resource-group "$resource_group" --name "$vm_name" --output none 2>/dev/null \
    || az network nic show --resource-group "$resource_group" --name "$nic_name" --output none 2>/dev/null \
    || az disk show --resource-group "$resource_group" --name "$os_disk_name" --output none 2>/dev/null \
    || az identity show --resource-group "$resource_group" --name "$runtime_identity" --output none 2>/dev/null \
    || az network nat gateway show --resource-group "$resource_group" --name "$bootstrap_nat" --output none 2>/dev/null \
    || az network public-ip show --resource-group "$resource_group" --name "$bootstrap_public_ip" --output none 2>/dev/null
}
runtime_rules_exist() {
  local rule
  for rule in "${runtime_rules[@]}"; do
    if az network nsg rule show \
      --resource-group "$resource_group" \
      --nsg-name "$workspace_nsg" \
      --name "$rule" \
      --output none 2>/dev/null; then
      return 0
    fi
  done
  return 1
}
if ! stack_exists "$workspace_stack" \
  && ! stack_exists "$openshell_stack" \
  && ! stack_exists "$lock_stack" \
  && ! stack_exists "$bootstrap_stack" \
  && ! workspace_resources_exist \
  && ! runtime_rules_exist; then
  python3 "$phase_dir/verify_foundation.py" --parameters "$foundation_parameters"
  rm -f \
    "$state_dir/workspace-$environment.parameters.json" \
    "$state_dir/workspace-$environment.ssh" \
    "$state_dir/workspace-$environment.ssh.pub"
  echo "Workspace stacks and deterministic resources are absent; no deletion is needed."
  exit 0
fi
if [[ "$apply" != true ]]; then
  echo "Dry run only. No resources were changed."
  echo "Use --apply with LEARNINGNEMO_AZURE_DELETE=wp5-wp6-saw-openshell."
  exit 0
fi
if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" || "$(az account show --query id --output tsv)" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Delete blocked: expected subscription is absent or differs." >&2
  exit 1
fi
if [[ "${LEARNINGNEMO_AZURE_DELETE:-}" != "wp5-wp6-saw-openshell" ]]; then
  echo "Delete blocked. Set LEARNINGNEMO_AZURE_DELETE=wp5-wp6-saw-openshell for this command only." >&2
  exit 2
fi

snapshot_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$phase_dir/snapshot_foundation.py" \
  --resource-group "$resource_group" \
  --output "$state_dir/pre-delete-workspace-$environment-$snapshot_timestamp.json"
if az vm show --resource-group "$resource_group" --name "$vm_name" --output none 2>/dev/null; then
  az vm deallocate --resource-group "$resource_group" --name "$vm_name" --no-wait
fi
az deployment group create \
  --name "$project_name-saw-bootstrap-detach-$environment" \
  --resource-group "$resource_group" \
  --template-file "$phase_dir/workspace-subnet-egress.bicep" \
  --parameters \
    environment="$environment" \
    projectName="$project_name" \
    workspaceSubnetPrefix="$subnet_prefix" \
    attachBootstrapEgress=false \
  --output none
if stack_exists "$bootstrap_stack"; then
  az stack group delete \
    --resource-group "$resource_group" \
    --name "$bootstrap_stack" \
    --action-on-unmanage deleteAll \
    --resources-without-delete-support fail \
    --yes \
    --output none
fi
if stack_exists "$openshell_stack"; then
  az stack group delete \
    --resource-group "$resource_group" \
    --name "$openshell_stack" \
    --action-on-unmanage deleteAll \
    --resources-without-delete-support fail \
    --yes \
    --output none
fi
if stack_exists "$workspace_stack"; then
  az stack group delete \
    --resource-group "$resource_group" \
    --name "$workspace_stack" \
    --action-on-unmanage deleteAll \
    --resources-without-delete-support fail \
    --yes \
    --output none
fi
if stack_exists "$lock_stack"; then
  az stack group delete \
    --resource-group "$resource_group" \
    --name "$lock_stack" \
    --action-on-unmanage deleteAll \
    --resources-without-delete-support fail \
    --yes \
    --output none
fi
if az vm show --resource-group "$resource_group" --name "$vm_name" --output none 2>/dev/null; then
  az vm delete --resource-group "$resource_group" --name "$vm_name" --yes --output none
fi
if az network nic show --resource-group "$resource_group" --name "$nic_name" --output none 2>/dev/null; then
  az network nic delete --resource-group "$resource_group" --name "$nic_name" --output none
fi
if az disk show --resource-group "$resource_group" --name "$os_disk_name" --output none 2>/dev/null; then
  az disk delete --resource-group "$resource_group" --name "$os_disk_name" --yes --output none
fi
if az identity show --resource-group "$resource_group" --name "$runtime_identity" --output none 2>/dev/null; then
  az identity delete --resource-group "$resource_group" --name "$runtime_identity" --output none
fi
if az network nat gateway show --resource-group "$resource_group" --name "$bootstrap_nat" --output none 2>/dev/null; then
  az network nat gateway delete --resource-group "$resource_group" --name "$bootstrap_nat" --output none
fi
if az network public-ip show --resource-group "$resource_group" --name "$bootstrap_public_ip" --output none 2>/dev/null; then
  az network public-ip delete --resource-group "$resource_group" --name "$bootstrap_public_ip" --output none
fi
for rule in "${runtime_rules[@]}"; do
  if az network nsg rule show \
    --resource-group "$resource_group" \
    --nsg-name "$workspace_nsg" \
    --name "$rule" \
    --output none 2>/dev/null; then
    az network nsg rule delete \
      --resource-group "$resource_group" \
      --nsg-name "$workspace_nsg" \
      --name "$rule" \
      --output none
  fi
done
if stack_exists "$workspace_stack" || stack_exists "$openshell_stack" || stack_exists "$lock_stack" || stack_exists "$bootstrap_stack"; then
  echo "Delete incomplete: a workspace deployment stack remains." >&2
  exit 1
fi
if workspace_resources_exist; then
  echo "Delete incomplete: a deterministic workspace resource remains." >&2
  exit 1
fi
if runtime_rules_exist; then
  echo "Delete incomplete: a runtime-lock rule remains." >&2
  exit 1
fi
python3 "$phase_dir/verify_foundation.py" --parameters "$foundation_parameters"
rm -f \
  "$state_dir/workspace-$environment.parameters.json" \
  "$state_dir/workspace-$environment.ssh" \
  "$state_dir/workspace-$environment.ssh.pub"
echo "SAW VM, disk, NIC, no-RBAC identity, runtime lock, and OpenShell state were removed; the network foundation remains."