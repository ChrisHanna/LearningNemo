#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "Source this script so the environment variables remain in your shell:" >&2
  echo "  source scripts/load-gateway-env.sh" >&2
  exit 2
fi

key_vault="${AZURE_KEY_VAULT_NAME:-kvnemo8370187d}"
api_management="${AZURE_APIM_NAME:-apim-nemo-8370187d}"

gateway_hostname="$(az apim show \
  --resource-group "${AZURE_RESOURCE_GROUP:-rg-nemo-agent-dev}" \
  --name "$api_management" \
  --query gatewayUrl \
  --output tsv)"

export OPENAI_BASE_URL="${gateway_hostname}/llm/v1"
export OPENAI_GUARDRAIL_BASE_URL="${gateway_hostname}/llm/v1/guardrails"
export OPENAI_API_KEY="$(az keyvault secret show \
  --vault-name "$key_vault" \
  --name llm-gateway-client-key \
  --query value \
  --output tsv)"

echo "Loaded the agent and semantic-guardrail gateway endpoints plus credential into this shell."