targetScope = 'subscription'

@description('Azure region for the development platform.')
param location string = 'eastus'

@description('Resource group name.')
param resourceGroupName string = 'rg-nemo-agent-dev'

@description('Globally unique Key Vault name.')
param keyVaultName string = 'kvnemo8370187d'

@description('Globally unique API Management service name.')
param apiManagementName string = 'apim-nemo-8370187d'

@description('API Management publisher name.')
param publisherName string = 'NeMo Agent Team'

@description('API Management publisher email.')
param publisherEmail string = 'noreply@example.com'

@secure()
@description('Object ID of the human deployer allowed to initialize Key Vault secrets.')
param deployerObjectId string

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

module platform './platform.bicep' = {
  name: 'nemo-agent-platform'
  scope: resourceGroup
  params: {
    location: location
    keyVaultName: keyVaultName
    apiManagementName: apiManagementName
    publisherName: publisherName
    publisherEmail: publisherEmail
    deployerObjectId: deployerObjectId
  }
}

output keyVaultName string = platform.outputs.keyVaultName
output apiManagementName string = platform.outputs.apiManagementName
output llmGatewayBaseUrl string = '${platform.outputs.gatewayUrl}/llm/v1'
output semanticGuardrailBaseUrl string = '${platform.outputs.gatewayUrl}/llm/v1/guardrails'