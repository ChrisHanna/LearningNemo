targetScope = 'resourceGroup'

@minLength(1)
@maxLength(12)
param registryAddresses array

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' existing = {
  name: 'nsg-vnet-learningnemo-saw-dev-workspace'
}

resource registryBootstrap 'Microsoft.Network/networkSecurityGroups/securityRules@2024-05-01' = {
  parent: nsg
  name: 'allow-bootstrap-ghcr-https'
  properties: {
    priority: 124
    direction: 'Outbound'
    access: 'Allow'
    protocol: 'Tcp'
    sourceAddressPrefix: '*'
    sourcePortRange: '*'
    destinationAddressPrefixes: registryAddresses
    destinationPortRange: '443'
    description: 'Temporary pinned sandbox image provisioning only; removed before runtime proof.'
  }
}