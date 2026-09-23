param principalId string
resource diagnostic 'Microsoft.App/containerApps@2025-01-01' existing = { name: 'ca-learningnemo-diagnostic-dev' }
resource readRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'cloud-demo-diagnostic-read')
  properties: {
    roleName: 'LearningNeMo diagnostic endpoint reader'
    description: 'Resolve the existing diagnostic app endpoint; no invoke or mutation authority.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{ actions: ['Microsoft.App/containerApps/read'], notActions: [], dataActions: [], notDataActions: [] }]
  }
}
resource reader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(diagnostic.id, 'id-learningnemo-cloud-controller-dev', 'cloud-demo-diagnostic-read')
  scope: diagnostic
  properties: { principalId: principalId, principalType: 'ServicePrincipal', roleDefinitionId: readRole.id }
}