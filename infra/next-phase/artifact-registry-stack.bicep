targetScope = 'subscription'

param location string
param environment string
param projectName string
param artifactResourceGroupName string
param platformResourceGroupName string
param databaseResourceGroupName string
param expiresAt string
param ownerTag string
param monthlyCostCeiling int
param additionalTags object = {}

var tags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep'
  owner: ownerTag
  costProfile: 'expiring-basic-container-registry'
  monthlyCostCeiling: string(monthlyCostCeiling)
  trustZone: 'trusted-platform'
  platformPhase: 'wp3-artifacts'
  disposable: 'true'
  expiresAt: expiresAt
})

resource artifactResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: artifactResourceGroupName
  location: location
  tags: tags
}

module registry './artifact-registry.bicep' = {
  name: 'learningnemo-artifact-registry-${environment}'
  scope: artifactResourceGroup
  params: {
    location: location
    environment: environment
    projectName: projectName
    platformResourceGroupName: platformResourceGroupName
    databaseResourceGroupName: databaseResourceGroupName
    expiresAt: expiresAt
    ownerTag: ownerTag
    monthlyCostCeiling: monthlyCostCeiling
    additionalTags: additionalTags
  }
}

output artifactResourceGroupName string = artifactResourceGroup.name
output pullAssignmentCount int = registry.outputs.pullAssignmentCount
output expiresAt string = expiresAt