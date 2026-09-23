targetScope = 'resourceGroup'

param location string
param environment string
param projectName string
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
var registryName = take('cr${replace(toLower(projectName), '-', '')}${environment}${uniqueString(subscription().subscriptionId, resourceGroup().id)}', 50)
var acrPullRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    dataEndpointEnabled: false
    networkRuleBypassOptions: 'AzureServices'
    publicNetworkAccess: 'Enabled'
  }
}

resource diagnosticIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: 'id-${projectName}-diagnostic-${environment}'
}

resource queryRunnerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: 'id-${projectName}-query-runner-${environment}'
}

resource remediationIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: 'id-${projectName}-remediation-${environment}'
}

resource verifierIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: resourceGroup(platformResourceGroupName)
  name: 'id-${projectName}-verifier-${environment}'
}

resource sqlAdminIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: resourceGroup(databaseResourceGroupName)
  name: 'id-${projectName}-sql-admin-${environment}'
}

resource diagnosticPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, diagnosticIdentity.id, acrPullRoleDefinitionId)
  scope: registry
  properties: {
    principalId: diagnosticIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleDefinitionId
  }
}

resource queryRunnerPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, queryRunnerIdentity.id, acrPullRoleDefinitionId)
  scope: registry
  properties: {
    principalId: queryRunnerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleDefinitionId
  }
}

resource remediationPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, remediationIdentity.id, acrPullRoleDefinitionId)
  scope: registry
  properties: {
    principalId: remediationIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleDefinitionId
  }
}

resource verifierPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, verifierIdentity.id, acrPullRoleDefinitionId)
  scope: registry
  properties: {
    principalId: verifierIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleDefinitionId
  }
}

resource sqlAdminPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, sqlAdminIdentity.id, acrPullRoleDefinitionId)
  scope: registry
  properties: {
    principalId: sqlAdminIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleDefinitionId
  }
}

output registryName string = registry.name
output loginServer string = registry.properties.loginServer
output pullAssignmentCount int = 5