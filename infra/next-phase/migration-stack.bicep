targetScope = 'subscription'

param location string
param environment string
param projectName string
param migrationResourceGroupName string
param platformResourceGroupName string
param containerAppsEnvironmentName string
param databaseResourceGroupName string
param databaseName string

@secure()
param imageReference string

@secure()
param registryServer string

@secure()
param sqlServerHostname string

@secure()
param migrationBundle string

param migrationBundleSha256 string
param expiresAt string
param ownerTag string
param additionalTags object = {}

var tags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep'
  owner: ownerTag
  costProfile: 'one-shot-consumption-job'
  trustZone: 'trusted-platform'
  platformPhase: 'wp3-database-migration'
  disposable: 'true'
  expiresAt: expiresAt
  imageDigest: last(split(imageReference, '@sha256:'))
  migrationBundleHash: migrationBundleSha256
})

resource migrationResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: migrationResourceGroupName
  location: location
  tags: tags
}

module migration './migration-job.bicep' = {
  name: 'learningnemo-sql-migration-${environment}'
  scope: migrationResourceGroup
  params: {
    location: location
    environment: environment
    projectName: projectName
    platformResourceGroupName: platformResourceGroupName
    containerAppsEnvironmentName: containerAppsEnvironmentName
    databaseResourceGroupName: databaseResourceGroupName
    databaseName: databaseName
    imageReference: imageReference
    registryServer: registryServer
    sqlServerHostname: sqlServerHostname
    migrationBundle: migrationBundle
    migrationBundleSha256: migrationBundleSha256
    expiresAt: expiresAt
    ownerTag: ownerTag
    additionalTags: additionalTags
  }
}

output migrationResourceGroupName string = migrationResourceGroup.name
output migrationJobName string = migration.outputs.migrationJobName
output migrationBundleSha256 string = migration.outputs.migrationBundleSha256
output expiresAt string = migration.outputs.expiresAt