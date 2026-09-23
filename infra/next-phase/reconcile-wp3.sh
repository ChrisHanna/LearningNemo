#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
environment="${LEARNINGNEMO_ENVIRONMENT:-dev}"
ttl_hours="${WP3_TTL_HOURS:-8}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      apply=true
      shift
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
if ! [[ "$ttl_hours" =~ ^([2-9]|1[0-9]|2[0-4])$ ]]; then
  echo "WP3_TTL_HOURS must be an integer from 2 through 24." >&2
  exit 2
fi

bash "$project_dir/infra/test-all-local.sh"
if [[ "$apply" != true ]]; then
  bash "$phase_dir/deploy-database.sh" --what-if
  if [[ -f "$state_dir/database-$environment.state.json" ]]; then
    bash "$phase_dir/deploy-database-network.sh" --what-if --ttl-hours "$ttl_hours"
    bash "$phase_dir/deploy-artifacts.sh" --what-if --ttl-hours "$ttl_hours"
  else
    echo "INFO network and artifact previews require owner-only database state from the base apply."
  fi
  bash "$phase_dir/deploy-platform.sh" --what-if --ttl-hours "$ttl_hours"
  if [[ -f "$state_dir/trusted-runtime-$environment.release.json" ]]; then
    if [[ -f "$state_dir/workloads-$environment.auth.json" ]]; then
      bash "$phase_dir/deploy-workloads.sh" --what-if --ttl-hours 1
    else
      echo "INFO worker preview requires owner-only Entra audience state."
    fi
    if [[ -f "$state_dir/runtime-$environment.parameters.json" ]]; then
      bash "$phase_dir/deploy-migration.sh" --what-if --ttl-hours 1
    else
      echo "INFO migration preview requires owner-only runtime state."
    fi
  else
    echo "INFO migration and worker previews require a verified trusted image release."
  fi
  echo "PASS reproducible WP3 local gates and available what-if stages complete; no resources changed."
  exit 0
fi

if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Apply blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription." >&2
  exit 2
fi
if [[ "$(az account show --query id --output tsv)" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Apply blocked: active subscription differs." >&2
  exit 1
fi
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "wp3-reproducible" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=wp3-reproducible for this command only." >&2
  exit 2
fi

LEARNINGNEMO_AZURE_APPLY=wp3-database \
  bash "$phase_dir/deploy-database.sh" --apply
LEARNINGNEMO_AZURE_APPLY=wp3-database-network \
  bash "$phase_dir/deploy-database-network.sh" --apply --ttl-hours "$ttl_hours"
LEARNINGNEMO_AZURE_APPLY=wp3-artifacts \
  bash "$phase_dir/deploy-artifacts.sh" --apply --ttl-hours "$ttl_hours"
LEARNINGNEMO_AZURE_APPLY=platform-runtime-envelope \
  bash "$phase_dir/deploy-platform.sh" --apply --ttl-hours "$ttl_hours"
bash "$project_dir/scripts/build-trusted-image.sh"
LEARNINGNEMO_AZURE_APPLY=trusted-workload-audiences \
  bash "$phase_dir/deploy-entra-workloads.sh" --apply
LEARNINGNEMO_AZURE_APPLY=wp3-database-migration \
  bash "$phase_dir/deploy-migration.sh" --apply --ttl-hours 1
LEARNINGNEMO_AZURE_APPLY=trusted-workloads \
  bash "$phase_dir/deploy-workloads.sh" --apply --ttl-hours 1
bash "$phase_dir/verify-wp3.sh"
echo "PASS reproducible WP3 reconciliation and live verification complete."