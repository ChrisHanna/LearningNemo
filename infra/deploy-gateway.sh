#!/usr/bin/env bash
set -euo pipefail

mode="what-if"
location="${AZURE_LOCATION:-eastus}"
resource_group="${AZURE_RESOURCE_GROUP:-rg-nemo-agent-dev}"
key_vault="${AZURE_KEY_VAULT_NAME:-kvnemo8370187d}"
api_management="${AZURE_APIM_NAME:-apim-nemo-8370187d}"
publisher_name="${AZURE_APIM_PUBLISHER_NAME:-NeMo Agent Team}"
publisher_email="${AZURE_APIM_PUBLISHER_EMAIL:-noreply@example.com}"
openai_key_file="${OPENAI_PROVIDER_API_KEY_FILE:-}"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
platform_what_if="$(mktemp)"
gateway_what_if="$(mktemp)"
compiled_gateway="$(mktemp)"
gateway_parameters="$(mktemp)"
generated_gateway_key="$(mktemp)"
prompted_openai_key="$(mktemp)"
cleanup() {
  rm -f \
    "$platform_what_if" \
    "$gateway_what_if" \
    "$compiled_gateway" \
    "$gateway_parameters" \
    "$generated_gateway_key" \
    "$prompted_openai_key"
}
trap cleanup EXIT
chmod 600 "$gateway_parameters" "$generated_gateway_key" "$prompted_openai_key"

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
    --openai-api-key-file)
      openai_key_file="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

az account show --output none
deployer_object_id="${AZURE_DEPLOYER_OBJECT_ID:-$(az ad signed-in-user show --query id --output tsv)}"
if [[ -z "$deployer_object_id" ]]; then
  echo "Unable to resolve the deployer object ID." >&2
  exit 1
fi
az bicep build --file "$project_dir/infra/gateway.bicep" --stdout > "$compiled_gateway"
python3 "$project_dir/infra/validate-gateway.py" "$compiled_gateway"
test_python="${LEARNINGNEMO_PYTHON:-$HOME/.venvs/nemo-agents/bin/python}"
if [[ ! -x "$test_python" ]]; then
  echo "Set LEARNINGNEMO_PYTHON to the locked project Python environment." >&2
  exit 1
fi
"$test_python" "$project_dir/scripts/validate-agent-config.py"
python3 "$project_dir/infra/gateway_parameters.py" \
  "$gateway_parameters" \
  --api-management-name "$api_management" \
  --key-vault-name "$key_vault"

platform_parameters=(
  location="$location"
  resourceGroupName="$resource_group"
  keyVaultName="$key_vault"
  apiManagementName="$api_management"
  publisherName="$publisher_name"
  publisherEmail="$publisher_email"
  deployerObjectId="$deployer_object_id"
)

az deployment sub validate \
  --name nemo-agent-platform \
  --location "$location" \
  --template-file "$project_dir/infra/main.bicep" \
  --parameters "${platform_parameters[@]}" \
  --output none

echo "Running platform what-if. No resources will be changed."
az deployment sub what-if \
  --name nemo-agent-platform \
  --location "$location" \
  --template-file "$project_dir/infra/main.bicep" \
  --parameters "${platform_parameters[@]}" \
  --result-format ResourceIdOnly \
  --no-pretty-print \
  --output json > "$platform_what_if"
python3 "$project_dir/infra/summarize-gateway-what-if.py" "$platform_what_if" --label platform

platform_ready=false
if [[ "$(az group exists --name "$resource_group" --output tsv)" == "true" ]] \
  && az apim show --resource-group "$resource_group" --name "$api_management" --output none 2>/dev/null \
  && az keyvault show --resource-group "$resource_group" --name "$key_vault" --output none 2>/dev/null; then
  platform_ready=true
fi

if [[ "$platform_ready" == true ]]; then
  az deployment group validate \
    --name nemo-llm-gateway \
    --resource-group "$resource_group" \
    --template-file "$project_dir/infra/gateway.bicep" \
    --parameters "@$gateway_parameters" \
    --output none
  echo "Running gateway what-if. No resources will be changed."
  az deployment group what-if \
    --name nemo-llm-gateway \
    --resource-group "$resource_group" \
    --template-file "$project_dir/infra/gateway.bicep" \
    --parameters "@$gateway_parameters" \
    --result-format ResourceIdOnly \
    --no-pretty-print \
    --output json > "$gateway_what_if"
  python3 "$project_dir/infra/summarize-gateway-what-if.py" "$gateway_what_if" --label gateway
elif [[ "$mode" == "what-if" ]]; then
  echo "Gateway what-if is deferred until the platform resources exist."
