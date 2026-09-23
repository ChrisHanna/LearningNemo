targetScope = 'resourceGroup'

@description('Azure region for the trusted platform identities.')
param location string = resourceGroup().location

@description('Short environment name used in resource names and tags.')
param environment string = 'dev'

@description('Non-sensitive project identifier used in resource names and tags.')
param projectName string = 'learningnemo'

@description('Expected resource group name. The deployment script validates this before apply.')
param platformResourceGroupName string

@description('Planned Container Apps environment associated with these service identities.')
param containerAppsEnvironmentName string = 'cae-learningnemo-dev'

@minValue(1)
@maxValue(100)
@description('Maximum acceptable notified subscription budget for this portfolio phase.')
param monthlyCostCeiling int = 50

@description('Non-sensitive ownership alias for cost reporting.')
param ownerTag string = 'learningnemo-portfolio'

@description('Additional non-sensitive tags applied to all resources.')
param additionalTags object = {}

var identityTags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep'
  owner: ownerTag
  costProfile: 'identity-only-no-compute'
  monthlyCostCeiling: string(monthlyCostCeiling)
  trustZone: 'trusted-platform'
  platformPhase: 'wp2a-identities'
  plannedRuntimeEnvironment: containerAppsEnvironmentName
})

module identities './modules/platform-identities.bicep' = {
  name: 'learningnemo-identities-module-${environment}'
  params: {
    location: location
    environment: environment
    projectName: projectName
    tags: identityTags
  }
}

output costProfile string = 'identity-only-no-compute'
output platformResourceGroupName string = platformResourceGroupName
output plannedRuntimeEnvironmentName string = containerAppsEnvironmentName
output monthlyCostCeiling int = monthlyCostCeiling
output identityNames array = identities.outputs.identityNames