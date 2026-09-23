targetScope = 'subscription'

param location string
param environment string
param projectName string
param databaseNetworkResourceGroupName string
param databaseResourceGroupName string

@secure()
param sqlServerName string

param platformResourceGroupName string
param platformVnetName string
param privateEndpointSubnetName string
param expiresAt string
param ownerTag string
param additionalTags object = {}

var tags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep'
  owner: ownerTag
  costProfile: 'expiring-sql-private-endpoint'
  trustZone: 'trusted-platform'
  platformPhase: 'wp3-database-network'
  disposable: 'true'
  expiresAt: expiresAt
})

resource networkResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: databaseNetworkResourceGroupName
  location: location
  tags: tags
}

module network './database-network.bicep' = {
  name: 'learningnemo-database-network-${environment}'
  scope: networkResourceGroup
  params: {
    location: location
    environment: environment
    projectName: projectName
    sqlServerName: sqlServerName
    databaseResourceGroupName: databaseResourceGroupName
    platformResourceGroupName: platformResourceGroupName
    platformVnetName: platformVnetName
    privateEndpointSubnetName: privateEndpointSubnetName
    expiresAt: expiresAt
    ownerTag: ownerTag
    additionalTags: additionalTags
  }
}

output databaseNetworkResourceGroupName string = networkResourceGroup.name
output expiresAt string = network.outputs.expiresAt
output privateEndpointName string = network.outputs.privateEndpointName