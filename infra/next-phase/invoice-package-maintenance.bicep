targetScope = 'resourceGroup'

@minLength(1)
@maxLength(4)
param mirrorAddresses array

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' existing = {
  name: 'nsg-vnet-learningnemo-saw-dev-workspace'
}

resource packageMaintenance 'Microsoft.Network/networkSecurityGroups/securityRules@2024-05-01' = {
  parent: nsg
  name: 'allow-invoice-package-maintenance'
  properties: {
    priority: 123
    direction: 'Outbound'
    access: 'Allow'
    protocol: 'Tcp'
    sourceAddressPrefix: '10.50.0.0/27'
    sourcePortRange: '*'
    destinationAddressPrefixes: mirrorAddresses
    destinationPortRange: '80'
    description: 'Bounded host package maintenance only; Ubuntu signed metadata; all sandboxes stopped.'
  }
}