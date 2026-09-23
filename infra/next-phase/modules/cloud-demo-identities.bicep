param location string
param tags object

var names = ['dashboard', 'controller', 'agent']
resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = [for name in names: {
  name: 'id-learningnemo-cloud-${name}-dev'
  location: location
  tags: union(tags, { identityPurpose: 'cloud-demo-${name}' })
  properties: { isolationScope: 'Regional' }
}]
output identityIds array = [for (name, index) in names: identities[index].id]
output principalIds array = [for (name, index) in names: identities[index].properties.principalId]
output clientIds array = [for (name, index) in names: identities[index].properties.clientId]