targetScope = 'resourceGroup'
param location string = 'eastus'
param environmentId string
param registryServer string
param image string
param agentImage string
param expiresAt string = ''
@allowed(['leased', 'operator-managed'])
param availabilityMode string = 'leased'
param tenantId string
param apiClientId string
param publicClientId string
param sqlServer string
param modelOrigin string
param guardrailOrigin string
param subscriptionId string
param deployApps bool = false
var kinds = ['operator', 'review', 'planning', 'execution', 'verifier']
var tags = union({ project: 'learningnemo', owner: 'learningnemo-portfolio', purpose: 'invoice-agent-workflow' }, availabilityMode == 'operator-managed' ? { availabilityMode: availabilityMode, disposable: 'false' } : { disposable: 'true', expiresAt: expiresAt })
resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for kind in kinds: {
  name: 'id-learningnemo-invoice-${kind}-dev'
  location: location
  tags: tags
}]
resource simulatorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-learningnemo-invoice-simulator-dev'
  location: location
  tags: tags
}
resource apps 'Microsoft.App/containerApps@2025-01-01' = [for (kind,index) in kinds: if (deployApps) {
  name: 'ca-nemo-invoice-${kind}-dev'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: union({ '${identities[index].id}': {} }, kind == 'operator' ? { '${simulatorIdentity.id}': {} } : {}) }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      maxInactiveRevisions: 1
      ingress: { external: contains(['planning','execution'],kind), allowInsecure: false, targetPort: 8080, transport: 'http' }
      registries: [{ server: registryServer, identity: identities[index].id }]
      secrets: []
    }
    template: {
      containers: [{
        name: kind
        image: image
        command: ['python', '-m', 'task_agent.console.invoice_deployed']
        env: [
          { name: 'INVOICE_SERVICE_KIND', value: kind }
          { name: 'AZURE_CLIENT_ID', value: identities[index].properties.clientId }
          { name: 'INVOICE_SIMULATOR_CLIENT_ID', value: kind == 'operator' ? simulatorIdentity.properties.clientId : '' }
          { name: 'AZURE_SUBSCRIPTION_ID', value: subscriptionId }
          { name: 'ENTRA_TENANT_ID', value: tenantId }
          { name: 'ENTRA_CLIENT_ID', value: apiClientId }
          { name: 'ENTRA_PUBLIC_CLIENT_ID', value: publicClientId }
          { name: 'LEARNINGNEMO_SQL_SERVER', value: sqlServer }
          { name: 'LEARNINGNEMO_SQL_DATABASE', value: 'learningnemo' }
          { name: 'INVOICE_EXPIRES_AT', value: expiresAt }
          { name: 'INVOICE_AVAILABILITY_MODE', value: availabilityMode }
          { name: 'INVOICE_AGENT_IMAGE', value: agentImage }
          { name: 'INVOICE_OPERATOR_OBJECT_ID', value: identities[0].properties.principalId }
          { name: 'INVOICE_VERIFIER_AUDIENCE', value: 'api://${apiClientId}' }
          { name: 'OPENAI_BASE_URL', value: modelOrigin }
          { name: 'OPENAI_GUARDRAIL_BASE_URL', value: guardrailOrigin }
        ]
        resources: { cpu: json('0.5'), memory: '1Gi' }
        probes: [{ type: 'Startup', httpGet: { path: '/healthz', port: 8080 }, failureThreshold: 24, periodSeconds: 5 }]
      }]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}]