fi

if [[ "$mode" == "what-if" ]]; then
  echo "What-if complete. Re-run with --apply and the explicit acknowledgement to deploy."
  exit 0
fi

if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Apply blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription." >&2
  exit 2
fi
active_subscription="$(az account show --query id --output tsv)"
if [[ "$active_subscription" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Apply blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi
unset active_subscription
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "llm-gateway" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=llm-gateway for this command only." >&2
  exit 2
fi

platform_created=false
if [[ "$platform_ready" != true ]]; then
  echo "Registering the API Management resource provider for clean-room bootstrap."
  az provider register --namespace Microsoft.ApiManagement --wait
  echo "Deploying Key Vault and API Management Consumption through Bicep."
  az deployment sub create \
    --name nemo-agent-platform \
    --location "$location" \
    --template-file "$project_dir/infra/main.bicep" \
    --parameters "${platform_parameters[@]}" \
    --output none
  platform_created=true
else
  echo "Existing Key Vault and API Management platform passed readiness checks."
fi

secret_parameters=()
if ! az keyvault secret show --vault-name "$key_vault" --name openai-api-key --output none 2>/dev/null; then
  if [[ -n "$openai_key_file" ]]; then
    resolved_key_file="$(realpath "$openai_key_file")"
    if [[ ! -f "$resolved_key_file" || "$resolved_key_file" == "$project_dir"/* ]]; then
      echo "OpenAI provider key file must exist outside the repository." >&2
      exit 2
    fi
    permissions="$(stat -c '%a' "$resolved_key_file")"
    if (( (8#$permissions & 077) != 0 )); then
      echo "OpenAI provider key file must be owner-readable only (mode 0600)." >&2
      exit 2
    fi
    secret_parameters+=(--openai-key-file "$resolved_key_file")
  elif [[ -t 0 ]]; then
    read -rsp "OpenAI API key for Key Vault (input hidden): " openai_api_key
    printf "\n"
    if [[ -z "$openai_api_key" || "$openai_api_key" == *$'\n'* ]]; then
      echo "OpenAI provider key must contain exactly one non-empty value." >&2
      unset openai_api_key
      exit 2
    fi
    printf '%s' "$openai_api_key" > "$prompted_openai_key"
    unset openai_api_key
    secret_parameters+=(--openai-key-file "$prompted_openai_key")
  else
    echo "OpenAI provider key is absent. Supply --openai-api-key-file with a mode 0600 file." >&2
    exit 2
  fi
fi

if ! az keyvault secret show --vault-name "$key_vault" --name llm-gateway-client-key --output none 2>/dev/null; then
  openssl rand -hex 32 > "$generated_gateway_key"
  secret_parameters+=(--gateway-key-file "$generated_gateway_key")
fi

python3 "$project_dir/infra/gateway_parameters.py" \
  "$gateway_parameters" \
  --api-management-name "$api_management" \
  --key-vault-name "$key_vault" \
  "${secret_parameters[@]}"
az deployment group validate \
  --name nemo-llm-gateway \
  --resource-group "$resource_group" \
  --template-file "$project_dir/infra/gateway.bicep" \
  --parameters "@$gateway_parameters" \
  --output none
if (( ${#secret_parameters[@]} > 0 )); then
  echo "Running secret-bootstrap gateway what-if. Values remain secure."
  az deployment group what-if \
    --name nemo-llm-gateway \
    --resource-group "$resource_group" \
    --template-file "$project_dir/infra/gateway.bicep" \
    --parameters "@$gateway_parameters" \
    --result-format ResourceIdOnly \
    --no-pretty-print \
    --output json > "$gateway_what_if"
  python3 "$project_dir/infra/summarize-gateway-what-if.py" "$gateway_what_if" --label gateway-bootstrap
fi
if [[ "$platform_created" == true ]]; then
  echo "Platform apply complete. Review the gateway what-if above, then rerun --apply to deploy the API."
  exit 0
fi

echo "Deploying the Key Vault-backed LLM gateway policy."
az deployment group create \
  --name nemo-llm-gateway \
  --resource-group "$resource_group" \
  --template-file "$project_dir/infra/gateway.bicep" \
  --parameters "@$gateway_parameters" \
  --output none

source "$project_dir/scripts/load-gateway-env.sh" >/dev/null
bash "$project_dir/scripts/test-gateway.sh"
"$test_python" "$project_dir/scripts/test-semantic-guardrail.py"

echo "Gateway deployed and both APIM operations verified."
echo "Source scripts/load-gateway-env.sh to load endpoints and the internal credential without printing them."