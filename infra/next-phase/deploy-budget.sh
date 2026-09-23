#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

mode="what-if"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
template="$phase_dir/budget.bicep"
config_file="${BUDGET_CONFIG_FILE:-$phase_dir/environments/dev.budget.config.json}"

python3 "$phase_dir/check_toolchain.py"
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
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

python3 "$phase_dir/budget_parameters.py" validate "$config_file"
get_config() {
  python3 "$phase_dir/budget_parameters.py" get "$config_file" "$1"
}
location="$(get_config location)"
environment="$(get_config environment)"
budget_name="$(get_config budgetName)"

az account show --output none
active_subscription="$(az account show --query id --output tsv)"
if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" && "$active_subscription" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Budget deployment blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi

runtime_dir="$(mktemp -d)"
compiled_template="$runtime_dir/budget.json"
resolved_parameters="$runtime_dir/budget.parameters.json"
contact_file="$runtime_dir/contact.txt"
existing_budgets="$runtime_dir/existing-budgets.json"
what_if_result="$runtime_dir/what-if.json"
deployment_result="$runtime_dir/deployment.json"
cleanup() {
  unset notification_email
  rm -rf "$runtime_dir"
}
trap cleanup EXIT
chmod 700 "$runtime_dir"

notification_email="${LEARNINGNEMO_BUDGET_CONTACT_EMAIL:-}"
if [[ -z "$notification_email" ]]; then
  signed_in_user="$runtime_dir/signed-in-user.json"
  if ! az ad signed-in-user show --output json > "$signed_in_user"; then
    echo "Budget deployment blocked. Set LEARNINGNEMO_BUDGET_CONTACT_EMAIL to a runtime recipient." >&2
    exit 2
  fi
  notification_email="$(python3 - "$signed_in_user" <<'PY'
import json
import sys

user = json.load(open(sys.argv[1], encoding="utf-8-sig"))
print(user.get("mail") or user.get("userPrincipalName") or "")
PY
)"
fi
printf '%s\n' "$notification_email" > "$contact_file"
chmod 600 "$contact_file"

az rest \
  --method GET \
  --url "https://management.azure.com/subscriptions/$active_subscription/providers/Microsoft.Consumption/budgets?api-version=2024-08-01" \
  --output json > "$existing_budgets"
start_date="$(python3 - "$existing_budgets" "$budget_name" <<'PY'
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8-sig"))
name = sys.argv[2]
match = next((item for item in document.get("value", []) if item.get("name") == name), None)
print(((match or {}).get("properties") or {}).get("timePeriod", {}).get("startDate", ""))
PY
)"
if [[ -z "$start_date" ]]; then
  start_date="$(date -u +%Y-%m-01T00:00:00Z)"
fi

python3 "$phase_dir/budget_parameters.py" materialize \
  "$config_file" \
  "$resolved_parameters" \
  --start-date "$start_date" \
  --notification-email-file "$contact_file"
az bicep build --file "$template" --stdout > "$compiled_template"
python3 "$phase_dir/validate-budget.py" "$compiled_template"

deployment_name="learningnemo-cost-budget-$environment"
az deployment sub validate \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$resolved_parameters" \
  --output none

if [[ "$mode" == "what-if" ]]; then
  echo "Running subscription budget what-if. No resources will be changed."
  az deployment sub what-if \
    --name "$deployment_name" \
    --location "$location" \
    --template-file "$template" \
    --parameters "@$resolved_parameters" \
    --result-format ResourceIdOnly \
    --no-pretty-print \
    --output json > "$what_if_result"
  python3 "$phase_dir/summarize_what_if.py" "$what_if_result"
  echo "Budget what-if complete. Re-run with --apply and the explicit acknowledgement to deploy."
  exit 0
fi

if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Apply blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription before any mutation." >&2
  exit 2
fi
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "cost-budget" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=cost-budget for this command only." >&2
  exit 2
fi

echo "Deploying one monthly subscription budget with 50%, 80%, and forecasted 100% notifications."
az deployment sub create \
  --name "$deployment_name" \
  --location "$location" \
  --template-file "$template" \
  --parameters "@$resolved_parameters" \
  --output json > "$deployment_result"

state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
manifest="$state_dir/budget-$environment.manifest.json"
python3 "$phase_dir/record_budget.py" \
  --deployment-result "$deployment_result" \
  --template "$compiled_template" \
  --config "$config_file" \
  --resolved-parameters "$resolved_parameters" \
  --output "$manifest"
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
  python3 "$phase_dir/verify_budget.py" --config "$config_file"

echo "Subscription budget deployed and verified. The runtime notification recipient was not stored locally."