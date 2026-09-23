targetScope = 'resourceGroup'

param environment string
param projectName string
param workspaceSubnetPrefix string
param attachBootstrapEgress bool

resource sawVnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: 'vnet-${projectName}-saw-${environment}'
}

resource workspaceNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' existing = {
  name: 'nsg-vnet-${projectName}-saw-${environment}-workspace'
}

resource workspaceRouteTable 'Microsoft.Network/routeTables@2024-05-01' existing = {
  name: 'rt-vnet-${projectName}-saw-${environment}-workspace'
}

resource bootstrapNat 'Microsoft.Network/natGateways@2024-05-01' existing = {
  name: 'nat-${projectName}-saw-bootstrap-${environment}'
}

resource workspaceSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  parent: sawVnet
  name: 'snet-workspace'
  properties: {
    addressPrefix: workspaceSubnetPrefix
    networkSecurityGroup: {
      id: workspaceNsg.id
    }
    routeTable: {
      id: workspaceRouteTable.id
    }
    natGateway: attachBootstrapEgress ? {
      id: bootstrapNat.id
    } : null
    privateEndpointNetworkPolicies: 'Enabled'
    privateLinkServiceNetworkPolicies: 'Enabled'
  }
}

output bootstrapEgressAttached bool = attachBootstrapEgress