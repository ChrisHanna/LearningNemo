@description('Azure region for all resources.')
param location string

@description('Globally unique Key Vault name.')
param keyVaultName string

@description('Globally unique API Management service name.')
param apiManagementName string

@description('API Management publisher name.')
param publisherName string

@description('API Management publisher email.')
param publisherEmail string

@secure()
@description('Object ID of the human deployer allowed to initialize Key Vault secrets.')
param deployerObjectId string

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Enabled'
  }
}

resource apiManagement 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: apiManagementName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'Consumption'
    capacity: 0
  }
  properties: {
    publisherName: publisherName
    publisherEmail: publisherEmail
  }
}

var keyVaultSecretsUserRole = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)
var keyVaultSecretsOfficerRole = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
)

resource apiManagementKeyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, apiManagement.id, keyVaultSecretsUserRole)
  scope: keyVault
  properties: {
    principalId: apiManagement.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole
  }
}

resource deployerKeyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, deployerObjectId, keyVaultSecretsOfficerRole)
  scope: keyVault
  properties: {
    principalId: deployerObjectId
    principalType: 'User'
    roleDefinitionId: keyVaultSecretsOfficerRole
  }
}

output keyVaultName string = keyVault.name
output apiManagementName string = apiManagement.name
output gatewayUrl string = apiManagement.properties.gatewayUrl