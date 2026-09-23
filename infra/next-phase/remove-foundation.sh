#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
expired_only=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
parameter_file="${FOUNDATION_PARAMETERS_FILE:-$phase_dir/environments/dev.parameters.json}"

python3 "$phase_dir/check_toolchain.py"
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
    --expired-only)
      expired_only=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

python3 "$phase_dir/foundation_parameters.py" validate "$parameter_file"
platform_resource_group="$(python3 "$phase_dir/foundation_parameters.py" get "$parameter_file" platformResourceGroupName)"
saw_resource_group="$(python3 "$phase_dir/foundation_parameters.py" get "$parameter_file" sawResourceGroupName)"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"

az account show --output none
cleanup_check_args=(--parameters "$parameter_file")
if [[ "$expired_only" != true ]]; then
  cleanup_check_args+=(--include-platform)
fi
python3 "$phase_dir/check_foundation_cleanup.py" "${cleanup_check_args[@]}"

if [[ "$expired_only" == true ]] && az group show --name "$saw_resource_group" --output none 2>/dev/null; then
  expires_on="$(az group show --name "$saw_resource_group" --query 'tags.expiresOn' --output tsv)"
  if [[ -z "$expires_on" || "$expires_on" == "null" ]]; then
    echo "Delete blocked: $saw_resource_group has no expiresOn tag." >&2
    exit 1
  fi
  if ! python3 - "$expires_on" <<'PY'
import datetime as dt
import sys

try:
    dt.date.fromisoformat(sys.argv[1])
except ValueError:
    raise SystemExit(1)
PY
  then
    echo "Delete blocked: $saw_resource_group has an invalid expiresOn tag." >&2
    exit 1
  fi
  if [[ "$expires_on" > "$(date -u +%F)" ]]; then
    echo "$saw_resource_group is not expired; no deletion is eligible."
    exit 0
  fi
  echo "$saw_resource_group is expired and eligible for guarded deletion."
fi

if [[ "$apply" != true ]]; then
  echo "Dry run only. No resources were changed."
  echo "Use --apply with LEARNINGNEMO_AZURE_DELETE=network-foundation to delete these foundation-only groups."
  exit 0
fi

if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Delete blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription before any mutation." >&2
  exit 2
fi
active_subscription="$(az account show --query id --output tsv)"
if [[ "$active_subscription" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Delete blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi
unset active_subscription
if [[ "${LEARNINGNEMO_AZURE_DELETE:-}" != "network-foundation" ]]; then
  echo "Delete blocked. Set LEARNINGNEMO_AZURE_DELETE=network-foundation for this command only." >&2
  exit 2
fi

resource_groups=("$saw_resource_group")
if [[ "$expired_only" != true ]]; then
  resource_groups+=("$platform_resource_group")
fi
snapshot_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
for resource_group in "${resource_groups[@]}"; do
  if az group show --name "$resource_group" --output none 2>/dev/null; then
    snapshot_path="$state_dir/pre-delete-$resource_group-$snapshot_timestamp.json"
    python3 "$phase_dir/snapshot_foundation.py" \
      --resource-group "$resource_group" \
      --output "$snapshot_path"
    echo "Deleting foundation-only resource group: $resource_group"
    az group delete --name "$resource_group" --yes
  fi
done

echo "Network foundation resource groups deleted."