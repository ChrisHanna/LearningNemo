#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
parameter_file="${DATABASE_PARAMETERS_FILE:-$phase_dir/environments/dev.database.parameters.json}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      apply=true
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

python3 "$phase_dir/check_toolchain.py"
python3 "$phase_dir/database_parameters.py" "$parameter_file"
resource_group="$(python3 - "$parameter_file" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["parameters"]["databaseResourceGroupName"]["value"])
PY
)"
python3 "$phase_dir/check_database_cleanup.py" --parameters "$parameter_file"
if [[ "$(az group exists --name "$resource_group" --output tsv)" != "true" ]]; then
  echo "WP3 database group is absent; no deletion is needed."
  exit 0
fi
if [[ "$apply" != true ]]; then
  echo "Dry run only. No resources were changed."
  echo "Use --apply with LEARNINGNEMO_AZURE_DELETE=wp3-database to remove the exact database group."
  exit 0
fi
if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Delete blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription." >&2
  exit 2
fi
active_subscription="$(az account show --query id --output tsv)"
if [[ "$active_subscription" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Delete blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi
if [[ "${LEARNINGNEMO_AZURE_DELETE:-}" != "wp3-database" ]]; then
  echo "Delete blocked. Set LEARNINGNEMO_AZURE_DELETE=wp3-database for this command only." >&2
  exit 2
fi

state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
snapshot_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$phase_dir/snapshot_foundation.py" \
  --resource-group "$resource_group" \
  --output "$state_dir/pre-delete-database-$snapshot_timestamp.json"
az group delete --name "$resource_group" --yes
if [[ "$(az group exists --name "$resource_group" --output tsv)" == "true" ]]; then
  echo "Delete incomplete: WP3 database group remains." >&2
  exit 1
fi
echo "WP3 database group removed after exact inventory and ownership verification."