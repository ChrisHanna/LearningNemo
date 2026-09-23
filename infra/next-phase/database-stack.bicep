targetScope = 'subscription'

param location string
param environment string
param projectName string
param platformResourceGroupName string
param databaseResourceGroupName string
param databaseName string
param ownerTag string
param monthlyCostCeiling int
param autoPauseDelayMinutes int
param minVcores string
param maxVcores int
param maxSizeBytes int
param useFreeLimit bool
param freeLimitExhaustionBehavior string
param additionalTags object = {}

var groupTags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep'
  owner: ownerTag
  costProfile: 'azure-sql-free-serverless'
  trustZone: 'trusted-platform'
  platformPhase: 'wp3-database'
  disposable: 'false'
})

resource databaseResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: databaseResourceGroupName
  location: location
  tags: groupTags
}

module database './database.bicep' = {
  name: 'learningnemo-database-${environment}'
  scope: databaseResourceGroup
  params: {
    location: location
    environment: environment
    projectName: projectName
    databaseName: databaseName
    ownerTag: ownerTag
    monthlyCostCeiling: monthlyCostCeiling
    autoPauseDelayMinutes: autoPauseDelayMinutes
    minVcores: minVcores
    maxVcores: maxVcores
    maxSizeBytes: maxSizeBytes
    useFreeLimit: useFreeLimit
    freeLimitExhaustionBehavior: freeLimitExhaustionBehavior
    additionalTags: additionalTags
  }
}

output databaseResourceGroupName string = databaseResourceGroup.name
output databaseName string = database.outputs.databaseName
output sqlAdminIdentityName string = database.outputs.sqlAdminIdentityName
output costProfile string = database.outputs.costProfile
output platformResourceGroupName string = platformResourceGroupName