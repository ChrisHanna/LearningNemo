@description('Existing API Management service name.')
param apiManagementName string

@description('Existing Key Vault name.')
param keyVaultName string

@secure()
@description('OpenAI provider key used only when initializing an absent Key Vault secret.')
param openAiApiKey string = ''

@secure()
@description('Internal gateway key used only when initializing an absent Key Vault secret.')
param gatewayClientKey string = ''

resource apiManagement 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apiManagementName
}

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: keyVaultName
}

resource openAiKeySecret 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = if (!empty(openAiApiKey)) {
  parent: keyVault
  name: 'openai-api-key'
  properties: {
    value: openAiApiKey
    attributes: {
      enabled: true
    }
  }
}

resource gatewayClientKeySecret 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = if (!empty(gatewayClientKey)) {
  parent: keyVault
  name: 'llm-gateway-client-key'
  properties: {
    value: gatewayClientKey
    attributes: {
      enabled: true
    }
  }
}

resource openAiApiKeyNamedValue 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apiManagement
  name: 'openai-api-key'
  properties: {
    displayName: 'openai-api-key'
    secret: true
    keyVault: {
      secretIdentifier: '${keyVault.properties.vaultUri}secrets/openai-api-key'
    }
  }
}

resource gatewayClientKeyNamedValue 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apiManagement
  name: 'llm-gateway-client-key'
  properties: {
    displayName: 'llm-gateway-client-key'
    secret: true
    keyVault: {
      secretIdentifier: '${keyVault.properties.vaultUri}secrets/llm-gateway-client-key'
    }
  }
}

resource llmGateway 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apiManagement
  name: 'llm-router'
  properties: {
    displayName: 'LLM Router'
    path: 'llm/v1'
    protocols: [
      'https'
    ]
    serviceUrl: 'https://api.openai.com/v1'
    subscriptionRequired: false
  }
}

resource chatCompletions 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: llmGateway
  name: 'chat-completions'
  properties: {
    displayName: 'Chat Completions'
    method: 'POST'
    urlTemplate: '/chat/completions'
    templateParameters: []
    responses: []
  }
}

resource semanticGuardrailChatCompletions 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: llmGateway
  name: 'semantic-guardrail-chat-completions'
  properties: {
    displayName: 'Semantic Guardrail Chat Completions'
    method: 'POST'
    urlTemplate: '/guardrails/chat/completions'
    templateParameters: []
    responses: []
  }
}

resource semanticGuardrailPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = {
  parent: semanticGuardrailChatCompletions
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('./policies/semantic-guardrail.xml')
  }
}

resource llmGatewayPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: llmGateway
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('./policies/llm-gateway.xml')
  }
  dependsOn: [
    openAiKeySecret
    gatewayClientKeySecret
    openAiApiKeyNamedValue
    gatewayClientKeyNamedValue
    chatCompletions
    semanticGuardrailChatCompletions
  ]
}

output llmGatewayBaseUrl string = '${apiManagement.properties.gatewayUrl}/llm/v1'
output semanticGuardrailBaseUrl string = '${apiManagement.properties.gatewayUrl}/llm/v1/guardrails'