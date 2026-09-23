targetScope = 'subscription'

param location string
param environment string
param projectName string
param controlResourceGroupName string
param platformResourceGroupName string
param containerAppsEnvironmentName string
param artifactResourceGroupName string
param databaseResourceGroupName string
param databaseName string

@secure()
param imageReference string

@secure()
param registryServer string

@secure()
param sqlServerHostname string

@secure()
param serviceApplicationIds object

@secure()
param approverHash string

@secure()
param operatorHash string

param expiresAt string
param ownerTag string
param additionalTags object = {}

var tags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep-deployment-stack'
  owner: ownerTag
  costProfile: 'one-shot-control-cycle'
  trustZone: 'trusted-platform'
  platformPhase: 'wp3-control-cycle'
  disposable: 'true'
  expiresAt: expiresAt
  imageDigest: last(split(imageReference, '@sha256:'))
})

resource controlResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: controlResourceGroupName
  location: location
  tags: tags
}

module control './control-cycle-job.bicep' = {
  name: 'learningnemo-control-cycle-${environment}'
  scope: controlResourceGroup
  params: {
    location: location
    environment: environment
    projectName: projectName
    platformResourceGroupName: platformResourceGroupName
    containerAppsEnvironmentName: containerAppsEnvironmentName
    imageReference: imageReference
    registryServer: registryServer
    sqlServerHostname: sqlServerHostname
    databaseName: databaseName
    serviceApplicationIds: serviceApplicationIds
    approverHash: approverHash
    operatorHash: operatorHash
    tags: tags
  }
}

output controlResourceGroupName string = controlResourceGroup.name
output controlJobName string = control.outputs.controlJobName
output controlIdentityName string = control.outputs.controlIdentityName
output registryIdentityName string = control.outputs.registryIdentityName
output expiresAt string = expiresAt
output targetArtifactResourceGroupName string = artifactResourceGroupName
output targetDatabaseResourceGroupName string = databaseResourceGroupName