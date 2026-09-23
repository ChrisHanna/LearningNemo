targetScope = 'subscription'

param location string
param environment string
param projectName string
param probeResourceGroupName string
param platformResourceGroupName string
param containerAppsEnvironmentName string
param artifactResourceGroupName string
param databaseResourceGroupName string

@secure()
param imageReference string

@secure()
param registryServer string

@secure()
param sqlServerHostname string

param expiresAt string
param ownerTag string
param additionalTags object = {}

var tags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep-deployment-stack'
  owner: ownerTag
  costProfile: 'one-shot-connectivity-probe'
  trustZone: 'trusted-platform'
  platformPhase: 'wp3-connectivity-probe'
  disposable: 'true'
  expiresAt: expiresAt
  imageDigest: last(split(imageReference, '@sha256:'))
})

resource probeResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: probeResourceGroupName
  location: location
  tags: tags
}

module probe './connectivity-probe-job.bicep' = {
  name: 'learningnemo-connectivity-probe-${environment}'
  scope: probeResourceGroup
  params: {
    location: location
    environment: environment
    projectName: projectName
    platformResourceGroupName: platformResourceGroupName
    containerAppsEnvironmentName: containerAppsEnvironmentName
    imageReference: imageReference
    registryServer: registryServer
    sqlServerHostname: sqlServerHostname
    tags: tags
  }
}

output probeResourceGroupName string = probeResourceGroup.name
output probeIdentityName string = probe.outputs.probeIdentityName
output probeJobName string = probe.outputs.probeJobName
output expiresAt string = expiresAt
output targetArtifactResourceGroupName string = artifactResourceGroupName
output targetDatabaseResourceGroupName string = databaseResourceGroupName