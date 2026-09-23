targetScope = 'subscription'

param location string = 'eastus'
param expiresAt string = ''
@allowed(['leased', 'operator-managed'])
param availabilityMode string = 'leased'
param environmentId string
param environmentDomain string
param registryName string
param registryServer string
@secure()
param consoleImage string
@secure()
param agentImage string
@secure()
param tenantId string
@secure()
param apiClientId string
@secure()
param publicClientId string
param vaultName string = 'kvnemo8370187d'
param apimOrigin string
param reviewOrigin string = ''
param incidentOrigin string = ''
param executionOrigin string = ''
param invoiceOperatorOrigin string = ''
param invoiceReviewOrigin string = ''

var tags = union({
  project: 'learningnemo'
  environment: 'dev'
  managedBy: 'bicep'
  owner: 'learningnemo-portfolio'
  costProfile: 'scheduled-cloud-demo'
  dataClassification: 'synthetic'
}, availabilityMode == 'operator-managed' ? { availabilityMode: availabilityMode, disposable: 'false' } : { disposable: 'true', expiresAt: expiresAt })

resource group 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-learningnemo-demo-dev'
  location: location
  tags: tags
}

module identities './modules/cloud-demo-identities.bicep' = {
  name: 'learningnemo-cloud-demo-identities'
  scope: group
  params: { location: location, tags: tags }
}

module registryAccess './modules/cloud-demo-registry-access.bicep' = {
  name: 'learningnemo-cloud-demo-registry-access'
  scope: resourceGroup('rg-learningnemo-artifacts-dev')
  params: {
    registryName: registryName
    principalIds: identities.outputs.principalIds
  }
}

module secretAccess './modules/cloud-demo-secret-access.bicep' = {
  name: 'learningnemo-cloud-demo-secret-access'
  scope: resourceGroup('rg-nemo-agent-dev')
  params: { vaultName: vaultName, principalId: identities.outputs.principalIds[2] }
}

module workspaceAccess './modules/cloud-demo-workspace-access.bicep' = {
  name: 'learningnemo-cloud-demo-workspace-access'
  scope: resourceGroup('rg-learningnemo-saw-dev')
  params: { principalId: identities.outputs.principalIds[1] }
}

module diagnosticAccess './modules/cloud-demo-diagnostic-access.bicep' = {
  name: 'learningnemo-cloud-demo-diagnostic-access'
  scope: resourceGroup('rg-learningnemo-platform-dev')
  params: { principalId: identities.outputs.principalIds[1] }
}

module apps './modules/cloud-demo-apps.bicep' = {
  name: 'learningnemo-cloud-demo-apps'
  scope: group
  params: {
    location: location
    tags: tags
    environmentId: environmentId
    environmentDomain: environmentDomain
    registryServer: registryServer
    consoleImage: consoleImage
    agentImage: agentImage
    identityIds: identities.outputs.identityIds
    clientIds: identities.outputs.clientIds
    tenantId: tenantId
    apiClientId: apiClientId
    publicClientId: publicClientId
    vaultName: vaultName
    apimOrigin: apimOrigin
    reviewOrigin: reviewOrigin
    incidentOrigin: incidentOrigin
    executionOrigin: executionOrigin
    invoiceOperatorOrigin: invoiceOperatorOrigin
    invoiceReviewOrigin: invoiceReviewOrigin
  }
  dependsOn: [registryAccess, secretAccess, workspaceAccess, diagnosticAccess]
}

output dashboardUrl string = apps.outputs.dashboardUrl