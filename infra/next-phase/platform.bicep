targetScope = 'resourceGroup'

@description('Azure region for the trusted platform services.')
param location string = resourceGroup().location

@description('Short environment name used in resource names and tags.')
param environment string = 'dev'

@description('Non-sensitive project identifier used in resource names and tags.')
param projectName string = 'learningnemo'

@description('Expected resource group name. The deployment script validates this before apply.')
param platformResourceGroupName string

@description('Name of the empty Consumption-only Container Apps environment.')
param containerAppsEnvironmentName string = 'cae-learningnemo-dev'

@minValue(1)
@maxValue(100)
@description('Maximum acceptable notified subscription budget for this portfolio phase.')
param monthlyCostCeiling int = 50

@description('Non-sensitive ownership alias for cost reporting.')
param ownerTag string = 'learningnemo-portfolio'

@description('UTC timestamp after which the runtime envelope is eligible for deletion.')
param expiresAt string

@description('Additional non-sensitive tags applied to all resources.')
param additionalTags object = {}

var runtimeTags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep'
  owner: ownerTag
  costProfile: 'vnet-integrated-runtime-envelope'
  monthlyCostCeiling: string(monthlyCostCeiling)
  trustZone: 'trusted-platform'
  platformPhase: 'wp2a-runtime'
  disposable: 'true'
  expiresAt: expiresAt
})

var platformVnetName = 'vnet-${projectName}-platform-${environment}'
var containerAppsSubnetName = 'snet-container-apps'
var infrastructureResourceGroupName = 'mrg-${projectName}-container-apps-${environment}'

resource platformVnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: platformVnetName
}

resource containerAppsSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: platformVnet
  name: containerAppsSubnetName
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2026-01-01' = {
  name: containerAppsEnvironmentName
  location: location
  tags: runtimeTags
  properties: {
    infrastructureResourceGroup: infrastructureResourceGroupName
    peerAuthentication: {
      mtls: {
        enabled: true
      }
    }
    peerTrafficConfiguration: {
      encryption: {
        enabled: true
      }
    }
    publicNetworkAccess: 'Enabled'
    vnetConfiguration: {
      infrastructureSubnetId: containerAppsSubnet.id
      internal: false
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    zoneRedundant: false
  }
}

output costProfile string = 'On-demand VNet-integrated runtime envelope; managed network charges apply.'
output platformResourceGroupName string = platformResourceGroupName
output containerAppsEnvironmentName string = containerAppsEnvironment.name
output infrastructureResourceGroupName string = infrastructureResourceGroupName
output monthlyCostCeiling int = monthlyCostCeiling
output expiresAt string = expiresAt