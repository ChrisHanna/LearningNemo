#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
config_file="${MIGRATION_CONFIG_FILE:-$phase_dir/environments/dev.migration.config.json}"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
parameters_override=""
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
    --parameters)
      parameters_override="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done
python3 "$phase_dir/migration_parameters.py" validate "$config_file"
get_config() {
  python3 "$phase_dir/migration_parameters.py" get-config "$config_file" "$1"
}
environment="$(get_config environment)"
resource_group="$(get_config migrationResourceGroupName)"
parameters="${parameters_override:-$state_dir/migration-$environment.parameters.json}"
if [[ "$(az group exists --name "$resource_group" --output tsv)" != "true" ]]; then
  rm -f "$parameters" "$state_dir/migration-$environment.execution.json"
  echo "Migration job is absent; no deletion is needed."
  exit 0
fi
if [[ ! -f "$parameters" ]]; then
  echo "Delete blocked: owner-only migration parameters are absent." >&2
  exit 1
fi
python3 "$phase_dir/check_migration_cleanup.py" --parameters "$parameters"
if [[ "$apply" != true ]]; then
  echo "Dry run only. No resources were changed."
  echo "Use --apply with LEARNINGNEMO_AZURE_DELETE=wp3-database-migration."
  exit 0
fi
if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" || "$(az account show --query id --output tsv)" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Delete blocked: expected subscription is absent or differs." >&2
  exit 1
fi
if [[ "${LEARNINGNEMO_AZURE_DELETE:-}" != "wp3-database-migration" ]]; then
  echo "Delete blocked. Set LEARNINGNEMO_AZURE_DELETE=wp3-database-migration for this command only." >&2
  exit 2
fi
snapshot_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$phase_dir/snapshot_foundation.py" \
  --resource-group "$resource_group" \
  --output "$state_dir/pre-delete-migration-$environment-$snapshot_timestamp.json"
az group delete --name "$resource_group" --yes
if [[ "$(az group exists --name "$resource_group" --output tsv)" == "true" ]]; then
  echo "Delete incomplete: migration resource group remains." >&2
  exit 1
fi
rm -f "$parameters" "$state_dir/migration-$environment.execution.json"
echo "One-shot migration compute removed; its sanitized receipt was preserved."