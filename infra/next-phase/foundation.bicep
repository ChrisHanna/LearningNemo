targetScope = 'subscription'

@description('Azure region for the low-cost portfolio environment.')
param location string = 'eastus'

@allowed([
  'dev'
  'test'
])
@description('Short environment name used in resource names and tags.')
param environment string = 'dev'

@description('Non-sensitive project identifier used in resource names and tags.')
param projectName string = 'learningnemo'

@description('Resource group for stable trusted platform resources.')
param platformResourceGroupName string = 'rg-learningnemo-platform-dev'

@description('Disposable resource group for the Secure Agent Workspace engagement.')
param sawResourceGroupName string = 'rg-learningnemo-saw-dev'

@description('Address space for trusted platform services.')
param platformVnetAddressPrefix string = '10.40.0.0/16'

@description('Dedicated delegated subnet for a future workload-profile Container Apps environment.')
param containerAppsSubnetPrefix string = '10.40.0.0/27'

@description('Subnet reserved for future private endpoints.')
param privateEndpointSubnetPrefix string = '10.40.1.0/27'

@description('Address space for the untrusted single-user SAW environment.')
param sawVnetAddressPrefix string = '10.50.0.0/16'

@description('Subnet for disposable Secure Agent Workspace VMs.')
param sawWorkspaceSubnetPrefix string = '10.50.0.0/27'

@description('Subnet reserved for a later Azure Firewall deployment. Azure requires this exact subnet name.')
param sawFirewallSubnetPrefix string = '10.50.255.0/26'

@description('Non-sensitive ownership alias for cost and cleanup reporting.')
param ownerTag string = 'learningnemo-portfolio'

@description('ISO-8601 date after which the disposable SAW resource group should be deleted.')
param expiresOn string

@description('Additional non-sensitive tags applied to all resources.')
param additionalTags object = {}

var commonTags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep'
  owner: ownerTag
  costProfile: 'low-cost-poc'
})

var sawTags = union(commonTags, {
  trustZone: 'untrusted-agent-workspace'
  disposable: 'true'
  expiresOn: expiresOn
  sawMaturity: 'network-foundation-only'
})

resource platformResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: platformResourceGroupName
  location: location
  tags: union(commonTags, {
    trustZone: 'trusted-platform'
    disposable: 'false'
  })
}

resource sawResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: sawResourceGroupName
  location: location
  tags: sawTags
}

module platformNetwork './modules/platform-network.bicep' = {
  name: 'learningnemo-platform-network-${environment}'
  scope: platformResourceGroup
  params: {
    location: location
    vnetName: 'vnet-${projectName}-platform-${environment}'
    addressPrefix: platformVnetAddressPrefix
    containerAppsSubnetPrefix: containerAppsSubnetPrefix
    privateEndpointSubnetPrefix: privateEndpointSubnetPrefix
    tags: commonTags
  }
}

module sawNetwork './modules/saw-network.bicep' = {
  name: 'learningnemo-saw-network-${environment}'
  scope: sawResourceGroup
  params: {
    location: location
    vnetName: 'vnet-${projectName}-saw-${environment}'
    addressPrefix: sawVnetAddressPrefix
    workspaceSubnetPrefix: sawWorkspaceSubnetPrefix
    firewallSubnetPrefix: sawFirewallSubnetPrefix
    deniedPlatformAddressPrefix: platformVnetAddressPrefix
    tags: sawTags
  }
}

output costProfile string = 'low-cost-poc-no-metered-compute'
output platformResourceGroupName string = platformResourceGroup.name
output sawResourceGroupName string = sawResourceGroup.name
output platformVnetName string = platformNetwork.outputs.vnetName
output sawVnetName string = sawNetwork.outputs.vnetName
output containerAppsSubnetId string = platformNetwork.outputs.containerAppsSubnetId
output privateEndpointSubnetId string = platformNetwork.outputs.privateEndpointSubnetId
output sawWorkspaceSubnetId string = sawNetwork.outputs.workspaceSubnetId
output sawFirewallSubnetId string = sawNetwork.outputs.firewallSubnetId
output perimeterClaim string = 'POC NSG foundation only; Azure Firewall and reference perimeter are not deployed.'