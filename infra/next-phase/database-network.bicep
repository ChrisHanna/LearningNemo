targetScope = 'resourceGroup'

param location string
param environment string
param projectName string

@secure()
param sqlServerName string

param databaseResourceGroupName string
param platformResourceGroupName string
param platformVnetName string
param privateEndpointSubnetName string = 'snet-private-endpoints'
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
var sqlPrivateDnsZoneName = 'privatelink${az.environment().suffixes.sqlServerHostname}'
var platformVnetId = resourceId(
  subscription().subscriptionId,
  platformResourceGroupName,
  'Microsoft.Network/virtualNetworks',
  platformVnetName
)
var privateEndpointSubnetId = resourceId(
  subscription().subscriptionId,
  platformResourceGroupName,
  'Microsoft.Network/virtualNetworks/subnets',
  platformVnetName,
  privateEndpointSubnetName
)

resource sqlServer 'Microsoft.Sql/servers@2023-08-01' existing = {
  scope: resourceGroup(databaseResourceGroupName)
  name: sqlServerName
}

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: sqlPrivateDnsZoneName
  location: 'global'
  tags: tags
}

resource privateDnsVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: privateDnsZone
  name: 'link-${projectName}-${environment}'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: platformVnetId
    }
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-${projectName}-sql-${environment}'
  location: location
  tags: tags
  properties: {
    privateLinkServiceConnections: [
      {
        name: 'sql-server'
        properties: {
          groupIds: [
            'sqlServer'
          ]
          privateLinkServiceId: sqlServer.id
          requestMessage: 'LearningNeMo controlled lab database access.'
        }
      }
    ]
    subnet: {
      id: privateEndpointSubnetId
    }
  }
}

resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'sql'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

output expiresAt string = expiresAt
output privateEndpointName string = privateEndpoint.name