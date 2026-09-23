targetScope = 'resourceGroup'

param location string
param environmentId string
param registryServer string
@secure()
param image string
param expiresAt string
param tenantId string
param apiClientId string
param publicClientId string
param sqlServer string
param sqlDatabase string
param deployApps bool = false
param initiationEnabled bool = false
param diagnosticAudience string = ''
param queryAudience string = ''
param brokerAudience string = ''
param verifierAudience string = ''

var services = ['incident', 'review', 'execution']
var tags = {
  project: 'learningnemo'
  owner: 'learningnemo-portfolio'
  environment: 'dev'
  managedBy: 'bicep'
  disposable: 'true'
  expiresAt: expiresAt
  costProfile: 'scheduled-human-handoff'
}

resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for service in services: {
  name: 'id-learningnemo-${service}-dev'
  location: location
  tags: tags
}]

resource apps 'Microsoft.App/containerApps@2025-01-01' = [for (service, index) in services: if (deployApps) {
  name: 'ca-learningnemo-${service}-dev'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identities[index].id}': {} }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      maxInactiveRevisions: 1
      ingress: { external: false, allowInsecure: false, targetPort: 8080, transport: 'http' }
      registries: [{ server: registryServer, identity: identities[index].id }]
      secrets: []
    }
    template: {
      containers: [{
        name: service
        image: image
        command: ['python', '-m', 'task_agent.console.${service}_service']
        env: [
          { name: 'AZURE_CLIENT_ID', value: identities[index].properties.clientId }
          { name: 'ENTRA_TENANT_ID', value: tenantId }
          { name: 'ENTRA_CLIENT_ID', value: apiClientId }
          { name: 'ENTRA_PUBLIC_CLIENT_ID', value: publicClientId }
          { name: 'LEARNINGNEMO_SQL_SERVER', value: sqlServer }
          { name: 'LEARNINGNEMO_SQL_DATABASE', value: sqlDatabase }
          { name: 'LEARNINGNEMO_${toUpper(service)}_EXPIRES_AT', value: expiresAt }
          { name: 'LEARNINGNEMO_INITIATION_ENABLED', value: service == 'incident' && initiationEnabled ? 'true' : 'false' }
          { name: 'LEARNINGNEMO_DIAGNOSTIC_AUDIENCE', value: service == 'incident' ? diagnosticAudience : '' }
          { name: 'LEARNINGNEMO_QUERY_AUDIENCE', value: service == 'incident' ? queryAudience : '' }
          { name: 'LEARNINGNEMO_BROKER_AUDIENCE', value: service == 'execution' ? brokerAudience : '' }
          { name: 'LEARNINGNEMO_VERIFIER_AUDIENCE', value: service == 'execution' ? verifierAudience : '' }
          { name: 'LEARNINGNEMO_BROKER_ORIGIN', value: 'https://ca-learningnemo-remediation-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io' }
          { name: 'LEARNINGNEMO_VERIFIER_ORIGIN', value: 'https://ca-learningnemo-verifier-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io' }
        ]
        resources: { cpu: json('0.5'), memory: '1Gi' }
        probes: [
          { type: 'Startup', httpGet: { path: '/healthz', port: 8080 }, failureThreshold: 20, periodSeconds: 5 }
          { type: 'Liveness', httpGet: { path: '/healthz', port: 8080 }, failureThreshold: 3, periodSeconds: 30 }
          { type: 'Readiness', httpGet: { path: '/readyz', port: 8080 }, failureThreshold: 3, periodSeconds: 15, timeoutSeconds: 20 }
        ]
      }]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}]

output principalIds array = [for (service, index) in services: { service: service, principalId: identities[index].properties.principalId }]
output endpoints array = [for (service, index) in services: { service: service, hostname: deployApps ? apps[index].properties.configuration.ingress.fqdn : '' }]