targetScope = 'resourceGroup'

param location string = 'eastus'
param expiresAt string = ''
@allowed(['leased', 'operator-managed'])
param availabilityMode string = 'leased'
param attach bool = false

var tags = union({
  project: 'learningnemo'
  owner: 'learningnemo-portfolio'
  environment: 'dev'
  purpose: 'runtime-approved-egress'
  managedBy: 'bicep'
}, availabilityMode == 'operator-managed' ? { availabilityMode: availabilityMode, disposable: 'false' } : { disposable: 'true', expiresAt: expiresAt })

resource address 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'pip-learningnemo-saw-runtime-dev'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Regional' }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
    idleTimeoutInMinutes: 4
  }
}

resource nat 'Microsoft.Network/natGateways@2024-05-01' = {
  name: 'nat-learningnemo-saw-runtime-dev'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { idleTimeoutInMinutes: 4, publicIpAddresses: [{ id: address.id }] }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: 'vnet-learningnemo-saw-dev'
}

resource subnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = if (attach) {
  parent: vnet
  name: 'snet-workspace'
  properties: {
    addressPrefix: '10.50.0.0/27'
    networkSecurityGroup: { id: resourceId('Microsoft.Network/networkSecurityGroups', 'nsg-vnet-learningnemo-saw-dev-workspace') }
    routeTable: { id: resourceId('Microsoft.Network/routeTables', 'rt-vnet-learningnemo-saw-dev-workspace') }
    natGateway: { id: nat.id }
    defaultOutboundAccess: false
    privateEndpointNetworkPolicies: 'Enabled'
    privateLinkServiceNetworkPolicies: 'Enabled'
  }
}

output natId string = nat.id