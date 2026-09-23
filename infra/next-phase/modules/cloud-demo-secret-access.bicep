param vaultName string
param principalId string
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: vaultName }
resource secret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: 'llm-gateway-client-key'
}
resource access 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(secret.id, 'id-learningnemo-cloud-agent-dev', 'cloud-agent-gateway-secret')
  scope: secret
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}