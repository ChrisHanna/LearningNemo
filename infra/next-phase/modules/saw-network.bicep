targetScope = 'resourceGroup'

param location string
param vnetName string
param addressPrefix string
param workspaceSubnetPrefix string
param firewallSubnetPrefix string
param deniedPlatformAddressPrefix string
param tags object

resource workspaceNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-${vnetName}-workspace'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'deny-all-inbound'
        properties: {
          priority: 100
          access: 'Deny'
          direction: 'Inbound'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: '*'
          description: 'The SAW VM has no direct inbound path; access will use an outbound trusted broker session.'
        }
      }
      {
        name: 'deny-trusted-platform-outbound'
        properties: {
          priority: 100
          access: 'Deny'
          direction: 'Outbound'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: deniedPlatformAddressPrefix
          description: 'Prevent an untrusted workspace from directly reaching the trusted platform address space.'
        }
      }
      {
        name: 'deny-saw-east-west'
        properties: {
          priority: 110
          access: 'Deny'
          direction: 'Outbound'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: addressPrefix
          description: 'Prevent direct workspace-to-workspace traffic inside the untrusted VNet.'
        }
      }
      {
        name: 'deny-direct-azure-sql'
        properties: {
          priority: 120
          access: 'Deny'
          direction: 'Outbound'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: 'Sql'
          description: 'Agent workspaces must use brokered APIs and must never connect directly to the Azure SQL service tag.'
        }
      }
    ]
  }
}

resource workspaceRouteTable 'Microsoft.Network/routeTables@2024-05-01' = {
  name: 'rt-${vnetName}-workspace'
  location: location
  tags: tags
  properties: {
    disableBgpRoutePropagation: true
    routes: []
  }
}

resource sawVnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        addressPrefix
      ]
    }
    subnets: [
      {
        name: 'snet-workspace'
        properties: {
          addressPrefix: workspaceSubnetPrefix
          networkSecurityGroup: {
            id: workspaceNsg.id
          }
          routeTable: {
            id: workspaceRouteTable.id
          }
          privateEndpointNetworkPolicies: 'Enabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
      }
      {
        name: 'AzureFirewallSubnet'
        properties: {
          addressPrefix: firewallSubnetPrefix
          privateEndpointNetworkPolicies: 'Enabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
      }
    ]
  }
}

output vnetName string = sawVnet.name
output workspaceSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', sawVnet.name, 'snet-workspace')
output firewallSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', sawVnet.name, 'AzureFirewallSubnet')