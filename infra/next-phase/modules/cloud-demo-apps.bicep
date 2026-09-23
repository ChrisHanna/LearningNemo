param location string
param tags object
param environmentId string
param environmentDomain string
param registryServer string
@secure()
param consoleImage string
@secure()
param agentImage string
param identityIds array
param clientIds array
@secure()
param tenantId string
@secure()
param apiClientId string
@secure()
param publicClientId string
param vaultName string
param apimOrigin string
param reviewOrigin string = ''
param incidentOrigin string = ''
param executionOrigin string = ''
param invoiceOperatorOrigin string = ''
param invoiceReviewOrigin string = ''

var dashboardName = 'ca-learningnemo-dashboard-dev'
var controllerName = 'ca-learningnemo-controller-dev'
var agentName = 'ca-learningnemo-agent-dev'
var entra = [
  { name: 'ENTRA_TENANT_ID', value: tenantId }
  { name: 'ENTRA_CLIENT_ID', value: apiClientId }
  { name: 'ENTRA_PUBLIC_CLIENT_ID', value: publicClientId }
]
var services = [
  {
    name: dashboardName
    identity: identityIds[0]
    image: consoleImage
    command: ['python', '-m', 'task_agent.console.cloud']
    port: 8080
    external: true
    cpu: '0.5'
    memory: '1Gi'
    health: '/healthz'
    env: concat(entra, [
      { name: 'LEARNINGNEMO_PUBLIC_ORIGIN', value: 'https://${dashboardName}.${environmentDomain}' }
      { name: 'LEARNINGNEMO_CONTROLLER_ORIGIN', value: 'https://${controllerName}.internal.${environmentDomain}' }
      { name: 'LEARNINGNEMO_CLOUD_AGENT_URL', value: 'https://${agentName}.internal.${environmentDomain}/v1/chat/completions' }
    ], empty(reviewOrigin) ? [] : [{ name: 'LEARNINGNEMO_REVIEW_ORIGIN', value: reviewOrigin }], empty(incidentOrigin) ? [] : [{ name: 'LEARNINGNEMO_INCIDENT_ORIGIN', value: incidentOrigin }], empty(executionOrigin) ? [] : [{ name: 'LEARNINGNEMO_EXECUTION_ORIGIN', value: executionOrigin }], empty(invoiceOperatorOrigin) ? [] : [{ name: 'LEARNINGNEMO_INVOICE_OPERATOR_ORIGIN', value: invoiceOperatorOrigin }, { name: 'LEARNINGNEMO_INVOICE_REVIEW_ORIGIN', value: invoiceReviewOrigin }])
  }
  {
    name: controllerName
    identity: identityIds[1]
    image: consoleImage
    command: ['python', '-m', 'task_agent.console.cloud_controller']
    port: 8080
    external: false
    cpu: '0.5'
    memory: '1Gi'
    health: '/healthz'
    env: concat(entra, [
      { name: 'AZURE_CLIENT_ID', value: clientIds[1] }
      { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
    ])
  }
  {
    name: agentName
    identity: identityIds[2]
    image: agentImage
    command: ['python', '/app/scripts/run-cloud-agent.py']
    port: 8001
    external: false
    cpu: '2.0'
    memory: '4Gi'
    health: '/health'
    env: concat(entra, [
      { name: 'AZURE_CLIENT_ID', value: clientIds[2] }
      { name: 'LEARNINGNEMO_GATEWAY_VAULT', value: vaultName }
      { name: 'OPENAI_BASE_URL', value: '${apimOrigin}/llm/v1' }
      { name: 'OPENAI_GUARDRAIL_BASE_URL', value: '${apimOrigin}/llm/v1/guardrails' }
      { name: 'NAT_TELEMETRY_ENABLED', value: 'false' }
    ])
  }
]

resource apps 'Microsoft.App/containerApps@2025-01-01' = [for service in services: {
  name: service.name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${service.identity}': {} }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      maxInactiveRevisions: 1
      ingress: {
        external: service.external
        allowInsecure: false
        targetPort: service.port
        transport: 'auto'
        stickySessions: { affinity: 'sticky' }
      }
      registries: [{ server: registryServer, identity: service.identity }]
      secrets: []
    }
    template: {
      containers: [{
        name: 'service'
        image: service.image
        command: service.command
        env: service.env
        resources: { cpu: json(service.cpu), memory: service.memory }
        probes: [
          { type: 'Startup', httpGet: { path: service.health, port: service.port }, failureThreshold: 60, periodSeconds: 5, timeoutSeconds: 3 }
          { type: 'Readiness', httpGet: { path: service.health, port: service.port }, failureThreshold: 3, periodSeconds: 15, timeoutSeconds: 3 }
          { type: 'Liveness', httpGet: { path: service.health, port: service.port }, failureThreshold: 3, periodSeconds: 30, timeoutSeconds: 3 }
        ]
      }]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}]
output dashboardUrl string = 'https://${dashboardName}.${environmentDomain}'