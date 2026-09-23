#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
config_file="${BUDGET_CONFIG_FILE:-$phase_dir/environments/dev.budget.config.json}"

python3 "$phase_dir/check_toolchain.py"
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

python3 "$phase_dir/budget_parameters.py" validate "$config_file"
budget_name="$(python3 "$phase_dir/budget_parameters.py" get "$config_file" budgetName)"
az account show --output none
active_subscription="$(az account show --query id --output tsv)"
budget_url="https://management.azure.com/subscriptions/$active_subscription/providers/Microsoft.Consumption/budgets/$budget_name?api-version=2024-08-01"

if ! az rest --method GET --url "$budget_url" --output none 2>/dev/null; then
  echo "Subscription budget is absent; no deletion is needed."
  exit 0
fi
python3 "$phase_dir/verify_budget.py" --config "$config_file"

if [[ "$apply" != true ]]; then
  echo "Dry run only. No resources were changed."
  echo "Use --apply with LEARNINGNEMO_AZURE_DELETE=cost-budget to delete this budget."
  exit 0
fi

if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Delete blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription before any mutation." >&2
  exit 2
fi
if [[ "$active_subscription" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Delete blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi
if [[ "${LEARNINGNEMO_AZURE_DELETE:-}" != "cost-budget" ]]; then
  echo "Delete blocked. Set LEARNINGNEMO_AZURE_DELETE=cost-budget for this command only." >&2
  exit 2
fi

echo "Deleting the verified LearningNeMo subscription budget."
az rest --method DELETE --url "$budget_url" --output none
if az rest --method GET --url "$budget_url" --output none 2>/dev/null; then
  echo "Delete incomplete: the subscription budget still exists." >&2
  exit 1
fi
echo "Subscription budget deleted. No application or infrastructure resource was changed."