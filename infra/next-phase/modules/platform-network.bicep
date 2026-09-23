targetScope = 'resourceGroup'

param location string
param vnetName string
param addressPrefix string
param containerAppsSubnetPrefix string
param privateEndpointSubnetPrefix string
param tags object

resource containerAppsNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-${vnetName}-container-apps'
  location: location
  tags: tags
  properties: {
    securityRules: []
  }
}

resource platformVnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
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
        name: 'snet-container-apps'
        properties: {
          addressPrefix: containerAppsSubnetPrefix
          networkSecurityGroup: {
            id: containerAppsNsg.id
          }
          delegations: [
            {
              name: 'container-apps-environment'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
          privateEndpointNetworkPolicies: 'Enabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
      }
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
      }
    ]
  }
}

output vnetName string = platformVnet.name
output containerAppsSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', platformVnet.name, 'snet-container-apps')
output privateEndpointSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', platformVnet.name, 'snet-private-endpoints')