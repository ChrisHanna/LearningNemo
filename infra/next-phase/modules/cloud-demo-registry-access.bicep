param registryName string
param principalIds array
resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' existing = { name: registryName }
var identityNames = ['dashboard', 'controller', 'agent']
resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (principalId, index) in principalIds: {
  name: guid(registry.id, 'id-learningnemo-cloud-${identityNames[index]}-dev', 'cloud-demo-pull')
  scope: registry
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}]