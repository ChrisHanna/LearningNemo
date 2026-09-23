targetScope = 'resourceGroup'

param location string
param environment string
param projectName string
param containerAppsEnvironmentName string

@secure()
param imageReference string

param databaseName string

@secure()
param registryServer string

@secure()
param sqlServerHostname string

param expiresAt string
param ownerTag string

@secure()
param tenantId string

@secure()
param controlCallerApplicationId string

@secure()
param controlCallerPrincipalId string

@secure()
param serviceApplicationIds object

param additionalTags object = {}

var tags = union(additionalTags, {
  project: projectName
  environment: environment
  managedBy: 'bicep'
  owner: ownerTag
  costProfile: 'scale-to-zero-trusted-workers'
  trustZone: 'trusted-platform'
  platformPhase: 'wp2b-trusted-workers'
  disposable: 'true'
  expiresAt: expiresAt
  imageDigest: last(split(imageReference, '@sha256:'))
})

resource managedEnvironment 'Microsoft.App/managedEnvironments@2026-01-01' existing = {
  name: containerAppsEnvironmentName
}

resource diagnosticIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  name: 'id-${projectName}-diagnostic-${environment}'
}

resource queryRunnerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  name: 'id-${projectName}-query-runner-${environment}'
}

resource remediationIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  name: 'id-${projectName}-remediation-${environment}'
}

resource verifierIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  name: 'id-${projectName}-verifier-${environment}'
}

module diagnostic './modules/trusted-service-app.bicep' = {
  name: 'learningnemo-diagnostic-service-${environment}'
  params: {
    location: location
    appName: 'ca-${projectName}-diagnostic-${environment}'
    environmentId: managedEnvironment.id
    identityResourceId: diagnosticIdentity.id
    identityClientId: diagnosticIdentity.properties.clientId
    imageReference: imageReference
    registryServer: registryServer
    sqlServerHostname: sqlServerHostname
    sqlDatabaseName: databaseName
    serviceMode: 'diagnostic'
    tenantId: tenantId
    serviceApplicationId: serviceApplicationIds.diagnostic
    controlCallerApplicationId: controlCallerApplicationId
    controlCallerPrincipalId: controlCallerPrincipalId
    tags: tags
  }
}

module queryRunner './modules/trusted-service-app.bicep' = {
  name: 'learningnemo-query-runner-service-${environment}'
  params: {
    location: location
    appName: 'ca-${projectName}-query-runner-${environment}'
    environmentId: managedEnvironment.id
    identityResourceId: queryRunnerIdentity.id
    identityClientId: queryRunnerIdentity.properties.clientId
    imageReference: imageReference
    registryServer: registryServer
    sqlServerHostname: sqlServerHostname
    sqlDatabaseName: databaseName
    serviceMode: 'query-runner'
    tenantId: tenantId
    serviceApplicationId: serviceApplicationIds.queryRunner
    controlCallerApplicationId: controlCallerApplicationId
    controlCallerPrincipalId: controlCallerPrincipalId
    tags: tags
  }
}

module remediation './modules/trusted-service-app.bicep' = {
  name: 'learningnemo-remediation-service-${environment}'
  params: {
    location: location
    appName: 'ca-${projectName}-remediation-${environment}'
    environmentId: managedEnvironment.id
    identityResourceId: remediationIdentity.id
    identityClientId: remediationIdentity.properties.clientId
    imageReference: imageReference
    registryServer: registryServer
    sqlServerHostname: sqlServerHostname
    sqlDatabaseName: databaseName
    serviceMode: 'remediation'
    tenantId: tenantId
    serviceApplicationId: serviceApplicationIds.remediation
    controlCallerApplicationId: controlCallerApplicationId
    controlCallerPrincipalId: controlCallerPrincipalId
    tags: tags
  }
}

module verifier './modules/trusted-service-app.bicep' = {
  name: 'learningnemo-verifier-service-${environment}'
  params: {
    location: location
    appName: 'ca-${projectName}-verifier-${environment}'
    environmentId: managedEnvironment.id
    identityResourceId: verifierIdentity.id
    identityClientId: verifierIdentity.properties.clientId
    imageReference: imageReference
    registryServer: registryServer
    sqlServerHostname: sqlServerHostname
    sqlDatabaseName: databaseName
    serviceMode: 'verifier'
    tenantId: tenantId
    serviceApplicationId: serviceApplicationIds.verifier
    controlCallerApplicationId: controlCallerApplicationId
    controlCallerPrincipalId: controlCallerPrincipalId
    tags: tags
  }
}

output serviceNames array = [
  diagnostic.outputs.appName
  queryRunner.outputs.appName
  remediation.outputs.appName
  verifier.outputs.appName
]
