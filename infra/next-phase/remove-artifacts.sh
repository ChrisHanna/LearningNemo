#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
expired_only=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
config_file="${ARTIFACT_CONFIG_FILE:-$phase_dir/environments/dev.artifacts.config.json}"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      apply=true
      shift
      ;;
    --expired-only)
      expired_only=true
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

python3 "$phase_dir/check_toolchain.py"
python3 "$phase_dir/artifact_parameters.py" validate "$config_file"
get_config() {
  python3 "$phase_dir/artifact_parameters.py" get-config "$config_file" "$1"
}
environment="$(get_config environment)"
resource_group="$(get_config artifactResourceGroupName)"
state_parameters="$state_dir/artifacts-$environment.parameters.json"
private_state="$state_dir/artifacts-$environment.state.json"
cleanup_args=(--config "$config_file")
if [[ -f "$state_parameters" && -f "$private_state" ]]; then
  cleanup_args+=(--parameters "$state_parameters" --private-state "$private_state")
fi
python3 "$phase_dir/check_artifact_cleanup.py" "${cleanup_args[@]}"
if [[ "$(az group exists --name "$resource_group" --output tsv)" != "true" ]]; then
  rm -f "$state_parameters" "$private_state"
  echo "Artifact registry is absent; no deletion is needed."
  exit 0
fi
if [[ "$expired_only" == true ]]; then
  expires_at="$(python3 "$phase_dir/artifact_parameters.py" get-config "$config_file" environment >/dev/null; python3 - "$state_parameters" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["parameters"]["expiresAt"]["value"])
PY
)"
  if [[ "$(python3 "$phase_dir/runtime_parameters.py" status --expires-at "$expires_at")" == "active" ]]; then
    echo "Artifact registry is not expired; no deletion is eligible."
    exit 0
  fi
fi
if [[ "$apply" != true ]]; then
  echo "Dry run only. No resources were changed."
  echo "Use --apply with LEARNINGNEMO_AZURE_DELETE=wp3-artifacts after workloads are removed."
  exit 0
fi
if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Delete blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription." >&2
  exit 2
fi
if [[ "$(az account show --query id --output tsv)" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Delete blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi
if [[ "${LEARNINGNEMO_AZURE_DELETE:-}" != "wp3-artifacts" ]]; then
  echo "Delete blocked. Set LEARNINGNEMO_AZURE_DELETE=wp3-artifacts for this command only." >&2
  exit 2
fi
snapshot_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$phase_dir/snapshot_foundation.py" \
  --resource-group "$resource_group" \
  --output "$state_dir/pre-delete-artifacts-$environment-$snapshot_timestamp.json"
az group delete --name "$resource_group" --yes
if [[ "$(az group exists --name "$resource_group" --output tsv)" == "true" ]]; then
  echo "Delete incomplete: artifact resource group remains." >&2
  exit 1
fi
rm -f "$state_parameters" "$private_state"
echo "Artifact registry removed after exact inventory and dependency verification."